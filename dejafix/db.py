"""SQLite storage: users, sessions, messages, problems, solutions and the LLM token log.
Everything is on disk, so nothing is lost when the server restarts."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            TEXT PRIMARY KEY,
    display_name  TEXT,
    created_at    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id                  TEXT PRIMARY KEY,
    user_id             TEXT NOT NULL REFERENCES users(id),
    state               TEXT NOT NULL DEFAULT 'open',   -- open | clarifying | awaiting_confirmation | ended
    current_problem_id  INTEGER,
    extracted           INTEGER NOT NULL DEFAULT 0,
    started_at          REAL NOT NULL,
    last_activity_at    REAL NOT NULL,
    ended_at            REAL
);

CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL REFERENCES sessions(id),
    user_id     TEXT NOT NULL REFERENCES users(id),
    role        TEXT NOT NULL,          -- customer | assistant
    source      TEXT,                   -- customer | persona | hub | llm
    content     TEXT NOT NULL,
    created_at  REAL NOT NULL
);

-- One row per problem raised inside a session (a session can hold several).
CREATE TABLE IF NOT EXISTS problems (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id           TEXT NOT NULL REFERENCES sessions(id),
    user_id              TEXT NOT NULL REFERENCES users(id),
    label                TEXT,
    summary              TEXT,
    description          TEXT,
    environment          TEXT,          -- JSON
    status               TEXT NOT NULL DEFAULT 'open',  -- open | clarifying | awaiting_confirmation | confirmed | failed | unconfirmed
    source               TEXT,          -- hub | llm : where the fix being tried came from
    solution_id          INTEGER REFERENCES solutions(id),
    clarified            INTEGER NOT NULL DEFAULT 0,
    attempts             INTEGER NOT NULL DEFAULT 0,
    confirm_message_id   INTEGER REFERENCES messages(id),
    created_at           REAL NOT NULL,
    updated_at           REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS solutions (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    label              TEXT,
    title              TEXT,
    symptoms           TEXT NOT NULL,
    environment        TEXT,           -- JSON {device, os, app_version}
    fix_steps          TEXT NOT NULL,  -- JSON list
    did_not_work       TEXT,           -- JSON list
    verified           INTEGER NOT NULL DEFAULT 0,  -- only verified rows are ever served
    count              INTEGER NOT NULL DEFAULT 1,  -- how many customers confirmed this fix
    fail_count         INTEGER NOT NULL DEFAULT 0,
    source_session_id  TEXT REFERENCES sessions(id),
    embedding          TEXT,           -- JSON vector
    embed_model        TEXT,
    created_at         REAL NOT NULL,
    updated_at         REAL NOT NULL
);

-- Every session that created, merged into or reused a solution.
CREATE TABLE IF NOT EXISTS solution_sessions (
    solution_id  INTEGER NOT NULL REFERENCES solutions(id),
    session_id   TEXT NOT NULL REFERENCES sessions(id),
    relation     TEXT NOT NULL,         -- created | merged | reused
    created_at   REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS llm_calls (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT,
    user_id     TEXT,
    purpose     TEXT NOT NULL,          -- triage | escalation | extraction | dedupe | embedding
    provider    TEXT,
    model       TEXT,
    tokens_in   INTEGER NOT NULL DEFAULT 0,
    tokens_out  INTEGER NOT NULL DEFAULT 0,
    estimated   INTEGER NOT NULL DEFAULT 0,
    latency_ms  INTEGER,
    ok          INTEGER NOT NULL DEFAULT 1,
    error       TEXT,
    created_at  REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id);
CREATE INDEX IF NOT EXISTS idx_messages_user_time ON messages(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id, started_at);
CREATE INDEX IF NOT EXISTS idx_problems_user ON problems(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_calls_session ON llm_calls(session_id);
"""


def now() -> float:
    return time.time()


def iso(ts):
    if ts is None:
        return None
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(config.db_path(), timeout=15)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA journal_mode=WAL")
    return con


_local = threading.local()


@contextmanager
def connect():
    con = _connect()
    stack = _local.__dict__.setdefault("stack", [])
    stack.append(con)
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        stack.pop()
        con.close()


def init() -> None:
    with connect() as con:
        con.executescript(SCHEMA)


def loads(value, default):
    if value in (None, ""):
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


