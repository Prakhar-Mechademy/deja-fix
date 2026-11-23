"""SQLite schema and database helpers."""

import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import DB_PATH


SESSION_FIELDS = {
    "status", "mode", "served_solution_id", "clarify_count",
    "topic_summary", "started_at", "ended_at", "extracted_at",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY,
            name TEXT UNIQUE,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS sessions(
            id INTEGER PRIMARY KEY,
            user_id INT,
            status TEXT DEFAULT 'active',
            mode TEXT DEFAULT 'new',
            served_solution_id INT NULL,
            clarify_count INT DEFAULT 0,
            topic_summary TEXT NULL,
            started_at TEXT,
            ended_at TEXT NULL,
            extracted_at TEXT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id),
            FOREIGN KEY(served_solution_id) REFERENCES solutions(id)
        );
        CREATE TABLE IF NOT EXISTS messages(
            id INTEGER PRIMARY KEY,
            session_id INT,
            user_id INT,
            role TEXT,
            content TEXT,
            created_at TEXT,
            FOREIGN KEY(session_id) REFERENCES sessions(id),
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS solutions(
            id INTEGER PRIMARY KEY,
            title TEXT,
            symptoms TEXT,
            environment TEXT,
            fix_steps TEXT,
            failed_attempts TEXT,
            embedding BLOB,
            use_count INT DEFAULT 1,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS solution_sources(
            id INTEGER PRIMARY KEY,
            solution_id INT,
            session_id INT,
            evidence_message_id INT,
            FOREIGN KEY(solution_id) REFERENCES solutions(id),
            FOREIGN KEY(session_id) REFERENCES sessions(id),
            FOREIGN KEY(evidence_message_id) REFERENCES messages(id)
        );
        CREATE TABLE IF NOT EXISTS llm_calls(
            id INTEGER PRIMARY KEY,
            session_id INT NULL,
            user_id INT NULL,
            kind TEXT,
            model TEXT,
            tokens_in INT,
            tokens_out INT,
            ok INT,
            created_at TEXT,
            FOREIGN KEY(session_id) REFERENCES sessions(id),
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
        CREATE INDEX IF NOT EXISTS idx_messages_user_created ON messages(user_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_sessions_user_status_ended ON sessions(user_id, status, ended_at);
        CREATE INDEX IF NOT EXISTS idx_llm_calls_session ON llm_calls(session_id);
        CREATE INDEX IF NOT EXISTS idx_llm_calls_user_kind_created ON llm_calls(user_id, kind, created_at);
        """)


def get_or_create_user(name: str) -> dict:
    clean_name = name.strip()
    with get_conn() as conn:
        row = conn.execute("SELECT id, name FROM users WHERE LOWER(name)=LOWER(?)", (clean_name,)).fetchone()
        if row:
            return dict(row)
        now = utc_now()
        cursor = conn.execute("INSERT INTO users(name, created_at) VALUES (?, ?)", (clean_name, now))
        return {"id": cursor.lastrowid, "name": clean_name}


def create_session(user_id: int) -> dict:
    now = utc_now()
    with get_conn() as conn:
        cursor = conn.execute(
            """INSERT INTO sessions(user_id, status, mode, clarify_count, started_at)
               VALUES (?, 'active', 'new', 0, ?)""",
            (user_id, now),
        )
        row = conn.execute("SELECT * FROM sessions WHERE id=?", (cursor.lastrowid,)).fetchone()
        return dict(row)


def get_session(session_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        return dict(row) if row else None


def update_session(session_id: int, **fields) -> None:
    allowed = {key: value for key, value in fields.items() if key in SESSION_FIELDS}
    if not allowed:
        return
    set_sql = ", ".join(f"{key}=?" for key in allowed)
    values = list(allowed.values()) + [session_id]
    with get_conn() as conn:
        conn.execute(f"UPDATE sessions SET {set_sql} WHERE id=?", values)


def add_message(session_id: int, user_id: int, role: str, content: str) -> int:
    with get_conn() as conn:
        cursor = conn.execute(
            "INSERT INTO messages(session_id, user_id, role, content, created_at) VALUES (?, ?, ?, ?, ?)",
            (session_id, user_id, role, content, utc_now()),
        )
        return int(cursor.lastrowid)


def get_messages(session_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT m.* FROM messages m
               JOIN sessions s ON s.id=m.session_id
               WHERE m.session_id=? AND m.user_id=s.user_id
               ORDER BY m.id ASC""",
            (session_id,),
        ).fetchall()
        return [dict(row) for row in rows]


def list_sessions(user_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT s.id, s.status, s.mode, s.topic_summary, s.started_at, s.ended_at,
                      COUNT(m.id) AS message_count
               FROM sessions s
               LEFT JOIN messages m ON m.session_id=s.id
               WHERE s.user_id=?
               GROUP BY s.id
               ORDER BY s.started_at DESC, s.id DESC""",
            (user_id,),
        ).fetchall()
        return [dict(row) for row in rows]


def log_llm_call(session_id, user_id, kind, model, tokens_in, tokens_out, ok) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO llm_calls(session_id, user_id, kind, model, tokens_in, tokens_out, ok, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (session_id, user_id, kind, model, tokens_in, tokens_out, int(ok), utc_now()),
        )


def count_recent_messages(user_id: int, seconds: int) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()
    with get_conn() as conn:
        row = conn.execute(
            """SELECT COUNT(*) AS count FROM messages
               WHERE user_id=? AND role='user' AND created_at>=?""",
            (user_id, cutoff),
        ).fetchone()
        return int(row["count"])


def count_recent_escalations(user_id: int, seconds: int) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()
    with get_conn() as conn:
        row = conn.execute(
            """SELECT COUNT(*) AS count FROM llm_calls
               WHERE user_id=? AND kind='escalation' AND created_at>=?""",
            (user_id, cutoff),
        ).fetchone()
        return int(row["count"])


def _json_load(value, default):
    if value is None:
        return default
    return json.loads(value)


def get_all_solutions() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM solutions").fetchall()
    solutions = []
    for row in rows:
        item = dict(row)
        item["symptoms"] = _json_load(item["symptoms"], [])
        item["environment"] = _json_load(item["environment"], {})
        item["fix_steps"] = _json_load(item["fix_steps"], [])
        item["failed_attempts"] = _json_load(item["failed_attempts"], [])
        raw = item["embedding"]
        item["embedding"] = np.frombuffer(raw, dtype=np.float32) if raw is not None else np.array([], dtype=np.float32)
        solutions.append(item)
    return solutions


def insert_solution(sol: dict, embedding: list[float], session_id: int, evidence_message_id: int) -> int:
    blob = np.asarray(embedding, dtype=np.float32).tobytes()
    with get_conn() as conn:
        cursor = conn.execute(
            """INSERT INTO solutions(title, symptoms, environment, fix_steps, failed_attempts, embedding, use_count, created_at)
               VALUES (?, ?, ?, ?, ?, ?, 1, ?)""",
            (
                sol["title"], json.dumps(sol["symptoms"]), json.dumps(sol["environment"]),
                json.dumps(sol["fix_steps"]), json.dumps(sol["failed_attempts"]), blob, utc_now(),
            ),
        )
        solution_id = int(cursor.lastrowid)
        conn.execute(
            "INSERT INTO solution_sources(solution_id, session_id, evidence_message_id) VALUES (?, ?, ?)",
            (solution_id, session_id, evidence_message_id),
        )
        return solution_id


def merge_into_solution(solution_id: int, new_sol: dict, session_id: int, evidence_message_id: int) -> None:
    with get_conn() as conn:
        row = conn.execute("SELECT failed_attempts FROM solutions WHERE id=?", (solution_id,)).fetchone()
        if not row:
            return
        existing = _json_load(row["failed_attempts"], [])
        seen = {str(item).casefold(): None for item in existing}
        merged = list(existing)
        for item in new_sol["failed_attempts"]:
            key = str(item).casefold()
            if key not in seen:
                seen[key] = None
                merged.append(item)

        source = conn.execute(
            "SELECT 1 FROM solution_sources WHERE solution_id=? AND session_id=? LIMIT 1",
            (solution_id, session_id),
        ).fetchone()
        if not source:
            conn.execute("UPDATE solutions SET failed_attempts=?, use_count=use_count+1 WHERE id=?", (json.dumps(merged), solution_id))
            conn.execute(
                "INSERT INTO solution_sources(solution_id, session_id, evidence_message_id) VALUES (?, ?, ?)",
                (solution_id, session_id, evidence_message_id),
            )
        else:
            conn.execute("UPDATE solutions SET failed_attempts=? WHERE id=?", (json.dumps(merged), solution_id))


def last_ended_session_topic(user_id: int) -> str | None:
    with get_conn() as conn:
        row = conn.execute(
            """SELECT topic_summary FROM sessions
               WHERE user_id=? AND status='ended' AND topic_summary IS NOT NULL
               ORDER BY ended_at DESC LIMIT 1""",
            (user_id,),
        ).fetchone()
        return row["topic_summary"] if row else None


if __name__ == "__main__":
    init_db()
    print("ok")
