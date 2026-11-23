"""SQLite schema and connection helpers. Nothing lives in memory: every restart re-opens the file."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id           TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id                   TEXT PRIMARY KEY,
    user_id              TEXT NOT NULL REFERENCES users(id),
    status               TEXT NOT NULL DEFAULT 'open',        -- open | closed
    state                TEXT NOT NULL DEFAULT 'gathering',   -- gathering | clarifying | fix_offered_hub | escalated | resolved
    current_problem_id   INTEGER,
    pending_solution_id  INTEGER,
    vague_count          INTEGER NOT NULL DEFAULT 0,
    extraction_pending   INTEGER NOT NULL DEFAULT 0,
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id, created_at);

CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL REFERENCES sessions(id),
    user_id     TEXT NOT NULL REFERENCES users(id),
    role        TEXT NOT NULL CHECK (role IN ('user','assistant')),
    source      TEXT NOT NULL,       -- user | hub | llm | persona | greeting | system
    content     TEXT NOT NULL,
    meta        TEXT,                -- JSON (e.g. raw LLM text, match info)
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id);

CREATE TABLE IF NOT EXISTS solutions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    title               TEXT NOT NULL,
    symptoms            TEXT NOT NULL,   -- JSON list (generalized phrasings; grows on merge)
    environment         TEXT NOT NULL,   -- JSON {device, os, app, app_version, also_seen_on[]}
    fix_steps           TEXT NOT NULL,   -- JSON list
    did_not_work        TEXT NOT NULL,   -- JSON list
    alt_fixes           TEXT NOT NULL DEFAULT '[]',  -- JSON list of step lists (other fixes that also worked)
    fault_types         TEXT NOT NULL,   -- JSON list, e.g. ["sync"]
    status              TEXT NOT NULL DEFAULT 'verified' CHECK (status IN ('verified','unverified')),
    confirm_count       INTEGER NOT NULL DEFAULT 1,
    fail_count          INTEGER NOT NULL DEFAULT 0,
    created_session_id  TEXT NOT NULL REFERENCES sessions(id),
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS solution_sources (
    solution_id INTEGER NOT NULL REFERENCES solutions(id),
    session_id  TEXT    NOT NULL REFERENCES sessions(id),
    kind        TEXT    NOT NULL,        -- origin | confirmation
    created_at  TEXT    NOT NULL,
    PRIMARY KEY (solution_id, session_id)
);

CREATE TABLE IF NOT EXISTS session_problems (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id         TEXT NOT NULL REFERENCES sessions(id),
    user_id            TEXT NOT NULL REFERENCES users(id),
    problem_text       TEXT NOT NULL,
    summary            TEXT NOT NULL,    -- short, scrubbed label used in the "welcome back" greeting
    status             TEXT NOT NULL DEFAULT 'open',   -- open | resolved | abandoned
    served_by          TEXT,             -- hub | llm
    solution_id        INTEGER REFERENCES solutions(id),
    start_message_id   INTEGER,
    confirm_message_id INTEGER,
    extracted          INTEGER NOT NULL DEFAULT 0,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_problems_user ON session_problems(user_id, id);

CREATE TABLE IF NOT EXISTS llm_calls (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT,
    purpose     TEXT NOT NULL,           -- escalation | extract | judge
    model       TEXT,
    tokens_in   INTEGER NOT NULL DEFAULT 0,
    tokens_out  INTEGER NOT NULL DEFAULT 0,
    status      TEXT NOT NULL,           -- ok | llm_timeout | llm_unavailable | llm_malformed | llm_error
    latency_ms  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_calls_session ON llm_calls(session_id);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@contextmanager
def connect(path: str):
    """Autocommit connection (use tx() for multi-statement atomic writes)."""
    conn = sqlite3.connect(path, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def tx(conn):
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def init_db(path: str) -> None:
    with connect(path) as conn:
        conn.executescript(SCHEMA)
