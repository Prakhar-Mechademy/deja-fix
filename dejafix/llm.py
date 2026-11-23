"""LLM access (Gemini REST API, no SDK needed) with token logging, timeouts and safe failure.

Every call is written to the llm_calls table with tokens in/out and the session it belongs to.
Errors (timeout, outage, malformed output) raise LLMError, which callers turn into a plain message.
If no GEMINI_API_KEY is set, a deterministic offline 'mock' provider is used so the demo still runs."""
from __future__ import annotations

import hashlib
import json
import math
import re
import socket
import time
import urllib.error
import urllib.request

from . import config, db, mock_llm

API = "https://generativelanguage.googleapis.com/v1beta/models"
_working_model = None


class LLMError(Exception):
    """Raised for timeouts, provider outages and malformed responses."""


def estimate_tokens(text: str) -> int:
    return max(1, len(text or "") // 4)


# ---------------------------------------------------------------- Gemini transport

def _post(url: str, body: dict) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", "x-goog-api-key": config.gemini_key()})
    with urllib.request.urlopen(req, timeout=config.LLM_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _gemini_generate(prompt: str, system: str | None, json_mode: bool):
    global _working_model
    gen_cfg = {"temperature": 0.2}
    if json_mode:
        gen_cfg["responseMimeType"] = "application/json"
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": gen_cfg}
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    budget = config.env("GEMINI_THINKING_BUDGET", "0")
    if budget != "" and "2.5" in (_working_model or config.GEMINI_MODEL):
        gen_cfg["thinkingConfig"] = {"thinkingBudget": int(budget)}

    models = [_working_model] if _working_model else [config.GEMINI_MODEL] + config.GEMINI_FALLBACK_MODELS
    last_err = "no model tried"
    for model in models:
        for attempt in range(2):  # one retry for transient failures
            try:
                data = _post(f"{API}/{model}:generateContent", body)
                _working_model = model
                return data, model
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "ignore")[:300]
                last_err = f"HTTP {e.code}: {detail}"
                if e.code == 404:
                    break  # model not available, try the next one
                if e.code == 400 and "thinking" in detail.lower() and "thinkingConfig" in gen_cfg:
                    gen_cfg.pop("thinkingConfig")  # model does not accept a thinking budget: retry without
                    continue
                if e.code in (429, 500, 502, 503, 504) and attempt == 0:
                    time.sleep(1.5)
                    continue
                raise LLMError(last_err)
            except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as e:
                last_err = f"network/timeout: {e}"
                if attempt == 0:
                    time.sleep(1.0)
                    continue
                raise LLMError(last_err)
            except ValueError as e:
                raise LLMError(f"malformed provider response: {e}")
    raise LLMError(last_err)


def _parse_gemini(data: dict):
    try:
        cand = data["candidates"][0]
        parts = cand.get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    except (KeyError, IndexError, TypeError, AttributeError):
        raise LLMError("malformed provider response: no candidates")
    if not text.strip():
        raise LLMError(f"empty response (finishReason={data.get('candidates', [{}])[0].get('finishReason')})")
    usage = data.get("usageMetadata", {}) or {}
    t_in = usage.get("promptTokenCount")
    t_out = (usage.get("candidatesTokenCount") or 0) + (usage.get("thoughtsTokenCount") or 0)
    return text, t_in, t_out


# ---------------------------------------------------------------- public API

def generate(prompt: str, *, purpose: str, session_id=None, user_id=None, system=None, json_mode=False) -> str:
    provider = config.provider()
    start = time.time()
    model = "mock"
    try:
        if provider == "gemini":
            data, model = _gemini_generate(prompt, system, json_mode)
            text, t_in, t_out = _parse_gemini(data)
            estimated = t_in is None
            if t_in is None:
                t_in, t_out = estimate_tokens((system or "") + prompt), estimate_tokens(text)
        else:
            text = mock_llm.generate(prompt, purpose)
            t_in, t_out, estimated = estimate_tokens((system or "") + prompt), estimate_tokens(text), True
    except LLMError as e:
        db.log_llm_call(session_id, user_id, purpose, provider, model, estimate_tokens(prompt), 0, True,
                        int((time.time() - start) * 1000), False, str(e))
        raise
    db.log_llm_call(session_id, user_id, purpose, provider, model, t_in, t_out, estimated,
                    int((time.time() - start) * 1000), True)
    return text


def parse_json(text: str):
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.I)
    try:
        return json.loads(cleaned)
    except ValueError:
        pass
    m = re.search(r"\{.*\}", cleaned, flags=re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except ValueError:
            pass
    raise LLMError("malformed JSON from model")


def generate_json(prompt: str, *, purpose: str, session_id=None, user_id=None, system=None) -> dict:
    """JSON call with one retry if the model returns something we cannot parse."""
    text = generate(prompt, purpose=purpose, session_id=session_id, user_id=user_id, system=system, json_mode=True)
    try:
        data = parse_json(text)
    except LLMError:
        text = generate(prompt + "\n\nReturn ONLY one valid JSON object, nothing else.", purpose=purpose,
                        session_id=session_id, user_id=user_id, system=system, json_mode=True)
        data = parse_json(text)
    if not isinstance(data, dict):
        raise LLMError("model JSON is not an object")
    return data


# ---------------------------------------------------------------- embeddings

STOP = set("""a an the and or but if is are was were be been being to of in on at for with by from as it its this that
these those i me my we our you your he she they them their do does did done have has had not no so too very can could
would should will just than then there here what which who when where why how all any some into out up down over
again also am im ive dont doesnt didnt cant wont isnt arent please hi hello thanks thank help issue problem""".split())

SYNONYMS = {
    "syncing": "sync", "synced": "sync", "synchronize": "sync", "synchronise": "sync", "synchronization": "sync",
    "updating": "update", "updated": "update", "updates": "update", "refresh": "update", "refreshing": "update",
    "mobile": "phone", "smartphone": "phone", "cellphone": "phone", "android": "phone", "iphone": "phone",
    "laptop": "computer", "pc": "computer", "desktop": "computer", "mac": "computer", "macbook": "computer",
    "appear": "show", "appearing": "show", "showing": "show", "shows": "show", "visible": "show",
    "crashes": "crash", "crashing": "crash", "crashed": "crash", "closes": "crash", "freezes": "crash",
    "devices": "device", "notes": "note", "edits": "edit", "edited": "edit", "editing": "edit", "changes": "edit",
    "login": "signin", "log": "signin", "logging": "signin", "sign": "signin", "signing": "signin",
}


def _tokens(text: str):
    words = re.findall(r"[a-z0-9]+", (text or "").lower().replace("'", ""))
    out = []
    for w in words:
        if w in STOP or len(w) < 2:
            continue
        w = SYNONYMS.get(w, w)
        if len(w) > 4 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        out.append(w)
    return out


def local_embed(text: str, dims: int = 512):
    """Offline fallback embedding: hashed bag of normalized words + bigrams."""
    vec = [0.0] * dims
    toks = _tokens(text)
    feats = toks + [a + "_" + b for a, b in zip(toks, toks[1:])]
    for f in feats:
        h = int(hashlib.md5(f.encode()).hexdigest(), 16)
        vec[h % dims] += 1.0 if "_" not in f else 0.5
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def cosine(a, b) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


def embed(text: str, *, session_id=None, user_id=None):
    """Returns (vector, model_name). Falls back to the local embedding if the provider fails."""
    if config.provider() == "gemini":
        start = time.time()
        model = config.GEMINI_EMBED_MODEL
        try:
            body = {"model": f"models/{model}", "content": {"parts": [{"text": text}]},
                    "taskType": "SEMANTIC_SIMILARITY", "outputDimensionality": 768}
            data = _post(f"{API}/{model}:embedContent", body)
            values = data["embedding"]["values"]
            db.log_llm_call(session_id, user_id, "embedding", "gemini", model, estimate_tokens(text), 0, True,
                            int((time.time() - start) * 1000), True)
            return values, model
        except Exception as e:  # never let retrieval crash the chat
            db.log_llm_call(session_id, user_id, "embedding", "gemini", model, estimate_tokens(text), 0, True,
                            int((time.time() - start) * 1000), False, str(e))
    return local_embed(text), "local-hash"
