"""Smoke test for the DB layer. Run from the project root:

    python tests/test_db.py        (or: pytest)
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Use a throwaway database
os.environ["DEJAFIX_DB"] = str(Path(tempfile.mkdtemp()) / "test.db")

from app import db  # noqa: E402


def test_everything():
    db.init_db()
    db.init_db()  # idempotent

    # Empty at start: no seed data
    assert db.get_verified_solutions() == []

    db.get_or_create_user("alice", "Alice")
    db.get_or_create_user("bob", "Bob")

    # Session 1 (Alice, escalated)
    s1 = db.create_session("alice", "Sync not working")
    db.add_message(s1, "alice", "user", "My app won't sync on my iPhone")
    db.add_message(s1, "alice", "assistant", "Try re-linking your account.", source="llm")
    db.log_llm_call(s1, "alice", "escalation", "gemini", tokens_in=900, tokens_out=300)
    db.log_llm_call(s1, "alice", "extraction", "gemini", tokens_in=700, tokens_out=200)
    db.set_served_from(s1, "escalation")
    db.close_session(s1)

    sol = db.add_solution(
        s1, "App sync failure", "Sync stalls", {"device": "iPhone", "os": "iOS 17"},
        ["Sign out", "Sign in"], ["Reinstall"], embedding=[0.1, 0.2, 0.3],
    )
    db.mark_extracted(s1)
    assert db.sessions_pending_extraction() == []

    # Session 2 (Bob, served from hub, cheaper)
    s2 = db.create_session("bob", "Sync issue")
    db.add_message(s2, "bob", "user", "Data isn't updating between devices")
    db.log_llm_call(s2, "bob", "embedding", "gemini-embedding", tokens_in=20)
    db.record_reuse(sol, s2)
    db.set_served_from(s2, "hub")
    db.set_served_from(s2, "escalation")  # escalation overrides
    db.set_served_from(s2, "hub")         # but hub must not override escalation
    assert db.get_session(s2)["served_from"] == "escalation"

    report = {r["session_id"]: r for r in db.token_report()}
    assert report[s1]["total_tokens"] == 2100
    assert report[s2]["total_tokens"] == 20

    # Merge duplicates
    db.merge_into_solution(sol, s2, ["Clear cache"])
    merged = db.get_solution(sol)
    assert merged["confirmed_count"] == 2
    assert merged["failed_attempts"] == ["Reinstall", "Clear cache"]
    assert merged["embedding"] == [0.1, 0.2, 0.3]

    # Unverified solutions are never returned for serving
    db.add_solution(s2, "Unconfirmed", "x", {}, ["y"], status="unverified")
    assert len(db.get_verified_solutions()) == 1

    # Memory isolation
    alice_hist = db.get_user_history("alice")
    assert alice_hist[0]["solution_titles"] == ["App sync failure"]
    assert db.get_messages(s1, user_id="bob") == []
    try:
        db.add_message(s1, "bob", "user", "intruder")
        raise AssertionError("expected PermissionError")
    except PermissionError:
        pass

    # Rate events
    db.record_rate_event("alice", "message")
    db.record_rate_event("alice", "message")
    assert db.count_rate_events("alice", "message", 60) == 2
    assert db.count_rate_events("bob", "message", 60) == 0

    # "Restart": nothing is in memory, a fresh read still sees everything
    assert len(db.get_messages(s1)) == 2
    assert db.get_solution(sol)["source_session_id"] == s1

    print("All DB checks passed.")


if __name__ == "__main__":
    test_everything()