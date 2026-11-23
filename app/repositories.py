import logging

from sqlalchemy import select

from app.models import User, ChatSession, Message, Solution, LLMCallLog

logger = logging.getLogger(__name__)


def get_or_create_user(db, user_id: str | None = None) -> User:
    """Get a particular user, or create a new isolated anonymous user.

    With no ID this always creates a new identity. It deliberately never
    selects an arbitrary/first user from the database.
    """
    if user_id:
        user = db.get(User, user_id)
        if user is not None:
            logger.debug("repository.user_found user_id=%s", user.id)
            return user
        user = User(id=user_id)
    else:
        user = User()

    db.add(user)
    db.flush()
    logger.info("repository.user_created user_id=%s", user.id)
    return user


def set_user_name(db, user_id: str, name: str) -> User:
    """Persist a normalized customer display name."""
    user = db.get(User, user_id)
    if user is None:
        raise ValueError("Unknown customer.")
    cleaned = " ".join((name or "").strip().split())
    user.name = cleaned[:100] if cleaned else None
    db.flush()
    logger.info("repository.user_name_updated user_id=%s", user_id)
    return user


def create_chat_session(db, user_id: str) -> ChatSession:
    if db.get(User, user_id) is None:
        raise ValueError("Cannot create a session for an unknown user.")
    chat_session = ChatSession(user_id=user_id)
    db.add(chat_session)
    db.flush()
    logger.info("repository.session_created user_id=%s session_id=%s", user_id, chat_session.id)
    return chat_session


def get_chat_session(db, session_id: str, user_id: str):
    """Fetch a session only when it belongs to the requesting user."""
    return db.scalar(
        select(ChatSession).where(
            ChatSession.id == session_id,
            ChatSession.user_id == user_id,
        )
    )


def list_chat_sessions(db, user_id: str) -> list[ChatSession]:
    """Return newest-first sessions for this user only."""
    sessions = list(db.scalars(
        select(ChatSession)
        .where(ChatSession.user_id == user_id)
        .order_by(ChatSession.created_at.desc(), ChatSession.id.desc())
    ))
    logger.debug("repository.sessions_listed user_id=%s count=%d", user_id, len(sessions))
    return sessions


def add_message(db, session_id: str, user_id: str, role: str, content: str) -> Message:
    if role not in {"user", "assistant", "system"}:
        raise ValueError("Message role must be user, assistant, or system.")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("Message content cannot be empty.")

    owner_session = get_chat_session(db, session_id, user_id)
    if owner_session is None:
        raise ValueError("The session does not exist or does not belong to this user.")

    message = Message(
        session_id=session_id,
        user_id=user_id,
        role=role,
        content=content.strip(),
    )
    db.add(message)
    db.flush()
    logger.debug("repository.message_added user_id=%s session_id=%s message_id=%s role=%s chars=%d", user_id, session_id, message.id, role, len(message.content))
    return message


def list_messages(db, session_id: str, user_id: str) -> list[Message]:
    if get_chat_session(db, session_id, user_id) is None:
        raise ValueError("The session does not exist or does not belong to this user.")
    messages = list(db.scalars(
        select(Message)
        .where(Message.session_id == session_id)
        .order_by(Message.created_at, Message.id)
    ))
    logger.debug("repository.messages_loaded user_id=%s session_id=%s count=%d", user_id, session_id, len(messages))
    return messages


def list_verified_solutions(db) -> list[Solution]:
    solutions = list(db.scalars(
        select(Solution)
        .where(Solution.verified.is_(True))
        .order_by(Solution.created_at, Solution.id)
    ))
    logger.debug("repository.verified_solutions_loaded count=%d", len(solutions))
    return solutions


def log_llm_call(
    db, session_id: str, model: str, input_tokens: int = 0, output_tokens: int = 0
) -> LLMCallLog:
    if input_tokens < 0 or output_tokens < 0:
        raise ValueError("Token counts cannot be negative.")
    if db.get(ChatSession, session_id) is None:
        raise ValueError("Cannot log an LLM call for an unknown session.")

    entry = LLMCallLog(
        session_id=session_id,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )
    db.add(entry)
    db.flush()
    logger.info("repository.llm_call_logged session_id=%s model=%s input_tokens=%d output_tokens=%d", session_id, model, input_tokens, output_tokens)
    return entry


def create_fix_attempt(
    db,
    session_id: str,
    user_id: str,
    trigger_message_id: str,
    response_message_id: str,
    *,
    source: str = "gemini_escalation",
    solution_id: str | None = None,
):
    """Track a proposed backend or hub fix until the user confirms its outcome."""
    from app.models import FixAttempt

    if get_chat_session(db, session_id, user_id) is None:
        raise ValueError("The session does not exist or does not belong to this user.")

    linked_messages = []
    for message_id in (trigger_message_id, response_message_id):
        message = db.get(Message, message_id)
        if message is None or message.session_id != session_id or message.user_id != user_id:
            raise ValueError("Fix attempt messages must belong to the same user and session.")
        linked_messages.append(message)
    if linked_messages[0].role != "user" or linked_messages[1].role != "assistant":
        raise ValueError("A fix attempt must link a user trigger and an assistant suggestion.")

    if solution_id is not None and db.get(Solution, solution_id) is None:
        raise ValueError("Cannot track a fix for an unknown solution.")

    attempt = FixAttempt(
        session_id=session_id,
        user_id=user_id,
        trigger_message_id=trigger_message_id,
        response_message_id=response_message_id,
        solution_id=solution_id,
        source=source,
        status="pending",
    )
    db.add(attempt)
    db.flush()
    logger.info("repository.fix_attempt_created attempt_id=%s user_id=%s session_id=%s source=%s solution_id=%s", attempt.id, user_id, session_id, source, solution_id)
    return attempt


