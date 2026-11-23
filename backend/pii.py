import re
from typing import Any


_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_URL_RE = re.compile(r"https?://[^\s)>\]\"']+", re.IGNORECASE)
_WWW_RE = re.compile(r"\bwww\.[^\s)>\]\"']+", re.IGNORECASE)
_ORDER_RE = re.compile(
    r"\b(?:order|ticket|invoice|account|acct|case|reference|ref|customer|user)"
    r"\s*(?:id|no|num|number|#)?\s*[:#-]?\s*[A-Z0-9][A-Z0-9-]{3,}\b",
    re.IGNORECASE,
)
_HASH_ID_RE = re.compile(r"#[A-Z]{1,5}-?\d{3,}\b")
_CODE_ID_RE = re.compile(r"\b[A-Z]{2,5}-\d{4,}\b")
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?\d{1,3}[\s-]?)?\(?\d{3}\)?[\s-]\d{3}[\s-]\d{4}(?!\d)")

_SKIP_WORDS = {"customer", "user", "admin", "support", "nimbussync", "unknown"}


def redact_pii(text: str, extra_sensitive_names: list[str] | None = None) -> str:
    if not text:
        return ""

    out = _EMAIL_RE.sub("[redacted-email]", text)
    out = _URL_RE.sub("[redacted-link]", out)
    out = _WWW_RE.sub("[redacted-link]", out)
    out = _ORDER_RE.sub("[redacted-id]", out)
    out = _HASH_ID_RE.sub("[redacted-id]", out)
    out = _CODE_ID_RE.sub("[redacted-id]", out)
    out = _PHONE_RE.sub("[redacted-phone]", out)

    for raw_name in extra_sensitive_names or []:
        cleaned = (raw_name or "").strip()
        if len(cleaned) < 2:
            continue
        parts = [p for p in cleaned.split() if len(p) >= 2]
        for candidate in [cleaned, *parts]:
            if candidate.lower() in _SKIP_WORDS:
                continue
            pattern = re.compile(rf"\b{re.escape(candidate)}\b", re.IGNORECASE)
            out = pattern.sub("[customer]", out)

    return re.sub(r"\s{2,}", " ", out).strip()


def redact_solution_fields(
    solution: dict[str, Any],
    extra_sensitive_names: list[str] | None = None,
) -> dict[str, Any]:
    names = extra_sensitive_names or []
    return {
        **solution,
        "symptoms": redact_pii(str(solution.get("symptoms", "")), names),
        "device": redact_pii(str(solution.get("device", "")), names),
        "os": redact_pii(str(solution.get("os", "")), names),
        "app_version": redact_pii(str(solution.get("app_version", "")), names),
        "fix_steps": [redact_pii(str(s), names) for s in (solution.get("fix_steps") or [])],
        "what_did_not_work": [
            redact_pii(str(s), names) for s in (solution.get("what_did_not_work") or [])
        ],
    }


def assert_pii_free(
    solution: dict[str, Any],
    extra_sensitive_names: list[str] | None = None,
) -> None:
    """
    Post-redaction / pre-write assertion verifying no email, URL, order/ticket ID,
    phone number, or known customer name remains in any saved solution field.
    """
    fields_to_check = [
        str(solution.get("symptoms", "")),
        str(solution.get("device", "")),
        str(solution.get("os", "")),
        str(solution.get("app_version", "")),
        *(str(s) for s in (solution.get("fix_steps") or [])),
        *(str(s) for s in (solution.get("what_did_not_work") or [])),
    ]
    combined = "\n".join(fields_to_check)

    checks = [
        ("email", _EMAIL_RE),
        ("url", _URL_RE),
        ("www-link", _WWW_RE),
        ("order-or-account-id", _ORDER_RE),
        ("hash-id", _HASH_ID_RE),
        ("code-id", _CODE_ID_RE),
        ("phone", _PHONE_RE),
    ]
    for label, pattern in checks:
        match = pattern.search(combined)
        if match:
            raise ValueError(f"PII assertion failed ({label}): detected '{match.group(0)}' after redaction")

    for raw_name in extra_sensitive_names or []:
        cleaned = (raw_name or "").strip()
        if len(cleaned) < 2:
            continue
        parts = [p for p in cleaned.split() if len(p) >= 2]
        for candidate in [cleaned, *parts]:
            if candidate.lower() in _SKIP_WORDS:
                continue
            rx = re.compile(rf"\b{re.escape(candidate)}\b", re.IGNORECASE)
            if rx.search(combined):
                raise ValueError(f"PII assertion failed (name): detected '{candidate}' after redaction")
