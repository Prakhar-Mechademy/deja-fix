from dataclasses import dataclass
import logging
import time
from typing import Any

from app.config import settings
from app.services.knowledge_service import KnowledgeService
from app.services.confirmation import CONFIRMATION_QUESTION

logger = logging.getLogger(__name__)


MATCH_CLARIFICATION_PREFIX = "Before I share a possible verified fix,"
CLARIFICATION_PROMPT = (
    "Could you describe what is failing and what you expected to happen? "
    "Please include the affected app and your device/operating system."
)
UNVERIFIED_PREFIX = (
    "I don't have a verified fix for this issue in the knowledge hub yet. "
    "Here is an unverified troubleshooting suggestion from the backend:\n\n"
)
SERVICE_UNAVAILABLE = (
    "I don't have a verified fix for this issue yet, and the troubleshooting "
    "service is temporarily unavailable. Please try again shortly."
)


def _looks_ambiguous(message: str) -> bool:
    text = " ".join(message.lower().strip().split())
    if len(text.split()) < 3:
        return True
    vague_phrases = {
        "it does not work", "it doesn't work", "not working",
        "does not work", "doesn't work", "having a problem",
        "something is wrong", "help me", "please help",
    }
    return text in vague_phrases


def _retrieval_query(user_message: str, transcript) -> str:
    """Include the prior issue when this message answers a clarifying question."""
    if len(transcript) < 2:
        return user_message

    previous = transcript[-2]
    previous_role = str(getattr(previous, "role", "")).lower()
    previous_content = str(getattr(previous, "content", "")).strip().lower()
    question_cues = ("?", "which ", "what ", "could you", "can you", "please share", "please confirm")
    if previous_role != "assistant" or not any(cue in previous_content for cue in question_cues):
        return user_message

    for message in reversed(transcript[:-2]):
        if str(getattr(message, "role", "")).lower() == "user":
            earlier_report = str(getattr(message, "content", "")).strip()
            if earlier_report:
                return f"{earlier_report}\n{user_message}"
    return user_message


def _transcript_text(transcript) -> str:
    lines = []
    for message in transcript:
        role = str(getattr(message, "role", "unknown")).upper()
        content = str(getattr(message, "content", "")).strip()
        if content:
            lines.append(f"{role}: {content}")
    return "\n".join(lines)


