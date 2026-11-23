"""Per-user sliding-window rate limits: one for messages, a tighter one for LLM escalations."""
from __future__ import annotations

import math
import threading
import time
from collections import defaultdict, deque

from .errors import RateLimited


class RateLimiter:
    def __init__(self, messages=(30, 60.0), escalations=(5, 60.0), clock=time.monotonic):
        self.limits = {"messages": messages, "escalations": escalations}
        self.clock = clock
        self._hits: dict[tuple[str, str], deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, user_id: str, scope: str) -> None:
        """Record one hit or raise RateLimited (-> HTTP 429)."""
        limit, window = self.limits[scope]
        now = self.clock()
        with self._lock:
            q = self._hits[(user_id, scope)]
            while q and now - q[0] >= window:
                q.popleft()
            if len(q) >= limit:
                wait = max(1, math.ceil(window - (now - q[0])))
                what = "messages" if scope == "messages" else "requests to our backend assistant"
                raise RateLimited(scope, wait, f"You've sent too many {what} ({limit} per {int(window)}s). "
                                               f"Please wait {wait} seconds and try again.")
            q.append(now)
