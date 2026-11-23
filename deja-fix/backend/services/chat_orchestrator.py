"""Transactional chat workflow connecting persistence, persona, confirmation and extraction."""
import uuid
from datetime import datetime, timezone
from fastapi import HTTPException
from models.user import User
from models.session import ChatSession
from models.message import Message
from models.solution import Solution
from agents.persona_agent import respond
from agents.extractor_agent import extract_confirmed_solutions
from services.confirmation_service import confirmation_state
from services.memory_service import owned_session
from utils.rate_limiter import enforce_limit

def process_message(db, user_id: str, session_id: str, text: str):
    user = db.query(User).filter(User.id == user_id).first()
    if not user: raise HTTPException(status_code=404, detail="Customer not found.")
    session = owned_session(db, user_id, session_id)
    if not session: raise HTTPException(status_code=404, detail="Session not found for this customer.")
    enforce_limit(db, user_id, "message")
    previous = db.query(Message).filter(Message.session_id == session_id).order_by(Message.id.desc()).first()
    state = confirmation_state(text)
    db.add(Message(session_id=session_id, user_id=user_id, role="user", content=text))
    session.updated_at = datetime.now(timezone.utc)
    db.commit()

    # A positive confirmation verifies only if the immediately preceding assistant turn
    # presented a proposed/retrieved fix. The extractor independently reads stored transcript.
    if state == "positive" and previous and previous.role == "assistant" and previous.source in {"llm", "knowledge_hub"}:
        extracted = extract_confirmed_solutions(db, session_id) if previous.source == "llm" else []
        if previous.source == "knowledge_hub" and previous.solution_id:
            reused = db.query(Solution).filter(Solution.id == previous.solution_id, Solution.verified.is_(True)).first()
            if reused:
                reused.occurrence_count += 1
                reused.last_confirmed_at = datetime.now(timezone.utc)
                db.commit()
        reply = "Thanks for confirming. I've recorded this verified fix for future customers." if extracted else "Thanks for confirming that the fix worked."
        source = "system"
        db.add(Message(session_id=session_id, user_id=user_id, role="assistant", content=reply, source=source))
        db.commit()
        return {"session_id": session_id, "reply": reply, "source": source, "solution_id": extracted[0]["solution_id"] if extracted else None, "requires_confirmation": False}

    if state == "negative" and previous and previous.role == "assistant" and previous.source in {"llm", "knowledge_hub"}:
        reply = "Thanks for letting me know. That fix is not confirmed, so I won't add it to the verified knowledge hub. What happened when you tried it?"
        db.add(Message(session_id=session_id, user_id=user_id, role="assistant", content=reply, source="clarification"))
        db.commit()
        return {"session_id": session_id, "reply": reply, "source": "clarification", "solution_id": None, "requires_confirmation": False}

    transcript_rows = db.query(Message).filter(Message.session_id == session_id).order_by(Message.id.asc()).all()
    transcript = [{"role": m.role, "content": m.content} for m in transcript_rows]
    reply, source, solution_id, needs_confirmation = respond(db, user_id, session_id, transcript, text)
    db.add(Message(session_id=session_id, user_id=user_id, role="assistant", content=reply, source=source, solution_id=solution_id))
    db.commit()
    return {"session_id": session_id, "reply": reply, "source": source, "solution_id": solution_id, "requires_confirmation": needs_confirmation}
