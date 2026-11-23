"""Best-effort PII scrubbing for solution records."""

import re


EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
URL_RE = re.compile(r"(?i)\b(?:https?://|www\.)[^\s]+")
PHONE_RE = re.compile(r"(?<![\w.])(?:\+\d{1,3}[\s.-]?)?(?:\(?\d{2,4}\)?[\s.-]?){2,4}\d{2,4}(?![\w.])")
NAMED_ID_RE = re.compile(
    r"(?i)\b(?:order(?:\s+(?:number|no\.?)?)?|ticket|account)\b(?:\s*#)?\s*[A-Z]*-?[A-Z0-9]*\d[A-Z0-9-]*"
)
HASH_ID_RE = re.compile(r"(?<!\w)#\d{3,}(?!\w)")
DASH_TOKEN_RE = re.compile(
    r"(?<![\w.])(?=[A-Z0-9-]{6,}(?![\w.]))(?=[A-Z0-9-]*\d)(?=[A-Z0-9-]*-)[A-Z0-9]+(?:-[A-Z0-9]+)+(?![\w.])",
    re.I,
)


def scrub_text(text: str, known_names: list[str] | None = None) -> str:
    """Scrub common personal identifiers from text."""
    result = text or ""
    result = EMAIL_RE.sub("[email]", result)
    result = URL_RE.sub("[link]", result)
    result = PHONE_RE.sub("[phone]", result)
    result = NAMED_ID_RE.sub("[id]", result)
    result = HASH_ID_RE.sub("[id]", result)
    result = DASH_TOKEN_RE.sub("[id]", result)

    for name in known_names or []:
        clean_name = name.strip()
        if not clean_name:
            continue
        escaped = re.escape(clean_name)
        result = re.sub(rf"(?<!\w){escaped}(?!\w)", "[name]", result, flags=re.I)
        if len(clean_name.split()) > 1:
            for word in clean_name.split():
                if len(word) > 2:
                    result = re.sub(rf"(?<!\w){re.escape(word)}(?!\w)", "[name]", result, flags=re.I)
    return result


def _scrub_list(values) -> list:
    return [scrub_text(str(value)) for value in (values or [])]


def scrub_solution(sol: dict, known_names: list[str] | None = None) -> dict:
    """Return a scrubbed copy of a solution dictionary."""
    scrubbed = dict(sol)
    if "title" in scrubbed:
        scrubbed["title"] = scrub_text(str(scrubbed["title"]), known_names)
    if "symptoms" in scrubbed:
        scrubbed["symptoms"] = [scrub_text(str(v), known_names) for v in scrubbed["symptoms"]]
    if "fix_steps" in scrubbed:
        scrubbed["fix_steps"] = [scrub_text(str(v), known_names) for v in scrubbed["fix_steps"]]
    if "failed_attempts" in scrubbed:
        scrubbed["failed_attempts"] = [scrub_text(str(v), known_names) for v in scrubbed["failed_attempts"]]
    if "environment" in scrubbed:
        scrubbed["environment"] = {
            str(key): scrub_text(str(value), known_names)
            for key, value in (scrubbed["environment"] or {}).items()
        }
    return scrubbed


if __name__ == "__main__":
    safe = "iPhone 13, iOS 17, app v4.2.1"
    unsafe = "Priya Sharma priya@x.com order #A-48213"
    assert scrub_text(safe) == safe
    scrubbed = scrub_text(unsafe, ["Priya Sharma"])
    assert "[name]" in scrubbed
    assert "[email]" in scrubbed
    assert "[id]" in scrubbed
    print("ok")
