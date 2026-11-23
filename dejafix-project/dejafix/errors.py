"""Application errors. Each maps to one HTTP status in api.py."""
from __future__ import annotations


class DejaError(Exception):
    status = 400
    code = "bad_request"

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


class BadRequest(DejaError):
    status = 400
    code = "bad_request"


class NotFound(DejaError):
    status = 404
    code = "not_found"


class RateLimited(DejaError):
    status = 429
    code = "rate_limited"

    def __init__(self, scope: str, retry_after: int, detail: str):
        super().__init__(detail)
        self.scope = scope
        self.retry_after = retry_after
