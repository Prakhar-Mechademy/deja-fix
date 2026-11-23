"""Extract and verify reusable solutions from persisted conversations."""

import json
from typing import List, Tuple

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import LLMUsage, Message, Solution, SolutionSource, Session as SupportSession
from app.schemas.solutions import ExtractedSolution, ExtractedSolutionSet
from app.services.confirmation import find_confirmation_message
from app.services.embedding import deserialize_embedding, serialize_embedding
from app.services.gemini import generate_response
from app.services.retrieval import cosine_similarity, embed_text
from app.utils.pii import redact_pii, sanitise_string_list

EXTRACTOR_SYSTEM_INSTRUCTION = """Extract troubleshooting knowledge from the supplied conversation.
Only extract resolutions if the customer explicitly confirms that the problem was resolved.
Return JSON only: solutions[{problem,symptoms,device,operating_system,app_version,fix_steps,failed_attempts}].
Generalise and redact names, emails, phone numbers, URLs, account IDs, and personal details.
Preserve technical device, operating-system, app-version, symptom, and step context.
Never infer confirmation from an assistant suggestion.
"""


def read_transcript(db: Session, session_id: str) -> List[Message]:
    return (
        db.query(Message)
        .filter(Message.session_id == session_id)
        .order_by(Message.created_at.asc(), Message.id.asc())
        .all()
    )


def format_transcript(messages: List[Message]) -> str:
    return "\n".join(
        f"{message.role.upper()}: {redact_pii(message.content)}"
        for message in messages
    )


def _heuristic_extract(messages: List[Message]) -> ExtractedSolutionSet:
    user_messages = [redact_pii(m.content) for m in messages if m.role == "user"]
    assistant_messages = [redact_pii(m.content) for m in messages if m.role == "assistant"]

    if not user_messages:
        return ExtractedSolutionSet(solutions=[])

    problem = " ".join(user_messages).strip()
    if len(problem) > 500:
        problem = problem[:497].rstrip() + "..."

    fix_steps = []
    for line in assistant_messages:
        for step in line.split("\n"):
            cleaned = step.strip()
            if cleaned:
                fix_steps.append(cleaned.lstrip("0123456789. "))

    if not fix_steps:
        return ExtractedSolutionSet(solutions=[])

    device = os_name = app_version = None
    combined = " ".join(user_messages + assistant_messages).lower()

    if "windows" in combined:
        os_name = "Windows"
    elif "macos" in combined or "mac os" in combined:
        os_name = "macOS"
    elif "linux" in combined:
        os_name = "Linux"
    elif "android" in combined:
        os_name = "Android"
    elif "ios" in combined:
        os_name = "iOS"

    for technical in ("phone", "laptop", "desktop", "tablet", "computer", "ipad", "iphone"):
        if technical in combined:
            device = technical.title()
            break

    return ExtractedSolutionSet(solutions=[ExtractedSolution(
        problem=problem,
        symptoms=[problem],
        device=device,
        operating_system=os_name,
        app_version=app_version,
        fix_steps=sanitise_string_list(fix_steps),
        failed_attempts=[],
    )])


async def _extract_with_gemini(messages: List[Message]) -> Tuple[ExtractedSolutionSet, dict]:
    transcript = format_transcript(messages)
    response = await generate_response(
        prompt=f"Conversation transcript:\n{transcript}\n\nReturn the JSON object only.",
        system_instruction=EXTRACTOR_SYSTEM_INSTRUCTION,
        thinking_level="LOW",
    )

    text = response["text"].strip()
    if text.startswith("```"):
        text = text.replace("```json", "", 1).replace("```", "", 1).strip()
        if text.endswith("```"):
            text = text[:-3].strip()

    try:
        payload = json.loads(text)
        return ExtractedSolutionSet.model_validate(payload), response
    except (json.JSONDecodeError, ValueError):
        return ExtractedSolutionSet(solutions=[]), response


def _merge_text(existing: str | None, incoming: str | None) -> str | None:
    if incoming and existing and existing.lower() == incoming.lower():
        return existing
    return incoming or existing


async def extract_solutions(db: Session, session_id: str, use_gemini: bool = True) -> List[Solution]:
    support_session = db.get(SupportSession, session_id)
    if not support_session:
        raise ValueError("Session not found")

    messages = read_transcript(db, session_id)
    confirmation_message = find_confirmation_message(messages)
    if not confirmation_message:
        return []

    if use_gemini:
        try:
            extracted, usage = await _extract_with_gemini(messages)
        except Exception:
            extracted = _heuristic_extract(messages)
            usage = None
    else:
        extracted = _heuristic_extract(messages)
        usage = None

    if usage:
        db.add(LLMUsage(
            session_id=session_id,
            model=settings.GEMINI_MODEL,
            purpose="extraction",
            input_tokens=usage["input_tokens"],
            output_tokens=usage["output_tokens"],
            total_tokens=usage["total_tokens"],
        ))

    saved = []

    for item in extracted.solutions:
        problem = redact_pii(item.problem).strip()
        fix_steps = sanitise_string_list(item.fix_steps)

        if not problem or not fix_steps:
            continue

        symptoms = sanitise_string_list(item.symptoms)
        failed_attempts = sanitise_string_list(item.failed_attempts)
        incoming_vector = embed_text(problem)

        duplicate = None
        for candidate in db.query(Solution).filter(Solution.status == "VERIFIED").all():
            if not candidate.embedding:
                continue

            try:
                candidate_vector = deserialize_embedding(candidate.embedding)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue

            if candidate_vector.size == incoming_vector.size:
                if cosine_similarity(incoming_vector, candidate_vector) >= 0.78:
                    duplicate = candidate
                    break

        if duplicate:
            solution = duplicate
            solution.symptoms = sanitise_string_list((solution.symptoms or []) + symptoms)
            solution.fix_steps = sanitise_string_list((solution.fix_steps or []) + fix_steps)
            solution.failed_attempts = sanitise_string_list((solution.failed_attempts or []) + failed_attempts)
            solution.device = _merge_text(solution.device, redact_pii(item.device) if item.device else None)
            solution.operating_system = _merge_text(solution.operating_system, redact_pii(item.operating_system) if item.operating_system else None)
            solution.app_version = _merge_text(solution.app_version, redact_pii(item.app_version) if item.app_version else None)
            solution.embedding = serialize_embedding(incoming_vector)
            solution.embedding_model = settings.EMBEDDING_MODEL
        else:
            solution = Solution(
                problem=problem,
                symptoms=symptoms,
                device=redact_pii(item.device) if item.device else None,
                operating_system=redact_pii(item.operating_system) if item.operating_system else None,
                app_version=redact_pii(item.app_version) if item.app_version else None,
                fix_steps=fix_steps,
                failed_attempts=failed_attempts,
                embedding=serialize_embedding(incoming_vector),
                embedding_model=settings.EMBEDDING_MODEL,
                status="VERIFIED",
            )
            db.add(solution)

        db.flush()

        source = db.query(SolutionSource).filter_by(
            solution_id=solution.id,
            session_id=session_id,
        ).first()

        if not source:
            db.add(SolutionSource(
                solution_id=solution.id,
                session_id=session_id,
                confirmation_message_id=confirmation_message.id,
            ))

        saved.append(solution)

    db.commit()

    for solution in saved:
        db.refresh(solution)

    return saved
