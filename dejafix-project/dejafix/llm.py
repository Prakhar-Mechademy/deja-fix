"""LLM layer: providers (Anthropic, OpenAI, offline Mock), an error taxonomy, and a logging wrapper that
records tokens in/out for EVERY call, tagged with its session. API keys come from the environment only.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass

import httpx

from .config import Settings
from .db import connect, utcnow
from .textutil import (classify_feedback, clean_sentences, detect_faults, parse_environment, text_sim, title_for)

log = logging.getLogger("dejafix.llm")


# --------------------------------------------------------------------------- errors
class LLMError(Exception):
    code = "llm_error"
    user_message = "Something went wrong while contacting our backend assistant. Please try again in a moment."


class LLMTimeout(LLMError):
    code = "llm_timeout"
    user_message = "Our backend assistant took too long to answer. Please try again in a minute."


class LLMUnavailable(LLMError):
    code = "llm_unavailable"
    user_message = ("I couldn't reach our backend assistant right now, so I don't have a fix to suggest yet. "
                    "Please try again in a few minutes.")


class LLMMalformed(LLMError):
    code = "llm_malformed"
    user_message = "Our backend assistant sent back something I couldn't read. Please try again, or rephrase your question."


@dataclass
class LLMResult:
    text: str
    tokens_in: int
    tokens_out: int
    model: str


def extract_json(text: str) -> dict:
    """Parse a JSON object out of an LLM reply (tolerates code fences / chatter)."""
    if not text or not text.strip():
        raise LLMMalformed("empty response")
    t = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    start, end = t.find("{"), t.rfind("}")
    if start < 0 or end <= start:
        raise LLMMalformed("no JSON object in response")
    try:
        obj = json.loads(t[start:end + 1])
    except ValueError as e:
        raise LLMMalformed(f"invalid JSON: {e}") from e
    if not isinstance(obj, dict):
        raise LLMMalformed("JSON root is not an object")
    return obj


SMALL_PURPOSES = {"judge"}


def _http_error(e: Exception) -> LLMError:
    if isinstance(e, httpx.TimeoutException):
        return LLMTimeout(str(e))
    if isinstance(e, httpx.HTTPError):
        return LLMUnavailable(str(e))
    return LLMError(str(e))


def _check_status(r: httpx.Response) -> None:
    if r.status_code in (408, 504):
        raise LLMTimeout(f"HTTP {r.status_code}")
    if r.status_code == 429 or r.status_code >= 500:
        raise LLMUnavailable(f"HTTP {r.status_code}")
    if r.status_code >= 400:
        raise LLMError(f"HTTP {r.status_code}: {r.text[:200]}")


# --------------------------------------------------------------------------- real providers
class AnthropicProvider:
    name = "anthropic"
    URL = "https://api.anthropic.com/v1/messages"

    def __init__(self, settings: Settings):
        self.s = settings
        self.client = httpx.Client(timeout=settings.llm_timeout)

    def complete(self, purpose: str, system: str, messages: list[dict], max_tokens: int = 1024) -> LLMResult:
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise LLMUnavailable("ANTHROPIC_API_KEY is not set")
        model = self.s.anthropic_model_small if purpose in SMALL_PURPOSES else self.s.anthropic_model
        body = {"model": model, "max_tokens": max_tokens, "messages": messages}
        if system:
            body["system"] = system
        headers = {"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
        try:
            r = self.client.post(self.URL, json=body, headers=headers)
        except Exception as e:  # noqa: BLE001
            raise _http_error(e) from e
        _check_status(r)
        try:
            data = r.json()
            text = "".join(b.get("text", "") for b in data["content"] if b.get("type") == "text")
            u = data.get("usage", {})
            return LLMResult(text, int(u.get("input_tokens", 0)), int(u.get("output_tokens", 0)), model)
        except (ValueError, KeyError, TypeError, AttributeError) as e:
            raise LLMMalformed(f"bad provider payload: {e}") from e


class OpenAIProvider:
    name = "openai"
    URL = "https://api.openai.com/v1/chat/completions"

    def __init__(self, settings: Settings):
        self.s = settings
        self.client = httpx.Client(timeout=settings.llm_timeout)

    def complete(self, purpose: str, system: str, messages: list[dict], max_tokens: int = 1024) -> LLMResult:
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            raise LLMUnavailable("OPENAI_API_KEY is not set")
        model = self.s.openai_model_small if purpose in SMALL_PURPOSES else self.s.openai_model
        msgs = ([{"role": "system", "content": system}] if system else []) + messages
        try:
            r = self.client.post(self.URL, json={"model": model, "messages": msgs, "max_tokens": max_tokens},
                                 headers={"Authorization": f"Bearer {key}"})
        except Exception as e:  # noqa: BLE001
            raise _http_error(e) from e
        _check_status(r)
        try:
            data = r.json()
            u = data.get("usage", {})
            return LLMResult(data["choices"][0]["message"]["content"] or "", int(u.get("prompt_tokens", 0)),
                             int(u.get("completion_tokens", 0)), model)
        except (ValueError, KeyError, IndexError, TypeError, AttributeError) as e:
            raise LLMMalformed(f"bad provider payload: {e}") from e



class GeminiProvider:
    """Google Gemini API provider using the generateContent REST endpoint."""
    name = "gemini"
    URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    def __init__(self, settings: Settings):
        self.s = settings
        self.client = httpx.Client(timeout=settings.llm_timeout)

    def complete(self, purpose: str, system: str, messages: list[dict], max_tokens: int = 1024) -> LLMResult:
        key = os.environ.get("GEMINI_API_KEY")
        if not key:
            raise LLMUnavailable("GEMINI_API_KEY is not set")

        model = self.s.gemini_model

        # Gemini uses "user" and "model" roles; assistant maps to model.
        contents = []
        for message in messages:
            role = "model" if message.get("role") == "assistant" else "user"
            content = str(message.get("content", ""))
            if content.strip():
                contents.append({"role": role, "parts": [{"text": content}]})

        body = {
            "contents": contents,
            "generationConfig": {"maxOutputTokens": max_tokens},
        }

        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}

        # Retry temporary rate-limit and service-unavailable errors.
        r = None
        for attempt in range(3):
            try:
                r = self.client.post(
                    self.URL.format(model=model),
                    json=body,
                    headers={
                        "x-goog-api-key": key,
                        "Content-Type": "application/json",
                    },
                )
            except Exception as e:  # noqa: BLE001
                raise _http_error(e) from e

            if r.status_code in (429, 503) and attempt < 2:
                wait_seconds = 2 ** (attempt + 1)
                log.warning(
                    "Gemini returned HTTP %s. Retrying in %s seconds (attempt %s/3).",
                    r.status_code,
                    wait_seconds,
                    attempt + 1,
                )
                time.sleep(wait_seconds)
                continue

            break

        _check_status(r)

        try:
            data = r.json()
            candidates = data.get("candidates", [])

            if not candidates:
                raise LLMMalformed("Gemini returned no candidates")

            parts = candidates[0].get("content", {}).get("parts", [])
            text = "".join(
                part.get("text", "")
                for part in parts
                if isinstance(part, dict)
            )

            usage = data.get("usageMetadata", {})

            if not text.strip():
                raise LLMMalformed("Gemini returned an empty response")

            return LLMResult(
                text,
                int(usage.get("promptTokenCount", 0)),
                int(usage.get("candidatesTokenCount", 0)),
                model,
            )

        except LLMMalformed:
            raise
        except (ValueError, KeyError, TypeError, AttributeError, IndexError) as e:
            raise LLMMalformed(f"bad Gemini payload: {e}") from e



# --------------------------------------------------------------------------- offline mock
PLAYBOOKS = {
    "sync": [
        ["Open Settings > General > Background App Refresh and make sure it is turned on for the app.",
         "Check that you are signed in to the same account on every device.",
         "Make sure all devices are online, then pull down inside the app to force a refresh."],
        ["Sign out of the app on every device (Settings > Account > Sign out).",
         "Sign back in on ONE device first and wait for the sync to finish.",
         "Then sign in on your other devices so they pick up a fresh sync token."],
        ["Back up your notes (export them), then delete and reinstall the app on the affected device.",
         "Sign in again and let the first sync finish before opening anything else."],
    ],
    "crash": [
        ["Force-close the app and open it again.", "Update the app to the latest version from the store."],
        ["Restart the device.", "Free up some storage space (at least 1 GB) and reopen the app."],
        ["Delete and reinstall the app.", "Sign in again and check whether the crash still happens."],
    ],
    "login": [
        ["Double-check the email address and password (watch for caps lock).",
         "Use 'Forgot password' to reset it and try the new one."],
        ["Clear the app's cache or reinstall it.", "Make sure the device date and time are set automatically."],
    ],
    "wifi": [
        ["Forget the Wi-Fi network on the device and join it again.", "Restart the router for 30 seconds."],
        ["Reset the device's network settings.", "Try the 2.4 GHz band if your router offers both."],
    ],
    "bluetooth": [
        ["Turn Bluetooth off and on again.", "Remove the old pairing and pair the accessory again."],
        ["Restart both devices and keep them close while pairing."],
    ],
    "print": [
        ["Check the printer is online and has paper and ink.", "Restart the printer and the computer."],
        ["Remove the printer, then add it again.", "Run a nozzle/head cleaning from the printer menu."],
    ],
    "battery": [
        ["Check Settings > Battery to see which apps use the most power.", "Turn off background refresh for those apps."],
        ["Update the OS and restart the device.", "Replace or service the battery if its health is low."],
    ],
    "slow": [
        ["Restart the app and the device.", "Free up storage space."],
        ["Update the app and OS.", "Reinstall the app if it is still slow."],
    ],
    "notify": [
        ["Open system Settings > Notifications and allow them for the app.", "Turn off Do Not Disturb / Focus."],
        ["Sign out and back in to the app.", "Reinstall the app and allow notifications when asked."],
    ],
    "install": [
        ["Free up storage space and make sure you are online.", "Restart the device and try again."],
        ["Update the OS first, then retry.", "Download the installer again."],
    ],
    "generic": [
        ["Restart the app and the device.", "Update the app and the operating system to the latest version."],
        ["Sign out and back in.", "Reinstall the app."],
    ],
}


class MockProvider:
    """Deterministic offline stand-in for a real LLM (no network, no API key). Playbook-based escalation answers,
    transcript-reading extraction, rule-based judge. `fail` simulates outages: timeout | unavailable | malformed | garbage."""
    name = "mock"

    def __init__(self, fail: str | None = None, fail_purposes: set[str] | None = None):
        self.fail, self.fail_purposes = fail, fail_purposes

    @staticmethod
    def _tok(*parts: str) -> int:
        return max(1, sum(len(p) for p in parts) // 4)

    def complete(self, purpose: str, system: str, messages: list[dict], max_tokens: int = 1024) -> LLMResult:
        if self.fail and (self.fail_purposes is None or purpose in self.fail_purposes):
            if self.fail == "timeout":
                raise LLMTimeout("mock timeout")
            if self.fail == "unavailable":
                raise LLMUnavailable("mock outage")
            if self.fail == "malformed":
                return LLMResult("", 10, 0, "mock")
            if self.fail == "garbage":
                return LLMResult("Sorry, I cannot help with that {{{", 10, 5, "mock")
        content = "\n".join(m["content"] for m in messages)
        text = {"escalation": self._escalate, "extract": self._extract, "judge": self._judge}[purpose](messages, content)
        return LLMResult(text, self._tok(system, content), self._tok(text), "mock")

    # -- escalation: first playbook level whose opening step was not already given in this conversation
    def _escalate(self, messages, content) -> str:
        users = [m["content"] for m in messages if m["role"] == "user" and classify_feedback(m["content"]) == "unclear"]
        faults = detect_faults(" ".join(users))
        levels = PLAYBOOKS[sorted(faults)[0]] if faults else PLAYBOOKS["generic"]
        said = " ".join(m["content"] for m in messages if m["role"] == "assistant").lower()
        fresh = [lv for lv in levels if lv[0].lower() not in said]
        if not fresh:
            return ("I'm out of standard fixes for this one. Please contact the app's support team and tell them your "
                    "device, OS version and app version.")
        lv = fresh[0]
        intro = "Here are some steps to try:" if lv is levels[0] else "Thanks for trying those. Let's go a step further:"
        steps = "\n".join(f"{i}. {s}" for i, s in enumerate(lv, 1))
        return f"{intro}\n\n{steps}\n\nIf this doesn't resolve it, tell me what happens."

    # -- extraction: reads the stored transcript passed in the prompt
    def _extract(self, messages, content) -> str:
        msgs = [(int(i), who, txt) for i, who, txt in re.findall(
            r"^\[(\d+)\] (CUSTOMER|SUPPORT): (.*?)(?=^\[\d+\] (?:CUSTOMER|SUPPORT): |\n\nCONFIRMATION_IDS:|\Z)", content, re.M | re.S)]
        conf_ids = [int(x) for x in re.findall(r"\d+", (re.search(r"CONFIRMATION_IDS:([^\n]*)", content) or [0, ""])[1])]
        starts = {int(a): int(b) for a, b in re.findall(r"(\d+):(\d+)", (re.search(r"PROBLEM_STARTS[^\n]*", content) or [""])[0])}
        names = [n.strip() for n in (re.search(r"KNOWN_NAMES:([^\n]*)", content) or [0, ""])[1].split(",") if n.strip()]
        out = []
        for cid in conf_ids:
            seg = [m for m in msgs if max(starts.get(cid, 1), 1) <= m[0] <= cid]
            cust = [(i, t) for i, w, t in seg if w == "CUSTOMER"]
            symptoms, tried, env = [], [], {}
            seen_support = False
            for i, w, t in seg:
                if w == "SUPPORT":
                    seen_support = True
                    continue
                fb = classify_feedback(t)
                if not seen_support and fb == "unclear":
                    symptoms += clean_sentences(t, names)
                env = {**parse_environment(t), **env}
                if fb == "failed":
                    m = re.search(r"tried\s+(.+?)(?:,|\.|\bbut\b|$)", t, re.I)
                    if m:
                        for part in re.split(r",|\band\b", re.sub(r"(?i)^\s*already\s+", "", m.group(1))):
                            if part.strip():
                                tried.append(part.strip())
            support_replies = [t for i, w, t in seg if w == "SUPPORT"]
            # Use the latest support reply that actually contains numbered fix steps.
            # A later "contact support" message must not hide the earlier actionable fix.
            steps = []
            for support_reply in reversed(support_replies):
                candidate_steps = [s.strip() for s in re.findall(r"^\s*\d+[.)]\s+(.+)$", support_reply, re.M)]
                if candidate_steps:
                    steps = candidate_steps
                    break
            faults = detect_faults(" ".join(symptoms))
            out.append({"confirmation_message_id": cid, "title": title_for(faults, env.get("app")),
                        "symptoms": symptoms, "environment": {"device": env.get("device", ""), "os": env.get("os", ""),
                                                              "app": env.get("app", ""), "app_version": env.get("app_version", "")},
                        "fix_steps": steps, "did_not_work": tried})
        return json.dumps({"solutions": out})

    def _judge(self, messages, content) -> str:
        a, _, b = content.partition("PROBLEM B (new):")
        fa, fb = detect_faults(a), detect_faults(b)
        same = bool(fa & fb) if fa and fb else text_sim(a, b) >= 0.3
        return json.dumps({"same_fault": same, "reason": "mock"})


# --------------------------------------------------------------------------- logging wrapper
class LLMService:
    """Every call (ok or failed) is written to llm_calls with session_id and tokens in/out."""

    def __init__(self, provider, db_path: str):
        self.provider, self.db_path = provider, db_path

    def _log(self, session_id, purpose, model, tin, tout, status, started):
        try:
            with connect(self.db_path) as conn:
                conn.execute("INSERT INTO llm_calls(session_id,purpose,model,tokens_in,tokens_out,status,latency_ms,created_at)"
                             " VALUES (?,?,?,?,?,?,?,?)",
                             (session_id, purpose, model, tin, tout, status, int((time.monotonic() - started) * 1000), utcnow()))
        except Exception:  # noqa: BLE001 - logging must never break a chat turn
            log.exception("could not write llm_calls row")

    def call(self, session_id: str | None, purpose: str, system: str, messages: list[dict], max_tokens: int = 1024) -> LLMResult:
        t0 = time.monotonic()

        try:
            res = self.provider.complete(purpose, system, messages, max_tokens)

            if not isinstance(res, LLMResult) or not (res.text or "").strip():
                raise LLMMalformed("empty or unreadable response")

        except LLMError as e:
            log.error(
                "LLM provider '%s' failed during '%s': %s",
                getattr(self.provider, "name", "unknown"),
                purpose,
                str(e)
            )
            self._log(session_id, purpose, None, 0, 0, e.code, t0)
            raise

        except Exception as e:
            log.exception("unexpected provider failure")
            self._log(session_id, purpose, None, 0, 0, LLMError.code, t0)
            raise LLMError(str(e)) from e

        self._log(
            session_id,
            purpose,
            res.model,
            res.tokens_in,
            res.tokens_out,
            "ok",
            t0
        )

        return res


    # -- reporting
    def usage(self) -> list[dict]:
        with connect(self.db_path) as conn:
            rows = conn.execute("""
                SELECT c.session_id, s.user_id, COUNT(*) llm_calls, SUM(c.tokens_in) tokens_in, SUM(c.tokens_out) tokens_out,
                  SUM(c.purpose='escalation') escalations, SUM(c.purpose='extract') extractions, SUM(c.purpose='judge') judge_calls
                FROM llm_calls c LEFT JOIN sessions s ON s.id=c.session_id WHERE c.session_id IS NOT NULL
                GROUP BY c.session_id ORDER BY MIN(c.id)""").fetchall()
            served_hub = {r[0] for r in conn.execute("SELECT DISTINCT session_id FROM session_problems WHERE served_by='hub'")}
        out = []
        for r in rows:
            d = dict(r)
            d["tokens_total"] = (d["tokens_in"] or 0) + (d["tokens_out"] or 0)
            d["served_from"] = "escalation" if d["escalations"] else ("hub" if d["session_id"] in served_hub else "none")
            out.append({k: d[k] for k in ("session_id", "user_id", "llm_calls", "tokens_in", "tokens_out", "tokens_total",
                                          "escalations", "extractions", "judge_calls", "served_from")})
        return out

    def calls(self, session_id: str | None = None) -> list[dict]:
        with connect(self.db_path) as conn:
            q, args = "SELECT * FROM llm_calls", ()
            if session_id:
                q, args = q + " WHERE session_id=?", (session_id,)
            return [dict(r) for r in conn.execute(q + " ORDER BY id", args)]


def build_provider(s: Settings):
    p = s.llm_provider
    if p == "auto":
        p = ("gemini" if os.environ.get("GEMINI_API_KEY") else
             "anthropic" if os.environ.get("ANTHROPIC_API_KEY") else
             "openai" if os.environ.get("OPENAI_API_KEY") else "mock")
        if p == "mock":
            log.warning("No GEMINI_API_KEY / ANTHROPIC_API_KEY / OPENAI_API_KEY found: using the offline MockProvider.")
    if p == "gemini":
        return GeminiProvider(s)
    if p == "anthropic":
        return AnthropicProvider(s)
    if p == "openai":
        return OpenAIProvider(s)
    if p == "mock":
        return MockProvider()
    raise ValueError(f"Unknown LLM_PROVIDER '{s.llm_provider}' (use auto, gemini, anthropic, openai or mock)")
