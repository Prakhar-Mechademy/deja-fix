"""Small SQLite-persistent sliding-window limiter, keyed by user and action."""
from datetime import datetime, timezone, timedelta
from fastapi import HTTPException
from sqlalchemy import text
from config import MESSAGE_LIMIT, MESSAGE_WINDOW_SECONDS, ESCALATION_LIMIT, ESCALATION_WINDOW_SECONDS

def _ensure_table(db):
    db.execute(text("CREATE TABLE IF NOT EXISTS rate_events (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, kind TEXT NOT NULL, created_at TEXT NOT NULL)"))
    db.commit()

def enforce_limit(db, user_id: str, kind: str):
    _ensure_table(db)
    now = datetime.now(timezone.utc)
    if kind == "message": limit, window = MESSAGE_LIMIT, MESSAGE_WINDOW_SECONDS
    else: limit, window = ESCALATION_LIMIT, ESCALATION_WINDOW_SECONDS
    cutoff = (now - timedelta(seconds=window)).isoformat()
    count = db.execute(text("SELECT COUNT(*) FROM rate_events WHERE user_id=:u AND kind=:k AND created_at>=:c"), {"u": user_id, "k": kind, "c": cutoff}).scalar()
    if count >= limit:
        raise HTTPException(status_code=429, detail=f"Rate limit reached for {kind}s. Please wait before trying again.", headers={"Retry-After": str(window)})
    db.execute(text("INSERT INTO rate_events(user_id, kind, created_at) VALUES(:u,:k,:t)"), {"u": user_id, "k": kind, "t": now.isoformat()})
    db.commit()
