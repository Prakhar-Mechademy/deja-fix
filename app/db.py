"""SQLite data layer for Déjà Fix.

Every function opens its own short-lived connection, so it is safe to call from
FastAPI request handlers. Nothing is cached in memory, so a server restart
loses nothing.

Set DEJAFIX_DB to override the database path (default: data/dejafix.db).
"""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def db_path() -> Path:
    return Path(os.getenv("DEJAFIX_DB", "data/dejafix.db"))


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def new_id() -> str:
    return uuid.uuid4().hex


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Create all tables if they don't exist. Call once at startup."""
    with get_conn() as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))


def _dict(row: Optional[sqlite3.Row]) -> Optional[dict]:
    return dict(row) if row is not None else None


def _solution(row: Optional[sqlite3.Row]) -> Optional[dict]:
    if row is None:
        return None
    d = dict(row)
    for key in ("environment", "fix_steps", "failed_attempts", "embedding"):
        if d.get(key):
            d[key] = json.loads(d[key])
    return d


# --------------------------------------------------------------------------- #
# Users
# --------------------------------------------------------------------------- #
def get_or_create_user(user_id: str, display_name: Optional[str] = None) -> dict:
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, display_name, created_at) VALUES (?, ?, ?)",
            (user_id, display_name, now()),
        )
        return _dict(conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())


# --------------------------------------------------------------------------- #
# Sessions
# --------------------------------------------------------------------------- #
def create_session(user_id: str, title: Optional[str] = None) -> str:
    sid = new_id()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO sessions (id, user_id, title, started_at) VALUES (?, ?, ?, ?)",
            (sid, user_id, title, now()),
        )
    return sid


def get_session(session_id: str) -> Optional[dict]:
    with get_conn() as conn:
        return _dict(conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone())


def list_sessions(user_id: str, limit: int = 50) -> list[dict]:
    """A user's sessions, newest first. Always scoped to that user."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM sessions WHERE user_id = ? ORDER BY started_at DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def set_session_title(session_id: str, title: str) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE sessions SET title = ? WHERE id = ?", (title, session_id))


def set_served_from(session_id: str, served_from: str) -> None:
    """'escalation' always wins; 'hub' only applies if nothing is set yet."""
    with get_conn() as conn:
        if served_from == "escalation":
            conn.execute("UPDATE sessions SET served_from = 'escalation' WHERE id = ?", (session_id,))
        else:
            conn.execute(
                "UPDATE sessions SET served_from = ? WHERE id = ? AND served_from IS NULL",
                (served_from, session_id),
            )


def close_session(session_id: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE sessions SET status = 'closed', ended_at = ? WHERE id = ?",
            (now(), session_id),
        )


def reopen_session(session_id: str) -> None:
    """Resume a session."""
    with get_conn() as conn:
        conn.execute("UPDATE sessions SET status = 'active', ended_at = NULL WHERE id = ?", (session_id,))


def mark_extracted(session_id: str) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE sessions SET extracted_at = ? WHERE id = ?", (now(), session_id))


def sessions_pending_extraction() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM sessions WHERE status = 'closed' AND extracted_at IS NULL"
        ).fetchall()
        return [dict(r) for r in rows]


# --------------------------------------------------------------------------- #
# Messages
# --------------------------------------------------------------------------- #
def add_message(
    session_id: str,
    user_id: str,
    role: str,
    content: str,
    source: Optional[str] = None,
) -> int:
    """Store a message. Raises if the session doesn't belong to this user."""
    with get_conn() as conn:
        owner = conn.execute("SELECT user_id FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if owner is None:
            raise ValueError(f"Unknown session {session_id}")
        if owner["user_id"] != user_id:
            raise PermissionError("Session does not belong to this user")
        cur = conn.execute(
            "INSERT INTO messages (session_id, user_id, role, content, source, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, user_id, role, content, source, now()),
        )
        return cur.lastrowid


def get_messages(session_id: str, user_id: Optional[str] = None) -> list[dict]:
    """Full session transcript in order. Pass user_id to enforce ownership."""
    with get_conn() as conn:
        if user_id is not None:
            owner = conn.execute("SELECT user_id FROM sessions WHERE id = ?", (session_id,)).fetchone()
            if owner is None or owner["user_id"] != user_id:
                return []
        rows = conn.execute(
            "SELECT id, role, content, source, created_at FROM messages WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
        return [dict(r) for r in rows]


# --------------------------------------------------------------------------- #
# Solutions
# --------------------------------------------------------------------------- #
def add_solution(
    source_session_id: str,
    title: str,
    symptoms: str,
    environment: dict,
    fix_steps: list[str],
    failed_attempts: Optional[list[str]] = None,
    embedding: Optional[list[float]] = None,
    status: str = "verified",
) -> int:
    ts = now()
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO solutions (title, symptoms, environment, fix_steps, failed_attempts, "
            "embedding, status, source_session_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                title,
                symptoms,
                json.dumps(environment),
                json.dumps(fix_steps),
                json.dumps(failed_attempts or []),
                json.dumps(embedding) if embedding is not None else None,
                status,
                source_session_id,
                ts,
                ts,
            ),
        )
        solution_id = cur.lastrowid
        conn.execute(
            "INSERT INTO solution_sessions (solution_id, session_id, relation, created_at) "
            "VALUES (?, ?, 'origin', ?)",
            (solution_id, source_session_id, ts),
        )
        return solution_id


def get_solution(solution_id: int) -> Optional[dict]:
    with get_conn() as conn:
        return _solution(conn.execute("SELECT * FROM solutions WHERE id = ?", (solution_id,)).fetchone())


def get_verified_solutions() -> list[dict]:
    """The ONLY solutions the hub may search or serve. Unverified never leave the DB."""
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM solutions WHERE status = 'verified' ORDER BY id").fetchall()
        return [_solution(r) for r in rows]


def merge_into_solution(
    solution_id: int,
    session_id: str,
    extra_failed_attempts: Optional[list[str]] = None,
) -> None:
    """A duplicate was found: bump the count instead of creating a copy."""
    ts = now()
    with get_conn() as conn:
        row = conn.execute("SELECT failed_attempts FROM solutions WHERE id = ?", (solution_id,)).fetchone()
        if row is None:
            raise ValueError(f"Unknown solution {solution_id}")
        existing = json.loads(row["failed_attempts"] or "[]")
        merged = list(dict.fromkeys(existing + (extra_failed_attempts or [])))
        conn.execute(
            "UPDATE solutions SET confirmed_count = confirmed_count + 1, failed_attempts = ?, "
            "updated_at = ? WHERE id = ?",
            (json.dumps(merged), ts, solution_id),
        )
        conn.execute(
            "INSERT OR IGNORE INTO solution_sessions (solution_id, session_id, relation, created_at) "
            "VALUES (?, ?, 'merge', ?)",
            (solution_id, session_id, ts),
        )


def record_reuse(solution_id: int, session_id: str) -> None:
    """Note that a session was served from this solution (no count change)."""
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO solution_sessions (solution_id, session_id, relation, created_at) "
            "VALUES (?, ?, 'reuse', ?)",
            (solution_id, session_id, now()),
        )


