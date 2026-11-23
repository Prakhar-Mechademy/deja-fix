import json
import math
import os
import re
import socket
import time
import urllib.error
import urllib.request
from typing import Any

from backend.db import log_token_usage


def get_text_model() -> str:
    return (os.environ.get("GEMINI_TEXT_MODEL") or "gemini-2.5-flash").strip()


def get_embedding_model() -> str:
    return (os.environ.get("GEMINI_EMBEDDING_MODEL") or "gemini-embedding-001").strip()


def is_mock_llm_enabled() -> bool:
    return os.environ.get("DEJAFIX_MOCK_LLM", "").strip() == "1"


API_BASE = "https://generativelanguage.googleapis.com/v1beta"

INPUT_COST_PER_1M = 0.15
OUTPUT_COST_PER_1M = 0.60
EMBED_COST_PER_1M = 0.02


class LLMUnavailableError(Exception):
    def __init__(self, message: str, user_message: str, status: int = 503) -> None:
        super().__init__(message)
        self.status = status
        self.user_message = user_message


def _get_api_key() -> str:
    api_key = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if not api_key or api_key == "MY_GEMINI_API_KEY":
        raise LLMUnavailableError(
            "GEMINI_API_KEY is not configured.",
            "The support assistant is temporarily unable to reach the language model because the API key is missing.",
            503,
        )
    return api_key


def _get_timeout_seconds() -> float:
    try:
        ms = float(os.environ.get("GEMINI_TIMEOUT_MS", "25000"))
        return max(2.0, ms / 1000.0)
    except ValueError:
        return 25.0


def _estimate_cost_usd(model: str, tokens_in: int, tokens_out: int) -> float:
    if model == get_embedding_model():
        return round((tokens_in / 1_000_000) * EMBED_COST_PER_1M, 8)
    cost = (tokens_in / 1_000_000) * INPUT_COST_PER_1M + (tokens_out / 1_000_000) * OUTPUT_COST_PER_1M
    return round(cost, 8)


# ---------------------------------------------------------------------------
# Deterministic Mock LLM (enabled ONLY when DEJAFIX_MOCK_LLM=1 for CI/evals)
# ---------------------------------------------------------------------------

_MOCK_CONCEPT_BUCKETS: list[tuple[int, tuple[str, ...]]] = [
    (0, ("preparing", "stuck", "hangs", "hanging", "indefinitely", "upload", "transferring", "queue", "stalled", "index", "lock", "pushing")),
    (1, ("401", "unauthorized", "2fa", "authenticator", "expired", "token", "login", "credentials", "sso", "oauth", "password")),
    (2, ("crash", "crashes", "segfault", "launch", "startup", "quits", "aborts", "exit")),
    (3, ("disk", "space", "enospc", "storage", "quota", "full", "bytes")),
    (4, ("ssl", "certificate", "proxy", "tls", "firewall", "handshake", "vpn")),
]


def _mock_embed_vector(text: str) -> list[float]:
    lower = text.lower()
    vec = [0.0] * 16
    for idx, keywords in _MOCK_CONCEPT_BUCKETS:
        hits = sum(1 for kw in keywords if kw in lower)
        if hits > 0:
            vec[idx * 2] = float(hits * 2.0)
            vec[idx * 2 + 1] = float(hits)
    # Small shared domain weight for generic sync terms so negative matches test Stage 2 gating
    if "sync" in lower or "nimbussync" in lower or "cloud" in lower or "drive" in lower:
        vec[12] = 0.8
    if "windows" in lower or "macos" in lower or "linux" in lower:
        vec[13] = 0.25
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0.0:
        vec[15] = 1.0
        return vec
    return [round(v / norm, 6) for v in vec]


def _dominant_bucket(text: str) -> int | None:
    lower = text.lower()
    best_idx: int | None = None
    best_hits = 0
    for idx, keywords in _MOCK_CONCEPT_BUCKETS:
        hits = sum(1 for kw in keywords if kw in lower)
        if hits > best_hits:
            best_hits = hits
            best_idx = idx
    return best_idx


