"""Session API routes."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session as DBSession

from app.db.database import get_db
from app.db.models import Session as SupportSession, User
from app.schemas.chat import SessionCreate, SessionResponse

router = APIRouter(tags=["sessions"])


@router.post("/sessions", response_model=SessionResponse, status_code=status.HTTP_201_CREATED)
def create_session(payload: SessionCreate, db: DBSession = Depends(get_db)):
    user = db.get(User, payload.user_id)

    if not user:
        raise HTTPException(status_code=404, detail="Customer not found")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="Customer is inactive")

    session = SupportSession(user_id=user.id, title=payload.title)
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


@router.get("/sessions/{session_id}", response_model=SessionResponse)
def get_session(session_id: str, user_id: str = Query(..., min_length=1), db: DBSession = Depends(get_db)):
    session = db.get(SupportSession, session_id)

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if session.user_id != user_id:
        raise HTTPException(status_code=404, detail="Session not found")

    return session


@router.get("/users/{user_id}/sessions", response_model=list[SessionResponse])
def list_user_sessions(user_id: str, db: DBSession = Depends(get_db)):
    user = db.get(User, user_id)

    if not user:
        raise HTTPException(status_code=404, detail="Customer not found")

    return (
        db.query(SupportSession)
        .filter(SupportSession.user_id == user_id)
        .order_by(SupportSession.created_at.desc())
        .all()
    )
