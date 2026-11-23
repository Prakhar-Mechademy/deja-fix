"""Personally identifiable information sanitisation helpers."""

import re


EMAIL_PATTERN = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
URL_PATTERN = re.compile(r"\b(?:https?://|www\.)[^\s]+", re.I)
PHONE_PATTERN = re.compile(r"(?<!\w)(?:\+?\d{1,3}[\s.-]?)?(?:\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4})(?!\w)")
ACCOUNT_PATTERN = re.compile(r"\b(?:account|user|customer)[ -]?(?:id|number|name)?\s*[:#]?\s*[A-Z0-9_-]{4,}\b", re.I)
NAME_PATTERN = re.compile(r"\b(?:my name is|i am|this is)\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?", re.I)
IP_PATTERN = re.compile(r"(?<!\d)(?:\d{1,3}\.){3}\d{1,3}(?!\d)")
USERNAME_PATTERN = re.compile(r"\b(?:@|username[:=\s]+)[A-Z0-9._-]+\b", re.I)
SECRET_PATTERN = re.compile(
    r"\b(?:password|passcode|api[_ -]?key|auth(?:entication)?[_ -]?token|bearer[_ -]?token|access[_ -]?token|secret)[\s:=]+\S+",
    re.I,
)


def redact_pii(text: str) -> str:
    if not text:
        return text

    redacted = SECRET_PATTERN.sub("[REDACTED_CREDENTIAL]", text)
    redacted = URL_PATTERN.sub("[URL]", redacted)
    redacted = EMAIL_PATTERN.sub("[EMAIL]", redacted)
    redacted = PHONE_PATTERN.sub("[PHONE]", redacted)
    redacted = IP_PATTERN.sub("[IP_ADDRESS]", redacted)
    redacted = USERNAME_PATTERN.sub("[USERNAME]", redacted)
    redacted = ACCOUNT_PATTERN.sub("[ACCOUNT_ID]", redacted)
    redacted = NAME_PATTERN.sub("[NAME]", redacted)

    return redacted


def contains_pii(text: str) -> bool:
    if not text:
        return False

    patterns = (
        EMAIL_PATTERN,
        URL_PATTERN,
        PHONE_PATTERN,
        IP_PATTERN,
        USERNAME_PATTERN,
        ACCOUNT_PATTERN,
        NAME_PATTERN,
        SECRET_PATTERN,
    )

    return any(pattern.search(text) for pattern in patterns)


def sanitise_string_list(values: list[str] | None) -> list[str]:
    if not values:
        return []

    unique = []

    for value in values:
        cleaned = redact_pii(str(value)).strip()

        if cleaned and cleaned not in unique:
            unique.append(cleaned)

    return unique
