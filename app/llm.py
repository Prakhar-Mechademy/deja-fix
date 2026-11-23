"""Gemini client wrapper with safe fallbacks and call logging."""

import json
import logging
import math
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from dataclasses import dataclass

import numpy as np
from google import genai
from google.genai import types

from app import config
from app.db import init_db, log_llm_call


logger = logging.getLogger(__name__)
_CLIENT = None
_FRIENDLY_ERROR = "Sorry, I'm having trouble reaching our assistant right now. Please try again in a moment."


@dataclass
class LLMResult:
    ok: bool
    text: str
    data: dict | list | None
    user_message: str
    tokens_in: int
    tokens_out: int


def _client():
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = genai.Client(api_key=config.GEMINI_API_KEY)
    return _CLIENT


def _transient(exc: Exception) -> bool:
    text = str(exc).lower()
    return isinstance(exc, (TimeoutError, ConnectionError, FuturesTimeoutError)) or any(
        marker in text for marker in ("timed out", "timeout", "429", "500", "502", "503", "504", "connection", "unavailable")
    )


def _usage(response):
    usage = getattr(response, "usage_metadata", None)
    prompt = getattr(usage, "prompt_token_count", 0) if usage else 0
    candidates = getattr(usage, "candidates_token_count", 0) if usage else 0
    thoughts = getattr(usage, "thoughts_token_count", 0) if usage else 0
    return int(prompt or 0), int((candidates or 0) + (thoughts or 0))


def _parts(contents: list[dict]) -> list[types.Content]:
    return [
        types.Content(role=item["role"], parts=[types.Part(text=item["text"])])
        for item in contents
    ]


def _generate_config(kind: str, system: str | None, json_mode: bool, max_output_tokens: int, use_thinking: bool):
    kwargs = {
        "temperature": 0.2 if kind == "escalation" else 0,
        "max_output_tokens": max_output_tokens,
    }
    if system is not None:
        kwargs["system_instruction"] = system
    if json_mode:
        kwargs["response_mime_type"] = "application/json"

    thinking_level = os.getenv("THINKING_LEVEL")
    if use_thinking and thinking_level:
        try:
            kwargs["thinking_config"] = types.ThinkingConfig(thinking_level=thinking_level)
        except Exception:
            pass

    try:
        kwargs["http_options"] = types.HttpOptions(timeout=config.LLM_TIMEOUT_SECONDS * 1000)
        return types.GenerateContentConfig(**kwargs)
    except (TypeError, AttributeError):
        kwargs.pop("http_options", None)
        return types.GenerateContentConfig(**kwargs)


def _text_from_response(response):
    candidates = getattr(response, "candidates", None)
    if candidates is not None and len(candidates) == 0:
        return None
    try:
        text = response.text
    except Exception:
        text = None
    if not text or not str(text).strip():
        return None
    return str(text).strip()