def _mock_generate_json(call_type: str, contents: str) -> tuple[dict[str, Any], int, int]:
    if call_type == "confirmation_check":
        lower = contents.lower()
        if any(w in lower for w in ("worked", "fixed", "resolved", "syncing normally", "solved")):
            return {"outcome": "confirmed", "reason": "Customer explicitly confirmed resolution."}, 90, 18
        if any(w in lower for w in ("did not work", "didn't work", "still broken", "not working")):
            return {"outcome": "failed", "reason": "Customer reported the fix did not work."}, 90, 18
        return {"outcome": "ongoing", "reason": "Troubleshooting continues."}, 90, 18

    if call_type == "hub_verify":
        already_clarified = "alreadyclarified=true" in contents.lower()
        cust_section = contents.split("Candidate Verified Solutions:")[0]
        cust_bucket = _dominant_bucket(cust_section)

        candidates_raw = contents.split("Candidate Verified Solutions:")[1] if "Candidate Verified Solutions:" in contents else ""
        matched_id = 0
        for block in candidates_raw.split("Candidate ID "):
            block = block.strip()
            if not block:
                continue
            cid_match = re.match(r"^(\d+)", block)
            if not cid_match:
                continue
            cid = int(cid_match.group(1))
            cand_bucket = _dominant_bucket(block)
            if cust_bucket is not None and cand_bucket == cust_bucket:
                matched_id = cid
                break

        if matched_id == 0:
            return {
                "matchedSolutionId": 0,
                "needsClarification": False,
                "clarifyingQuestion": "",
                "reason": "Negative match rejected: symptoms map to a different root cause.",
            }, 160, 28

        has_env = any(os_kw in cust_section.lower() for os_kw in ("windows", "macos", "linux", "ios", "android")) and bool(
            re.search(r"v?\d+\.\d+", cust_section)
        )
        needs_clarify = (not already_clarified) and (not has_env)
        return {
            "matchedSolutionId": matched_id,
            "needsClarification": needs_clarify,
            "clarifyingQuestion": (
                "Before I share the verified fix, could you confirm your device, operating system, "
                "and NimbusSync app version, and whether the client is stuck on 'Preparing sync...'?"
                if needs_clarify
                else ""
            ),
            "reason": "Root fault matches verified Knowledge Hub entry.",
        }, 175, 38

    if call_type == "duplicate_verify":
        parts = contents.split("New Solution:")
        existing_part = parts[0] if len(parts) > 0 else ""
        new_part = parts[1] if len(parts) > 1 else ""
        b1 = _dominant_bucket(existing_part)
        b2 = _dominant_bucket(new_part)
        same = b1 is not None and b1 == b2
        return {
            "isSameProblemAndFix": same,
            "stepPolicy": "keep_existing",
            "reason": "Both entries target the same underlying fault and fix." if same else "Different root issues.",
        }, 140, 24

    if call_type == "extractor":
        already_match = re.search(r"Already extracted in this session:\s*(.+)", contents)
        already_text = already_match.group(1).lower() if already_match else ""
        lower = contents.lower()

        dev = "Dell XPS 15" if "dell xps 15" in lower else ("MacBook Pro" if "macbook pro" in lower else "Desktop client")
        os_name = "Windows 11" if "windows 11" in lower else ("macOS 15.1" if "macos" in lower else "Desktop OS")
        ver_match = re.search(r"v?(\d+\.\d+\.\d+)", contents)
        app_ver = f"v{ver_match.group(1)}" if ver_match else "v4.2.1"

        solutions: list[dict[str, Any]] = []
        # Extract newly confirmed sync-queue problem if not already extracted
        if ("preparing sync" in lower or "stuck" in lower or "hangs" in lower) and "preparing sync" not in already_text:
            failed = ["Restarting Wi-Fi"] if "wi-fi" in lower or "wifi" in lower else []
            solutions.append(
                {
                    "symptoms": 'Desktop sync client stalls indefinitely at "Preparing sync..." and fails to upload files due to a stale local index lock.',
                    "device": dev,
                    "os": os_name,
                    "app_version": app_ver,
                    "fix_steps": [
                        "Quit NimbusSync completely from the system tray.",
                        "Delete the stale local index lock file in the NimbusSync cache directory.",
                        "Relaunch NimbusSync and allow the upload queue to resume.",
                    ],
                    "what_did_not_work": failed,
                }
            )
        if ("401" in lower or "2fa" in lower) and "401" not in already_text and "auth" not in already_text:
            solutions.append(
                {
                    "symptoms": "NimbusSync fails to sync with Error 401 unauthorized after rotating a 2FA authenticator key.",
                    "device": dev,
                    "os": os_name,
                    "app_version": app_ver,
                    "fix_steps": [
                        "Sign out of the NimbusSync desktop client.",
                        "Clear cached session tokens and re-authenticate with the new 2FA code.",
                    ],
                    "what_did_not_work": [],
                }
            )
        if not solutions and "none" in already_text:
            solutions.append(
                {
                    "symptoms": "NimbusSync sync failure resolved after applying backend troubleshooting steps.",
                    "device": dev,
                    "os": os_name,
                    "app_version": app_ver,
                    "fix_steps": ["Reset the local NimbusSync state cache and restart the application."],
                    "what_did_not_work": [],
                }
            )

        return {
            "session_topic_summary": "NimbusSync sync queue stuck preparing upload",
            "solutions": solutions,
        }, 420, 190

    return {}, 100, 20


