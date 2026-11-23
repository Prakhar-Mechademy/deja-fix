"""FastAPI application routes for Déjà Fix."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from app import chat, config, db


logger = logging.getLogger(__name__)


# Hackathon assumption: user IDs supplied by the caller are trusted (no authentication).


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT id FROM sessions WHERE status='ended' AND extracted_at IS NULL"
        ).fetchall()
    for row in rows:
        try:
            from app.extractor import run_extraction

            run_extraction(row["id"])
            db.update_session(row["id"], extracted_at=db.utc_now())
        except Exception:
            logger.exception("Startup extraction failed for session %s", row["id"])
    yield


app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def internal_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled application error")
    return JSONResponse(
        status_code=500,
        content={"error": "internal", "detail": "Something went wrong."},
    )


class UserRequest(BaseModel):
    name: str


class SessionRequest(BaseModel):
    user_id: int


class MessageRequest(BaseModel):
    user_id: int
    content: str


class EndRequest(BaseModel):
    user_id: int


@app.post("/users")
def create_user(payload: UserRequest):
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name must not be empty.")
    return db.get_or_create_user(name)


@app.post("/sessions")
def create_session(payload: SessionRequest):
    with db.get_conn() as conn:
        user = conn.execute("SELECT id, name FROM users WHERE id=?", (payload.user_id,)).fetchone()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    session = db.create_session(payload.user_id)
    greeting = chat.greeting_for(dict(user))
    db.add_message(session["id"], payload.user_id, "assistant", greeting)
    return {"session": session, "greeting": greeting}


@app.get("/users/{user_id}/sessions")
def get_user_sessions(user_id: int):
    return db.list_sessions(user_id)


def _owned_session(session_id: int, user_id: int):
    session = db.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found.")
    if session["user_id"] != user_id:
        raise HTTPException(status_code=403, detail="Forbidden.")
    return session


@app.get("/sessions/{session_id}")
def get_session(session_id: int, user_id: int):
    session = _owned_session(session_id, user_id)
    return {"session": session, "messages": db.get_messages(session_id)}


@app.post("/sessions/{session_id}/messages")
def post_message(session_id: int, payload: MessageRequest):
    session = _owned_session(session_id, payload.user_id)
    if session["status"] == "ended":
        raise HTTPException(status_code=409, detail="This session has ended. Start a new one.")

    if db.count_recent_messages(payload.user_id, 60) >= config.MSG_LIMIT_PER_MIN:
        return JSONResponse(
            status_code=429,
            headers={"Retry-After": "60"},
            content={
                "error": "rate_limited",
                "detail": "You're sending messages too quickly. Please wait a moment.",
                "retry_after_seconds": 60,
            },
        )

    content = payload.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="Message must not be empty.")
    if len(content) > 4000:
        raise HTTPException(status_code=400, detail="Message must be 4000 characters or fewer.")

    db.add_message(session_id, payload.user_id, "user", content)
    try:
        result = chat.handle_message(session_id, payload.user_id, content)
    except chat.EscalationLimitError:
        return JSONResponse(
            status_code=429,
            headers={"Retry-After": "3600"},
            content={
                "error": "escalation_limited",
                "detail": "You've reached the limit for advanced troubleshooting requests. Please try again later.",
                "retry_after_seconds": 3600,
            },
        )
    except Exception:
        logger.exception("Chat handling failed for session %s", session_id)
        result = {"reply": "Something went wrong on our side. Please try again.", "mode": session["mode"], "escalated": False}

    db.add_message(session_id, payload.user_id, "assistant", result["reply"])
    return result


@app.post("/sessions/{session_id}/end")
def end_session(session_id: int, payload: EndRequest):
    session = _owned_session(session_id, payload.user_id)
    if session["status"] == "ended":
        if session["extracted_at"]:
            return {"status": "ended", "solution_ids": []}

    now = db.utc_now()
    db.update_session(session_id, status="ended", ended_at=now)

    solution_ids = []
    try:
        from app.extractor import run_extraction

        solution_ids = run_extraction(session_id)
        db.update_session(session_id, extracted_at=db.utc_now())
    except Exception:
        logger.exception("Extraction failed for ended session %s", session_id)

    return {"status": "ended", "solution_ids": solution_ids}


@app.get("/solutions")
def list_solutions():
    output = []
    for solution in db.get_all_solutions():
        item = {
            "id": solution["id"],
            "title": solution["title"],
            "symptoms": solution["symptoms"],
            "environment": solution["environment"],
            "fix_steps": solution["fix_steps"],
            "failed_attempts": solution["failed_attempts"],
            "use_count": solution["use_count"],
        }
        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT session_id FROM solution_sources WHERE solution_id=?",
                (solution["id"],),
            ).fetchall()
        item["source_session_ids"] = list(dict.fromkeys(row["session_id"] for row in rows))
        output.append(item)
    return output


@app.get("/stats/tokens")
def token_stats():
    with db.get_conn() as conn:
        sessions = conn.execute(
            "SELECT id, user_id, mode, served_solution_id FROM sessions"
        ).fetchall()

        rows = []
        hub_totals = []
        escalated_totals = []
        for session in sessions:
            calls = conn.execute(
                """SELECT kind, COALESCE(SUM(tokens_in),0) AS tokens_in,
                          COALESCE(SUM(tokens_out),0) AS tokens_out
                   FROM llm_calls WHERE session_id=? GROUP BY kind""",
                (session["id"],),
            ).fetchall()
            escalation_calls = sum(row["tokens_in"] * 0 + (1 if row["kind"] == "escalation" else 0) for row in calls)
            tokens_in = sum(int(row["tokens_in"] or 0) for row in calls)
            tokens_out = sum(int(row["tokens_out"] or 0) for row in calls)
            total = tokens_in + tokens_out

            escalation_count = conn.execute(
                "SELECT COUNT(*) AS count FROM llm_calls WHERE session_id=? AND kind='escalation'",
                (session["id"],),
            ).fetchone()["count"]
            served_from_hub = bool(
                escalation_count == 0
                and (session["served_solution_id"] is not None or session["mode"] == "hub")
            )
            rows.append({
                "session_id": session["id"],
                "user_id": session["user_id"],
                "mode": session["mode"],
                "served_from_hub": served_from_hub,
                "escalation_calls": escalation_count,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "total_tokens": total,
            })
            if served_from_hub:
                hub_totals.append(total)
            if escalation_count > 0:
                escalated_totals.append(total)

    def average(values):
        return round(sum(values) / len(values), 2) if values else 0.0

    return {
        "sessions": rows,
        "summary": {
            "average_total_tokens_hub": average(hub_totals),
            "average_total_tokens_escalated": average(escalated_totals),
        },
    }


@app.get("/", include_in_schema=False)
@app.get("/dashboard", include_in_schema=False)
def dashboard():
    return FileResponse("app/static/dashboard.html")


@app.get("/api/dashboard/overview", include_in_schema=False)
def dashboard_overview():
    with db.get_conn() as conn:
        rows = conn.execute("SELECT id FROM users ORDER BY id DESC").fetchall()
    return {"users": [row["id"] for row in rows]}


@app.get("/health")
def health():
    return {"status": "ok"}
