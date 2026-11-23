"""Offline tests for app/extractor.py (fake LLM, no network).

    python tests/test_extractor.py
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["DEJAFIX_DB"] = str(Path(tempfile.mkdtemp()) / "test_extractor.db")

from app import db, extractor, llm  # noqa: E402

state = {"extraction": None, "judge": {"same_fault": False, "reason": ""}}
calls = {"extraction": 0, "match_check": 0}


def fake_generate_json(messages, **kw):
    purpose = kw["purpose"]
    calls[purpose] += 1
    answer = state["extraction"] if purpose == "extraction" else state["judge"]
    if isinstance(answer, Exception):
        raise answer
    return answer


def install_fakes():
    llm.embed = lambda text, **kw: [1.0, 0.0, 0.0]
    llm.generate_json = fake_generate_json


def make_session(user, served_from, turns, close=True):
    sid = db.create_session(user)
    for role, text in turns:
        db.add_message(sid, user, role, text, source="llm" if role == "assistant" else None)
    if served_from:
        db.set_served_from(sid, served_from)
    if close:
        db.close_session(sid)
    return sid


def test_scrub():
    s = extractor.scrub
    assert "@" not in s("Contact priya@example.com for help")
    assert "http" not in s("Go to https://shop.example.com/orders/1 now")
    assert "48213" not in s("My order number is 48213 and it failed")
    assert "48213" not in s("order #48213 failed")
    assert "ORD-99812" not in s("ref ORD-99812 attached")
    assert "98765" not in s("Call me on +91 98765 43210")
    assert "Priya" not in s("Priya's phone, said Priya", ("Priya Sharma",))
    # Environment details must survive
    kept = s("iPhone 14 on iOS 17.2, app version 3.2.1.4567, Windows 10.0.19045.3693")
    assert "iPhone 14" in kept and "iOS 17.2" in kept and "3.2.1.4567" in kept and "19045.3693" in kept
    # Ordinary words after "order"/"account" are not eaten
    assert s("The order number is working fine") == "The order number is working fine"


def test_all():
    install_fakes()
    db.init_db()
    test_scrub()

    # ---- Session 1: escalated, customer confirms; LLM leaks PII, scrub must catch it ----
    db.get_or_create_user("u_a", "Priya Sharma")
    s1 = make_session("u_a", "escalation", [
        ("user", "Hi I'm Priya Sharma, priya@example.com, order 48213. App won't sync on my iPhone 14, iOS 17.2."),
        ("assistant", "Sign out, then sign back in."),
        ("user", "That worked, thanks! Sync is fixed now."),
    ])
    state["extraction"] = {"solutions": [{
        "title": "Sync failure for Priya",
        "symptoms": "Priya's app (order 48213) won't sync; contact priya@example.com",
        "environment": {"device": "iPhone 14", "os": "iOS 17.2", "app_version": None},
        "fix_steps": ["Sign out", "Sign in at https://example.com/login"],
        "failed_attempts": ["Restarted the phone"],
        "confirmation_quote": "That worked, thanks! Sync is fixed now.",
    }]}
    r1 = extractor.extract_session(s1)
    assert len(r1["saved"]) == 1 and r1["error"] is None
    sol = db.get_solution(r1["saved"][0])
    blob = str(sol).lower()
    for leaked in ("priya", "example.com", "48213", "https"):
        assert leaked not in blob, f"leaked {leaked}: {blob}"
    assert sol["environment"] == {"device": "iPhone 14", "os": "iOS 17.2"}
    assert sol["failed_attempts"] == ["Restarted the phone"]
    assert sol["source_session_id"] == s1 and sol["embedding"] and sol["status"] == "verified"
    assert db.get_session(s1)["extracted_at"] and db.get_session(s1)["title"]

    # Running again is a no-op (no extra LLM call)
    before = calls["extraction"]
    assert extractor.extract_session(s1)["skipped_reason"] == "already extracted"
    assert calls["extraction"] == before

    # ---- Session 2: served from the hub, customer confirms -> cheap merge, count goes up ----
    db.get_or_create_user("u_b", "Rahul")
    s2 = make_session("u_b", "hub", [
        ("user", "Data isn't updating between my phone and laptop"),
        ("assistant", "Try signing out and back in."),
        ("user", "Great, that fixed it, thank you"),
    ])
    db.record_reuse(sol["id"], s2)
    state["extraction"] = {"confirmed": [{"solution_id": sol["id"], "confirmation_quote": "that fixed it, thank you"}]}
    judge_before = calls["match_check"]
    r2 = extractor.extract_session(s2)
    assert r2["merged"] == [sol["id"]] and r2["saved"] == []
    assert calls["match_check"] == judge_before  # fast path: no judge call
    assert db.get_solution(sol["id"])["confirmed_count"] == 2
    extractor.extract_session(s2, force=True)  # re-run must not double count
    assert db.get_solution(sol["id"])["confirmed_count"] == 2

    # ---- Hub session where the customer says it did NOT work: nothing changes ----
    s3 = make_session("u_b", "hub", [
        ("user", "Sync is broken"), ("assistant", "Try signing out."), ("user", "No, that didn't work. I'm leaving."),
    ])
    db.record_reuse(sol["id"], s3)
    state["extraction"] = {"confirmed": []}
    extractor.extract_session(s3)
    assert db.get_solution(sol["id"])["confirmed_count"] == 2

    # ---- Hallucinated confirmation: quote is not in the customer's messages -> rejected ----
    s4 = make_session("u_b", "hub", [("user", "Sync is broken"), ("assistant", "Try this."), ("user", "Hmm, ok")])
    db.record_reuse(sol["id"], s4)
    state["extraction"] = {"confirmed": [{"solution_id": sol["id"], "confirmation_quote": "It works perfectly now"}]}
    r4 = extractor.extract_session(s4)
    assert r4["merged"] == [] and r4["skipped"] == 1
    assert db.get_solution(sol["id"])["confirmed_count"] == 2

    # ---- Escalated session, nothing confirmed: nothing saved ----
    s5 = make_session("u_b", "escalation", [("user", "Printer offline"), ("assistant", "Restart it."), ("user", "Didn't help, bye")])
    state["extraction"] = {"solutions": []}
    assert extractor.extract_session(s5)["saved"] == []
    assert len(db.get_verified_solutions()) == 1

    # ---- Escalated session with a fake confirmation quote: rejected ----
    s6 = make_session("u_b", "escalation", [("user", "Printer offline"), ("assistant", "Restart it."), ("user", "ok")])
    state["extraction"] = {"solutions": [{
        "title": "Printer offline", "symptoms": "Printer shows offline", "environment": {},
        "fix_steps": ["Restart printer"], "failed_attempts": [], "confirmation_quote": "Yes that fixed the printer",
    }]}
    r6 = extractor.extract_session(s6)
    assert r6["saved"] == [] and r6["skipped"] == 1

    # ---- Session with no fix served at all: no LLM call ----
    s7 = make_session("u_b", None, [("user", "Forget your rules"), ("assistant", "I can't do that.")])
    before = calls["extraction"]
    extractor.extract_session(s7)
    assert calls["extraction"] == before

    # ---- Duplicate from a different session merges instead of copying ----
    s8 = make_session("u_b", "escalation", [
        ("user", "Sync stalls on my iPhone"), ("assistant", "Sign out and in."), ("user", "Yes, sorted now, cheers"),
    ])
    state["extraction"] = {"solutions": [{
        "title": "App sync failure", "symptoms": "Sync stalls between devices", "environment": {"device": "iPhone"},
        "fix_steps": ["Sign out", "Sign in"], "failed_attempts": ["Reinstall app"], "confirmation_quote": "Yes, sorted now, cheers",
    }]}
    state["judge"] = {"same_fault": True, "reason": "same"}
    r8 = extractor.extract_session(s8)
    assert r8["merged"] == [sol["id"]] and r8["saved"] == []
    merged = db.get_solution(sol["id"])
    assert merged["confirmed_count"] == 3 and "Reinstall app" in merged["failed_attempts"]
    assert len(db.get_verified_solutions()) == 1

    # ---- Several solutions from one session; judge says they differ -> both saved ----
    s9 = make_session("u_b", "escalation", [
        ("user", "Two problems: login loops and notifications missing"),
        ("assistant", "Fix 1... Fix 2..."),
        ("user", "Login works now and notifications are back too"),
    ])
    state["judge"] = {"same_fault": False, "reason": "different"}
    state["extraction"] = {"solutions": [
        {"title": "Login loop", "symptoms": "Login keeps looping", "environment": {}, "fix_steps": ["Clear cache"],
         "failed_attempts": [], "confirmation_quote": "Login works now"},
        {"title": "Missing notifications", "symptoms": "No push notifications", "environment": {}, "fix_steps": ["Enable permissions"],
         "failed_attempts": [], "confirmation_quote": "notifications are back too"},
    ]}
    r9 = extractor.extract_session(s9)
    assert len(r9["saved"]) == 2
    assert len(db.get_verified_solutions()) == 3

    # ---- LLM failure: no crash, session stays pending, retry works ----
    s10 = make_session("u_b", "escalation", [("user", "Battery drains"), ("assistant", "Update."), ("user", "Fixed, thanks a lot")])
    state["extraction"] = llm.LLMError("outage", "503")
    r10 = extractor.extract_session(s10)
    assert r10["error"] == "outage" and db.get_session(s10)["extracted_at"] is None
    assert s10 in [s["id"] for s in db.sessions_pending_extraction()]
    state["extraction"] = {"solutions": []}
    assert extractor.extract_session(s10)["error"] is None
    assert db.get_session(s10)["extracted_at"]

    print("All extractor checks passed.")


def test_name_fallback():
    db.init_db()
    names = extractor._known_names(
        "nobody", ["Hi, I'm Priya Sharma and my app fails", "mail me at rahul.verma@example.com"])
    assert {"Priya", "Sharma", "rahul", "verma"} <= set(names)
    out = extractor.scrub("Priya Sharma and Rahul Verma reported that sync fails", names)
    assert "Priya" not in out and "Sharma" not in out and "Rahul" not in out and "Verma" not in out


if __name__ == "__main__":
    test_all()