def _mock_generate_text(call_type: str, contents: Any) -> tuple[str, int, int]:
    raw_text = json.dumps(contents)
    if call_type == "persona_reply":
        steps = re.findall(r"^\d+\.\s+.+$", str(contents), flags=re.MULTILINE)
        formatted_steps = "\n".join(steps) if steps else "1. Clear the stale local index lock and restart NimbusSync."
        reply = (
            "Here is the verified fix from our Knowledge Hub:\n"
            f"{formatted_steps}\n\n"
            "Please try these steps and let me know if they resolve your sync issue."
        )
        return reply, 145, 72

    if call_type == "escalation":
        lower = raw_text.lower()
        if "401" in lower or "2fa" in lower:
            reply = (
                "An Error 401 after rotating your 2FA key means your desktop client still holds a revoked OAuth refresh token:\n"
                "1. Open NimbusSync Settings -> Account and click Sign Out.\n"
                "2. Clear the stored credential token from your OS keychain.\n"
                "3. Sign back in using your updated 2FA authenticator code."
            )
        elif "crash" in lower or "segfault" in lower:
            reply = (
                "A launch crash after an OS update typically points to a corrupted GPU/webview cache or incompatible binary:\n"
                "1. Launch NimbusSync from terminal with --disable-gpu to isolate renderer crashes.\n"
                "2. Reinstall the latest signed client build for your OS."
            )
        else:
            reply = (
                "When NimbusSync remains stuck at 'Preparing sync...' even after restarting Wi-Fi, the local SQLite sync index lock file is typically orphaned:\n"
                "1. Fully quit NimbusSync from the system tray/task manager.\n"
                "2. Navigate to your local NimbusSync app data cache folder and remove the `.sync.lock` file.\n"
                "3. Restart NimbusSync so it rebuilds the pending upload queue cleanly.\n\n"
                "Let me know once you try these steps and whether your files start uploading."
            )
        # Escalation receives full conversation and emits detailed reasoning -> higher token footprint
        return reply, max(260, math.ceil(len(raw_text) / 3)), 340

    return "I can help look into that.", 120, 40


# ---------------------------------------------------------------------------
# Live Gemini REST API Calls
# ---------------------------------------------------------------------------


