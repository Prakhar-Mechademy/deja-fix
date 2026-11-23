"""Extractor: turns confirmed fixes in a session into anonymised, de-duplicated solutions.

When it runs: once per session, after the session is closed (extract_session), or in bulk
for any closed sessions not yet processed (extract_pending). It reads the transcript from
the database, never from a variable.

Safeguards:
  * CONFIRMATION: the LLM must quote the customer's own words confirming the fix worked.
    The quote is checked against the stored customer messages. No genuine quote, no save.
  * PRIVACY: the LLM is told to strip personal data, then a regex pass (scrub) removes
    emails, links, phone numbers, order/account numbers and the customer's name again.
  * DUPLICATES: a new solution is compared with saved ones (hub.find_match). A match bumps
    that solution's count instead of creating a copy.
  * COST: sessions served from the hub only run a cheap confirm-and-merge check; sessions
    with no served fix skip the LLM entirely.
"""
from __future__ import annotations

import re
from typing import Optional

from . import db, hub, llm

# --------------------------------------------------------------------------- #
# Prompts
# --------------------------------------------------------------------------- #
EXTRACT_SYSTEM = """You extract confirmed fixes from a customer-support conversation.
Reply with ONLY JSON in this shape:
{"solutions": [{"title": str, "symptoms": str,
  "environment": {"device": str|null, "os": str|null, "app_version": str|null},
  "fix_steps": [str], "failed_attempts": [str], "confirmation_quote": str}]}

Rules:
- Include a solution ONLY if the CUSTOMER explicitly said this fix worked or the problem is
  solved. Thanks alone, "I'll try it", silence, or the customer saying it did not work all
  mean: do not include it.
- confirmation_quote: copy, word for word, the customer's own message (or part of it) that
  confirms the fix worked. Do not paraphrase it.
- One entry per distinct problem that was solved. Several entries are allowed.
- title: short generic name of the problem (at most 8 words).
- symptoms: one or two general sentences on what the customer experienced.
- environment: device, operating system and app version exactly as the customer stated them.
  Use null if not stated. Never guess.
- fix_steps: the ordered steps that actually fixed it, written generically.
- failed_attempts: things that were tried and did NOT work (empty list if none).
- PRIVACY: remove all personal data from EVERY field: names, email addresses, phone numbers,
  order/ticket/account numbers, usernames, links, street addresses. Keep device model,
  operating system and app version.
- If nothing was confirmed, reply {"solutions": []}."""

CONFIRM_SYSTEM = """You check whether a customer confirmed that a suggested fix worked.
Reply with ONLY JSON: {"confirmed": [{"solution_id": int, "confirmation_quote": str}]}
Rules:
- List a solution ONLY if the CUSTOMER explicitly said that fix worked or the problem is solved.
- confirmation_quote: copy, word for word, the customer's own message (or part of it).
- If the customer said it did not work, was unsure, or never answered: reply {"confirmed": []}."""


# --------------------------------------------------------------------------- #
# Privacy scrubbing (safety net on top of the LLM's own anonymisation)
# --------------------------------------------------------------------------- #
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_URL = re.compile(r"(?:https?://|www\.)\S+", re.I)
_ID_WORD = re.compile(
    r"\b(?:order|ticket|case|invoice|account|acct|reference|ref|tracking)\b"
    r"(?:\s*(?:no\.?|number|num|id|#))?(?:\s+(?:is|was))?\s*[:#]?\s*"
    r"(?=[A-Z0-9-]*\d)[A-Z0-9][A-Z0-9-]{3,}\b",
    re.I,
)
_HASH_ID = re.compile(r"#\w*\d\w*")
_CODE_ID = re.compile(r"\b[A-Z]{2,6}-\d{3,}\b")
_PHONE = re.compile(r"(?<![\w.])\+?\d[\d\s().-]{7,}\d(?!\w)")
_LONG_DIGITS = re.compile(r"\b\d{6,}\b")


def _phone_sub(m: re.Match) -> str:
    s = m.group(0)
    if sum(c.isdigit() for c in s) < 9 or re.fullmatch(r"[\d.]+", s):
        return s  # too short, or a dotted number such as a version string
    return ""


