"""Merge duplicate verified fixes instead of creating repeated knowledge entries."""
import json
from datetime import datetime, timezone
from services.retrieval_service import find_best_match

def merge_or_create(db, solution):
    existing, score, method = find_best_match(db, solution.symptoms, session_id=solution.source_session_id)
    if existing and score >= 0.90:
        existing.occurrence_count += 1
        existing.last_confirmed_at = solution.last_confirmed_at or datetime.now(timezone.utc)
        # Preserve newly learned failed attempts without duplicating existing values.
        old = json.loads(existing.failed_attempts or "[]")
        new = json.loads(solution.failed_attempts or "[]")
        existing.failed_attempts = json.dumps(list(dict.fromkeys(old + new)))
        # The candidate is still transient; do not attach it to the session.
        db.commit()
        db.refresh(existing)
        return existing, "merged"
    db.add(solution)
    db.commit()
    db.refresh(solution)
    return solution, "created"
