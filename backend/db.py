import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None
_active_db_path: str | None = None


def get_default_db_path() -> str:
    raw = os.environ.get("DATABASE_PATH", "./data/dejafix.db")
    return str(Path(raw).resolve())


def _apply_schema(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          handle TEXT NOT NULL UNIQUE,
          display_name TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS sessions (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL,
          title TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'active',
          memory_summary TEXT,
          last_extracted_message_id INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          updated_at TEXT NOT NULL DEFAULT (datetime('now')),
          FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS messages (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          session_id INTEGER NOT NULL,
          user_id INTEGER NOT NULL,
          role TEXT NOT NULL,
          route TEXT NOT NULL,
          content TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
          FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS solutions (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          source_session_id INTEGER NOT NULL,
          verified INTEGER NOT NULL DEFAULT 1,
          symptoms TEXT NOT NULL,
          device TEXT NOT NULL,
          os TEXT NOT NULL,
          app_version TEXT NOT NULL,
          fix_steps TEXT NOT NULL,
          what_did_not_work TEXT NOT NULL,
          embedding TEXT NOT NULL,
          confirmation_count INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          updated_at TEXT NOT NULL DEFAULT (datetime('now')),
          FOREIGN KEY (source_session_id) REFERENCES sessions(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS solution_confirmations (
          solution_id INTEGER NOT NULL,
          session_id INTEGER NOT NULL,
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          PRIMARY KEY (solution_id, session_id),
          FOREIGN KEY (solution_id) REFERENCES solutions(id) ON DELETE CASCADE,
          FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS token_logs (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          session_id INTEGER NOT NULL,
          user_id INTEGER NOT NULL,
          call_type TEXT NOT NULL,
          model TEXT NOT NULL,
          tokens_in INTEGER NOT NULL,
          tokens_out INTEGER NOT NULL,
          estimated_cost_usd REAL NOT NULL,
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
          FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS rate_limit_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL,
          event_type TEXT NOT NULL,
          created_at_ms INTEGER NOT NULL,
          FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
        CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
        CREATE INDEX IF NOT EXISTS idx_solutions_verified ON solutions(verified);
        CREATE INDEX IF NOT EXISTS idx_solutions_source_session ON solutions(source_session_id);
        CREATE INDEX IF NOT EXISTS idx_token_logs_session ON token_logs(session_id);
        CREATE INDEX IF NOT EXISTS idx_rate_limit_user_type ON rate_limit_events(user_id, event_type, created_at_ms);
        """
    )
    # Backward-compatible migration if an existing local SQLite DB lacked last_extracted_message_id
    cols = {
        r["name"]
        for r in conn.execute("PRAGMA table_info(sessions)").fetchall()
    }
    if "last_extracted_message_id" not in cols:
        conn.execute("ALTER TABLE sessions ADD COLUMN last_extracted_message_id INTEGER NOT NULL DEFAULT 0")
    conn.commit()


def init_db(custom_path: str | None = None) -> sqlite3.Connection:
    global _conn, _active_db_path
    with _lock:
        target = str(Path(custom_path or get_default_db_path()).resolve())
        if _conn is not None and _active_db_path == target:
            return _conn
        if _conn is not None:
            _conn.close()
            _conn = None
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(target, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        _apply_schema(conn)
        _conn = conn
        _active_db_path = target
        return conn


def get_db() -> sqlite3.Connection:
    with _lock:
        if _conn is None:
            return init_db()
        return _conn


def close_db() -> None:
    global _conn, _active_db_path
    with _lock:
        if _conn is not None:
            _conn.close()
            _conn = None
            _active_db_path = None


def reset_db() -> None:
    with _lock:
        conn = get_db()
        conn.executescript(
            """
            DELETE FROM rate_limit_events;
            DELETE FROM token_logs;
            DELETE FROM solution_confirmations;
            DELETE FROM solutions;
            DELETE FROM messages;
            DELETE FROM sessions;
            DELETE FROM users;
            DELETE FROM sqlite_sequence;
            """
        )
        conn.commit()


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def get_or_create_user(handle: str, display_name: str | None = None) -> dict[str, Any]:
    with _lock:
        conn = get_db()
        normalized = "".join(
            ch if ch.isalnum() or ch in ("_", "-") else "_"
            for ch in handle.strip().lower()
        )
        existing = conn.execute(
            "SELECT id, handle, display_name, created_at FROM users WHERE handle = ?",
            (normalized,),
        ).fetchone()

        if existing is not None:
            user = dict(existing)
            if display_name and display_name.strip() and user["display_name"] != display_name.strip():
                conn.execute(
                    "UPDATE users SET display_name = ? WHERE id = ?",
                    (display_name.strip(), user["id"]),
                )
                conn.commit()
                user["display_name"] = display_name.strip()
            return user

        label = (display_name or "").strip() or normalized
        cur = conn.execute(
            "INSERT INTO users (handle, display_name) VALUES (?, ?)",
            (normalized, label),
        )
        conn.commit()
        created = conn.execute(
            "SELECT id, handle, display_name, created_at FROM users WHERE id = ?",
            (cur.lastrowid,),
        ).fetchone()
        return dict(created)


def get_user_by_id(user_id: int) -> dict[str, Any] | None:
    with _lock:
        row = get_db().execute(
            "SELECT id, handle, display_name, created_at FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        return _row_to_dict(row)


def list_users() -> list[dict[str, Any]]:
    with _lock:
        rows = get_db().execute(
            "SELECT id, handle, display_name, created_at FROM users ORDER BY id ASC"
        ).fetchall()
        return [dict(r) for r in rows]


def create_session(
    user_id: int,
    title: str = "New support session",
    memory_summary: str | None = None,
) -> dict[str, Any]:
    with _lock:
        conn = get_db()
        cur = conn.execute(
            "INSERT INTO sessions (user_id, title, status, memory_summary) VALUES (?, ?, ?, ?)",
            (user_id, title, "active", memory_summary),
        )
        conn.commit()
        return get_session(int(cur.lastrowid))  # type: ignore[return-value]


def get_session(session_id: int) -> dict[str, Any] | None:
    with _lock:
        row = get_db().execute(
            """
            SELECT
              s.id, s.user_id, s.title, s.status, s.memory_summary, s.last_extracted_message_id,
              s.created_at, s.updated_at,
              u.handle AS user_handle, u.display_name AS user_display_name,
              (SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id) AS message_count,
              COALESCE((SELECT SUM(t.tokens_in) FROM token_logs t WHERE t.session_id = s.id), 0) AS tokens_in_total,
              COALESCE((SELECT SUM(t.tokens_out) FROM token_logs t WHERE t.session_id = s.id), 0) AS tokens_out_total,
              COALESCE((SELECT SUM(t.estimated_cost_usd) FROM token_logs t WHERE t.session_id = s.id), 0) AS cost_total_usd
            FROM sessions s
            JOIN users u ON u.id = s.user_id
            WHERE s.id = ?
            """,
            (session_id,),
        ).fetchone()
        return _row_to_dict(row)


def list_sessions(user_id: int | None = None) -> list[dict[str, Any]]:
    with _lock:
        conn = get_db()
        base_query = """
            SELECT
              s.id, s.user_id, s.title, s.status, s.memory_summary, s.last_extracted_message_id,
              s.created_at, s.updated_at,
              u.handle AS user_handle, u.display_name AS user_display_name,
              (SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id) AS message_count,
              COALESCE((SELECT SUM(t.tokens_in) FROM token_logs t WHERE t.session_id = s.id), 0) AS tokens_in_total,
              COALESCE((SELECT SUM(t.tokens_out) FROM token_logs t WHERE t.session_id = s.id), 0) AS tokens_out_total,
              COALESCE((SELECT SUM(t.estimated_cost_usd) FROM token_logs t WHERE t.session_id = s.id), 0) AS cost_total_usd
            FROM sessions s
            JOIN users u ON u.id = s.user_id
        """
        if user_id is not None:
            rows = conn.execute(
                f"{base_query} WHERE s.user_id = ? ORDER BY s.updated_at DESC, s.id DESC",
                (user_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                f"{base_query} ORDER BY s.updated_at DESC, s.id DESC"
            ).fetchall()
        return [dict(r) for r in rows]


def update_session(
    session_id: int,
    title: str | None = None,
    status: str | None = None,
    memory_summary: str | None = None,
    update_memory_summary: bool = False,
    last_extracted_message_id: int | None = None,
) -> None:
    with _lock:
        conn = get_db()
        existing = conn.execute(
            "SELECT id, title, status, memory_summary, last_extracted_message_id FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        if existing is None:
            return
        conn.execute(
            """
            UPDATE sessions
            SET title = ?, status = ?, memory_summary = ?, last_extracted_message_id = ?, updated_at = datetime('now')
            WHERE id = ?
            """,
            (
                title if title is not None else existing["title"],
                status if status is not None else existing["status"],
                memory_summary if update_memory_summary else existing["memory_summary"],
                last_extracted_message_id
                if last_extracted_message_id is not None
                else existing["last_extracted_message_id"],
                session_id,
            ),
        )
        conn.commit()


def add_message(
    session_id: int,
    user_id: int,
    role: str,
    route: str,
    content: str,
) -> dict[str, Any]:
    with _lock:
        conn = get_db()
        cur = conn.execute(
            "INSERT INTO messages (session_id, user_id, role, route, content) VALUES (?, ?, ?, ?, ?)",
            (session_id, user_id, role, route, content),
        )
        conn.execute("UPDATE sessions SET updated_at = datetime('now') WHERE id = ?", (session_id,))
        conn.commit()
        row = conn.execute(
            "SELECT id, session_id, user_id, role, route, content, created_at FROM messages WHERE id = ?",
            (cur.lastrowid,),
        ).fetchone()
        return dict(row)


def get_session_messages(session_id: int) -> list[dict[str, Any]]:
    with _lock:
        rows = get_db().execute(
            "SELECT id, session_id, user_id, role, route, content, created_at FROM messages WHERE session_id = ? ORDER BY id ASC",
            (session_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_prior_user_history(
    user_id: int,
    exclude_session_id: int | None = None,
) -> list[dict[str, Any]]:
    """Strictly isolated per-user memory query. Never touches another user's sessions or solutions."""
    with _lock:
        conn = get_db()
        sessions = conn.execute(
            """
            SELECT id, user_id, title, status, memory_summary, created_at, updated_at
            FROM sessions
            WHERE user_id = ? AND (? IS NULL OR id != ?)
            ORDER BY id DESC
            LIMIT 5
            """,
            (user_id, exclude_session_id, exclude_session_id),
        ).fetchall()

        history: list[dict[str, Any]] = []
        for s_row in sessions:
            sess = dict(s_row)
            first_msg = conn.execute(
                "SELECT content FROM messages WHERE session_id = ? AND user_id = ? AND role = 'user' ORDER BY id ASC LIMIT 1",
                (sess["id"], user_id),
            ).fetchone()
            sol = conn.execute(
                "SELECT symptoms FROM solutions WHERE source_session_id = ? AND verified = 1 ORDER BY id DESC LIMIT 1",
                (sess["id"],),
            ).fetchone()
            history.append(
                {
                    "session": sess,
                    "first_user_message": first_msg["content"] if first_msg else None,
                    "linked_solution_symptoms": sol["symptoms"] if sol else None,
                }
            )
        return history


def _hydrate_solution(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["fix_steps"] = json.loads(d.get("fix_steps") or "[]")
    d["what_did_not_work"] = json.loads(d.get("what_did_not_work") or "[]")
    d["embedding"] = json.loads(d.get("embedding") or "[]")
    return d


def list_solutions(include_unverified: bool = False) -> list[dict[str, Any]]:
    with _lock:
        conn = get_db()
        query = (
            "SELECT * FROM solutions ORDER BY confirmation_count DESC, id DESC"
            if include_unverified
            else "SELECT * FROM solutions WHERE verified = 1 ORDER BY confirmation_count DESC, id DESC"
        )
        rows = conn.execute(query).fetchall()
        return [_hydrate_solution(r) for r in rows]


def list_solutions_by_session(session_id: int) -> list[dict[str, Any]]:
    with _lock:
        rows = get_db().execute(
            "SELECT * FROM solutions WHERE source_session_id = ? ORDER BY id ASC",
            (session_id,),
        ).fetchall()
        return [_hydrate_solution(r) for r in rows]


def insert_solution(
    source_session_id: int,
    verified: bool,
    symptoms: str,
    device: str,
    os_name: str,
    app_version: str,
    fix_steps: list[str],
    what_did_not_work: list[str],
    embedding: list[float],
) -> dict[str, Any]:
    with _lock:
        conn = get_db()
        cur = conn.execute(
            """
            INSERT INTO solutions (
              source_session_id, verified, symptoms, device, os, app_version,
              fix_steps, what_did_not_work, embedding, confirmation_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                source_session_id,
                1 if verified else 0,
                symptoms,
                device,
                os_name,
                app_version,
                json.dumps(fix_steps),
                json.dumps(what_did_not_work),
                json.dumps(embedding),
            ),
        )
        new_id = int(cur.lastrowid)
        if verified:
            conn.execute(
                "INSERT OR IGNORE INTO solution_confirmations (solution_id, session_id) VALUES (?, ?)",
                (new_id, source_session_id),
            )
        conn.commit()
        row = conn.execute("SELECT * FROM solutions WHERE id = ?", (new_id,)).fetchone()
        return _hydrate_solution(row)


def merge_solution_duplicate(
    existing_id: int,
    confirming_session_id: int,
    extra_what_did_not_work: list[str],
    replacement_fix_steps: list[str] | None = None,
) -> tuple[dict[str, Any], bool]:
    """
    Idempotent duplicate merge:
    - If `confirming_session_id` already confirmed or originated `existing_id`, do NOT increment `confirmation_count`.
    - Only a distinct session increments `confirmation_count` (enforced by PRIMARY KEY (solution_id, session_id)).
    - Optionally updates `fix_steps` if `replacement_fix_steps` was explicitly chosen by the merge quality gate;
      otherwise preserves the canonical original steps.
    Returns (hydrated_solution, incremented_bool).
    """
    with _lock:
        conn = get_db()
        row = conn.execute("SELECT * FROM solutions WHERE id = ?", (existing_id,)).fetchone()
        existing = _hydrate_solution(row)

        if int(existing["source_session_id"]) == int(confirming_session_id):
            return existing, False

        already = conn.execute(
            "SELECT 1 FROM solution_confirmations WHERE solution_id = ? AND session_id = ?",
            (existing_id, confirming_session_id),
        ).fetchone()
        if already is not None:
            return existing, False

        merged_failed: list[str] = []
        seen = set()
        for item in [*existing["what_did_not_work"], *extra_what_did_not_work]:
            cleaned = str(item).strip()
            if cleaned and cleaned not in seen:
                seen.add(cleaned)
                merged_failed.append(cleaned)

        next_steps = (
            replacement_fix_steps
            if replacement_fix_steps and len(replacement_fix_steps) > 0
            else existing["fix_steps"]
        )

        conn.execute(
            "INSERT INTO solution_confirmations (solution_id, session_id) VALUES (?, ?)",
            (existing_id, confirming_session_id),
        )
        conn.execute(
            """
            UPDATE solutions
            SET confirmation_count = confirmation_count + 1,
                fix_steps = ?,
                what_did_not_work = ?,
                updated_at = datetime('now')
            WHERE id = ?
            """,
            (json.dumps(next_steps), json.dumps(merged_failed), existing_id),
        )
        conn.commit()
        updated = conn.execute("SELECT * FROM solutions WHERE id = ?", (existing_id,)).fetchone()
        return _hydrate_solution(updated), True


def log_token_usage(
    session_id: int,
    user_id: int,
    call_type: str,
    model: str,
    tokens_in: int,
    tokens_out: int,
    estimated_cost_usd: float,
) -> dict[str, Any]:
    with _lock:
        conn = get_db()
        cur = conn.execute(
            """
            INSERT INTO token_logs (session_id, user_id, call_type, model, tokens_in, tokens_out, estimated_cost_usd)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (session_id, user_id, call_type, model, tokens_in, tokens_out, estimated_cost_usd),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM token_logs WHERE id = ?", (cur.lastrowid,)).fetchone()
        return dict(row)


def list_token_logs(session_id: int | None = None) -> list[dict[str, Any]]:
    with _lock:
        conn = get_db()
        if session_id is not None:
            rows = conn.execute(
                "SELECT * FROM token_logs WHERE session_id = ? ORDER BY id DESC",
                (session_id,),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM token_logs ORDER BY id DESC LIMIT 200").fetchall()
        return [dict(r) for r in rows]


def get_session_token_summaries() -> list[dict[str, Any]]:
    with _lock:
        rows = get_db().execute(
            """
            SELECT
              s.id AS session_id,
              s.title AS session_title,
              u.handle AS user_handle,
              u.display_name AS user_display_name,
              s.status AS status,
              EXISTS(SELECT 1 FROM messages m WHERE m.session_id = s.id AND m.route = 'escalation') AS had_escalation,
              EXISTS(SELECT 1 FROM messages m WHERE m.session_id = s.id AND m.route = 'hub') AS had_hub_reuse,
              COUNT(t.id) AS calls_count,
              COALESCE(SUM(t.tokens_in), 0) AS tokens_in,
              COALESCE(SUM(t.tokens_out), 0) AS tokens_out,
              COALESCE(SUM(t.tokens_in + t.tokens_out), 0) AS total_tokens,
              COALESCE(SUM(t.estimated_cost_usd), 0) AS estimated_cost_usd
            FROM sessions s
            JOIN users u ON u.id = s.user_id
            LEFT JOIN token_logs t ON t.session_id = s.id
            GROUP BY s.id
            ORDER BY s.id DESC
            """
        ).fetchall()
        result: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            d["had_escalation"] = bool(d["had_escalation"])
            d["had_hub_reuse"] = bool(d["had_hub_reuse"])
            result.append(d)
        return result


def record_rate_limit_event(user_id: int, event_type: str) -> None:
    now_ms = int(time.time() * 1000)
    with _lock:
        conn = get_db()
        conn.execute(
            "INSERT INTO rate_limit_events (user_id, event_type, created_at_ms) VALUES (?, ?, ?)",
            (user_id, event_type, now_ms),
        )
        conn.execute("DELETE FROM rate_limit_events WHERE created_at_ms < ?", (now_ms - 600_000,))
        conn.commit()


def get_rate_limit_window_stats(user_id: int, event_type: str, window_ms: int) -> dict[str, int | None]:
    since = int(time.time() * 1000) - window_ms
    with _lock:
        row = get_db().execute(
            """
            SELECT COUNT(*) AS cnt, MIN(created_at_ms) AS oldest
            FROM rate_limit_events
            WHERE user_id = ? AND event_type = ? AND created_at_ms >= ?
            """,
            (user_id, event_type, since),
        ).fetchone()
        return {
            "count": int(row["cnt"]) if row else 0,
            "oldest_in_window_ms": int(row["oldest"]) if row and row["oldest"] is not None else None,
        }
