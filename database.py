import sqlite3
import json
from datetime import datetime
from typing import Optional, List, Dict, Any

DB_PATH = "dejafix.db"

def get_connection():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id TEXT PRIMARY KEY,
        created_at TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS sessions (
        session_id TEXT PRIMARY KEY,
        user_id TEXT,
        status TEXT DEFAULT 'active',
        created_at TEXT,
        FOREIGN KEY(user_id) REFERENCES users(user_id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT,
        user_id TEXT,
        role TEXT,
        content TEXT,
        timestamp TEXT,
        FOREIGN KEY(session_id) REFERENCES sessions(session_id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS solutions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_session_id TEXT,
        symptoms TEXT,
        environment TEXT,
        fix_steps TEXT,
        what_did_not_work TEXT,
        embedding TEXT,
        confirmed_count INTEGER DEFAULT 1,
        created_at TEXT,
        FOREIGN KEY(source_session_id) REFERENCES sessions(session_id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS token_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT,
        call_type TEXT,
        tokens_in INTEGER,
        tokens_out INTEGER,
        timestamp TEXT
    )
    """)

    conn.commit()
    conn.close()

def ensure_user_and_session(user_id: str, session_id: str):
    conn = get_connection()
    cur = conn.cursor()
    now = datetime.utcnow().isoformat()
    cur.execute("INSERT OR IGNORE INTO users (user_id, created_at) VALUES (?, ?)", (user_id, now))
    cur.execute("INSERT OR IGNORE INTO sessions (session_id, user_id, created_at) VALUES (?, ?, ?)", (session_id, user_id, now))
    conn.commit()
    conn.close()

def save_message(session_id: str, user_id: str, role: str, content: str):
    conn = get_connection()
    cur = conn.cursor()
    now = datetime.utcnow().isoformat()
    cur.execute(
        "INSERT INTO messages (session_id, user_id, role, content, timestamp) VALUES (?, ?, ?, ?, ?)",
        (session_id, user_id, role, content, now)
    )
    conn.commit()
    conn.close()

def get_session_messages(session_id: str) -> List[Dict[str, Any]]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT role, content, timestamp FROM messages WHERE session_id = ? ORDER BY id ASC", (session_id,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows

def get_user_past_sessions(user_id: str, exclude_session_id: str) -> List[Dict[str, Any]]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT s.session_id, m.role, m.content 
        FROM sessions s 
        JOIN messages m ON s.session_id = m.session_id 
        WHERE s.user_id = ? AND s.session_id != ?
        ORDER BY m.id ASC
        """,
        (user_id, exclude_session_id)
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows

def get_all_solutions() -> List[Dict[str, Any]]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM solutions")
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows

def upsert_solution(source_session_id: str, symptoms: str, environment: str, fix_steps: str, what_did_not_work: str, embedding: List[float], duplicate_id: Optional[int] = None):
    conn = get_connection()
    cur = conn.cursor()
    if duplicate_id is not None:
        cur.execute("UPDATE solutions SET confirmed_count = confirmed_count + 1 WHERE id = ?", (duplicate_id,))
    else:
        now = datetime.utcnow().isoformat()
        cur.execute(
            """
            INSERT INTO solutions (source_session_id, symptoms, environment, fix_steps, what_did_not_work, embedding, confirmed_count, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 1, ?)
            """,
            (source_session_id, symptoms, environment, fix_steps, what_did_not_work, json.dumps(embedding), now)
        )
    conn.commit()
    conn.close()

def log_tokens(session_id: str, call_type: str, tokens_in: int, tokens_out: int):
    conn = get_connection()
    cur = conn.cursor()
    now = datetime.utcnow().isoformat()
    cur.execute(
        "INSERT INTO token_logs (session_id, call_type, tokens_in, tokens_out, timestamp) VALUES (?, ?, ?, ?, ?)",
        (session_id, call_type, tokens_in, tokens_out, now)
    )
    conn.commit()
    conn.close()

def get_session_token_summary(session_id: str) -> Dict[str, int]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT COALESCE(SUM(tokens_in), 0) as total_in, COALESCE(SUM(tokens_out), 0) as total_out FROM token_logs WHERE session_id = ?",
        (session_id,)
    )
    row = cur.fetchone()
    conn.close()
    return {"tokens_in": row["total_in"], "tokens_out": row["total_out"], "total": row["total_in"] + row["total_out"]}
