"""Chat API routes."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session as DBSession

from app.db.database import get_db
from app.db.models import Message, Session as SupportSession, User
from app.schemas.chat import ChatReplyResponse, MessageCreate, MessageResponse

router = APIRouter(tags=["chat"])


@router.post("/chat/message", response_model=ChatReplyResponse, status_code=201)
def create_message(payload: MessageCreate, db: DBSession = Depends(get_db)):
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

    message = Message(
        session_id=session.id,
        user_id=user.id,
        role=payload.role,
        content=payload.content,
    )
    db.add(message)
    db.commit()
    db.refresh(message)

    return ChatReplyResponse(reply=message.content, source="customer_message")


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
        .order_by(Message.created_at.asc())
        .all()
    )
