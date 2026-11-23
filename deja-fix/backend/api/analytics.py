from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from database import get_db
from models.session import ChatSession
from models.llm_log import LLMLog

router = APIRouter(prefix="/analytics", tags=["analytics"])
@router.get("/tokens/{session_id}")
def session_tokens(session_id: str, user_id: str, db: Session = Depends(get_db)):
    session = db.query(ChatSession).filter(ChatSession.id == session_id, ChatSession.user_id == user_id).first()
    if not session: raise HTTPException(404, "Session not found for this customer.")
    rows = db.query(LLMLog).filter(LLMLog.session_id == session_id).all()
    return {"session_id": session_id, "calls": [{"purpose": x.purpose, "model": x.model, "input_tokens": x.input_tokens, "output_tokens": x.output_tokens} for x in rows],
            "input_tokens": sum(x.input_tokens for x in rows), "output_tokens": sum(x.output_tokens for x in rows),
            "total_tokens": sum(x.input_tokens + x.output_tokens for x in rows)}
