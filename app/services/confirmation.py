"""Explicit customer confirmation detection."""

import re

CONFIRMATION_PATTERNS = (
    r"\b(?:it|that|this) (?:worked|fixed|resolved|solved)\b",
    r"\b(?:fixed|resolved|solved|working) now\b",
    r"\b(?:all|both) (?:fixes|solutions|problems) (?:worked|fixed|resolved|solved)\b",
    r"\bproblem (?:is )?(?:fixed|resolved|solved)\b",
    r"\b(?:issue|problem) (?:has been )?(?:fixed|resolved|solved)\b",
    r"\b(?:yes|yep|yeah)[,.! ]+(?:it|that|this) (?:worked|fixed|resolved|solved)\b",
    r"\byes[,!]? (?:the )?(?:issue|problem|fix) (?:is |has been )?(?:fixed|resolved|solved|working)\b",
)

_CONFIRMATION_RE = re.compile("|".join(f"(?:{pattern})" for pattern in CONFIRMATION_PATTERNS), re.I)


def find_confirmation_message(messages: list) -> object | None:
    for message in messages:
        if getattr(message, "role", None) != "user":
            continue

        content = getattr(message, "content", "")

        if _CONFIRMATION_RE.search(content):
            return message

    return None


def is_explicit_confirmation(text: str) -> bool:
    return bool(_CONFIRMATION_RE.search(text or ""))
