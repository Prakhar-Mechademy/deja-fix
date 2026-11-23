"""HTTP API (FastAPI). Run: python -m uvicorn dejafix.app:app --port 8000"""
import logging
import threading
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from . import config, db, extractor, llm, persona

log = logging.getLogger("dejafix")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
app = FastAPI(title="Deja Fix", version="1.0")
STATIC = Path(__file__).parent 


class StartSession(BaseModel):
    user_id: str = Field(min_length=1, max_length=64)
    name: Optional[str] = Field(default=None, max_length=80)


class Message(BaseModel):
    user_id: str = Field(min_length=1, max_length=64)
    text: str = Field(min_length=1, max_length=4000)


def error(status, code, message, headers=None):
    return JSONResponse(status_code=status, content={"error": code, "message": message}, headers=headers)


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    log.exception("unhandled error")
    return error(500, "internal_error", "Something went wrong on our side. Please try again.")


@app.on_event("startup")
def startup():
    db.init()
    log.info("Deja Fix started. LLM provider=%s, database=%s", config.provider(), config.db_path())
    if config.provider() == "mock":
        log.warning("No GEMINI_API_KEY set: running with the offline MOCK model.")
    threading.Thread(target=sweep_idle_sessions, daemon=True).start()


def sweep_idle_sessions():
    """Sessions the customer simply walked away from are closed and extracted after SESSION_IDLE_MINUTES."""
    cutoff = db.now() - config.SESSION_IDLE_MINUTES * 60
    with db.connect() as con:
        idle = [r["id"] for r in con.execute(
            "SELECT id FROM sessions WHERE state!='ended' AND last_activity_at<?", (cutoff,)).fetchall()]
        for sid in idle:
            con.execute("UPDATE sessions SET state='ended', ended_at=? WHERE id=?", (db.now(), sid))
        ids = [r["id"] for r in con.execute(
            "SELECT id FROM sessions WHERE state='ended' AND extracted=0").fetchall()]
    for sid in ids:
        try:
            extractor.run(sid)
        except Exception:
            log.exception("idle extraction failed for %s", sid)


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/health")
def health():
    return {"ok": True, "provider": config.provider()}


@app.post("/sessions")
def start_session(body: StartSession):
    with db.connect() as con:
        db.upsert_user(con, body.user_id, body.name)
        sid = db.create_session(con, body.user_id)
        hello = persona.greeting(con, body.user_id, sid)
        db.add_message(con, sid, body.user_id, "assistant", hello, "persona")
    return {"session_id": sid, "user_id": body.user_id, "greeting": hello}


@app.get("/users/{user_id}/sessions")
def list_sessions(user_id: str):
    with db.connect() as con:
        rows = db.list_sessions(con, user_id)
    return [{"session_id": r["id"], "state": r["state"], "started_at": db.iso(r["started_at"]),
             "ended_at": db.iso(r["ended_at"]), "messages": r["message_count"], "topics": r["topics"]}
            for r in rows]


def _owned_session(con, session_id, user_id):
    s = db.get_session(con, session_id)
    if s is None:
        return None, error(404, "not_found", "Session not found.")
    if user_id is not None and s["user_id"] != user_id:
        return None, error(403, "forbidden", "This session belongs to another user.")
    return s, None


@app.get("/sessions/{session_id}")
def read_session(session_id: str, user_id: Optional[str] = None):
    with db.connect() as con:
        s, err = _owned_session(con, session_id, user_id)
        if err:
            return err
        msgs = db.get_messages(con, session_id)
    return {"session_id": s["id"], "user_id": s["user_id"], "state": s["state"],
            "started_at": db.iso(s["started_at"]), "ended_at": db.iso(s["ended_at"]),
            "messages": [{"id": m["id"], "role": m["role"], "source": m["source"], "content": m["content"],
                          "at": db.iso(m["created_at"])} for m in msgs]}


@app.post("/sessions/{session_id}/messages")
def send_message(session_id: str, body: Message):
    with db.connect() as con:
        s, err = _owned_session(con, session_id, body.user_id)
        if err:
            return err
        if s["state"] == "ended":
            return error(409, "session_ended", "This session has ended. Please start a new one.")
        try:
            persona.check_message_limit(con, body.user_id)
        except persona.RateLimited as e:
            return error(429, "rate_limited", e.message, {"Retry-After": str(e.retry_after)})
        try:
            out = persona.handle(con, s, body.user_id, body.text)
        except persona.RateLimited as e:
            db.add_message(con, session_id, body.user_id, "assistant", e.message, "persona")
            con.commit()
            return error(429, "rate_limited", e.message, {"Retry-After": str(e.retry_after)})
    return {"session_id": session_id, **out}


@app.post("/sessions/{session_id}/end")
def end_session(session_id: str, user_id: Optional[str] = None):
    with db.connect() as con:
        s, err = _owned_session(con, session_id, user_id)
        if err:
            return err
        if s["state"] != "ended":
            con.execute("UPDATE sessions SET state='ended', ended_at=? WHERE id=?", (db.now(), session_id))
    try:
        result = extractor.run(session_id)
    except llm.LLMError as e:
        log.warning("extraction failed for %s: %s", session_id, e)
        return {"session_id": session_id, "ended": True, "extraction": "failed, will retry later",
                "message": "Session ended. Saving the solution failed for now and will be retried."}
    return {"session_id": session_id, "ended": True, "extraction": result}


@app.get("/solutions")
def solutions(include_unverified: bool = False):
    with db.connect() as con:
        sql = "SELECT * FROM solutions" + ("" if include_unverified else " WHERE verified=1") + " ORDER BY id"
        return [db.solution_public(r) for r in con.execute(sql).fetchall()]


@app.get("/usage")
def usage(session_id: Optional[str] = None):
    with db.connect() as con:
        rows = db.usage_by_session(con, session_id)
        out = [dict(r) for r in rows]
        if session_id:
            out = {"totals": out[0] if out else None,
                   "calls": [dict(c) for c in db.calls_for_session(con, session_id)]}
    return out
