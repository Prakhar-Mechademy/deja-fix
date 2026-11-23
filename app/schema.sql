-- Déjà Fix schema. Safe to run repeatedly (IF NOT EXISTS everywhere).
-- Timestamps are ISO-8601 UTC strings, e.g. 2026-11-23T10:00:00.123Z

CREATE TABLE IF NOT EXISTS users (
    id           TEXT PRIMARY KEY,
    display_name TEXT,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id           TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL REFERENCES users(id),
    title        TEXT,
    status       TEXT NOT NULL DEFAULT 'active'
                 CHECK (status IN ('active', 'closed')),
    -- how the session was ultimately served: from the hub or via LLM escalation
    served_from  TEXT CHECK (served_from IN ('hub', 'escalation') OR served_from IS NULL),
    extracted_at TEXT,              -- set once the extractor has processed this session
    started_at   TEXT NOT NULL,
    ended_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id, started_at DESC);

CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    user_id    TEXT NOT NULL REFERENCES users(id),
    role       TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content    TEXT NOT NULL,
    -- where an assistant message came from: persona (hub answer / clarifier) or raw LLM
    source     TEXT CHECK (source IN ('hub', 'persona', 'persona_clarify', 'llm') OR source IS NULL),
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id);

-- Solutions are GLOBAL and anonymised: deliberately no user_id column.
-- Provenance is kept via source_session_id and solution_sessions.
CREATE TABLE IF NOT EXISTS solutions (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    title             TEXT NOT NULL,
    symptoms          TEXT NOT NULL,
    environment       TEXT NOT NULL DEFAULT '{}',   -- JSON: device, os, app_version
    fix_steps         TEXT NOT NULL DEFAULT '[]',   -- JSON array of strings
    failed_attempts   TEXT NOT NULL DEFAULT '[]',   -- JSON array: what did NOT work
    embedding         TEXT,                         -- JSON array of floats
    status            TEXT NOT NULL DEFAULT 'verified'
                      CHECK (status IN ('verified', 'unverified')),
    confirmed_count   INTEGER NOT NULL DEFAULT 1,   -- bumped when duplicates merge in
    source_session_id TEXT NOT NULL REFERENCES sessions(id),
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_solutions_status ON solutions(status);

-- Every session that created, merged into, or was served by a solution.
CREATE TABLE IF NOT EXISTS solution_sessions (
    solution_id INTEGER NOT NULL REFERENCES solutions(id),
    session_id  TEXT NOT NULL REFERENCES sessions(id),
    relation    TEXT NOT NULL CHECK (relation IN ('origin', 'merge', 'reuse')),
    created_at  TEXT NOT NULL,
    PRIMARY KEY (solution_id, session_id, relation)
);

-- One row per LLM call (chat, escalation, extraction, classification, embedding).
CREATE TABLE IF NOT EXISTS llm_calls (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT REFERENCES sessions(id),
    user_id    TEXT REFERENCES users(id),
    purpose    TEXT NOT NULL,       -- 'escalation' | 'persona' | 'extraction' | 'classify' | 'embedding'
    model      TEXT,
    tokens_in  INTEGER NOT NULL DEFAULT 0,
    tokens_out INTEGER NOT NULL DEFAULT 0,
    latency_ms INTEGER,
    status     TEXT NOT NULL DEFAULT 'ok',   -- 'ok' | 'error' | 'timeout' | 'malformed'
    error      TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_llm_calls_session ON llm_calls(session_id);

-- Persisted so rate limits survive restarts.
CREATE TABLE IF NOT EXISTS rate_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    TEXT NOT NULL REFERENCES users(id),
    kind       TEXT NOT NULL CHECK (kind IN ('message', 'escalation')),
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rate_user_kind ON rate_events(user_id, kind, created_at);