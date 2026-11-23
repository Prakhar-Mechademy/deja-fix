"""Terminal chat and database inspector for Déjà Fix.

Use the configured Gemini key in api_key.env or GEMINI_API_KEY in the environment.
The ordinary chat mode uses the persistent database configured by DATABASE_PATH.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Chat with Déjà Fix or inspect its SQLite history.")
    parser.add_argument("--user-id", help="Stable customer identifier; with --inspect, show only this user's records.")
    parser.add_argument("--resume", metavar="SESSION_ID", help="Resume an existing session for this user.")
    parser.add_argument("--inspect", action="store_true", help="Print persisted sessions, messages, and verified fixes; do not start a chat.")
    parser.add_argument("--self-test", action="store_true", help="Run the deterministic three-session acceptance scenario in a temporary database.")
    return parser


ARGS = make_parser().parse_args()
_TEMP_DIR = None
if ARGS.self_test:
    _TEMP_DIR = tempfile.TemporaryDirectory(prefix="deja-fix-test-")
    os.environ["DATABASE_PATH"] = os.path.join(_TEMP_DIR.name, "scenario.sqlite3")
    os.environ.pop("OPENAI_API_KEY", None)
    os.environ.pop("MAIN_API_KEY", None)
    os.environ.pop("GEMINI_API_KEY", None)
    os.environ.pop("gemini_key", None)

import agent  # noqa: E402


def inspect_database() -> None:
    user_id = ARGS.user_id
    where_user = "WHERE user_id = ?" if user_id else ""
    params = (user_id,) if user_id else ()
    with agent.connect_db() as db:
        sessions = db.execute(
            f"SELECT session_id, user_id, created_at, updated_at FROM sessions {where_user} ORDER BY created_at", params
        ).fetchall()
        messages = db.execute(
            f"SELECT message_id, session_id, user_id, role, content, created_at FROM messages {where_user} ORDER BY message_id", params
        ).fetchall()
        solution_filter = "WHERE s.verified = 1 AND ss.session_id IN (SELECT session_id FROM sessions WHERE user_id = ?)" if user_id else "WHERE s.verified = 1"
        solution_params = (user_id,) if user_id else ()
        solutions = db.execute(
            "SELECT DISTINCT s.solution_id, s.original_problem, s.solution, s.symptoms, s.fixes_that_did_not_work, s.environment, "
            f"s.confirmation_count, s.created_at FROM solutions s JOIN solution_sessions ss ON ss.solution_id = s.solution_id "
            f"{solution_filter} ORDER BY s.solution_id", solution_params
        ).fetchall()
    print(f"\n=== Records for user: {user_id} ===" if user_id else "\n=== Records for all users ===")
    print("\n=== Sessions ===")
    if not sessions:
        print("No sessions found.")
    for row in sessions:
        print(f"{row['session_id']} | user={row['user_id']} | created={row['created_at']} | updated={row['updated_at']}")
    print("\n=== Messages (full persisted transcript) ===")
    if not messages:
        print("No messages found.")
    for row in messages:
        print(f"[{row['message_id']}] {row['created_at']} | user={row['user_id']} | session={row['session_id']} | {row['role']}:\n{row['content']}\n")
    print("=== Verified solution fields ===")
    if not solutions:
        print("No confirmed fixes found for this user." if user_id else "No confirmed fixes saved yet.")
    for row in solutions:
        print(f"\nSolution #{row['solution_id']} | confirmations={row['confirmation_count']}")
        for field in ("original_problem", "solution", "symptoms", "fixes_that_did_not_work", "environment"):
            print(f"{field}:\n{row[field] or '(not recorded)'}")


def interactive_chat() -> None:
    provider = agent.get_provider()
    if not provider.has_api_key:
        print("No API key found. Add GEMINI_API_KEY to the environment or set gemini_key in api_key.env.")
        print("For Gemini, the default embedding model is gemini-embedding-001 and the chat model is gemini-2.5-flash.")
        return

    user_id = ARGS.user_id or input("Customer ID (reuse the same ID to see your own history): ").strip()
    if not user_id:
        print("A customer ID is required.")
        return
    if ARGS.resume:
        session_id = ARGS.resume
        if agent.get_session_user(session_id) != user_id:
            print("That session does not belong to this customer ID.")
            return
        messages = agent.transcript(session_id)
        print(f"Resuming session {session_id}")
        for message in messages:
            print(f"{message['role']}: {message['content']}")
    else:
        session = agent.create_session(agent.CreateSessionRequest(user_id=user_id))
        session_id = session["session_id"]
        print(session["greeting"])
        print(f"Session ID: {session_id}")

    print("Type /help for commands; /quit ends this session. Messages and confirmed fixes are stored in SQLite.")
    while True:
        try:
            content = input("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nSession saved.")
            return
        if not content:
            continue
        if content == "/quit":
            print("Session saved.")
            return
        if content == "/help":
            print("/history - show this session's saved messages | /sessions - list your sessions | /solutions - show verified fixes | /quit - exit")
            continue
        if content == "/history":
            for message in agent.transcript(session_id):
                print(f"{message['role']}: {message['content']}")
            continue
        if content == "/sessions":
            with agent.connect_db() as db:
                rows = db.execute(
                    "SELECT session_id, created_at FROM sessions WHERE user_id = ? ORDER BY created_at DESC", (user_id,)
                ).fetchall()
            for row in rows:
                print(f"{row['session_id']} | {row['created_at']}")
            continue
        if content == "/solutions":
            with agent.connect_db() as db:
                rows = db.execute(
                    "SELECT original_problem, solution, symptoms, fixes_that_did_not_work, environment "
                    "FROM solutions WHERE verified = 1 ORDER BY solution_id"
                ).fetchall()
            if not rows:
                print("No confirmed fixes saved yet.")
            for row in rows:
                print("\n--- Verified fix ---")
                for key in ("original_problem", "solution", "symptoms", "fixes_that_did_not_work", "environment"):
                    print(f"{key}: {row[key] or '(not recorded)'}")
            continue
        try:
            result = agent.process_user_message(session_id, content)
        except Exception as exc:
            print(f"Request failed: {exc}")
            continue
        print(f"\nAssistant [{result['source']}]: {result['reply']}")
        if result.get("saved_solutions"):
            print(f"Saved {result['saved_solutions']} verified solution(s).")


def run_self_test() -> None:
    class DemoProvider:
        has_api_key = True
        chat_model = "demo-mock-v1"
        embedding_model = "demo-category-v1"

        def embed(self, text: str):
            lower = text.lower()
            if ("photo" in lower or "picture" in lower) and any(word in lower for word in ("sync", "cloud", "backup", "appear")):
                vector = [1.0, 0.0, 0.0]
            elif any(word in lower for word in ("wifi", "network", "router", "connection")):
                vector = [0.0, 1.0, 0.0]
            else:
                vector = [0.0, 0.0, 1.0]
            return vector, {"input_tokens": max(1, len(text.split())), "output_tokens": 0}

        def chat(self, messages, *, json_mode=False):
            if "Extract every distinct problem" in messages[0]["content"]:
                return json.dumps({"solutions": [{
                    "problem": "Google Photos stops syncing photos from an Android phone to cloud storage",
                    "original_problem": "Photos on an Android phone remain on the device instead of syncing to cloud storage.",
                    "symptoms": "Photos remain on the device and do not appear in the cloud. Reported by Alex Morgan; email alex.morgan@example.test; order #ZX-48291.",
                    "environment": "Google Pixel phone, Android 14, Google Photos app 6.1",
                    "solution": "1. Enable Backup in Google Photos. 2. Check available account storage. 3. Retry over Wi-Fi.",
                    "fixes_that_did_not_work": "Restarting the phone. Contact: Alex Morgan, alex.morgan@example.test, order #ZX-48291.",
                }]}), {"input_tokens": 320, "output_tokens": 90}
            return "Let's check the Photos backup setting and available storage first. What Android and app versions are you using?", {"input_tokens": 110, "output_tokens": 28}

    agent.provider_override = DemoProvider()
    with agent.connect_db() as db:
        assert db.execute("SELECT COUNT(*) FROM solutions").fetchone()[0] == 0

    a1 = agent.create_session(agent.CreateSessionRequest(user_id="customer-a"))
    session_a = a1["session_id"]
    first = agent.process_user_message(session_a, "I'm Alex Morgan (alex.morgan@example.test). Google Photos on Pixel/Android 14 won't sync; pictures stay on my phone. Order #ZX-48291. App 6.1.")
    assert first["source"] == "escalation"
    assert agent.process_user_message(session_a, "That fixed it; photos are syncing now.")["saved_solutions"] == 1
    with agent.connect_db() as db:
        solution = dict(db.execute("SELECT * FROM solutions").fetchone())
    stored = json.dumps(solution).lower()
    assert all(value not in stored for value in ("alex", "morgan", "example.test", "zx-48291")), stored
    assert solution["original_problem"] and solution["solution"] and solution["symptoms"] and solution["fixes_that_did_not_work"]
    assert "Android 14" in solution["environment"]

    b1 = agent.create_session(agent.CreateSessionRequest(user_id="customer-b"))
    session_b = b1["session_id"]
    assert agent.process_user_message(session_b, "Pictures on my phone never show up in the cloud backup.")["source"] == "knowledge_hub_clarification"
    assert agent.process_user_message(session_b, "Yes.")["source"] == "knowledge_hub"
    c1 = agent.create_session(agent.CreateSessionRequest(user_id="customer-c"))
    assert agent.process_user_message(c1["session_id"], "My phone cannot connect to the Wi-Fi network.")["source"] == "escalation"
    d1 = agent.create_session(agent.CreateSessionRequest(user_id="customer-d"))
    agent.process_user_message(d1["session_id"], "My laptop battery drains unusually quickly.")
    with agent.connect_db() as db:
        assert db.execute("SELECT COUNT(*) FROM solutions").fetchone()[0] == 1
        first_cost = db.execute("SELECT SUM(input_tokens + output_tokens) FROM llm_calls WHERE session_id = ?", (session_a,)).fetchone()[0]
        reuse_cost = db.execute("SELECT SUM(input_tokens + output_tokens) FROM llm_calls WHERE session_id = ?", (session_b,)).fetchone()[0]
        assert first_cost > reuse_cost
    assert "Google Photos" in agent.create_session(agent.CreateSessionRequest(user_id="customer-a"))["greeting"]
    print(f"PASS: persistent schema fields, PII removal, paraphrase reuse, negative match, unconfirmed exclusion, memory isolation; tokens session1={first_cost}, session2={reuse_cost}")


def main() -> None:
    if ARGS.inspect:
        inspect_database()
    elif ARGS.self_test:
        run_self_test()
    else:
        interactive_chat()


if __name__ == "__main__":
    main()
