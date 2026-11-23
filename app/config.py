"""Central settings. Everything comes from environment variables (.env is loaded automatically).

No secrets live in code: the Gemini key is read from GEMINI_API_KEY.
"""
import os

from dotenv import load_dotenv

load_dotenv()


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, default))


def _float(name: str, default: float) -> float:
    return float(os.getenv(name, default))


def get_api_key() -> str:
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY is not set. Copy .env.example to .env and add your key.")
    return key


# --- LLM ---------------------------------------------------------------------
# Check these names against what your key can access (Google AI Studio lists them).
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")
EMBED_MODEL = os.getenv("GEMINI_EMBED_MODEL", "gemini-embedding-001")
LLM_TIMEOUT_SECONDS = _float("LLM_TIMEOUT_SECONDS", 30)
LLM_MAX_RETRIES = _int("LLM_MAX_RETRIES", 1)  # retries on timeout/outage only

# --- Retrieval (tune these once hub.py exists) -------------------------------
MATCH_THRESHOLD = _float("MATCH_THRESHOLD", 0.80)      # at/above: confident match
BORDERLINE_THRESHOLD = _float("BORDERLINE_THRESHOLD", 0.65)  # between: verify with the LLM

# --- Rate limits (per user) --------------------------------------------------
MESSAGES_PER_MINUTE = _int("MESSAGES_PER_MINUTE", 20)
ESCALATIONS_PER_HOUR = _int("ESCALATIONS_PER_HOUR", 5)