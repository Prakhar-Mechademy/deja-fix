
import sqlite3
from pathlib import Path

# Project root directory
BASE_DIR = Path(__file__).resolve().parent.parent

# Database location
DB_PATH = BASE_DIR / "data" / "dejafix.db"


def get_connection():
    """Create a connection to the SQLite database."""

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # Enable foreign key constraints
    conn.execute("PRAGMA foreign_keys = ON")

    return conn


def init_db():
    """Create all database tables."""

    conn = get_connection()

    try:
        conn.executescript("""

        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            display_name TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY (user_id)
                REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY (session_id)
                REFERENCES sessions(id),

            FOREIGN KEY (user_id)
                REFERENCES users(id),

            CHECK (role IN ('user', 'assistant', 'system'))
        );

        CREATE TABLE IF NOT EXISTS solutions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            source_session_id TEXT NOT NULL,

            symptoms TEXT NOT NULL,
            device TEXT,
            operating_system TEXT,
            app_version TEXT,

            fix_steps TEXT NOT NULL,
            failed_attempts TEXT,

            status TEXT NOT NULL DEFAULT 'unverified',

            confirmation_count INTEGER DEFAULT 0,

            normalized_text TEXT,

            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY (source_session_id)
                REFERENCES sessions(id),

            CHECK (status IN ('verified', 'unverified'))
        );

        CREATE TABLE IF NOT EXISTS llm_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            session_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,

            input_tokens INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0,

            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY (session_id)
                REFERENCES sessions(id)
        );

        CREATE INDEX IF NOT EXISTS idx_sessions_user
        ON sessions(user_id);

        CREATE INDEX IF NOT EXISTS idx_messages_session
        ON messages(session_id);

        CREATE INDEX IF NOT EXISTS idx_solutions_status
        ON solutions(status);

        CREATE INDEX IF NOT EXISTS idx_logs_session
        ON llm_logs(session_id);

        """)

        conn.commit()
        print("Database initialized successfully!")

    finally:
        conn.close()


if __name__ == "__main__":
    init_db()