def _post_json(path: str, payload: dict[str, Any], operation_name: str) -> dict[str, Any]:
    api_key = _get_api_key()
    timeout_s = _get_timeout_seconds()
    url = f"{API_BASE}{path}"
    body = json.dumps(payload).encode("utf-8")

    last_error: Exception | None = None
    for attempt in range(3):
        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": api_key,
                "User-Agent": "aistudio-build",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                raw = resp.read().decode("utf-8")
            parsed = json.loads(raw)
            if not isinstance(parsed, dict):
                raise LLMUnavailableError(
                    f"Malformed response object from {operation_name}",
                    "The support system received an unexpected response format. Please try again.",
                    502,
                )
            return parsed
        except urllib.error.HTTPError as err:
            err_body = ""
            try:
                err_body = err.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            last_error = err
            if err.code in (400, 401, 403, 404):
                raise LLMUnavailableError(
                    f"{operation_name} failed ({err.code}): {err_body}",
                    "The support model rejected the request configuration. Please verify the model or API key setup.",
                    502,
                ) from err
            if attempt == 2:
                break
        except (TimeoutError, socket.timeout) as err:
            raise LLMUnavailableError(
                f"{operation_name} timed out after {timeout_s:.1f}s",
                "The support model took too long to respond. Please try sending your message again.",
                504,
            ) from err
        except (urllib.error.URLError, json.JSONDecodeError, OSError) as err:
            last_error = err
            if attempt == 2:
                break

        time.sleep(0.35 * (attempt + 1))

    raise LLMUnavailableError(
        f"{operation_name} failed: {last_error}",
        "The support model is temporarily unavailable. Your message was saved—please try again in a moment.",
        503,
    )


def _extract_text_from_response(data: dict[str, Any]) -> str:
    candidates = data.get("candidates") or []
    if not candidates or not isinstance(candidates, list):
        return ""
    parts = (candidates[0].get("content") or {}).get("parts") or []
    chunks: list[str] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        if part.get("thought") is True:
            continue
        text = part.get("text")
        if isinstance(text, str):
            chunks.append(text)
    return "".join(chunks).strip()


def call_gemini_text(
    session_id: int,
    user_id: int,
    call_type: str,
    contents: str | list[dict[str, Any]],
    system_instruction: str | None = None,
    low_thinking: bool = False,
) -> dict[str, Any]:
    text_model = get_text_model()

    if is_mock_llm_enabled():
        text, tokens_in, tokens_out = _mock_generate_text(call_type, contents)
        log_token_usage(
            session_id=session_id,
            user_id=user_id,
            call_type=call_type,
            model=f"{text_model}:mock",
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            estimated_cost_usd=_estimate_cost_usd(text_model, tokens_in, tokens_out),
        )
        return {"text": text, "tokens_in": tokens_in, "tokens_out": tokens_out}

    formatted_contents = (
        [{"role": "user", "parts": [{"text": contents}]}]
        if isinstance(contents, str)
        else contents
    )
    payload: dict[str, Any] = {"contents": formatted_contents}
    if system_instruction:
        payload["systemInstruction"] = {"parts": [{"text": system_instruction}]}

    data = _post_json(
        f"/models/{text_model}:generateContent",
        payload,
        f"generateContent:{call_type}",
    )
    text = _extract_text_from_response(data)
    if not text:
        time.sleep(0.5)
        data = _post_json(
            f"/models/{text_model}:generateContent",
            payload,
            f"generateContentRetry:{call_type}",
        )
        text = _extract_text_from_response(data)
    if not text:
        raise LLMUnavailableError(
            f"Empty text response after retry from {text_model} ({call_type})",
            "The support model did not produce a usable answer after retrying. Please rephrase the issue or try again.",
            502,
        )

    usage = data.get("usageMetadata") or {}
    tokens_in = int(usage.get("promptTokenCount") or max(1, math.ceil(len(json.dumps(formatted_contents)) / 4)))
    raw_out = int(usage.get("candidatesTokenCount") or 0) + int(usage.get("thoughtsTokenCount") or 0)
    tokens_out = raw_out if raw_out > 0 else max(1, math.ceil(len(text) / 4))

    log_token_usage(
        session_id=session_id,
        user_id=user_id,
        call_type=call_type,
        model=text_model,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        estimated_cost_usd=_estimate_cost_usd(text_model, tokens_in, tokens_out),
    )

    return {"text": text, "tokens_in": tokens_in, "tokens_out": tokens_out}


