from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field
import uuid

from database import get_db
from models.session import ChatSession
from models.user import User
from models.message import Message
from services.memory_service import owned_session, latest_user_problem

router = APIRouter(prefix="/sessions", tags=["sessions"])


class SessionCreate(BaseModel):
    user_id: str
    title: str | None = Field(default=None, max_length=200)


@router.post("")
def create_session(payload: SessionCreate, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == payload.user_id).first()

    if not user:
        raise HTTPException(404, "Customer not found.")

    # Retrieve only this customer's previous problem.
    previous_problem = latest_user_problem(db, payload.user_id)

    row = ChatSession(
        id=str(uuid.uuid4()),
        user_id=payload.user_id,
        title=payload.title
    )

    db.add(row)
    db.flush()

    # Save a personalized greeting as the first assistant message.
    if previous_problem:
        greeting = (
            "Welcome back! Last time, you reported: "
            f"{previous_problem[:250]}. How can I help you today?"
        )
    else:
        greeting = "Hello! I'm here to help you troubleshoot your issue step by step. How can I help?"

    db.add(Message(
        session_id=row.id,
        user_id=payload.user_id,
        role="assistant",
        content=greeting,
        source="system"
    ))

    db.commit()
    db.refresh(row)

    return {
        "id": row.id,
        "user_id": row.user_id,
        "title": row.title,
        "status": row.status,
        "created_at": row.created_at,
        "greeting": greeting
    }


@router.get("")
def list_sessions(user_id: str, db: Session = Depends(get_db)):
    if not db.query(User).filter(User.id == user_id).first():
        raise HTTPException(404, "Customer not found.")

    rows = (
        db.query(ChatSession)
        .filter(ChatSession.user_id == user_id)
        .order_by(ChatSession.updated_at.desc())
        .all()
    )

    return [
        {
            "id": x.id,
            "title": x.title,
            "status": x.status,
            "created_at": x.created_at,
            "updated_at": x.updated_at
        }
        for x in rows
    ]


@router.get("/{session_id}/messages")
def read_messages(session_id: str, user_id: str, db: Session = Depends(get_db)):
    session = owned_session(db, user_id, session_id)

    if not session:
        raise HTTPException(404, "Session not found for this customer.")

    rows = (
        db.query(Message)
        .filter(
            Message.session_id == session_id,
            Message.user_id == user_id
        )
        .order_by(Message.id.asc())
        .all()
    )

    return [
        {
            "id": m.id,
            "role": m.role,
            "content": m.content,
            "source": m.source,
            "created_at": m.created_at
        }
        for m in rows
    ]


@router.get("/{session_id}")
def resume_session(session_id: str, user_id: str, db: Session = Depends(get_db)):
    session = owned_session(db, user_id, session_id)

    if not session:
        raise HTTPException(404, "Session not found for this customer.")

    rows = (
        db.query(Message)
        .filter(
            Message.session_id == session_id,
            Message.user_id == user_id
        )
        .order_by(Message.id.asc())
        .all()
    )

    return {
        "id": session.id,
        "user_id": session.user_id,
        "title": session.title,
        "status": session.status,
        "messages": [
            {
                "role": m.role,
                "content": m.content,
                "source": m.source
            }
            for m in rows
        ]
    }