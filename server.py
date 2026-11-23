from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import chatbot
import database

database.init_db()
app = FastAPI(title="Déjà Fix")


class SessionReq(BaseModel):
    user_id: str
    session_id: str = None


class ChatReq(BaseModel):
    session_id: str
    user_id: str
    message: str


@app.post("/sessions")
def create_session(req: SessionReq):
    return chatbot.start_session(req.user_id, req.session_id)


@app.get("/sessions")
def list_sessions(user_id: str = None):
    with database.get_connection() as conn:
        rows = (
            conn.execute("SELECT * FROM sessions WHERE user_id = ?", (user_id,)).fetchall()
            if user_id
            else conn.execute("SELECT * FROM sessions").fetchall()
        )
    return {"sessions": [dict(r) for r in rows]}


@app.get("/sessions/{session_id}")
def read_session(session_id: str):
    msgs = database.get_messages(session_id)
    if not msgs:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"session_id": session_id, "messages": msgs, "tokens": database.get_token_totals(session_id)}


@app.post("/chat")
def chat(req: ChatReq):
    try:
        return chatbot.handle_message(req.session_id, req.user_id, req.message)
    except chatbot.RateLimitExceeded as e:
        return JSONResponse(status_code=429, content={"detail": str(e)})