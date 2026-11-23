"""Small Gemini adapter. All provider calls are isolated here."""
import json
from config import GEMINI_API_KEY, GEMINI_MODEL, EMBEDDING_MODEL, LLM_TIMEOUT_SECONDS

try:
    from google import genai
    from google.genai import types
except Exception:  # package may be absent in lightweight/test installs
    genai = None
    types = None

client = genai.Client(api_key=GEMINI_API_KEY, http_options={"timeout": int(LLM_TIMEOUT_SECONDS * 1000)}) if GEMINI_API_KEY and genai else None

class ProviderError(RuntimeError):
    def __init__(self, message, usage=None):
        super().__init__(message)
        self.usage = usage

def generate_text(prompt: str, purpose: str = "solve") -> dict:
    if client is None:
        raise ProviderError("The language model is not configured. Please try again later.")
    try:
        response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
        text = getattr(response, "text", None)
        if not text:
            raise ProviderError("The language model returned an empty response.")
        usage = getattr(response, "usage_metadata", None)
        return {"text": text, "input_tokens": int(getattr(usage, "prompt_token_count", 0) or 0),
                "output_tokens": int(getattr(usage, "candidates_token_count", 0) or 0), "model": GEMINI_MODEL}
    except ProviderError:
        raise
    except Exception as exc:
        raise ProviderError("The support service is temporarily unavailable. Please try again later.") from exc

def generate_json(prompt: str, purpose: str = "solve") -> dict:
    result = generate_text(prompt, purpose)
    raw = result["text"].strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict): raise ValueError("Expected JSON object")
        result["data"] = parsed
        return result
    except Exception as exc:
        raise ProviderError("The support service returned an unreadable response. Please try again.", usage=result) from exc

def embed_text(text: str) -> tuple[list[float], int]:
    if client is None:
        raise ProviderError("Embedding provider is not configured")
    try:
        result = client.models.embed_content(model=EMBEDDING_MODEL, contents=text)
        usage = getattr(result, "usage_metadata", None)
        input_tokens = int(getattr(usage, "prompt_token_count", 0) or 0)
        return list(result.embeddings[0].values), input_tokens
    except Exception as exc:
        raise ProviderError("Could not create a search representation.") from exc