# ---------- users & sessions ----------

def upsert_user(con, user_id: str, display_name: str | None) -> sqlite3.Row:
    row = con.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if row is None:
        con.execute("INSERT INTO users(id, display_name, created_at) VALUES (?,?,?)",
                    (user_id, display_name, now()))
    elif display_name and display_name != row["display_name"]:
        con.execute("UPDATE users SET display_name=? WHERE id=?", (display_name, user_id))
    return con.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()


def get_user(con, user_id):
    return con.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()


def create_session(con, user_id: str) -> str:
    sid = uuid.uuid4().hex[:12]
    t = now()
    con.execute("INSERT INTO sessions(id, user_id, started_at, last_activity_at) VALUES (?,?,?,?)",
                (sid, user_id, t, t))
    return sid


def get_session(con, session_id):
    return con.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()


def update_session(con, session_id, **fields):
    fields["last_activity_at"] = now()
    cols = ", ".join(f"{k}=?" for k in fields)
    con.execute(f"UPDATE sessions SET {cols} WHERE id=?", (*fields.values(), session_id))


def list_sessions(con, user_id):
    return con.execute(
        """SELECT s.*, (SELECT COUNT(*) FROM messages m WHERE m.session_id=s.id) AS message_count,
                  (SELECT GROUP_CONCAT(label, ', ') FROM problems p WHERE p.session_id=s.id) AS topics
           FROM sessions s WHERE s.user_id=? ORDER BY s.started_at DESC""", (user_id,)).fetchall()


# ---------- messages ----------

def add_message(con, session_id, user_id, role, content, source) -> int:
    cur = con.execute(
        "INSERT INTO messages(session_id, user_id, role, source, content, created_at) VALUES (?,?,?,?,?,?)",
        (session_id, user_id, role, source, content, now()))
    con.execute("UPDATE sessions SET last_activity_at=? WHERE id=?", (now(), session_id))
    return cur.lastrowid


def get_messages(con, session_id):
    return con.execute("SELECT * FROM messages WHERE session_id=? ORDER BY id", (session_id,)).fetchall()


def transcript_text(con, session_id) -> str:
    lines = []
    for m in get_messages(con, session_id):
        who = "Customer" if m["role"] == "customer" else "Support"
        lines.append(f"{who}: {m['content']}")
    return "\n".join(lines)


def count_customer_messages_since(con, user_id, since_ts) -> int:
    return con.execute("SELECT COUNT(*) FROM messages WHERE user_id=? AND role='customer' AND created_at>=?",
                       (user_id, since_ts)).fetchone()[0]


def count_escalations_since(con, user_id, since_ts) -> int:
    return con.execute("SELECT COUNT(*) FROM llm_calls WHERE user_id=? AND purpose='escalation' AND created_at>=?",
                       (user_id, since_ts)).fetchone()[0]


# ---------- problems ----------

