import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()

@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./deja_fix.db")
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")
    gemini_timeout_ms: int = int(os.getenv("GEMINI_TIMEOUT_MS", "30000"))
    embedding_model: str = os.getenv(
        "EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
    )
    similarity_threshold: float = float(os.getenv("SIMILARITY_THRESHOLD", "0.78"))
    rate_messages_per_minute: int = int(os.getenv("RATE_MESSAGES_PER_MINUTE", "30"))
    rate_escalations_per_minute: int = int(os.getenv("RATE_ESCALATIONS_PER_MINUTE", "5"))
    gemini_fallback_models: tuple[str, ...] = tuple(
        model.strip()
        for model in os.getenv(
            "GEMINI_FALLBACK_MODELS",
            "gemini-3.5-flash,gemini-3.1-flash-lite",
        ).split(",")
        if model.strip()
    )

settings = Settings()