def scrub(text: str, names: tuple[str, ...] = ()) -> str:
    """Remove emails, links, phone numbers, order/account numbers and known names."""
    t = text or ""
    for pat in (_EMAIL, _URL, _ID_WORD, _HASH_ID, _CODE_ID):
        t = pat.sub("", t)
    t = _PHONE.sub(_phone_sub, t)
    t = _LONG_DIGITS.sub("", t)
    for name in names:
        for token in re.split(r"\s+", name.strip()):
            if len(token) >= 2:
                t = re.sub(rf"\b{re.escape(token)}(?:['’]s)?\b", "", t, flags=re.I)
    t = re.sub(r"\(\s*\)|\[\s*\]", "", t)
    t = re.sub(r"\s{2,}", " ", t)
    t = re.sub(r"\s+([,.;:!?])", r"\1", t)
    return t.strip(" ,;-")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _norm(s: str) -> str:
    return re.sub(r"\W+", " ", (s or "").lower()).strip()


def _quote_is_genuine(quote: Optional[str], user_msgs_norm: list[str]) -> bool:
    """The quoted confirmation must appear in one of the customer's real messages."""
    q = _norm(quote or "")
    return len(q.split()) >= 2 and any(q in m for m in user_msgs_norm)


def _format_transcript(messages: list[dict]) -> str:
    label = {"user": "Customer", "assistant": "Assistant"}
    return "\n".join(f"{label.get(m['role'], m['role'])}: {m['content']}" for m in messages)


_NAME_INTRO = re.compile(
    r"(?i:my name is|my name's|i am|i['’]m|this is|name:)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)"
)
_EMAIL_NAME = re.compile(r"\b([A-Za-z]{3,})[._-]([A-Za-z]{3,})@")
_NOT_NAMES = {
    "having", "trying", "using", "getting", "sorry", "not", "unable", "still", "just", "back",
    "on", "in", "at", "with", "running", "stuck", "sure", "also", "now", "here", "there",
}


def _names_from_text(texts) -> list[str]:
    """Fallback anonymisation: names the customer introduced themselves with, or put in an email."""
    found: list[str] = []
    for t in texts:
        for m in _NAME_INTRO.finditer(t or ""):
            found.extend(w for w in m.group(1).split() if w.lower() not in _NOT_NAMES)
        for m in _EMAIL_NAME.finditer(t or ""):
            found.extend(m.groups())
    return list(dict.fromkeys(found))


def _known_names(user_id: str, texts=()) -> tuple[str, ...]:
    with db.get_conn() as conn:
        row = conn.execute("SELECT display_name FROM users WHERE id = ?", (user_id,)).fetchone()
    names = [row["display_name"]] if row and row["display_name"] else []
    return tuple(names + _names_from_text(texts))


def _already_linked(solution_id: int, session_id: str) -> bool:
    """True if this session already created or was merged into the solution (keeps re-runs idempotent)."""
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM solution_sessions WHERE solution_id = ? AND session_id = ? "
            "AND relation IN ('origin', 'merge')",
            (solution_id, session_id),
        ).fetchone()
    return row is not None


def _reused_solution_ids(session_id: str) -> list[int]:
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT solution_id FROM solution_sessions WHERE session_id = ? AND relation = 'reuse'",
            (session_id,),
        ).fetchall()
    return [r["solution_id"] for r in rows]


def _clean(item, names: tuple[str, ...]) -> Optional[dict]:
    """Validate one LLM-extracted solution and scrub every field. None if unusable."""
    if not isinstance(item, dict):
        return None

    def strings(value) -> list[str]:
        if not isinstance(value, list):
            return []
        out = [scrub(str(v), names) for v in value if str(v).strip()]
        return [s for s in out if s]

    title = scrub(str(item.get("title") or ""), names)
    symptoms = scrub(str(item.get("symptoms") or ""), names)
    steps = strings(item.get("fix_steps"))
    if not (title and symptoms and steps):
        return None

    env_in = item.get("environment") if isinstance(item.get("environment"), dict) else {}
    env = {}
    for key in ("device", "os", "app_version"):
        value = env_in.get(key)
        if value not in (None, ""):
            value = scrub(str(value), names)
            if value:
                env[key] = value

    return {
        "title": title,
        "symptoms": symptoms,
        "environment": env,
        "fix_steps": steps,
        "failed_attempts": strings(item.get("failed_attempts")),
    }