# --------------------------------------------------------------------------- #
# Memory (returning-customer greeting)
# --------------------------------------------------------------------------- #
def get_user_history(user_id: str, limit: int = 3) -> list[dict]:
    """A user's own recent sessions with the (anonymised) solution titles linked to them.

    Scoped strictly by user_id, so one customer's history never reaches another.
    """
    with get_conn() as conn:
        sessions = conn.execute(
            "SELECT id, title, started_at FROM sessions WHERE user_id = ? "
            "ORDER BY started_at DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        history = []
        for s in sessions:
            titles = conn.execute(
                "SELECT DISTINCT sol.title FROM solution_sessions ss "
                "JOIN solutions sol ON sol.id = ss.solution_id "
                "WHERE ss.session_id = ? AND sol.status = 'verified'",
                (s["id"],),
            ).fetchall()
            history.append(
                {
                    "session_id": s["id"],
                    "session_title": s["title"],
                    "started_at": s["started_at"],
                    "solution_titles": [t["title"] for t in titles],
                }
            )
        return history


# --------------------------------------------------------------------------- #
# LLM call log
# --------------------------------------------------------------------------- #
def log_llm_call(
    session_id: Optional[str],
    user_id: Optional[str],
    purpose: str,
    model: Optional[str],
    tokens_in: int = 0,
    tokens_out: int = 0,
    latency_ms: Optional[int] = None,
    status: str = "ok",
    error: Optional[str] = None,
) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO llm_calls (session_id, user_id, purpose, model, tokens_in, tokens_out, "
            "latency_ms, status, error, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (session_id, user_id, purpose, model, tokens_in, tokens_out, latency_ms, status, error, now()),
        )
        return cur.lastrowid


def session_token_usage(session_id: str) -> dict:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS calls, COALESCE(SUM(tokens_in),0) AS tokens_in, "
            "COALESCE(SUM(tokens_out),0) AS tokens_out FROM llm_calls WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        d = dict(row)
        d["total_tokens"] = d["tokens_in"] + d["tokens_out"]
        return d


def token_report() -> list[dict]:
    """Per-session token totals, so hub-served vs escalated cost is easy to compare."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT s.id AS session_id, s.user_id, s.served_from, COUNT(c.id) AS llm_calls, "
            "COALESCE(SUM(c.tokens_in),0) AS tokens_in, COALESCE(SUM(c.tokens_out),0) AS tokens_out, "
            "COALESCE(SUM(c.tokens_in + c.tokens_out),0) AS total_tokens "
            "FROM sessions s LEFT JOIN llm_calls c ON c.session_id = s.id "
            "GROUP BY s.id ORDER BY s.started_at"
        ).fetchall()
        return [dict(r) for r in rows]


# --------------------------------------------------------------------------- #
# Rate limiting (persisted)
# --------------------------------------------------------------------------- #
def record_rate_event(user_id: str, kind: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO rate_events (user_id, kind, created_at) VALUES (?, ?, ?)",
            (user_id, kind, now()),
        )


def count_rate_events(user_id: str, kind: str, window_seconds: int) -> int:
    cutoff = (
        (datetime.now(timezone.utc) - timedelta(seconds=window_seconds))
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM rate_events WHERE user_id = ? AND kind = ? AND created_at >= ?",
            (user_id, kind, cutoff),
        ).fetchone()
        return row["n"]