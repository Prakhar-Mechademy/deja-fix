"""Runtime configuration. Everything comes from environment variables (or a .env file).

API keys are NEVER stored here: providers read ANTHROPIC_API_KEY / OPENAI_API_KEY from
the environment at call time.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


def load_dotenv(path: str = ".env") -> None:
    """Tiny .env loader (no dependency). Real environment variables win."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _rate(name: str, default: str) -> tuple[int, float]:
    raw = os.environ.get(name, default)
    count, _, window = raw.partition("/")
    return int(count), float(window or 60)


@dataclass
class Settings:
    db_path: str = "dejafix.db"
    llm_provider: str = "auto"          # auto | gemini | anthropic | openai | mock
    llm_timeout: float = 30.0
    anthropic_model: str = "claude-sonnet-5-5"
    anthropic_model_small: str = "claude-haiku-5-5"
    openai_model: str = "gpt-4o"
    openai_model_small: str = "gpt-4o-mini"
    gemini_model: str = "gemini-2.5-flash"
    judge_mode: str = "borderline"      # borderline | always | never
    match_accept: float = 0.55          # minimum score to treat a saved fix as a candidate
    match_high: float = 0.80            # at/above this the judge call is skipped
    rate_messages: tuple = (30, 60.0)   # (count, window seconds) per user
    rate_escalations: tuple = (5, 60.0)  # tighter: LLM escalations per user
    max_message_chars: int = 4000

    @classmethod
    def from_env(cls) -> "Settings":
        e = os.environ.get
        d = cls()
        return cls(
            db_path=e("DB_PATH", d.db_path),
            llm_provider=e("LLM_PROVIDER", d.llm_provider).lower(),
            llm_timeout=float(e("LLM_TIMEOUT_SECONDS", d.llm_timeout)),
            anthropic_model=e("ANTHROPIC_MODEL", d.anthropic_model),
            anthropic_model_small=e("ANTHROPIC_MODEL_SMALL", d.anthropic_model_small),
            openai_model=e("OPENAI_MODEL", d.openai_model),
            openai_model_small=e("OPENAI_MODEL_SMALL", d.openai_model_small),
            gemini_model=e("GEMINI_MODEL", d.gemini_model),
            judge_mode=e("JUDGE_MODE", d.judge_mode).lower(),
            match_accept=float(e("MATCH_ACCEPT", d.match_accept)),
            match_high=float(e("MATCH_HIGH", d.match_high)),
            rate_messages=_rate("RATE_LIMIT_MESSAGES", "30/60"),
            rate_escalations=_rate("RATE_LIMIT_ESCALATIONS", "5/60"),
            max_message_chars=int(e("MAX_MESSAGE_CHARS", d.max_message_chars)),
        )
