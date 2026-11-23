"""Déjà Fix HTTP API.   Run:  uvicorn app.main:app --reload

Auth is out of scope: the caller supplies user_id, and every session read/write is checked
against that user_id (a session that isn't yours looks like it doesn't exist).
"""
from __future__ import annotations



import asyncio
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import config, db, extractor, persona, rate_limit


def _close_idle() -> list[dict]:
    """Close sessions idle for IDLE_CLOSE_MINUTES and extract them. Returns the extraction results."""
    if config.IDLE_CLOSE_MINUTES == 0:
        return []
    out = []
    for sid in db.idle_active_sessions(config.IDLE_CLOSE_MINUTES):
        db.close_session(sid)
        out.append(extractor.extract_session(sid))
    return out


async def _idle_closer():
    while True:
        await asyncio.sleep(60)
        try:
            await asyncio.to_thread(_close_idle)
        except Exception:  # never let the background loop die
            pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()  # creates tables if missing; existing data is untouched (restart-safe)
    task = asyncio.create_task(_idle_closer())
    try:
        yield
    finally:
        task.cancel()


app = FastAPI(title="Déjà Fix", lifespan=lifespan)

STATIC_DIR = Path(__file__).resolve().parent / "static"


@app.get("/", include_in_schema=False)
def chat_page():
    """The chat web page."""
    return FileResponse(STATIC_DIR / "index.html")


# --------------------------------------------------------------------------- #
# Error handling
# --------------------------------------------------------------------------- #
@app.exception_handler(rate_limit.RateLimitExceeded)
async def _rate_limited(request, exc: rate_limit.RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={
            "error": "rate_limited",
            "kind": exc.kind,
            "message": exc.message,
            "retry_after_seconds": exc.retry_after,
        },
        headers={"Retry-After": str(exc.retry_after)},
    )


@app.exception_handler(Exception)
async def _unexpected(request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"error": "internal_error", "message": "Something went wrong on our side. Please try again."},
    )


# --------------------------------------------------------------------------- #
# Request models
# --------------------------------------------------------------------------- #
class UserIn(BaseModel):
    user_id: str = Field(min_length=1, max_length=64)
    display_name: Optional[str] = Field(default=None, max_length=100)


class SessionIn(BaseModel):
    user_id: str = Field(min_length=1, max_length=64)


class MessageIn(BaseModel):
    user_id: str = Field(min_length=1, max_length=64)
    text: str = Field(min_length=1, max_length=4000)


def _owned_session(session_id: str, user_id: str) -> dict:
    session = db.get_session(session_id)
    if session is None or session["user_id"] != user_id:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #
@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/users")
def create_user(body: UserIn):
    return db.get_or_create_user(body.user_id, body.display_name)


@app.post("/sessions")
def create_session(body: SessionIn):
    """Start a session. Returns a greeting (from this user's own history only) or null."""
    db.get_or_create_user(body.user_id)
    session_id = db.create_session(body.user_id)
    greeting = persona.greeting_for_user(body.user_id, current_session_id=session_id)
    return {"session_id": session_id, "greeting": greeting}


@app.post("/sessions/{session_id}/messages")
def send_message(session_id: str, body: MessageIn):
    session = _owned_session(session_id, body.user_id)
    if session["status"] == "closed":
        raise HTTPException(status_code=409, detail="Session is closed. Resume it first.")
    rate_limit.check(body.user_id, "message")  # 429 before anything is stored or sent
    return persona.handle_message(
        session_id,
        body.user_id,
        body.text,
        before_escalation=lambda: rate_limit.check(body.user_id, "escalation"),
    )


@app.get("/users/{user_id}/sessions")
def list_user_sessions(user_id: str):
    return db.list_sessions(user_id)


@app.get("/sessions/{session_id}")
def read_session(session_id: str, user_id: str = Query(...)):
    """Read a session back in full."""
    session = _owned_session(session_id, user_id)
    return {"session": session, "messages": db.get_messages(session_id, user_id=user_id)}


@app.post("/sessions/{session_id}/resume")
def resume_session(session_id: str, body: SessionIn):
    _owned_session(session_id, body.user_id)
    db.reopen_session(session_id)
    with db.get_conn() as conn:  # a resumed session must be re-extracted when closed again
        conn.execute("UPDATE sessions SET extracted_at = NULL WHERE id = ?", (session_id,))
    return {"session_id": session_id, "status": "active"}


@app.post("/sessions/{session_id}/close")
def close_session(session_id: str, body: SessionIn):
    """Close a session and run the extractor on its stored transcript."""
    _owned_session(session_id, body.user_id)
    db.close_session(session_id)
    return {"status": "closed", "extraction": extractor.extract_session(session_id)}


@app.post("/admin/extract-pending")
def extract_pending():
    """Retry extraction for closed sessions that failed earlier (e.g. provider outage)."""
    return extractor.extract_pending()


@app.post("/admin/close-idle")
def close_idle():
    """Close idle sessions now and extract them (also runs automatically every minute)."""
    return _close_idle()


@app.get("/solutions")
def list_solutions():
    """Verified, anonymised solutions currently in the hub (embeddings omitted)."""
    return [{k: v for k, v in s.items() if k != "embedding"} for s in db.get_verified_solutions()]


@app.get("/token-report")
def token_report():
    """Tokens in/out per session, with how each session was served (hub vs escalation)."""
    return db.token_report()