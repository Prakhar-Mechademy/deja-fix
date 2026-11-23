from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from database import get_db
from schemas.chat import ChatMessageRequest, ChatMessageResponse
from services.chat_orchestrator import process_message

router = APIRouter(prefix="/chat", tags=["chat"])

@router.post("/message", response_model=ChatMessageResponse)
def send_message(payload: ChatMessageRequest, db: Session = Depends(get_db)):
    return process_message(db, payload.user_id, payload.session_id, payload.message)