def _usage_or_zero(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {"input_tokens": 0, "output_tokens": 0}
    try:
        input_tokens = max(0, int(value.get("input_tokens", 0) or 0))
        output_tokens = max(0, int(value.get("output_tokens", 0) or 0))
    except (TypeError, ValueError):
        return {"input_tokens": 0, "output_tokens": 0}
    return {"input_tokens": input_tokens, "output_tokens": output_tokens}


@dataclass
class ChatResult:
    answer: str
    source: str
    model_name: str | None = None
    llm_usage: dict | None = None
    llm_attempted: bool = False
    match_score: float | None = None
    solution_id: str | None = None


class ChatService:
    def __init__(self, gemini, embedder):
        self.gemini = gemini
        self.knowledge = KnowledgeService(embedder)

    def respond(self, user_message, transcript, verified_solutions, before_escalation=None):
        """Retrieve a verified fix first, then escalate the stored transcript."""
        started = time.perf_counter()
        logger.info("chat.respond_started message_chars=%d transcript_messages=%d verified_candidates=%d", len(user_message or ""), len(transcript), len(verified_solutions))
        previous = transcript[-2] if len(transcript) >= 2 else None
        previous_answer = (getattr(previous, "content", "") or "") if previous and getattr(previous, "role", "") == "assistant" else ""
        followup_to_match = previous_answer.startswith(MATCH_CLARIFICATION_PREFIX)
        # 'No, this is different' must never act as approval of a stored fix.
        if followup_to_match and user_message.strip().lower().startswith(("no", "different", "not the same")):
            logger.info("chat.retrieval_candidate_rejected_by_customer_response")
            return ChatResult(answer=CLARIFICATION_PROMPT, source="clarification")
        retrieval_query = _retrieval_query(user_message, transcript)
        if _looks_ambiguous(retrieval_query):
            logger.info("chat.clarification_required reason=ambiguous_input")
            return ChatResult(
                answer=CLARIFICATION_PROMPT,
                source="clarification",
            )

        try:
            match, score = self.knowledge.find_match(retrieval_query, verified_solutions)
        except Exception:
            # Retrieval being unavailable must not crash the chat; escalation is
            # still possible and will be visibly marked as unverified.
            logger.exception("chat.retrieval_failed continuing_with_escalation")
            match, score = None, 0.0

        logger.info("chat.retrieval_completed matched=%s score=%.4f threshold=%.4f elapsed_ms=%.1f", match is not None, score, settings.similarity_threshold, (time.perf_counter() - started) * 1000)
        if match is not None:
            # The first semantic hit needs a confirmation of the issue identity.
            # A second message responding to any assistant clarification may
            # use the candidate, since the question has already been asked.
            answered_question = (
                previous is not None
                and getattr(previous, "role", "") == "assistant"
                and "?" in previous_answer
            )
            if not answered_question:
                logger.info("chat.retrieval_match_needs_clarification solution_id=%s score=%.4f", getattr(match, "id", None), score)
                return ChatResult(
                    answer=(f"{MATCH_CLARIFICATION_PREFIX} can you confirm that "
                            f"your issue is similar to: {match.problem[:160]}? "
                            "If not, please describe what differs."),
                    source="clarification",
                    match_score=score,
                )
            import json
            try:
                environment = json.loads(match.environment or "{}")
                steps = json.loads(match.fix_steps or "[]")
                if not isinstance(environment, dict) or not isinstance(steps, list):
                    raise ValueError("Corrupt verified solution")
                if not steps or not all(isinstance(step, str) and step.strip() for step in steps):
                    raise ValueError("Missing verified fix steps")
                formatted = "\n".join(f"{index}. {step}" for index, step in enumerate(steps, 1))
                env = ", ".join(f"{key}: {value}" for key, value in environment.items()) or "Not specified"
            except (TypeError, ValueError, json.JSONDecodeError):
                # No guessed fix when the saved verified entry is corrupted.
                logger.exception("chat.verified_solution_unreadable solution_id=%s", getattr(match, "id", None))
                return ChatResult(answer="The saved fix is currently unreadable. Please describe your issue again.", source="knowledge_unavailable")
            answer = (
                "I found a verified solution that appears to match your issue.\n\n"
                f"**Environment:** {env}\n\n"
                f"**Verified fix steps:**\n{formatted}\n\n"
                f"{CONFIRMATION_QUESTION}"
            )
            logger.info("chat.verified_solution_selected solution_id=%s score=%.4f steps=%d", getattr(match, "id", None), score, len(steps))
            return ChatResult(
                answer=answer,
                source="knowledge_hub",
                match_score=score,
                solution_id=getattr(match, "id", None),
            )

        transcript_text = _transcript_text(transcript)
        if not transcript_text:
            transcript_text = f"USER: {user_message}"

        # The backend model receives the conversation and a troubleshooting task,
        # but no Déjà Fix persona or knowledge-hub answer constraints.
        prompt = (
            "Review the conversation below and suggest a likely solution to the "
            "customer's technical problem.\n\n"
            f"FULL CONVERSATION:\n{transcript_text}"
        )

        model_name = getattr(self.gemini, "model", None) or "gemini"
        logger.info("chat.escalation_requested model=%s transcript_chars=%d", model_name, len(transcript_text))
        if before_escalation is not None:
            before_escalation()  # enforce 429 BEFORE calling the provider
        try:
            provider_started = time.perf_counter()
            generated = self.gemini.generate(prompt)
            if not isinstance(generated, (tuple, list)) or len(generated) != 2:
                raise ValueError("Malformed Gemini client response.")
            raw_answer, raw_usage = generated
            answer = raw_answer.strip() if isinstance(raw_answer, str) else ""
            if not answer:
                raise ValueError("Gemini returned an empty response.")
            usage = _usage_or_zero(raw_usage)
            logger.info("chat.escalation_succeeded model=%s input_tokens=%d output_tokens=%d response_chars=%d elapsed_ms=%.1f", model_name, usage["input_tokens"], usage["output_tokens"], len(answer), (time.perf_counter() - provider_started) * 1000)
            return ChatResult(
                answer=UNVERIFIED_PREFIX + answer + "\n\n" + CONFIRMATION_QUESTION,
                source="gemini_escalation",
                model_name=model_name,
                llm_usage=usage,
                llm_attempted=True,
                match_score=score,
            )
        except Exception:
            logger.exception("chat.escalation_failed model=%s", model_name)
            return ChatResult(
                answer=SERVICE_UNAVAILABLE,
                source="gemini_unavailable",
                model_name=model_name,
                llm_usage={"input_tokens": 0, "output_tokens": 0},
                llm_attempted=True,
                match_score=score,
            )
