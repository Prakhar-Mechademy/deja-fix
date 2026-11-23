"""core Application configuration."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_NAME: str = "Deja Fix"

    DATABASE_URL: str = "sqlite:///./data/deja_fix.db"

    GEMINI_API_KEY: str
    GEMINI_MODEL: str = "gemini-3.5-flash"
    ADMIN_TOKEN: str | None = None

    EMBEDDING_MODEL: str = "all-MiniLM-L6-v2"

    RATE_LIMIT_MESSAGES: int = 30
    RATE_LIMIT_ESCALATIONS: int = 5

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
    )


settings = Settings()
