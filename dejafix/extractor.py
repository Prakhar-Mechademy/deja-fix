"""Extractor: reads a finished session's transcript FROM THE DATABASE and saves every problem the
customer confirmed as solved, as a generalized solution (symptoms, environment, fix steps, what did not work).

Runs when a session ends (POST /sessions/{id}/end, or automatically for sessions idle too long).
Fixes that came from the hub and were confirmed were already counted live, so only escalated
(LLM) fixes that the customer confirmed are extracted. Unconfirmed fixes are stored as unverified
rows, which are never served."""

from . import db, hub, llm, privacy
from .confirm import classify as classify_confirmation

SYSTEM = "You turn support transcripts into reusable, anonymous knowledge-base entries. Reply with JSON only."

PROMPT = """Read this support chat. Messages are numbered.
### TRANSCRIPT
{transcript}
### INSTRUCTIONS
Find EVERY distinct technical problem in the chat that received a suggested fix. One chat can hold several.
For each, write a GENERALIZED record that would help a different customer:
- Remove ALL personal data: names, emails, phone numbers, order/account/ticket numbers, links, addresses.
- KEEP the environment: device, operating system and app version.
- "confirmed" is true ONLY if the customer explicitly said that fix worked. If they said it did not work,
  or never answered, it is false.
Return JSON:
{{"solutions": [{{"label": "2-4 word lowercase name, e.g. 'note sync'",
   "title": "short title",
   "symptoms": "what the customer saw, generic",
   "environment": {{"device": string or null, "os": string or null, "app_version": string or null}},
   "fix_steps": ["step 1", "step 2"],
   "did_not_work": ["things tried that did not fix it"],
   "confirmed": true/false,
   "confirmation_message": number of the customer message that confirmed it, or null}}]}}
"""


def run(session_id: str) -> dict:
    result = {"session_id": session_id, "created": [], "merged": [], "unverified": [], "skipped": None}
    with db.connect() as con:
        session = db.get_session(con, session_id)
        if session is None:
            raise ValueError("unknown session")
        if session["extracted"]:
            result["skipped"] = "already extracted"
            return result
        user = db.get_user(con, session["user_id"])
        messages = db.get_messages(con, session_id)  # transcript comes from the store, not from memory
        escalated = con.execute("SELECT * FROM problems WHERE session_id=? AND source='llm'",
                                (session_id,)).fetchall()
        if not escalated:
            con.execute("UPDATE sessions SET extracted=1 WHERE id=?", (session_id,))
            result["skipped"] = "no escalated problems in this session (hub reuse is counted live)"
            return result
        confirmed_msg_ids = {p["confirm_message_id"] for p in escalated
                             if p["status"] == "confirmed" and p["confirm_message_id"]}
        hub_msg_ids = {r[0] for r in con.execute(
            "SELECT confirm_message_id FROM problems WHERE session_id=? AND source='hub' AND status='confirmed'",
            (session_id,)).fetchall()}

    numbered, index_to_msg = [], {}
    for i, m in enumerate(messages, 1):
        who = "Customer" if m["role"] == "customer" else "Support"
        numbered.append(f"[{i}] {who}: {m['content']}")
        index_to_msg[i] = m

    data = llm.generate_json(PROMPT.format(transcript="\n".join(numbered)), purpose="extraction",
                             session_id=session_id, user_id=session["user_id"], system=SYSTEM)
    records = data.get("solutions") if isinstance(data.get("solutions"), list) else []

    names = privacy.find_names([m["content"] for m in messages if m["role"] == "customer"],
                               known=[user["display_name"] if user else None])
    with db.connect() as con:
        for raw in records:
            if not isinstance(raw, dict) or not raw.get("fix_steps"):
                continue
            rec = {
                "label": str(raw.get("label") or "").lower()[:40] or None,
                "title": raw.get("title"),
                "symptoms": raw.get("symptoms") or "",
                "environment": raw.get("environment") if isinstance(raw.get("environment"), dict) else {},
                "fix_steps": [str(s) for s in raw.get("fix_steps") or []],
                "did_not_work": [str(s) for s in raw.get("did_not_work") or []],
            }
            rec = privacy.scrub_record(rec, names)

            # 'Confirmed' must be backed by a real customer message that our classifier also reads as a yes,
            # and that confirmed an escalated fix in this session. The model alone cannot mark something verified.
            msg = index_to_msg.get(_as_int(raw.get("confirmation_message")))
            if msg is not None and msg["id"] in hub_msg_ids:
                continue  # a hub fix the customer confirmed: already counted on the existing solution
            verified = bool(raw.get("confirmed")) and msg is not None and msg["role"] == "customer" \
                and classify_confirmation(msg["content"]) == "yes" and msg["id"] in confirmed_msg_ids

            if not verified:
                sid = db.insert_solution(con, rec, session_id, verified=False, embedding=None, embed_model=None)
                result["unverified"].append(sid)
                continue
            dup = hub.find_duplicate(con, rec, session_id, session["user_id"])
            if dup:
                db.bump_solution(con, dup, session_id, "merged", rec["did_not_work"])
                result["merged"].append(dup)
                _link_problem(con, session_id, msg["id"], dup)
                continue
            vec, model = llm.embed(f"{rec['label']} {rec['symptoms']}", session_id=session_id,
                                   user_id=session["user_id"])
            sid = db.insert_solution(con, rec, session_id, verified=True, embedding=vec, embed_model=model)
            result["created"].append(sid)
            _link_problem(con, session_id, msg["id"], sid)
        con.execute("UPDATE sessions SET extracted=1 WHERE id=?", (session_id,))
    return result


def _link_problem(con, session_id, confirm_msg_id, solution_id):
    con.execute("UPDATE problems SET solution_id=? WHERE session_id=? AND confirm_message_id=?",
                (solution_id, session_id, confirm_msg_id))


def _as_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None
