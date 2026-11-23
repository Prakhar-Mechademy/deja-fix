"""Persona agent: the customer-facing chatbot.

Design choices (for DESIGN.md):
  * Hub answers and clarifying questions are TEMPLATED, not LLM-generated. The fixed tone
    the spec asks for is easiest to guarantee by writing it once, and it keeps a hub-served
    session cheap: matching already paid for an embed + judge call (via hub.find_match);
    serving the fix itself costs nothing extra.
  * Clarifying questions are asked on the customer's first message only, and only once
    per session (tracked by a 'persona_clarify' message already existing). They're built
    from whatever the matched solution's environment fields are, or a generic question if
    nothing matched yet and the message looks too short to act on.
  * Containment ("forget your rules...") is caught by a rule-based check before any hub
    search or LLM call, so a jailbreak attempt costs nothing and can't reach the backend LLM.
  * When the hub has no match, the persona says so plainly, then hands the FULL conversation
    to the backend LLM (no persona, no restrictions) and returns its suggestion as-is, per
    the "Escalation" component's own spec. That one call does both jobs.
"""
from __future__ import annotations

import re
from typing import Optional

from . import db, hub, llm

# --------------------------------------------------------------------------- #
# Fixed-tone templates
# --------------------------------------------------------------------------- #
DECLINE_MESSAGE = (
    "I can only help using our verified support solutions, and I'm not able to set that "
    "aside. If you're running into a product issue, tell me what's happening and I'll do "
    "my best to help from there."
)

NO_MATCH_PREFIX = (
    "I don't have a verified fix for this from past cases yet. Let me check with our "
    "broader support system:"
)

FIX_HEADER = "Here's a fix that's worked for this before:"
FIX_FOOTER = "If that doesn't resolve it, let me know and we'll dig further."

ESCALATION_ERROR_PREFIX = (
    "I don't have a verified fix for this from past cases, and I'm having trouble "
    "reaching our broader support system right now. "
)

# --------------------------------------------------------------------------- #
# Containment: catch jailbreak attempts before any hub search or LLM call
# --------------------------------------------------------------------------- #
_INJECTION_PATTERNS = [
    r"forget (your|all)( the)? (rules|instructions)",
    r"ignore (your|all|the|previous) (rules|instructions)",
    r"disregard (your|the|all) (rules|instructions)",
    r"(answer|respond) from your own knowledge",
    r"without (any )?(restrictions|rules|limits|filters)",
    r"\bsystem prompt\b",
    r"(reveal|show|print|repeat|leak|output) (me )?(your|the) (system )?(prompt|instructions)",
    r"pretend (you('?re| are)|to be)",
    r"\bact as (if|though) you\b",
    r"\bjailbreak\b",
    r"bypass your (rules|restrictions|instructions)",
]
_INJECTION_RE = re.compile("|".join(_INJECTION_PATTERNS), re.I)


def _is_injection_attempt(text: str) -> bool:
    return bool(_INJECTION_RE.search(text or ""))


# --------------------------------------------------------------------------- #
# Ambiguity / clarifying questions (rule-based, no LLM call)
# --------------------------------------------------------------------------- #
def _looks_vague(text: str) -> bool:
    return len((text or "").split()) < 6


def _already_clarified(messages: list[dict]) -> bool:
    return any(m.get("source") == "persona_clarify" for m in messages)


def _clarifying_question_for(solution: dict) -> str:
    env = solution.get("environment") or {}
    device, os_ = env.get("device"), env.get("os")
    if device and os_:
        return f"Just to confirm — are you on {device} running {os_}?"
    if device:
        return f"Just to confirm — are you using {device}?"
    if os_:
        return f"Just to confirm — are you running {os_}?"
    return "Could you tell me a bit more: what device and OS are you on, and when does this happen?"


def _generic_clarifying_question() -> str:
    return "Could you tell me a bit more: what device and OS are you on, and when does this happen?"


# --------------------------------------------------------------------------- #
# Formatting a hub fix in the fixed tone
# --------------------------------------------------------------------------- #
def _format_fix(solution: dict) -> str:
    steps = solution.get("fix_steps") or []
    numbered = "\n".join(f"{i}. {s}" for i, s in enumerate(steps, 1))
    return f"{FIX_HEADER}\n{numbered}\n\n{FIX_FOOTER}"


# --------------------------------------------------------------------------- #
# Transcript helpers
# --------------------------------------------------------------------------- #
def _user_turns(messages: list[dict]) -> list[str]:
    return [m["content"] for m in messages if m["role"] == "user"]


def _conversation_for_llm(messages: list[dict]) -> list[dict]:
    return [{"role": m["role"], "content": m["content"]} for m in messages]


# --------------------------------------------------------------------------- #
# Returning-customer greeting
# --------------------------------------------------------------------------- #
def greeting_for_user(user_id: str, current_session_id: Optional[str] = None) -> Optional[str]:
    """A greeting based on the user's OWN past sessions only, or None for a new customer.

    Call this once, right after creating a new session, before any message is handled.
    """
    history = db.get_user_history(user_id, limit=5)
    for entry in history:
        if entry["session_id"] == current_session_id:
            continue
        if entry["solution_titles"]:
            return f"Welcome back! Last time it was about {entry['solution_titles'][0].lower()}."
    return None


# --------------------------------------------------------------------------- #
# Main entry point
# --------------------------------------------------------------------------- #
def handle_message(session_id: str, user_id: str, text: str) -> dict:
    """Process one customer message. Stores both the customer's and the assistant's
    message, and returns {"reply": str, "source": str, "solution_id": int|None}.
    """
    db.add_message(session_id, user_id, "user", text)
    history = db.get_messages(session_id)  # includes the message just added
    user_turns = _user_turns(history)
    is_first_turn = len(user_turns) == 1

    def respond(text: str, source: str, solution_id: Optional[int] = None) -> dict:
        db.add_message(session_id, user_id, "assistant", text, source=source)
        return {"reply": text, "source": source, "solution_id": solution_id}

    # 1. Containment: never let a jailbreak attempt reach the hub or the LLM.
    if _is_injection_attempt(text):
        return respond(DECLINE_MESSAGE, "persona")

    # 2. Search the hub using everything the customer has said so far.
    query_text = " ".join(user_turns)
    try:
        match = hub.find_match(query_text, session_id=session_id, user_id=user_id)
    except llm.LLMError as e:
        return respond(ESCALATION_ERROR_PREFIX + e.user_message, "persona")

    already_clarified = _already_clarified(history)

    if match:
        solution = match["solution"]
        if is_first_turn and not already_clarified:
            return respond(_clarifying_question_for(solution), "persona_clarify")
        db.set_served_from(session_id, "hub")
        db.record_reuse(solution["id"], session_id)
        return respond(_format_fix(solution), "hub", solution["id"])

    # 3. No match: ask once if the problem is too vague to escalate usefully.
    if is_first_turn and not already_clarified and _looks_vague(text):
        return respond(_generic_clarifying_question(), "persona_clarify")

    # 4. Escalate: hand the full conversation to the unrestricted backend LLM.
    try:
        suggestion = llm.generate(
            _conversation_for_llm(history), session_id=session_id, user_id=user_id, purpose="escalation",
        )
    except llm.LLMError as e:
        return respond(ESCALATION_ERROR_PREFIX + e.user_message, "persona")

    db.set_served_from(session_id, "escalation")
    return respond(f"{NO_MATCH_PREFIX}\n\n{suggestion}", "llm")