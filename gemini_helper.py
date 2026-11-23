from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
import json
import os
import sys
import time
from google import genai
from google.genai import types
import database

MODELS = ["gemini-3.1-flash-lite", "gemini-2.5-flash-lite"]
API_KEY = "PASTE_YOUR_GEMINI_API_KEY_HERE"
_CLIENT = None


def _get_client():
    global _CLIENT
    if _CLIENT is None:
        for base in (os.getcwd(), os.path.dirname(os.path.abspath(__file__))):
            env_path = os.path.join(base, ".env")
            if not os.environ.get("GEMINI_API_KEY") and os.path.exists(env_path):
                with open(env_path, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            k, v = line.split("=", 1)
                            os.environ[k.strip()] = v.strip().strip('"').strip("'")

        key = os.environ.get("GEMINI_API_KEY") or API_KEY
        if not key or key == "PASTE_YOUR_GEMINI_API_KEY_HERE":
            print(
                "[API Error] Set GEMINI_API_KEY in environment/.env or paste it into API_KEY in gemini_helper.py",
                file=sys.stderr,
            )
            raise RuntimeError("GEMINI_API_KEY is missing")

        _CLIENT = genai.Client(api_key=key)
    return _CLIENT


def _generate(contents: str, config=None, timeout_sec: int = 20):
    client = _get_client()
    last_err = None
    for model in MODELS:
        for attempt in range(3):
            pool = ThreadPoolExecutor(max_workers=1)
            fut = pool.submit(
                lambda m=model: client.models.generate_content(model=m, contents=contents, config=config)
            )
            try:
                return fut.result(timeout=timeout_sec)
            except FuturesTimeout:
                last_err = TimeoutError(f"Gemini request timed out on {model}")
                break
            except Exception as e:
                last_err = e
                if any(code in str(e) for code in ("503", "429", "UNAVAILABLE", "RESOURCE_EXHAUSTED")):
                    time.sleep(2.0 * (attempt + 1))
                    continue
                break
            finally:
                pool.shutdown(wait=False)

    print(f"[API Error] {last_err}", file=sys.stderr)
    raise last_err


def ask_text(session_id: str, step: str, prompt: str) -> str:
    """Calls Gemini for plain text responses (used during escalation)."""
    res = _generate(prompt)
    u = getattr(res, "usage_metadata", None)
    database.log_tokens(
        session_id,
        step,
        getattr(u, "prompt_token_count", None) or len(prompt) // 4,
        getattr(u, "candidates_token_count", None) or len(res.text or "") // 4,
    )
    if not res.text or not res.text.strip():
        raise ValueError("Empty response from Gemini")
    return res.text.strip()


def ask_json(session_id: str, step: str, prompt: str) -> dict:
    """Calls Gemini and parses a structured JSON dictionary."""
    config = types.GenerateContentConfig(response_mime_type="application/json", temperature=0.0)
    res = _generate(prompt, config=config)
    u = getattr(res, "usage_metadata", None)
    database.log_tokens(
        session_id,
        step,
        getattr(u, "prompt_token_count", None) or len(prompt) // 4,
        getattr(u, "candidates_token_count", None) or len(res.text or "") // 4,
    )
    text = (res.text or "").strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(text)