def get_pending_fix_attempt(db, session_id: str, user_id: str):
    from app.models import FixAttempt

    if get_chat_session(db, session_id, user_id) is None:
        raise ValueError("The session does not exist or does not belong to this user.")
    attempt = db.scalar(
        select(FixAttempt)
        .where(
            FixAttempt.session_id == session_id,
            FixAttempt.user_id == user_id,
            FixAttempt.status == "pending",
        )
        .order_by(FixAttempt.created_at.desc(), FixAttempt.id.desc())
        .limit(1)
    )
    logger.debug("repository.pending_attempt_lookup user_id=%s session_id=%s found=%s attempt_id=%s", user_id, session_id, attempt is not None, attempt.id if attempt else None)
    return attempt


def get_fix_attempt(db, attempt_id: str, user_id: str):
    from app.models import FixAttempt

    attempt = db.get(FixAttempt, attempt_id)
    if attempt is None or attempt.user_id != user_id:
        return None
    return attempt


def update_fix_attempt_status(
    db,
    attempt_id: str,
    user_id: str,
    status: str,
    confirmation_message_id: str | None = None,
):
    from app.models import FixAttempt

    if status not in {"pending", "confirmed", "rejected", "abandoned"}:
        raise ValueError("Invalid fix-attempt status.")
    attempt = get_fix_attempt(db, attempt_id, user_id)
    if attempt is None:
        raise ValueError("The fix attempt does not exist or does not belong to this user.")
    retrying_failed_extraction = (
        attempt.status == "confirmed"
        and status == "pending"
        and get_solution_source_for_attempt(db, attempt.id) is None
    )
    if attempt.status not in {"pending", status} and not retrying_failed_extraction:
        raise ValueError("A completed fix attempt cannot be changed.")
    if retrying_failed_extraction:
        attempt.confirmation_message_id = None

    if status in {"confirmed", "rejected"} and confirmation_message_id is None and attempt.confirmation_message_id is None:
        raise ValueError("A confirmed or rejected attempt must reference the user's outcome message.")

    if confirmation_message_id is not None:
        message = db.get(Message, confirmation_message_id)
        if (
            message is None
            or message.session_id != attempt.session_id
            or message.user_id != user_id
            or message.role != "user"
        ):
            raise ValueError("The confirmation message must belong to this user and session.")
        attempt.confirmation_message_id = confirmation_message_id

    old_status = attempt.status
    attempt.status = status
    db.flush()
    logger.info("repository.fix_attempt_status_changed attempt_id=%s user_id=%s from=%s to=%s confirmation_message_id=%s", attempt_id, user_id, old_status, status, confirmation_message_id or attempt.confirmation_message_id)
    return attempt


def list_fix_attempts(db, session_id: str, user_id: str):
    from app.models import FixAttempt

    if get_chat_session(db, session_id, user_id) is None:
        raise ValueError("The session does not exist or does not belong to this user.")
    return list(db.scalars(
        select(FixAttempt)
        .where(FixAttempt.session_id == session_id, FixAttempt.user_id == user_id)
        .order_by(FixAttempt.created_at, FixAttempt.id)
    ))


def get_solution_source_for_attempt(db, attempt_id: str):
    from app.models import SolutionSource

    return db.scalar(
        select(SolutionSource).where(SolutionSource.fix_attempt_id == attempt_id)
    )


def add_solution_source(db, solution_id: str, session_id: str, fix_attempt_id: str):
    """Idempotently attach a confirmed attempt to the solution it contributed to."""
    from app.models import SolutionSource

    existing = get_solution_source_for_attempt(db, fix_attempt_id)
    if existing is not None:
        logger.debug("repository.solution_source_exists solution_id=%s attempt_id=%s", solution_id, fix_attempt_id)
        return existing, False
    source = SolutionSource(
        solution_id=solution_id,
        session_id=session_id,
        fix_attempt_id=fix_attempt_id,
    )
    db.add(source)
    db.flush()
    logger.info("repository.solution_source_added solution_id=%s session_id=%s attempt_id=%s", solution_id, session_id, fix_attempt_id)
    return source, True


def set_user_display_name(db, user_id: str, display_name: str):
    """Set or update the display name for a user."""
    user = db.get(User, user_id)
    if user is None:
        raise ValueError("Cannot set display name for an unknown user.")
    user.display_name = display_name.strip()
    db.flush()
    logger.info("repository.user_display_name_set user_id=%s display_name=%s", user_id, display_name)


def get_user_display_name(db, user_id: str) -> str | None:
    """Get the display name for a user, if set."""
    user = db.get(User, user_id)
    if user is None:
        return None
    return user.display_name