def create_problem(con, session_id, user_id, label, summary, description, environment) -> int:
    t = now()
    cur = con.execute(
        """INSERT INTO problems(session_id, user_id, label, summary, description, environment, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (session_id, user_id, label, summary, description, json.dumps(environment or {}), t, t))
    return cur.lastrowid


def get_problem(con, problem_id):
    if not problem_id:
        return None
    return con.execute("SELECT * FROM problems WHERE id=?", (problem_id,)).fetchone()


def update_problem(con, problem_id, **fields):
    fields["updated_at"] = now()
    if "environment" in fields and not isinstance(fields["environment"], str):
        fields["environment"] = json.dumps(fields["environment"] or {})
    cols = ", ".join(f"{k}=?" for k in fields)
    con.execute(f"UPDATE problems SET {cols} WHERE id=?", (*fields.values(), problem_id))


def last_problem_for_user(con, user_id, exclude_session_id):
    """Memory lookup. Always filtered by user_id, so one customer never sees another's history."""
    return con.execute(
        """SELECT * FROM problems WHERE user_id=? AND session_id!=? AND label IS NOT NULL
           ORDER BY created_at DESC LIMIT 1""", (user_id, exclude_session_id)).fetchone()


# ---------- solutions ----------

def verified_solutions(con):
    return con.execute("SELECT * FROM solutions WHERE verified=1").fetchall()


def get_solution(con, solution_id):
    if not solution_id:
        return None
    return con.execute("SELECT * FROM solutions WHERE id=?", (solution_id,)).fetchone()


def insert_solution(con, rec: dict, session_id: str, verified: bool, embedding, embed_model) -> int:
    t = now()
    cur = con.execute(
        """INSERT INTO solutions(label, title, symptoms, environment, fix_steps, did_not_work, verified, count,
                                 source_session_id, embedding, embed_model, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (rec.get("label"), rec.get("title"), rec.get("symptoms", ""), json.dumps(rec.get("environment") or {}),
         json.dumps(rec.get("fix_steps") or []), json.dumps(rec.get("did_not_work") or []),
         1 if verified else 0, 1 if verified else 0, session_id,
         json.dumps(embedding) if embedding else None, embed_model, t, t))
    sid = cur.lastrowid
    link_solution(con, sid, session_id, "created")
    return sid


def link_solution(con, solution_id, session_id, relation):
    con.execute("INSERT INTO solution_sessions(solution_id, session_id, relation, created_at) VALUES (?,?,?,?)",
                (solution_id, session_id, relation, now()))


def bump_solution(con, solution_id, session_id, relation, extra_did_not_work=None):
    sol = get_solution(con, solution_id)
    dnw = loads(sol["did_not_work"], [])
    for item in extra_did_not_work or []:
        if item and item.lower() not in {d.lower() for d in dnw}:
            dnw.append(item)
    con.execute("UPDATE solutions SET count=count+1, did_not_work=?, updated_at=? WHERE id=?",
                (json.dumps(dnw), now(), solution_id))
    link_solution(con, solution_id, session_id, relation)


def mark_solution_failed(con, solution_id):
    con.execute("UPDATE solutions SET fail_count=fail_count+1, updated_at=? WHERE id=?", (now(), solution_id))


def solution_public(row) -> dict:
    return {
        "id": row["id"], "label": row["label"], "title": row["title"], "symptoms": row["symptoms"],
        "environment": loads(row["environment"], {}), "fix_steps": loads(row["fix_steps"], []),
        "did_not_work": loads(row["did_not_work"], []), "verified": bool(row["verified"]),
        "count": row["count"], "fail_count": row["fail_count"], "source_session_id": row["source_session_id"],
        "created_at": iso(row["created_at"]),
    }


# ---------- token log ----------

def log_llm_call(session_id, user_id, purpose, provider, model, tokens_in, tokens_out, estimated,
                 latency_ms, ok, error=None):
    # Reuse the request's open connection (SQLite allows one writer at a time); otherwise open one.
    stack = _local.__dict__.get("stack") or []
    if stack:
        _insert_call(stack[-1], session_id, user_id, purpose, provider, model, tokens_in, tokens_out, estimated,
                     latency_ms, ok, error)
        return
    with connect() as con:
        _insert_call(con, session_id, user_id, purpose, provider, model, tokens_in, tokens_out, estimated,
                     latency_ms, ok, error)


def _insert_call(con, session_id, user_id, purpose, provider, model, tokens_in, tokens_out, estimated,
                 latency_ms, ok, error):
    con.execute(
        """INSERT INTO llm_calls(session_id, user_id, purpose, provider, model, tokens_in, tokens_out, estimated,
                                 latency_ms, ok, error, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (session_id, user_id, purpose, provider, model, int(tokens_in or 0), int(tokens_out or 0),
         1 if estimated else 0, latency_ms, 1 if ok else 0, (error or "")[:500] or None, now()))


def usage_by_session(con, session_id=None):
    sql = """SELECT session_id, COUNT(*) AS calls, SUM(tokens_in) AS tokens_in, SUM(tokens_out) AS tokens_out,
                    SUM(tokens_in + tokens_out) AS total_tokens,
                    SUM(CASE WHEN purpose='escalation' THEN 1 ELSE 0 END) AS escalations
             FROM llm_calls {where} GROUP BY session_id ORDER BY MIN(created_at)"""
    if session_id:
        return con.execute(sql.format(where="WHERE session_id=?"), (session_id,)).fetchall()
    return con.execute(sql.format(where="")).fetchall()


def calls_for_session(con, session_id):
    return con.execute("SELECT * FROM llm_calls WHERE session_id=? ORDER BY id", (session_id,)).fetchall()
