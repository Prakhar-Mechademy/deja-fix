"""
Persona agent: the customer-facing chatbot.

Design choices (for DESIGN.md):
  * Hub answers and clarifying questions are TEMPLATED, not LLM-generated.
  * Clarifying questions are asked on the customer's first message only.
  * Containment catches jailbreak attempts before hub search or LLM calls.
  * Friendly closing messages are handled without another LLM call.
  * When the hub has no match, the full conversation is handed to the backend LLM.
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

CLOSING_MESSAGE = (
    "I'm glad that's working again! Thanks for confirming. "
    "Feel free to reach out if you need anything else."
)


# --------------------------------------------------------------------------- #
# Containment: catch jailbreak attempts before hub search or LLM calls
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
# Friendly closing / resolution detection
# --------------------------------------------------------------------------- #

_RESOLUTION_PATTERNS = [
    r"\bthat fixed it\b",
    r"\bworking again\b",
    r"\bworks now\b",
    r"\bit is working\b",
    r"\bit's working\b",
    r"\bproblem solved\b",
    r"\bissue is resolved\b",
    r"\bthat worked\b",
    r"\bback to normal\b",
]

_RESOLUTION_RE = re.compile("|".join(_RESOLUTION_PATTERNS), re.I)


# A confirmation that also raises something else ("that fixed it, but now my printer...")
# or says the fix did not last must NOT end the conversation: it goes through the normal flow.
_CONTRAST_RE = re.compile(
    r"\b(?:but|however|although|though|except)\b"
    r"|\bfor (?:a|a few|few) (?:minutes?|mins?|seconds?|while|bit|hours?|days?)\b"
    r"|\b(?:came|comes) back\b|\b(?:broke|stopped|failed|happened) again\b",
    re.I,
)


def _is_resolution_confirmation(text: str) -> bool:
    t = text or ""
    return bool(_RESOLUTION_RE.search(t)) and not _CONTRAST_RE.search(t)


# --------------------------------------------------------------------------- #
# Ambiguity / clarifying questions (rule-based, no LLM call)
# --------------------------------------------------------------------------- #

def _looks_vague(text: str) -> bool:
    return len((text or "").split()) < 6


def _already_clarified(messages: list[dict]) -> bool:
    return any(m.get("source") == "persona_clarify" for m in messages)


def _clarifying_question_for(solution: dict) -> str:
    env = solution.get("environment") or {}

    device = env.get("device")
    os_ = env.get("os")

    if device and os_:
        return f"Just to confirm — are you on {device} running {os_}?"

    if device:
        return f"Just to confirm — are you using {device}?"

    if os_:
        return f"Just to confirm — are you running {os_}?"

    return (
        "Could you tell me a bit more: what device and OS are you on, "
        "and when does this happen?"
    )


def _generic_clarifying_question() -> str:
    return (
        "Could you tell me a bit more: what device and OS are you on, "
        "and when does this happen?"
    )


# --------------------------------------------------------------------------- #
# Formatting a hub fix in the fixed tone
# --------------------------------------------------------------------------- #

def _format_fix(solution: dict) -> str:
    steps = solution.get("fix_steps") or []

    numbered = "\n".join(
        f"{i}. {step}"
        for i, step in enumerate(steps, 1)
    )

    return f"{FIX_HEADER}\n{numbered}\n\n{FIX_FOOTER}"


# --------------------------------------------------------------------------- #
# Transcript helpers
# --------------------------------------------------------------------------- #

def _user_turns(messages: list[dict]) -> list[str]:
    return [
        m["content"]
        for m in messages
        if m["role"] == "user"
    ]


def _conversation_for_llm(messages: list[dict]) -> list[dict]:
    return [
        {
            "role": m["role"],
            "content": m["content"],
        }
        for m in messages
    ]


# --------------------------------------------------------------------------- #
# Returning-customer greeting
# --------------------------------------------------------------------------- #

def greeting_for_user(
    user_id: str,
    current_session_id: Optional[str] = None
) -> Optional[str]:
    """
    A greeting based on the user's OWN past sessions only,
    or None for a new customer.

    Call this once, right after creating a new session,
    before any message is handled.
    """

    history = db.get_user_history(user_id, limit=5)

    for entry in history:

        if entry["session_id"] == current_session_id:
            continue

        if entry["solution_titles"]:
            return (
                "Welcome back! Last time it was about "
                f"{entry['solution_titles'][0].lower()}."
            )

    return None


# --------------------------------------------------------------------------- #
# Main entry point
# --------------------------------------------------------------------------- #

def handle_message(
    session_id: str,
    user_id: str,
    text: str,
    before_escalation=None,
) -> dict:
    """
    Process one customer message.

    Stores both the customer's and the assistant's message.

    Returns:
        {
            "reply": str,
            "source": str,
            "solution_id": int | None
        }
    """

    # Store the customer's message.
    user_msg_id = db.add_message(session_id, user_id, "user", text)

    # Retrieve the complete conversation.
    history = db.get_messages(session_id)

    user_turns = _user_turns(history)

    is_first_turn = len(user_turns) == 1

    # ----------------------------------------------------------------------- #
    # Response helper
    # ----------------------------------------------------------------------- #

    def respond(
        reply: str,
        source: str,
        solution_id: Optional[int] = None,
    ) -> dict:

        db.add_message(
            session_id,
            user_id,
            "assistant",
            reply,
            source=source,
        )

        return {
            "reply": reply,
            "source": source,
            "solution_id": solution_id,
        }

    # ----------------------------------------------------------------------- #
    # 1. Containment: detect jailbreak attempts
    # ----------------------------------------------------------------------- #

    if _is_injection_attempt(text):
        return respond(
            DECLINE_MESSAGE,
            "persona",
        )

    # ----------------------------------------------------------------------- #
    # 2. Friendly closing: no hub search or LLM call
    # ----------------------------------------------------------------------- #

    if _is_resolution_confirmation(text):
        return respond(
            CLOSING_MESSAGE,
            "persona",
        )

    # ----------------------------------------------------------------------- #
    # 3. Search the hub using everything the customer has said so far
    # ----------------------------------------------------------------------- #

    query_text = " ".join(user_turns)

    try:
        match = hub.find_match(
            query_text,
            session_id=session_id,
            user_id=user_id,
        )

    except llm.LLMError as e:
        return respond(
            ESCALATION_ERROR_PREFIX + e.user_message,
            "persona",
        )

    already_clarified = _already_clarified(history)

    # ----------------------------------------------------------------------- #
    # 4. Hub match found
    # ----------------------------------------------------------------------- #

    if match:

        solution = match["solution"]

        # Ask a clarifying question on the first turn only.
        if is_first_turn and not already_clarified:

            return respond(
                _clarifying_question_for(solution),
                "persona_clarify",
            )

        # Serve the verified solution.
        db.set_served_from(session_id, "hub")

        db.record_reuse(
            solution["id"],
            session_id,
        )

        return respond(
            _format_fix(solution),
            "hub",
            solution["id"],
        )

    # ----------------------------------------------------------------------- #
    # 5. No hub match: clarify vague first messages
    # ----------------------------------------------------------------------- #

    if (
        is_first_turn
        and not already_clarified
        and _looks_vague(text)
    ):

        return respond(
            _generic_clarifying_question(),
            "persona_clarify",
        )

    # ----------------------------------------------------------------------- #
    # 6. Escalation: check rate limit before calling the LLM
    # ----------------------------------------------------------------------- #

    if before_escalation is not None:
        try:
            before_escalation()
        except Exception:
            # Rate-limited (429): don't leave an unanswered customer message in the transcript.
            db.delete_message(user_msg_id)
            raise

    try:

        suggestion = llm.generate(
            _conversation_for_llm(history),
            session_id=session_id,
            user_id=user_id,
            purpose="escalation",
        )

    except llm.LLMError as e:

        return respond(
            ESCALATION_ERROR_PREFIX + e.user_message,
            "persona",
        )

    # ----------------------------------------------------------------------- #
    # 7. Save escalation information and return response
    # ----------------------------------------------------------------------- #

    db.set_served_from(
        session_id,
        "escalation",
    )

    return respond(
        f"{NO_MATCH_PREFIX}\n\n{suggestion}",
        "llm",
    )
