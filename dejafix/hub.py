"""Knowledge hub: retrieval over verified solutions.

Two stages:
 1. Recall  - embed the customer's description and take the closest verified solutions (cosine similarity).
 2. Verify  - a small LLM 'triage' call decides whether any candidate is the SAME fault, not just similar words.
This is what lets a reworded problem match, while a problem that only shares words gets escalated."""
import json

from . import db, llm

TOP_K = 3
GEMINI_MIN_SIM = 0.50
LOCAL_MIN_SIM = 0.12

SYSTEM_TRIAGE = ("You are the triage step of a customer-support system. You classify and match problems. "
                 "You never write fixes. Reply with JSON only.")

TRIAGE_PROMPT = """Decide how to handle the customer's latest problem description.

### CUSTOMER_TEXT
{text}
### CANDIDATES
{candidates}
### ALLOW_CLARIFY
{allow}
### INSTRUCTIONS
CANDIDATES are saved, verified solutions found by similarity search. They may share words with the customer's
text but describe a DIFFERENT fault. Only pick one if it is the same underlying fault (same symptom and same
failure mode). Shared words such as the app name, 'sync' or the device are NOT enough. If unsure, pick none.
Return JSON with exactly these keys:
{{"is_support_problem": true/false (does the customer describe a technical problem with a product, app or device?),
 "label": "2-4 word lowercase name for the problem, e.g. 'note sync', or null",
 "summary": "one generic sentence describing the problem with no personal data, or null",
 "environment": {{"device": string or null, "os": string or null, "app_version": string or null}},
 "match_id": id of the matching candidate or null,
 "needs_clarification": true/false (true if the description is ambiguous or details needed to be sure the
    candidate applies are missing; must be false when ALLOW_CLARIFY is no),
 "clarifying_question": "one short question to resolve the ambiguity (never include a fix), or null"}}
"""

DEDUPE_PROMPT = """Is the NEW solution the same fault and fix as one of the EXISTING solutions?
### NEW
{new}
### EXISTING
{existing}
### INSTRUCTIONS
Return JSON: {{"same_as": id of the existing solution that describes the same fault, or null}}.
Different faults that share words are NOT the same."""


def candidate_view(row) -> dict:
    return {"id": row["id"], "label": row["label"], "symptoms": row["symptoms"],
            "environment": db.loads(row["environment"], {})}


def find_candidates(con, text, session_id=None, user_id=None, k=TOP_K):
    rows = db.verified_solutions(con)
    if not rows:
        return []
    vec, model = llm.embed(text, session_id=session_id, user_id=user_id)
    local_q = llm.local_embed(text)
    scored = []
    for r in rows:
        stored = db.loads(r["embedding"], None)
        if stored and r["embed_model"] == model:
            sim, floor = llm.cosine(vec, stored), (LOCAL_MIN_SIM if model == "local-hash" else GEMINI_MIN_SIM)
        else:
            sim, floor = llm.cosine(local_q, llm.local_embed(f"{r['label']} {r['symptoms']}")), LOCAL_MIN_SIM
        if sim >= floor:
            scored.append((sim, r))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [r for _, r in scored[:k]]


def triage(text, candidates, allow_clarify, session_id=None, user_id=None) -> dict:
    prompt = TRIAGE_PROMPT.format(
        text=text, candidates=json.dumps([candidate_view(c) for c in candidates], ensure_ascii=False),
        allow="yes" if allow_clarify else "no")
    out = llm.generate_json(prompt, purpose="triage", session_id=session_id, user_id=user_id, system=SYSTEM_TRIAGE)
    valid_ids = {c["id"] for c in candidates}
    try:
        match = int(out.get("match_id")) if out.get("match_id") is not None else None
    except (TypeError, ValueError):
        match = None
    env = out.get("environment") if isinstance(out.get("environment"), dict) else {}
    question = out.get("clarifying_question")
    return {
        "is_support_problem": bool(out.get("is_support_problem")),
        "label": (out.get("label") or "").strip().lower()[:40] or None,
        "summary": out.get("summary"),
        "environment": {k: env.get(k) for k in ("device", "os", "app_version")},
        "match_id": match if match in valid_ids else None,  # never trust an id we did not offer
        "needs_clarification": bool(out.get("needs_clarification")) and allow_clarify,
        "clarifying_question": question if isinstance(question, str) and 0 < len(question) < 300 else None,
    }


def find_duplicate(con, rec, session_id=None, user_id=None):
    """Return the id of an existing verified solution for the same fault, or None."""
    text = f"{rec.get('label', '')} {rec.get('symptoms', '')}"
    cands = find_candidates(con, text, session_id, user_id)
    if not cands:
        return None
    new_view = {"label": rec.get("label"), "symptoms": rec.get("symptoms"), "environment": rec.get("environment"),
                "fix_steps": rec.get("fix_steps")}
    existing = [dict(candidate_view(c), fix_steps=db.loads(c["fix_steps"], [])) for c in cands]
    out = llm.generate_json(DEDUPE_PROMPT.format(new=json.dumps(new_view, ensure_ascii=False),
                                                 existing=json.dumps(existing, ensure_ascii=False)),
                            purpose="dedupe", session_id=session_id, user_id=user_id)
    try:
        same = int(out.get("same_as")) if out.get("same_as") is not None else None
    except (TypeError, ValueError):
        return None
    return same if same in {c["id"] for c in cands} else None
