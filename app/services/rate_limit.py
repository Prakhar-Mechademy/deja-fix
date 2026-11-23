"""Database-backed fixed-window rate limits; no in-memory counters."""
from dataclasses import dataclass
import logging
from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from app.config import settings

from app.models import RateEvent

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RateLimits:
    messages_per_minute: int = 30
    escalations_per_minute: int = 5
    window_seconds: int = 60


class RateLimitExceeded(Exception):
    def __init__(self, kind: str, retry_after: int):
        self.kind = kind
        self.retry_after = max(1, retry_after)
        super().__init__(
            f"{kind.capitalize()} rate limit exceeded. "
            f"Please try again in {self.retry_after} seconds."
        )


class RateLimiter:
    def __init__(self, limits: RateLimits | None = None):
        self.limits = limits or RateLimits(
            messages_per_minute=settings.rate_messages_per_minute,
            escalations_per_minute=settings.rate_escalations_per_minute,
        )

    def consume(self, db, user_id: str, kind: str, now: datetime | None = None) -> None:
        if kind not in {"message", "escalation"}:
            raise ValueError("Unknown rate-limit kind")
        limit = (
            self.limits.messages_per_minute if kind == "message"
            else self.limits.escalations_per_minute
        )
        now = now or datetime.now(timezone.utc)
        cutoff = now - timedelta(seconds=self.limits.window_seconds)
        within_window = select(RateEvent.created_at).where(
            RateEvent.user_id == user_id,
            RateEvent.kind == kind,
            RateEvent.created_at > cutoff,
        ).order_by(RateEvent.created_at.asc())
        events = list(db.scalars(within_window))
        if len(events) >= limit:
            oldest = events[0]
            # SQLite often returns timezone-naive timestamps.
            if oldest.tzinfo is None:
                oldest = oldest.replace(tzinfo=timezone.utc)
            remaining = (oldest + timedelta(seconds=self.limits.window_seconds) - now).total_seconds()
            retry_after = int(remaining) + 1
            logger.warning("rate_limit.exceeded user_id=%s kind=%s current_count=%d limit=%d retry_after_seconds=%d", user_id, kind, len(events), limit, retry_after)
            raise RateLimitExceeded(kind, retry_after)
        db.add(RateEvent(user_id=user_id, kind=kind, created_at=now))
        db.flush()
        logger.debug("rate_limit.event_recorded user_id=%s kind=%s count_in_window=%d limit=%d", user_id, kind, len(events) + 1, limit)