def call_gemini_json(
    session_id: int,
    user_id: int,
    call_type: str,
    contents: str,
    response_schema: dict[str, Any],
    system_instruction: str | None = None,
    low_thinking: bool = False,
) -> dict[str, Any]:
    text_model = get_text_model()

    if is_mock_llm_enabled():
        parsed, tokens_in, tokens_out = _mock_generate_json(call_type, contents)
        log_token_usage(
            session_id=session_id,
            user_id=user_id,
            call_type=call_type,
            model=f"{text_model}:mock",
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            estimated_cost_usd=_estimate_cost_usd(text_model, tokens_in, tokens_out),
        )
        return {"data": parsed, "tokens_in": tokens_in, "tokens_out": tokens_out}

    payload: dict[str, Any] = {
        "contents": [{"role": "user", "parts": [{"text": contents}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": response_schema,
        },
    }
    if system_instruction:
        payload["systemInstruction"] = {"parts": [{"text": system_instruction}]}

    data = _post_json(
        f"/models/{text_model}:generateContent",
        payload,
        f"generateJson:{call_type}",
    )
    raw = _extract_text_from_response(data)

    usage = data.get("usageMetadata") or {}
    tokens_in = int(usage.get("promptTokenCount") or max(1, math.ceil(len(contents) / 4)))
    raw_out = int(usage.get("candidatesTokenCount") or 0) + int(usage.get("thoughtsTokenCount") or 0)
    tokens_out = raw_out if raw_out > 0 else max(1, math.ceil(len(raw) / 4))

    log_token_usage(
        session_id=session_id,
        user_id=user_id,
        call_type=call_type,
        model=text_model,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        estimated_cost_usd=_estimate_cost_usd(text_model, tokens_in, tokens_out),
    )

    stripped = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
    stripped = re.sub(r"```\s*$", "", stripped).strip()
    try:
        parsed = json.loads(stripped)
        if not isinstance(parsed, dict):
            raise ValueError("Parsed JSON is not an object")
        return {"data": parsed, "tokens_in": tokens_in, "tokens_out": tokens_out}
    except Exception as err:
        raise LLMUnavailableError(
            f"Malformed JSON from {text_model} ({call_type}): {err}",
            "The support system received a malformed response from the model. Please try your request again.",
            502,
        ) from err


def embed_text(session_id: int, user_id: int, text: str) -> list[float]:
    clean_text = text.strip() or "empty query"
    embed_model = get_embedding_model()

    if is_mock_llm_enabled():
        vec = _mock_embed_vector(clean_text)
        approx_in = max(1, math.ceil(len(clean_text) / 4))
        log_token_usage(
            session_id=session_id,
            user_id=user_id,
            call_type="embedding",
            model=f"{embed_model}:mock",
            tokens_in=approx_in,
            tokens_out=0,
            estimated_cost_usd=_estimate_cost_usd(embed_model, approx_in, 0),
        )
        return vec

    data = _post_json(
        f"/models/{embed_model}:embedContent",
        {
            "model": f"models/{embed_model}",
            "content": {"parts": [{"text": clean_text}]},
        },
        "embedContent",
    )

    vector = (data.get("embedding") or {}).get("values")
    if not isinstance(vector, list) or len(vector) == 0:
        raise LLMUnavailableError(
            "Embedding vector returned empty from Gemini",
            "Unable to search the Knowledge Hub right now. Please try again in a moment.",
            502,
        )

    approx_tokens_in = max(1, math.ceil(len(clean_text) / 4))
    log_token_usage(
        session_id=session_id,
        user_id=user_id,
        call_type="embedding",
        model=embed_model,
        tokens_in=approx_tokens_in,
        tokens_out=0,
        estimated_cost_usd=_estimate_cost_usd(embed_model, approx_tokens_in, 0),
    )
    return [float(x) for x in vector]
