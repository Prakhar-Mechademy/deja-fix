"""Environment-backed application settings."""
import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "gemini-embedding-001")
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'data' / 'deja_fix.db'}")
MESSAGE_LIMIT = int(os.getenv("MESSAGE_LIMIT", "30"))
MESSAGE_WINDOW_SECONDS = int(os.getenv("MESSAGE_WINDOW_SECONDS", "60"))
ESCALATION_LIMIT = int(os.getenv("ESCALATION_LIMIT", "5"))
ESCALATION_WINDOW_SECONDS = int(os.getenv("ESCALATION_WINDOW_SECONDS", "3600"))
RETRIEVAL_THRESHOLD = float(os.getenv("RETRIEVAL_THRESHOLD", "0.82"))
AMBIGUOUS_THRESHOLD = float(os.getenv("AMBIGUOUS_THRESHOLD", "0.68"))
LLM_TIMEOUT_SECONDS = float(os.getenv("LLM_TIMEOUT_SECONDS", "25"))
