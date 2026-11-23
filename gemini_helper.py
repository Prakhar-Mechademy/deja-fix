import json
import os
from google import genai
from google.genai import types
import database

MODEL = "gemini-2.5-flash"


def _get_client():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is missing from environment variables.")
    return genai.Client(api_key=api_key)


def ask_text(session_id: str, step: str, prompt: str) -> str:
    """Calls Gemini with no persona or restrictions (used for escalation)."""
    res = _get_client().models.generate_content(model=MODEL, contents=prompt)
    usage = getattr(res, "usage_metadata", None)
    database.log_tokens(
        session_id,
        step,
        getattr(usage, "prompt_token_count", None) or len(prompt) // 4,
        getattr(usage, "candidates_token_count", None) or len(res.text or "") // 4,
    )
    if not res.text or not res.text.strip():
        raise ValueError("Empty response from Gemini")
    return res.text.strip()


def ask_json(session_id: str, step: str, prompt: str) -> dict:
    """Calls Gemini and returns a clean Python dictionary."""
    res = _get_client().models.generate_content(
        model=MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.0),
    )
    usage = getattr(res, "usage_metadata", None)
    database.log_tokens(
        session_id,
        step,
        getattr(usage, "prompt_token_count", None) or len(prompt) // 4,
        getattr(usage, "candidates_token_count", None) or len(res.text or "") // 4,
    )
    text = (res.text or "").strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(text)