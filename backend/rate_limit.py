import math
import os
import time

from backend.db import get_rate_limit_window_stats, record_rate_limit_event

WINDOW_MS = 60_000


class RateLimitError(Exception):
    def __init__(self, limit_type: str, limit: int, retry_after_seconds: int) -> None:
        label = "LLM escalation" if limit_type == "escalation" else "chat message"
        super().__init__(
            f"Rate limit exceeded for {label} requests ({limit}/minute). "
            f"Please wait {max(1, retry_after_seconds)}s before trying again."
        )
        self.status = 429
        self.limit_type = limit_type
        self.limit = limit
        self.retry_after_seconds = max(1, retry_after_seconds)


def get_message_rate_limit_per_min() -> int:
    try:
        val = int(os.environ.get("RATE_LIMIT_MESSAGES_PER_MIN", "20"))
        return val if val > 0 else 20
    except ValueError:
        return 20


def get_escalation_rate_limit_per_min() -> int:
    try:
        val = int(os.environ.get("RATE_LIMIT_ESCALATIONS_PER_MIN", "5"))
        return val if val > 0 else 5
    except ValueError:
        return 5


def enforce_message_rate_limit(user_id: int) -> None:
    limit = get_message_rate_limit_per_min()
    stats = get_rate_limit_window_stats(user_id, "message", WINDOW_MS)
    if stats["count"] >= limit:
        oldest = stats["oldest_in_window_ms"] or int(time.time() * 1000)
        retry_after = math.ceil((oldest + WINDOW_MS - int(time.time() * 1000)) / 1000)
        raise RateLimitError("message", limit, retry_after)
    record_rate_limit_event(user_id, "message")


def enforce_escalation_rate_limit(user_id: int) -> None:
    limit = get_escalation_rate_limit_per_min()
    stats = get_rate_limit_window_stats(user_id, "escalation", WINDOW_MS)
    if stats["count"] >= limit:
        oldest = stats["oldest_in_window_ms"] or int(time.time() * 1000)
        retry_after = math.ceil((oldest + WINDOW_MS - int(time.time() * 1000)) / 1000)
        raise RateLimitError("escalation", limit, retry_after)
    record_rate_limit_event(user_id, "escalation")


def get_rate_limit_status_for_user(user_id: int) -> dict[str, dict[str, int]]:
    msg_limit = get_message_rate_limit_per_min()
    esc_limit = get_escalation_rate_limit_per_min()
    msg_stats = get_rate_limit_window_stats(user_id, "message", WINDOW_MS)
    esc_stats = get_rate_limit_window_stats(user_id, "escalation", WINDOW_MS)
    return {
        "messages": {
            "used": int(msg_stats["count"] or 0),
            "limit": msg_limit,
            "remaining": max(0, msg_limit - int(msg_stats["count"] or 0)),
        },
        "escalations": {
            "used": int(esc_stats["count"] or 0),
            "limit": esc_limit,
            "remaining": max(0, esc_limit - int(esc_stats["count"] or 0)),
        },
    }
