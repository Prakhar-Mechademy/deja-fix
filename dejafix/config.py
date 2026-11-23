"""Settings. Everything comes from environment variables (optionally loaded from a local .env file).
API keys are never stored in code."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        if value:
            os.environ.setdefault(key.strip(), value)


_load_dotenv()


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def env_int(name: str, default: int) -> int:
    try:
        return int(env(name, str(default)))
    except ValueError:
        return default


def db_path() -> str:
    p = Path(env("DEJA_DB", "data/dejafix.db"))
    if not p.is_absolute():
        p = ROOT / p
    p.parent.mkdir(parents=True, exist_ok=True)
    return str(p)


def gemini_key() -> str:
    return env("GEMINI_API_KEY") or env("GOOGLE_API_KEY")


def provider() -> str:
    choice = env("LLM_PROVIDER", "auto").lower()
    if choice in ("gemini", "mock"):
        return choice
    return "gemini" if gemini_key() else "mock"


GEMINI_MODEL = env("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_FALLBACK_MODELS = ["gemini-flash-latest", "gemini-2.0-flash"]
GEMINI_EMBED_MODEL = env("GEMINI_EMBED_MODEL", "gemini-embedding-001")
LLM_TIMEOUT = env_int("LLM_TIMEOUT_SECONDS", 30)

MSG_LIMIT_PER_MINUTE = env_int("MSG_LIMIT_PER_MINUTE", 20)
ESCALATION_LIMIT_PER_HOUR = env_int("ESCALATION_LIMIT_PER_HOUR", 6)
MAX_FIX_ATTEMPTS = env_int("MAX_FIX_ATTEMPTS", 3)
SESSION_IDLE_MINUTES = env_int("SESSION_IDLE_MINUTES", 30)
