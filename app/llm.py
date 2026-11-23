"""Gemini wrapper.

Every call is logged to the llm_calls table (tokens in/out, tagged with the session),
including failed ones. Timeouts, outages and malformed responses raise LLMError, which
callers catch and turn into a plain message for the user. The server never crashes.
"""
from __future__ import annotations

import json
import re
import time
from typing import Optional

from . import config, db

FRIENDLY_ERROR = "Sorry, I'm having trouble reaching our assistant right now. Please try again in a moment."


class LLMError(Exception):
    """kind: 'timeout' | 'outage' | 'malformed' | 'config' | 'error'"""

    def __init__(self, kind: str, detail: str = ""):
        super().__init__(f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail

    @property
    def user_message(self) -> str:
        return FRIENDLY_ERROR


_client = None


def _get_client():
    global _client
    if _client is None:
        from google import genai
        from google.genai import types

        _client = genai.Client(
            api_key=config.get_api_key(),
            http_options=types.HttpOptions(timeout=int(config.LLM_TIMEOUT_SECONDS * 1000)),
        )
    return _client


def _classify(exc: Exception) -> str:
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if "timeout" in name or "timed out" in msg or "deadline" in msg:
        return "timeout"
    if code in (429, 500, 502, 503, 504) or any(
        w in msg for w in ("unavailable", "overloaded", "connection", "connect error", "resource_exhausted")
    ):
        return "outage"
    return "error"


def _tokens(resp) -> tuple[int, int]:
    usage = getattr(resp, "usage_metadata", None)
    if usage is None:
        return 0, 0
    tokens_in = getattr(usage, "prompt_token_count", 0) or 0
    # Gemini "thinking" tokens are billed as output, so count them too
    tokens_out = (getattr(usage, "candidates_token_count", 0) or 0) + (
        getattr(usage, "thoughts_token_count", 0) or 0
    )
    return tokens_in, tokens_out


def generate(
    messages: list[dict],
    *,
    session_id: Optional[str],
    user_id: Optional[str],
    purpose: str,
    system: Optional[str] = None,
    json_mode: bool = False,
    temperature: float = 0.2,
    model: Optional[str] = None,
) -> str:
    """messages: [{"role": "user"|"assistant", "content": str}, ...]. Returns the text reply."""
    model = model or config.GEMINI_MODEL
    last_error: Optional[LLMError] = None

    for attempt in range(config.LLM_MAX_RETRIES + 1):
        start = time.monotonic()
        try:
            from google.genai import types

            contents = [
                types.Content(
                    role="model" if m["role"] == "assistant" else "user",
                    parts=[types.Part(text=m["content"])],
                )
                for m in messages
            ]
            cfg = types.GenerateContentConfig(
                system_instruction=system,
                temperature=temperature,
                response_mime_type="application/json" if json_mode else None,
            )
            resp = _get_client().models.generate_content(model=model, contents=contents, config=cfg)
            latency = int((time.monotonic() - start) * 1000)
            tokens_in, tokens_out = _tokens(resp)
            text = getattr(resp, "text", None)
            if not text:
                db.log_llm_call(session_id, user_id, purpose, model, tokens_in, tokens_out,
                                latency, "malformed", "empty response")
                raise LLMError("malformed", "empty response")
            db.log_llm_call(session_id, user_id, purpose, model, tokens_in, tokens_out, latency)
            return text
        except LLMError as e:
            raise e  # already logged; no retry for malformed
        except RuntimeError as e:  # missing API key
            db.log_llm_call(session_id, user_id, purpose, model, status="config", error=str(e))
            raise LLMError("config", str(e)) from e
        except Exception as e:  # provider errors, timeouts, network
            kind = _classify(e)
            latency = int((time.monotonic() - start) * 1000)
            db.log_llm_call(session_id, user_id, purpose, model, latency_ms=latency,
                            status=kind, error=str(e)[:300])
            last_error = LLMError(kind, str(e)[:300])
            if kind in ("timeout", "outage") and attempt < config.LLM_MAX_RETRIES:
                time.sleep(1 + attempt)
                continue
            raise last_error from e

    raise last_error or LLMError("error", "unknown")


def _parse_json(raw: str):
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())
    return json.loads(cleaned)


def generate_json(messages: list[dict], **kwargs):
    """Like generate(), but returns parsed JSON. Raises LLMError('malformed') if unparseable."""
    raw = generate(messages, json_mode=True, **kwargs)
    try:
        return _parse_json(raw)
    except ValueError as e:
        db.log_llm_call(kwargs.get("session_id"), kwargs.get("user_id"), kwargs.get("purpose", "unknown"),
                        kwargs.get("model") or config.GEMINI_MODEL, status="malformed",
                        error="invalid JSON: " + raw[:150])
        raise LLMError("malformed", "response was not valid JSON") from e


def embed(
    text: str,
    *,
    session_id: Optional[str],
    user_id: Optional[str],
    task_type: str = "SEMANTIC_SIMILARITY",
) -> list[float]:
    """Return an embedding vector. Logged as an LLM call (token count is an estimate: ~4 chars/token)."""
    start = time.monotonic()
    est_tokens = max(1, len(text) // 4)
    try:
        from google.genai import types

        resp = _get_client().models.embed_content(
            model=config.EMBED_MODEL,
            contents=text,
            config=types.EmbedContentConfig(task_type=task_type),
        )
        values = list(resp.embeddings[0].values)
        if not values:
            raise LLMError("malformed", "empty embedding")
        db.log_llm_call(session_id, user_id, "embedding", config.EMBED_MODEL, est_tokens, 0,
                        int((time.monotonic() - start) * 1000))
        return values
    except LLMError:
        db.log_llm_call(session_id, user_id, "embedding", config.EMBED_MODEL, status="malformed")
        raise
    except RuntimeError as e:
        db.log_llm_call(session_id, user_id, "embedding", config.EMBED_MODEL, status="config", error=str(e))
        raise LLMError("config", str(e)) from e
    except Exception as e:
        kind = _classify(e)
        db.log_llm_call(session_id, user_id, "embedding", config.EMBED_MODEL,
                        latency_ms=int((time.monotonic() - start) * 1000), status=kind, error=str(e)[:300])
        raise LLMError(kind, str(e)[:300]) from e