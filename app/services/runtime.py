"""Single persisted message-processing path for Streamlit, HTTP and demo tests."""
from dataclasses import dataclass
import logging
import time

from app.config import settings
from app.repositories import (
    add_message, create_fix_attempt, get_chat_session, get_pending_fix_attempt,
    list_messages, list_verified_solutions, log_llm_call, update_fix_attempt_status,
)
from app.services.confirmation import (
    ConfirmationDecision, classify_confirmation, confirmed_message,
    REJECTED_CONFIRMATION_MESSAGE, AMBIGUOUS_CONFIRMATION_PROMPT,
)
from app.services.extractor import SolutionExtractor
from app.services.rate_limit import RateLimiter

logger = logging.getLogger(__name__)


@dataclass
class ProcessedMessage:
    answer: str
    source: str
    match_score: float | None = None
    solution_id: str | None = None


def process_message(db, user_id, session_id, content, chat_service, *, rate_limiter=None):
    """Read the whole transcript from DB. Caller commits/rolls back atomically."""
    started = time.perf_counter()
    logger.info("runtime.message_received user_id=%s session_id=%s chars=%d", user_id, session_id, len(content) if isinstance(content, str) else -1)
    if get_chat_session(db, session_id, user_id) is None:
        logger.warning("runtime.session_not_found_or_not_owned user_id=%s session_id=%s", user_id, session_id)
        raise ValueError("Session not found for this user")
    if not isinstance(content, str) or not content.strip():
        logger.warning("runtime.empty_message_rejected user_id=%s session_id=%s", user_id, session_id)
        raise ValueError("Message cannot be empty")
    limiter = rate_limiter or RateLimiter()
    limiter.consume(db, user_id, "message")
    user_message = add_message(db, session_id, user_id, "user", content)
    logger.debug("runtime.user_message_persisted user_id=%s session_id=%s message_id=%s", user_id, session_id, user_message.id)
    pending = get_pending_fix_attempt(db, session_id, user_id)

    if pending:
        decision = classify_confirmation(content)
        logger.info("runtime.confirmation_checked user_id=%s session_id=%s attempt_id=%s decision=%s", user_id, session_id, pending.id, decision.value)
        if decision == ConfirmationDecision.CONFIRMED:
            update_fix_attempt_status(db, pending.id, user_id, "confirmed", user_message.id)
            extractor = SolutionExtractor(chat_service.knowledge.embedder)
            try:
                extracted = extractor.extract_confirmed_attempt(db, user_id, pending.id)
                logger.info("runtime.extraction_finished user_id=%s session_id=%s attempt_id=%s status=%s solution_id=%s reason=%s", user_id, session_id, pending.id, extracted.status, extracted.solution_id, extracted.reason)
                answer = confirmed_message(int(extracted.saved), extracted.merged)
                result = ProcessedMessage(answer, "confirmation", solution_id=extracted.solution_id)
            except Exception:
                logger.exception("runtime.extraction_failed user_id=%s session_id=%s attempt_id=%s", user_id, session_id, pending.id)
                update_fix_attempt_status(db, pending.id, user_id, "pending")
                result = ProcessedMessage(
                    "I couldn't store that confirmed fix. Please reply **yes** again to retry.",
                    "extraction_unavailable",
                )
        elif decision == ConfirmationDecision.REJECTED:
            update_fix_attempt_status(db, pending.id, user_id, "rejected", user_message.id)
            result = ProcessedMessage(REJECTED_CONFIRMATION_MESSAGE, "rejected")
        else:
            result = ProcessedMessage(AMBIGUOUS_CONFIRMATION_PROMPT, "awaiting_confirmation")
        assistant_message = add_message(db, session_id, user_id, "assistant", result.answer)
        logger.info("runtime.confirmation_response_persisted user_id=%s session_id=%s message_id=%s source=%s elapsed_ms=%.1f", user_id, session_id, assistant_message.id, result.source, (time.perf_counter() - started) * 1000)
        return result

    transcript = list_messages(db, session_id, user_id)
    solutions = list_verified_solutions(db)
    logger.info("runtime.pipeline_started user_id=%s session_id=%s transcript_messages=%d verified_solutions=%d", user_id, session_id, len(transcript), len(solutions))
    result = chat_service.respond(
        user_message=content,
        transcript=transcript,
        verified_solutions=solutions,
        before_escalation=lambda: limiter.consume(db, user_id, "escalation"),
    )
    response_message = add_message(db, session_id, user_id, "assistant", result.answer)
    if result.llm_attempted:
        log_llm_call(
            db, session_id, result.model_name or settings.gemini_model,
            (result.llm_usage or {}).get("input_tokens", 0),
            (result.llm_usage or {}).get("output_tokens", 0),
        )
    if result.source == "gemini_escalation" or (
        result.source == "knowledge_hub" and result.solution_id
    ):
        create_fix_attempt(
            db, session_id, user_id, user_message.id, response_message.id,
            source=result.source, solution_id=result.solution_id,
        )
    logger.info("runtime.pipeline_completed user_id=%s session_id=%s source=%s match_score=%s solution_id=%s llm_attempted=%s input_tokens=%s output_tokens=%s elapsed_ms=%.1f", user_id, session_id, result.source, result.match_score, result.solution_id, result.llm_attempted, (result.llm_usage or {}).get("input_tokens", 0), (result.llm_usage or {}).get("output_tokens", 0), (time.perf_counter() - started) * 1000)
    return ProcessedMessage(result.answer, result.source, result.match_score, result.solution_id)
