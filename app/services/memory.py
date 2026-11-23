"""Returning-user greeting sourced only from that user's previous sessions."""
import logging

from sqlalchemy import select

from app.models import FixAttempt, Message, User
from app.repositories import create_chat_session, add_message
from app.services.extractor import redact_personal_data

logger = logging.getLogger(__name__)


def welcome_back_text(db, user_id: str) -> str | None:
    attempts = list(db.scalars(
        select(FixAttempt).where(FixAttempt.user_id == user_id)
        .order_by(FixAttempt.created_at.desc(), FixAttempt.id.desc())
    ))
    logger.debug("memory.lookup_started user_id=%s attempts=%d", user_id, len(attempts))
    for attempt in attempts:
        msg = db.get(Message, attempt.trigger_message_id)
        if msg and msg.user_id == user_id:
            summary = redact_personal_data(msg.content).strip()
            if summary:
                summary = summary[:190].rsplit(" ", 1)[0] if len(summary) > 190 else summary
                logger.info("memory.greeting_available user_id=%s attempt_id=%s", user_id, attempt.id)
                return f"Welcome back! Last time you reported: {summary}"
    logger.info("memory.no_prior_issue user_id=%s", user_id)
    return None


def start_session(db, user_id: str):
    """Creates a session and persists a customer-specific welcome."""
    logger.info("memory.new_session_requested user_id=%s", user_id)
    user = db.get(User, user_id)
    greeting = welcome_back_text(db, user_id)

    if not greeting:
        name = (user.name or "").strip() if user else ""
        if name:
            greeting = (
                f"Hey, {name}! Please describe the exact issue you are facing with "
                "error codes/messages, if applicable."
            )
        else:
            greeting = (
                "Hey there! Please describe the exact issue you are facing with "
                "error codes/messages, if applicable."
            )

    new_session = create_chat_session(db, user_id)
    add_message(db, new_session.id, user_id, "assistant", greeting)
    logger.info("memory.new_session_ready user_id=%s session_id=%s greeting_added=%s", user_id, new_session.id, True)
    return new_session
