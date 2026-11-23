import sqlite3
import time
import uuid

DB_PATH = "dejafix.db"


def get_connection():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_connection() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (user_id TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS sessions (session_id TEXT PRIMARY KEY, user_id TEXT, created_at REAL);
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT, user_id TEXT, role TEXT, content TEXT, route TEXT
            );
            CREATE TABLE IF NOT EXISTS solutions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT, symptoms TEXT, environment TEXT,
                fix_steps TEXT, what_did_not_work TEXT, confirm_count INTEGER DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS token_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT, step TEXT, tokens_in INTEGER, tokens_out INTEGER
            );
            CREATE TABLE IF NOT EXISTS rate_limits (user_id TEXT, kind TEXT, timestamp REAL);
        """)


def create_session(user_id: str, session_id: str = None) -> str:
    sid = session_id or str(uuid.uuid4())
    with get_connection() as conn:
        conn.execute("INSERT OR IGNORE INTO users VALUES (?)", (user_id,))
        conn.execute("INSERT OR IGNORE INTO sessions VALUES (?, ?, ?)", (sid, user_id, time.time()))
    return sid


def save_message(session_id: str, user_id: str, role: str, content: str, route: str):
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO messages (session_id, user_id, role, content, route) VALUES (?, ?, ?, ?, ?)",
            (session_id, user_id, role, content, route),
        )


def get_messages(session_id: str) -> list:
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM messages WHERE session_id = ? ORDER BY id ASC", (session_id,)).fetchall()
    return [dict(r) for r in rows]


def get_user_previous_fix(user_id: str, current_session_id: str):
    """Memory isolation: fetches ONLY this specific user's past solved problem."""
    with get_connection() as conn:
        row = conn.execute("""
            SELECT sol.symptoms, sol.environment FROM solutions sol
            JOIN sessions s ON sol.session_id = s.session_id
            WHERE s.user_id = ? AND s.session_id != ?
            ORDER BY sol.id DESC LIMIT 1
        """, (user_id, current_session_id)).fetchone()
    return dict(row) if row else None


def get_all_solutions() -> list:
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM solutions ORDER BY confirm_count DESC, id ASC").fetchall()
    return [dict(r) for r in rows]


def save_or_merge_solution(session_id: str, symptoms: str, env: str, steps: str, failed: str, duplicate_id: int = None):
    with get_connection() as conn:
        if duplicate_id:
            conn.execute("UPDATE solutions SET confirm_count = confirm_count + 1 WHERE id = ?", (duplicate_id,))
            return {"action": "merged", "solution_id": duplicate_id}

        cur = conn.execute(
            "INSERT INTO solutions (session_id, symptoms, environment, fix_steps, what_did_not_work) VALUES (?, ?, ?, ?, ?)",
            (session_id, symptoms, env, steps, failed),
        )
        return {"action": "created", "solution_id": cur.lastrowid}


def log_tokens(session_id: str, step: str, tokens_in: int, tokens_out: int):
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO token_logs (session_id, step, tokens_in, tokens_out) VALUES (?, ?, ?, ?)",
            (session_id, step, int(tokens_in), int(tokens_out)),
        )


def get_token_totals(session_id: str) -> dict:
    with get_connection() as conn:
        rows = conn.execute("SELECT tokens_in, tokens_out FROM token_logs WHERE session_id = ?", (session_id,)).fetchall()
    t_in = sum(r["tokens_in"] for r in rows)
    t_out = sum(r["tokens_out"] for r in rows)
    return {"tokens_in": t_in, "tokens_out": t_out, "total": t_in + t_out}


def check_rate_limit(user_id: str, kind: str, max_per_minute: int) -> bool:
    now = time.time()
    with get_connection() as conn:
        conn.execute("DELETE FROM rate_limits WHERE timestamp < ?", (now - 60,))
        count = conn.execute(
            "SELECT COUNT(*) FROM rate_limits WHERE user_id = ? AND kind = ?", (user_id, kind)
        ).fetchone()[0]
        if count >= max_per_minute:
            return False
        conn.execute("INSERT INTO rate_limits VALUES (?, ?, ?)", (user_id, kind, now))
    return True