def generate(session_id, user_id, kind, contents, system=None, json_mode=False, max_output_tokens=1024) -> LLMResult:
    if not config.GEMINI_API_KEY:
        log_llm_call(session_id, user_id, kind, config.GEMINI_MODEL, 0, 0, 0)
        return LLMResult(False, "", None, "The assistant isn't configured yet. Please contact support.", 0, 0)

    failure = LLMResult(False, "", None, _FRIENDLY_ERROR, 0, 0)

    def call_once(payload, use_thinking=True):
        provider_config = _generate_config(kind, system, json_mode, max_output_tokens, use_thinking)

        def provider_call():
            return _client().models.generate_content(
                model=config.GEMINI_MODEL,
                contents=_parts(payload),
                config=provider_config,
            )

        try:
            http_supported = getattr(provider_config, "http_options", None) is not None
            if http_supported:
                return provider_call()
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(provider_call)
                return future.result(timeout=config.LLM_TIMEOUT_SECONDS)
        except TypeError:
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(provider_call)
                return future.result(timeout=config.LLM_TIMEOUT_SECONDS)

    attempts = 0
    payload = list(contents)
    use_thinking = True
    while attempts < 2:
        attempts += 1
        try:
            response = call_once(payload, use_thinking)
            tokens_in, tokens_out = _usage(response)
            text = _text_from_response(response)
            if text is None:
                log_llm_call(session_id, user_id, kind, config.GEMINI_MODEL, tokens_in, tokens_out, 0)
                return failure

            if json_mode:
                candidate = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I).strip()
                try:
                    data = json.loads(candidate)
                    log_llm_call(session_id, user_id, kind, config.GEMINI_MODEL, tokens_in, tokens_out, 1)
                    return LLMResult(True, text, data, "", tokens_in, tokens_out)
                except json.JSONDecodeError:
                    log_llm_call(session_id, user_id, kind, config.GEMINI_MODEL, tokens_in, tokens_out, 0)
                    if attempts == 1:
                        payload = list(contents) + [{"role": "user", "text": "Return ONLY valid JSON."}]
                        continue
                    return failure

            log_llm_call(session_id, user_id, kind, config.GEMINI_MODEL, tokens_in, tokens_out, 1)
            return LLMResult(True, text, None, "", tokens_in, tokens_out)
        except Exception as exc:
            thinking_rejected = use_thinking and os.getenv("THINKING_LEVEL") and any(
                word in str(exc).lower() for word in ("thinking", "unsupported", "invalid", "unknown")
            )
            log_llm_call(session_id, user_id, kind, config.GEMINI_MODEL, 0, 0, 0)
            if thinking_rejected:
                use_thinking = False
                continue

            logger.exception("Gemini generate failed for kind=%s", kind)
            if attempts < 2 and _transient(exc):
                time.sleep(1)
                continue
            return failure

    log_llm_call(session_id, user_id, kind, config.GEMINI_MODEL, 0, 0, 0)
    return failure


def embed(texts: list[str], task_type: str, session_id, user_id) -> list[list[float]] | None:
    if not config.GEMINI_API_KEY:
        estimated_tokens = math.ceil(sum(len(text) for text in texts) / 4)
        log_llm_call(session_id, user_id, "embedding", config.EMBED_MODEL, estimated_tokens, 0, 0)
        return None

    # Embedding usage metadata is unavailable here, so token input is an estimate.
    estimated_tokens = math.ceil(sum(len(text) for text in texts) / 4)

    def call_once():
        def provider_call():
            return _client().models.embed_content(
                model=config.EMBED_MODEL,
                contents=texts,
                config=types.EmbedContentConfig(task_type=task_type),
            )

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(provider_call)
            return future.result(timeout=config.LLM_TIMEOUT_SECONDS)

    for attempt in range(2):
        try:
            response = call_once()
            vectors = getattr(response, "embeddings", None)
            if not vectors:
                log_llm_call(session_id, user_id, "embedding", config.EMBED_MODEL, estimated_tokens, 0, 0)
                return None

            result = []
            for item in vectors:
                values = getattr(item, "values", None)
                if values is None:
                    log_llm_call(session_id, user_id, "embedding", config.EMBED_MODEL, estimated_tokens, 0, 0)
                    return None
                vector = np.asarray(values, dtype=np.float32)
                norm = float(np.linalg.norm(vector))
                if norm > 0:
                    vector = vector / norm
                result.append([float(value) for value in vector])

            log_llm_call(session_id, user_id, "embedding", config.EMBED_MODEL, estimated_tokens, 0, 1)
            return result
        except Exception as exc:
            log_llm_call(session_id, user_id, "embedding", config.EMBED_MODEL, estimated_tokens, 0, 0)
            logger.exception("Gemini embedding failed")
            if attempt == 0 and _transient(exc):
                time.sleep(1)
                continue
            return None
    return None


if __name__ == "__main__":
    init_db()
    result = generate(None, None, "smoke", [{"role": "user", "text": "Reply with the word OK"}])
    vectors = embed(["hello"], "RETRIEVAL_DOCUMENT", None, None)
    print(result)
    print("embedding_length", len(vectors[0]) if vectors else None)
