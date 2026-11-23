"""Generalization helpers: strip personal data from anything saved to the knowledge hub,
while keeping the environment (device, OS, app version)."""
import re

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
URL = re.compile(r"(https?://\S+|www\.\S+)", re.I)
PHONE = re.compile(r"(?<![\w.])\+?\d[\d\s()-]{8,}\d(?!\w)(?!\.\d)")
ORDER_WITH_KEYWORD = re.compile(
    r"\b(order|ord|ticket|invoice|account|acct|customer|booking|ref|reference|case|serial)\s*(no\.?|number|id|#)?\s*[:#-]?\s*(?=[A-Z-]*\d)[A-Z0-9][A-Z0-9-]{3,}\b",
    re.I)
HASH_ID = re.compile(r"#\s?(?=[A-Z-]*\d)[A-Z0-9-]{3,}\b", re.I)
PREFIXED_ID = re.compile(r"\b[A-Z]{2,5}-\d{3,}\b")
LONG_NUMBER = re.compile(r"(?<![\w.])\d{5,}(?!\w)(?!\.\d)")
NAME_INTRO = re.compile(
    r"\b(?i:my name is|i am|i'm|this is|name's|regards,?|thanks,?|cheers,?)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)")

NOT_NAMES = {"Android", "iPhone", "Windows", "Mac", "Pixel", "Samsung", "Galaxy", "Linux", "Chrome", "Using",
             "On", "Having", "Facing", "Still", "Trying", "The", "It", "Not", "Getting", "Unable", "Sure", "Yes",
             "No", "Ok", "Okay", "Fine", "Good", "Glad", "Back", "Here", "Again", "So", "Very", "All"}


def find_names(texts, known=()):
    """Names introduced in the chat ("I'm Priya Mehta", "Thanks, Rahul") plus names we already know."""
    names = {n.strip() for n in known if n and n.strip()}
    for text in texts:
        for m in NAME_INTRO.finditer(text or ""):
            parts = [p for p in m.group(1).split() if p not in NOT_NAMES]
            if parts:
                names.add(" ".join(parts))
    expanded = set()
    for n in names:
        expanded.add(n)
        expanded.update(p for p in n.split() if len(p) > 2 and p not in NOT_NAMES)
    return sorted(expanded, key=len, reverse=True)


def scrub(text, names=()):
    if not text:
        return text
    out = EMAIL.sub("[email removed]", text)
    out = URL.sub("[link removed]", out)
    out = ORDER_WITH_KEYWORD.sub("[reference removed]", out)
    out = PREFIXED_ID.sub("[reference removed]", out)
    out = HASH_ID.sub("[reference removed]", out)
    out = PHONE.sub("[phone removed]", out)
    out = LONG_NUMBER.sub("[number removed]", out)
    for name in names:
        out = re.sub(rf"\b{re.escape(name)}\b", "the customer", out, flags=re.I)
    out = re.sub(r"\s{2,}", " ", out)
    return out.strip()


def scrub_record(rec: dict, names=()) -> dict:
    def clean(v):
        if isinstance(v, str):
            return scrub(v, names)
        if isinstance(v, list):
            return [clean(x) for x in v if x]
        if isinstance(v, dict):
            return {k: clean_env(k, x) for k, x in v.items()}
        return v

    def clean_env(key, v):
        # Environment fields are kept, only names/emails/links are removed from them.
        if isinstance(v, str):
            out = EMAIL.sub("", v)
            out = URL.sub("", out)
            for name in names:
                out = re.sub(rf"\b{re.escape(name)}\b", "", out, flags=re.I)
            return out.strip() or None
        return v

    return {k: clean(v) for k, v in rec.items()}