def _save_or_merge(sol: dict, session_id: str, user_id: str, result: dict) -> None:
    vec = hub.embed_solution(sol["title"], sol["symptoms"], session_id=session_id, user_id=user_id)
    match = hub.find_match(
        hub.solution_text(sol["title"], sol["symptoms"]),
        session_id=session_id,
        user_id=user_id,
        query_vec=vec,
    )
    if match:
        sid = match["solution"]["id"]
        if not _already_linked(sid, session_id):
            db.merge_into_solution(sid, session_id, sol["failed_attempts"])
        result["merged"].append(sid)
    else:
        new_id = db.add_solution(
            source_session_id=session_id,
            title=sol["title"],
            symptoms=sol["symptoms"],
            environment=sol["environment"],
            fix_steps=sol["fix_steps"],
            failed_attempts=sol["failed_attempts"],
            embedding=vec,
            status="verified",
        )
        result["saved"].append(new_id)


# --------------------------------------------------------------------------- #
# The two extraction paths
# --------------------------------------------------------------------------- #
def _extract_full(session_id, user_id, transcript, user_msgs, names, result) -> None:
    """Escalated session: extract every confirmed problem from the transcript."""
    data = llm.generate_json(
        [{"role": "user", "content": f"Transcript:\n{transcript}"}],
        session_id=session_id,
        user_id=user_id,
        purpose="extraction",
        system=EXTRACT_SYSTEM,
        temperature=0.0,
    )
    items = data.get("solutions", []) if isinstance(data, dict) else []
    for item in items:
        quote = item.get("confirmation_quote") if isinstance(item, dict) else None
        sol = _clean(item, names)
        if sol is None or not _quote_is_genuine(quote, user_msgs):
            result["skipped"] += 1
            continue
        _save_or_merge(sol, session_id, user_id, result)


def _extract_hub_session(session_id, user_id, transcript, user_msgs, result) -> None:
    """Hub-served session: just check the customer confirmed, then bump the count. Cheap."""
    ids = _reused_solution_ids(session_id)
    if not ids:
        return
    listing = "\n".join(
        f"- id {i}: {s['title']}: {s['symptoms']}" for i in ids if (s := db.get_solution(i))
    )
    data = llm.generate_json(
        [{"role": "user", "content": f"Suggested fixes:\n{listing}\n\nTranscript:\n{transcript}"}],
        session_id=session_id,
        user_id=user_id,
        purpose="extraction",
        system=CONFIRM_SYSTEM,
        temperature=0.0,
    )
    confirmed = data.get("confirmed", []) if isinstance(data, dict) else []
    for c in confirmed:
        sid = c.get("solution_id") if isinstance(c, dict) else None
        if sid not in ids or not _quote_is_genuine(c.get("confirmation_quote"), user_msgs):
            result["skipped"] += 1
            continue
        if not _already_linked(sid, session_id):
            db.merge_into_solution(sid, session_id, [])
        result["merged"].append(sid)


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def extract_session(session_id: str, force: bool = False) -> dict:
    """Process one session. Safe to call twice: a finished session is skipped unless force=True.

    Returns {"session_id", "saved": [ids], "merged": [ids], "skipped": int, "error": kind|None}.
    On an LLM failure the session is left unprocessed so it can be retried later.
    """
    result = {"session_id": session_id, "saved": [], "merged": [], "skipped": 0, "error": None}
    session = db.get_session(session_id)
    if session is None:
        raise ValueError(f"Unknown session {session_id}")
    if session["extracted_at"] and not force:
        result["skipped_reason"] = "already extracted"
        return result

    user_id = session["user_id"]
    messages = db.get_messages(session_id)  # always read from the store
    raw_user_msgs = [m["content"] for m in messages if m["role"] == "user"]
    user_msgs = [_norm(t) for t in raw_user_msgs]
    served_from = session["served_from"]

    if user_msgs and served_from in ("hub", "escalation"):
        transcript = _format_transcript(messages)
        try:
            if served_from == "hub":
                _extract_hub_session(session_id, user_id, transcript, user_msgs, result)
            else:
                _extract_full(session_id, user_id, transcript, user_msgs, _known_names(user_id, raw_user_msgs), result)
        except llm.LLMError as e:
            result["error"] = e.kind
            return result  # not marked extracted: can be retried

    if not session["title"]:
        linked = (result["saved"] + result["merged"])[:1]
        if linked and (sol := db.get_solution(linked[0])):
            db.set_session_title(session_id, sol["title"])

    db.mark_extracted(session_id)
    return result


def extract_pending() -> list[dict]:
    """Process every closed session that has not been extracted yet."""
    return [extract_session(s["id"]) for s in db.sessions_pending_extraction()]