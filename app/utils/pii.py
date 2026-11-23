"""Personally identifiable information sanitisation helpers."""

import re


EMAIL_PATTERN = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
URL_PATTERN = re.compile(r"\b(?:https?://|www\.)[^\s]+", re.I)
PHONE_PATTERN = re.compile(r"(?<!\w)(?:\+?\d{1,3}[\s.-]?)?(?:\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4})(?!\w)")
ACCOUNT_PATTERN = re.compile(r"\b(?:account|user|customer)[ -]?(?:id|number)?\s*[:#]?\s*[A-Z0-9_-]{6,}\b", re.I)
NAME_PATTERN = re.compile(r"\b(?:my name is|i am|this is)\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?", re.I)


def redact_pii(text: str) -> str:
    if not text:
        return text

    redacted = URL_PATTERN.sub("[URL]", text)
    redacted = EMAIL_PATTERN.sub("[EMAIL]", redacted)
    redacted = PHONE_PATTERN.sub("[PHONE]", redacted)
    redacted = ACCOUNT_PATTERN.sub("[ACCOUNT_ID]", redacted)
    redacted = NAME_PATTERN.sub("[NAME]", redacted)

    return redacted


def sanitise_string_list(values: list[str] | None) -> list[str]:
    if not values:
        return []

    unique = []

    for value in values:
        cleaned = redact_pii(str(value)).strip()

        if cleaned and cleaned not in unique:
            unique.append(cleaned)

    return unique
