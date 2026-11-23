"""Per-user rate limits, stored in the database so they survive restarts.

Two limits (both configurable in config.py / .env):
  * "message":    every customer message           (MESSAGES_PER_MINUTE, 60 s window)
  * "escalation": every call to the backend LLM    (ESCALATIONS_PER_HOUR, 3600 s window)

check() raises RateLimitExceeded, which main.py turns into an HTTP 429 response.
"""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import config, db


class RateLimitExceeded(Exception):
    def __init__(self, kind: str, limit: int, window_seconds: int):
        self.kind = kind
        self.limit = limit
        self.retry_after = window_seconds  # upper bound: the window always clears within this long
        what = "messages" if kind == "message" else "requests that need extra support"
        per = "minute" if window_seconds == 60 else "hour" if window_seconds == 3600 else f"{window_seconds}s"
        self.message = (
            f"You've reached the limit of {limit} {what} per {per}. "
            f"Please wait a little and try again."
        )
        super().__init__(self.message)


def _limits() -> dict:
    # read at call time so tests (and .env changes) take effect immediately
    return {
        "message": (config.MESSAGES_PER_MINUTE, 60),
        "escalation": (config.ESCALATIONS_PER_HOUR, 3600),
    }


def check(user_id: str, kind: str) -> None:
    """Raise RateLimitExceeded if the user is over the limit; otherwise record this event."""
    limit, window = _limits()[kind]
    if db.count_rate_events(user_id, kind, window) >= limit:
        raise RateLimitExceeded(kind, limit, window)
    db.record_rate_event(user_id, kind)