"""Chat API routes."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session as DBSession

from app.db.database import get_db
from app.db.models import Message, Session as SupportSession, User
from app.schemas.chat import ChatReplyResponse, MessageCreate, MessageResponse
from app.services.escalation import escalate_to_gemini
from app.services.persona import format_verified_reply
from app.services.retrieval import get_retrieval_decision, retrieve_solutions

router = APIRouter(tags=["chat"])


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

    history_problem = "\n".join(
        message.content
        for message in history
        if message.role == "user"
    )

    candidates = retrieve_solutions(db, history_problem)
    decision = get_retrieval_decision(candidates)

    if decision == "high":
        reply = format_verified_reply(candidates[0])
        source = "knowledge_hub"
    elif decision == "uncertain":
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
    else:
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
    db.refresh(assistant_message)

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
