"""Customer history is always scoped to the owning user."""
from models.session import ChatSession
from models.message import Message

def recent_user_history(db, user_id: str, limit: int = 10):
    sessions = (db.query(ChatSession).filter(ChatSession.user_id == user_id)
                .order_by(ChatSession.updated_at.desc()).limit(limit).all())
    return sessions

def owned_session(db, user_id: str, session_id: str):
    return db.query(ChatSession).filter(ChatSession.id == session_id, ChatSession.user_id == user_id).first()

def latest_user_problem(db, user_id: str):
    sessions = (db.query(ChatSession).filter(ChatSession.user_id == user_id)
                .order_by(ChatSession.updated_at.desc()).all())
    for session in sessions:
        messages = db.query(Message).filter(Message.session_id == session.id).order_by(Message.id.asc()).all()
        problem = next((m.content for m in messages if m.role == "user"), None)
        if problem:
            return problem
    return None
