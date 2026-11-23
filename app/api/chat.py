"""Chat API routes."""

import re

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session as DBSession

from app.core.config import settings
from app.db.database import get_db
from app.db.models import LLMUsage, Message, Session as SupportSession, User
from app.schemas.chat import ChatReplyResponse, MessageCreate, MessageResponse
from app.services.escalation import escalate_to_gemini
from app.services.persona import format_verified_reply
from app.services.retrieval import get_retrieval_decision, retrieve_solutions

router = APIRouter(tags=["chat"])

INJECTION_PATTERN = re.compile(
    r"\b(?:forget|ignore|disregard|override|reveal|bypass).{0,40}\b(?:rules|instructions|prompt|system|policy|fix)\b",
    re.I,
)
CLARIFICATION_LIMIT = 2


def count_user_messages(db: DBSession, user_id: str) -> int:
    return (
        db.query(Message)
        .join(SupportSession, Message.session_id == SupportSession.id)
        .filter(SupportSession.user_id == user_id, Message.role == "user")
        .count()
    )


def count_user_escalations(db: DBSession, user_id: str) -> int:
    return (
        db.query(LLMUsage)
        .join(SupportSession, LLMUsage.session_id == SupportSession.id)
        .filter(SupportSession.user_id == user_id, LLMUsage.purpose == "escalation")
        .count()
    )


def count_clarifications(db: DBSession, session_id: str) -> int:
    return (
        db.query(Message)
        .filter(Message.session_id == session_id, Message.role == "assistant")
        .filter(Message.content.contains("confirm"))
        .count()
    )


def environment_matches(candidate: dict, user_text: str) -> bool:
    text = user_text.lower()

    if candidate.get("operating_system"):
        candidate_os = str(candidate["operating_system"]).lower()
        os_tokens = ("windows", "macos", "mac os", "linux", "android", "ios")
        mentioned = [token for token in os_tokens if token in text]

        if mentioned and not any(token in candidate_os for token in mentioned):
            return False

    return True


@router.post("/chat/message", response_model=ChatReplyResponse, status_code=201)
async def create_message(payload: MessageCreate, db: DBSession = Depends(get_db)):
    user = db.get(User, payload.user_id)

    if not user:
        raise HTTPException(status_code=404, detail="Customer not found")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="Customer is inactive")

    session = db.get(SupportSession, payload.session_id)

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if session.user_id != payload.user_id:
        raise HTTPException(status_code=404, detail="Session not found")

    if count_user_messages(db, user.id) >= settings.RATE_LIMIT_MESSAGES:
        raise HTTPException(status_code=429, detail="Message limit reached. Please start a new session.")

    if INJECTION_PATTERN.search(payload.content):
        customer_message = Message(
            session_id=session.id,
            user_id=user.id,
            role=payload.role,
            content=payload.content,
        )
        db.add(customer_message)
        db.commit()

        reply = "I can't change my support rules or reveal internal instructions. I can only help with verified troubleshooting steps."
        source = "clarification"
    else:
        customer_message = Message(
            session_id=session.id,
            user_id=user.id,
            role=payload.role,
            content=payload.content,
        )
        db.add(customer_message)
        db.commit()
        db.refresh(customer_message)

        history = (
            db.query(Message)
            .filter(Message.session_id == session.id)
            .order_by(Message.created_at.asc(), Message.id.asc())
            .all()
        )

        history_problem = "\n".join(message.content for message in history if message.role == "user")
        candidates = retrieve_solutions(db, history_problem)
        decision = get_retrieval_decision(candidates)

        if decision == "high" and candidates and not environment_matches(candidates[0], history_problem):
            decision = "low"

        if decision == "high":
            reply = format_verified_reply(candidates[0])
            source = "knowledge_hub"
        elif decision == "uncertain":
            if count_clarifications(db, session.id) >= CLARIFICATION_LIMIT:
                decision = "low"

        if decision == "uncertain" and count_clarifications(db, session.id) < CLARIFICATION_LIMIT:
            top_problem = candidates[0].get("problem")
            symptoms = candidates[0].get("symptoms") or []

            if symptoms:
                symptom_text = ", ".join(str(item) for item in symptoms[:3])
                reply = f"Before suggesting a fix, can you confirm whether you are experiencing: {symptom_text}?"
            elif top_problem:
                reply = f"Before suggesting a fix, can you confirm whether the problem is: {top_problem}?"
            else:
                reply = "Could you describe what happens and when the issue started?"

            source = "clarification"
        elif decision == "low":
            if count_user_escalations(db, user.id) >= settings.RATE_LIMIT_ESCALATIONS:
                raise HTTPException(status_code=429, detail="Escalation limit reached. Please start a new session.")

            escalation = await escalate_to_gemini(db, session.id, history)
            reply = (
                "I don't have a verified fix for this issue yet. "
                "Here is some troubleshooting guidance to try cautiously:\n"
                f"{escalation['text']}"
            )
            source = "gemini"

    assistant_message = Message(
        session_id=session.id,
        user_id=user.id,
        role="assistant",
        content=reply,
    )
    db.add(assistant_message)
    db.commit()

    return ChatReplyResponse(reply=reply, source=source)


@router.get("/sessions/{session_id}/messages", response_model=list[MessageResponse])
def get_messages(
    session_id: str,
    user_id: str = Query(..., min_length=1),
    db: DBSession = Depends(get_db),
):
    session = db.get(SupportSession, session_id)

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if session.user_id != user_id:
        raise HTTPException(status_code=404, detail="Session not found")

    return (
        db.query(Message)
        .filter(Message.session_id == session_id)
        .order_by(Message.created_at.asc(), Message.id.asc())
        .all()
    )
