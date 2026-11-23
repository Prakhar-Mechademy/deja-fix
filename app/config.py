"""Environment-backed application configuration."""

import os

from dotenv import load_dotenv

load_dotenv()


GEMINI_API_KEY: str | None = os.getenv("GEMINI_API_KEY") or None
GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")
EMBED_MODEL: str = os.getenv("EMBED_MODEL", "gemini-embedding-001")
DB_PATH: str = os.getenv("DB_PATH", "dejafix.db")
MSG_LIMIT_PER_MIN: int = int(os.getenv("MSG_LIMIT_PER_MIN", "10"))
ESC_LIMIT_PER_HOUR: int = int(os.getenv("ESC_LIMIT_PER_HOUR", "5"))
LLM_TIMEOUT_SECONDS: int = int(os.getenv("LLM_TIMEOUT_SECONDS", "30"))
RETRIEVAL_THRESHOLD: float = float(os.getenv("RETRIEVAL_THRESHOLD", "0.60"))
MERGE_THRESHOLD: float = float(os.getenv("MERGE_THRESHOLD", "0.88"))
