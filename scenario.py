"""Terminal chat and database inspector for Déjà Fix.

Use the configured Gemini key in api_key.env or GEMINI_API_KEY in the environment.
The ordinary chat mode uses the persistent database configured by DATABASE_PATH.
"""

from __future__ import annotations

import argparse
import atexit
import json
import os
import tempfile


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Chat with Déjà Fix or inspect its SQLite history.")
    parser.add_argument("--user-id", help="Stable customer identifier; with --inspect, show only this user's records.")
    parser.add_argument("--toaster-model", help="Select an already-known toaster model ID without the model-identification dialogue.")
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

_active_session_id: str | None = None
_reported_sessions: set[str] = set()


def report_active_session_usage() -> None:
    if _active_session_id and _active_session_id not in _reported_sessions:
        print_token_summary(_active_session_id)
        _reported_sessions.add(_active_session_id)


atexit.register(report_active_session_usage)


def inspect_database() -> None:
    user_id = ARGS.user_id
    where_user = "WHERE user_id = ?" if user_id else ""
    params = (user_id,) if user_id else ()
    with agent.connect_db() as db:
        sessions = db.execute(
            f"SELECT session_id, user_id, toaster_model_id, created_at, updated_at FROM sessions {where_user} ORDER BY created_at", params
        ).fetchall()
        messages = db.execute(
            f"SELECT message_id, session_id, user_id, role, content, created_at FROM messages {where_user} ORDER BY message_id", params
        ).fetchall()
        solution_filter = "WHERE s.verified = 1 AND ss.session_id IN (SELECT session_id FROM sessions WHERE user_id = ?)" if user_id else "WHERE s.verified = 1"
        solution_params = (user_id,) if user_id else ()
        solutions = db.execute(
            "SELECT DISTINCT s.solution_id, s.toaster_model_id, s.original_problem, s.solution, s.symptoms, s.fixes_that_did_not_work, s.environment, "
            f"s.confirmation_count, s.created_at FROM solutions s JOIN solution_sessions ss ON ss.solution_id = s.solution_id "
            f"{solution_filter} ORDER BY s.solution_id", solution_params
        ).fetchall()
    print(f"\n=== Records for user: {user_id} ===" if user_id else "\n=== Records for all users ===")
    print("\n=== Sessions ===")
    if not sessions:
        print("No sessions found.")
    for row in sessions:
        print(f"{row['session_id']} | user={row['user_id']} | model={row['toaster_model_id'] or 'not selected'} | created={row['created_at']} | updated={row['updated_at']}")
    print("\n=== Messages (full persisted transcript) ===")
    if not messages:
        print("No messages found.")
    for row in messages:
        print(f"[{row['message_id']}] {row['created_at']} | user={row['user_id']} | session={row['session_id']} | {row['role']}:\n{row['content']}\n")
    print("=== Verified solution fields ===")
    if not solutions:
        print("No confirmed fixes found for this user." if user_id else "No confirmed fixes saved yet.")
    for row in solutions:
        print(f"\nSolution #{row['solution_id']} | model={row['toaster_model_id']} | confirmations={row['confirmation_count']}")
        for field in ("original_problem", "solution", "symptoms", "fixes_that_did_not_work", "environment"):
            print(f"{field}:\n{row[field] or '(not recorded)'}")


def print_token_summary(session_id: str) -> None:
    usage = agent.session_token_usage(session_id)
    print("\n=== Session token usage ===")
    print(f"Session: {session_id}")
    print(f"Input tokens: {usage['input_tokens']}")
    print(f"Output tokens: {usage['output_tokens']}")
    print(f"Total tokens: {usage['input_tokens'] + usage['output_tokens']}")
    if usage["calls"]:
        print("Calls:")
        for call in usage["calls"]:
            print(
                f"  {call['call_type']} / {call['model']}: input={call['input_tokens']}, "
                f"output={call['output_tokens']}, outcome={call['outcome']}"
            )
    else:
        print("No LLM calls recorded for this session.")
    _reported_sessions.add(session_id)


def choose_existing_session(user_id: str) -> str | None:
    with agent.connect_db() as db:
        rows = db.execute(
            "SELECT session_id, toaster_model_id, created_at, updated_at FROM sessions "
            "WHERE user_id = ? ORDER BY updated_at DESC LIMIT 10",
            (user_id,),
        ).fetchall()
    if not rows:
        return None
    print("\nPrevious sessions for this user:")
    for index, row in enumerate(rows, start=1):
        model_name = agent.TOASTER_MODELS.get(row["toaster_model_id"], "model not selected")
        print(f"  {index}. {row['session_id']} | {model_name} | last active {row['updated_at']}")
    print("  N. Start a new session")
    while True:
        selection = input("Resume which session? [N]: ").strip().lower()
        if not selection or selection in {"n", "new"}:
            return None
        if selection.isdigit() and 1 <= int(selection) <= len(rows):
            return rows[int(selection) - 1]["session_id"]
        print("Enter a listed number or N for a new session.")


def interactive_chat() -> None:
    global _active_session_id
    provider = agent.get_provider()
    if not provider.has_api_key:
        print("No API key found. Add GEMINI_API_KEY to the environment or set gemini_key in api_key.env.")
        print("For Gemini, the default embedding model is gemini-embedding-001 and the chat model is gemini-2.5-flash.")
        return

    user_id = ARGS.user_id or input("Customer ID (reuse the same ID to see your own history): ").strip()
    if not user_id:
        print("A customer ID is required.")
        return
    session_id = ARGS.resume or choose_existing_session(user_id)
    if session_id:
        if agent.get_session_user(session_id) != user_id:
            print("That session does not belong to this customer ID.")
            return
        messages = agent.transcript(session_id)
        with agent.connect_db() as db:
            row = db.execute("SELECT toaster_model_id FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
        toaster_model_id = row["toaster_model_id"]
        if row["toaster_model_id"]:
            print(f"Toaster model: {agent.TOASTER_MODELS[row['toaster_model_id']]}")
        else:
            print("This session needs a toaster model. Type its name when prompted; the assistant will check and confirm it.")
        print(f"Resuming session {session_id}")
        for message in messages:
            print(f"{message['role']}: {message['content']}")
    else:
        toaster_model_id = ARGS.toaster_model
        if toaster_model_id and toaster_model_id not in agent.TOASTER_MODELS:
            print("Unknown model ID. Use one from --help or start chat and enter the model name for verification.")
            return
        session = agent.create_session(
            agent.CreateSessionRequest(user_id=user_id, toaster_model_id=toaster_model_id)
        )
        session_id = session["session_id"]
        print(session["greeting"])
        print(f"Session ID: {session_id}")

    _active_session_id = session_id

    print("Type /help for commands; /quit ends this session. Messages and confirmed fixes are stored in SQLite.")
    while True:
        try:
            content = input("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nSession saved.")
            print_token_summary(session_id)
            return
        if not content:
            continue
        if content == "/quit":
            print("Session saved.")
            print_token_summary(session_id)
            return
        if content == "/help":
            print("/history - show this session's saved messages | /sessions - list your sessions | /solutions - fixes for this model | /model - switch toaster model | /quit - exit")
            continue
        if content == "/model":
            print_token_summary(session_id)
            new_session = agent.create_session(
                agent.CreateSessionRequest(user_id=user_id)
            )
            session_id = new_session["session_id"]
            _active_session_id = session_id
            toaster_model_id = None
            print("Started a new session. Enter the toaster model name when prompted.")
            print(new_session["greeting"])
            print(f"Session ID: {session_id}")
            continue
        if content == "/history":
            for message in agent.transcript(session_id):
                print(f"{message['role']}: {message['content']}")
            continue
        if content == "/sessions":
            with agent.connect_db() as db:
                rows = db.execute(
                    "SELECT session_id, toaster_model_id, created_at FROM sessions WHERE user_id = ? ORDER BY created_at DESC", (user_id,)
                ).fetchall()
            for row in rows:
                print(f"{row['session_id']} | model={row['toaster_model_id'] or 'not selected'} | {row['created_at']}")
            continue
        if content == "/solutions":
            with agent.connect_db() as db:
                rows = db.execute(
                    "SELECT toaster_model_id, original_problem, solution, symptoms, fixes_that_did_not_work, environment "
                    "FROM solutions WHERE verified = 1 AND toaster_model_id = ? ORDER BY solution_id", (toaster_model_id,)
                ).fetchall()
            if not rows:
                print("No confirmed fixes saved yet.")
            for row in rows:
                print("\n--- Verified fix ---")
                print(f"toaster_model_id: {row['toaster_model_id']}")
                for key in ("original_problem", "solution", "symptoms", "fixes_that_did_not_work", "environment"):
                    print(f"{key}: {row[key] or '(not recorded)'}")
            continue
        try:
            result = agent.process_user_message(session_id, content)
        except Exception as exc:
            print(f"Request failed: {exc}")
            continue
        print(f"\nAssistant [{result['source']}]: {result['reply']}")
        if result.get("toaster_model_id"):
            toaster_model_id = result["toaster_model_id"]
        if result.get("saved_solutions"):
            print(f"Saved {result['saved_solutions']} verified solution(s).")


def run_self_test() -> None:
    class DemoProvider:
        has_api_key = True
        chat_model = "demo-mock-v1"
        embedding_model = "demo-category-v1"

        def embed(self, text: str):
            lower = text.lower()
            if any(word in lower for word in ("lever", "stay down", "pops back", "won't stay")):
                vector = [1.0, 0.0, 0.0]
            elif any(word in lower for word in ("spark", "smoke", "burnt smell", "sparking")):
                vector = [0.0, 1.0, 0.0]
            else:
                vector = [0.0, 0.0, 1.0]
            return vector, {"input_tokens": max(1, len(text.split())), "output_tokens": 0}

        def chat(self, messages, *, json_mode=False):
            if "Identify the toaster model" in messages[0]["content"]:
                entered = messages[-1]["content"].lower()
                if "brandx heatmaster 900" in entered:
                    return json.dumps({"is_toaster_model": True, "known_model_id": None, "proposed_name": "BrandX HeatMaster 900"}), {"input_tokens": 30, "output_tokens": 15}
                if "toaster model 1" in entered:
                    return json.dumps({"is_toaster_model": True, "known_model_id": "toaster-model-1", "proposed_name": "Toaster Model 1"}), {"input_tokens": 30, "output_tokens": 15}
                return json.dumps({"is_toaster_model": False, "known_model_id": None, "proposed_name": None}), {"input_tokens": 30, "output_tokens": 10}
            if "Extract every distinct problem" in messages[0]["content"]:
                return json.dumps({"solutions": [{
                    "problem": "Toaster lever will not stay down",
                    "original_problem": "My toaster lever pops back up before the bread toasts.",
                    "symptoms": "The carriage lever will not stay down. Reported by Alex Morgan; email alex.morgan@example.test; order #ZX-48291.",
                    "environment": "Toaster Model 1",
                    "solution": "Unplug the cool toaster, remove crumbs from the crumb tray, then plug it back in and test once.",
                    "fixes_that_did_not_work": "Restarting the toaster. Contact: Alex Morgan, alex.morgan@example.test, order #ZX-48291.",
                }]}), {"input_tokens": 320, "output_tokens": 90}
            return "Unplug the toaster and let it cool. Then check whether the crumb tray is full. What model is printed on the label?", {"input_tokens": 110, "output_tokens": 28}

    agent.provider_override = DemoProvider()
    with agent.connect_db() as db:
        for table in ("users", "sessions", "messages", "solutions", "solution_sessions", "llm_calls", "rate_events"):
            assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0, f"fresh database contains rows in {table}"
        assert db.execute("SELECT COUNT(*) FROM toaster_models").fetchone()[0] == 5

    a1 = agent.create_session(agent.CreateSessionRequest(user_id="customer-a", toaster_model_id="toaster-model-1"))
    session_a = a1["session_id"]
    first = agent.process_user_message(session_a, "I'm Alex Morgan (alex.morgan@example.test). My toaster lever pops back up and won't stay down. Order #ZX-48291.")
    assert first["source"] == "escalation"
    assert "Did that fix the problem? Reply yes or no." in first["reply"]
    confirmed_fix = agent.process_user_message(session_a, "Yes")
    assert confirmed_fix["saved_solutions"] == 1, confirmed_fix
    with agent.connect_db() as db:
        solution = dict(db.execute("SELECT * FROM solutions").fetchone())
    stored = json.dumps(solution).lower()
    assert all(value not in stored for value in ("alex", "morgan", "example.test", "zx-48291")), stored
    assert solution["original_problem"] and solution["solution"] and solution["symptoms"] and solution["fixes_that_did_not_work"]
    assert solution["toaster_model_id"] == "toaster-model-1"

    b1 = agent.create_session(agent.CreateSessionRequest(user_id="customer-b", toaster_model_id="toaster-model-1"))
    session_b = b1["session_id"]
    proposed = agent.process_user_message(session_b, "The toaster carriage pops back up instead of staying down.")
    assert proposed["source"] == "knowledge_hub_clarification"
    assert any(origin["user_id"] == "customer-a" and origin["session_id"] == session_a for origin in proposed["provenance"])
    assert "customer-a" in proposed["reply"] and session_a in proposed["reply"]
    reused = agent.process_user_message(session_b, "Yes.")
    assert reused["source"] == "knowledge_hub"
    assert "customer-a" in reused["reply"] and session_a in reused["reply"]
    assert "Did that fix the problem?" in reused["reply"]
    assert agent.process_user_message(session_b, "Yes")["source"] == "hub_confirmation"
    with agent.connect_db() as db:
        assert db.execute("SELECT COUNT(*) FROM solutions").fetchone()[0] == 1
    c1 = agent.create_session(agent.CreateSessionRequest(user_id="customer-c", toaster_model_id="toaster-model-1"))
    assert agent.process_user_message(c1["session_id"], "My toaster sparks and has a burnt smell.")["source"] == "escalation"
    other_model = agent.create_session(agent.CreateSessionRequest(user_id="customer-other-model", toaster_model_id="toaster-model-2"))
    model_miss = agent.process_user_message(other_model["session_id"], "The toaster carriage pops back up instead of staying down.")
    assert model_miss["source"] == "escalation", model_miss
    d1 = agent.create_session(agent.CreateSessionRequest(user_id="customer-d", toaster_model_id="toaster-model-2"))
    agent.process_user_message(d1["session_id"], "My toaster does not heat evenly.")
    with agent.connect_db() as db:
        assert db.execute("SELECT COUNT(*) FROM solutions").fetchone()[0] == 1
        first_cost = db.execute("SELECT SUM(input_tokens + output_tokens) FROM llm_calls WHERE session_id = ?", (session_a,)).fetchone()[0]
        reuse_cost = db.execute("SELECT SUM(input_tokens + output_tokens) FROM llm_calls WHERE session_id = ?", (session_b,)).fetchone()[0]
        assert first_cost > reuse_cost
    usage = agent.session_token_usage(session_a)
    assert usage["input_tokens"] > 0 and usage["output_tokens"] > 0
    assert usage["input_tokens"] + usage["output_tokens"] == first_cost
    assert "lever" in agent.create_session(agent.CreateSessionRequest(user_id="customer-a", toaster_model_id="toaster-model-1"))["greeting"].lower()
    assert "Toaster Model 2" in other_model["greeting"]

    new_user = agent.create_session(agent.CreateSessionRequest(user_id="customer-new-model"))
    candidate = agent.process_user_message(new_user["session_id"], "BrandX HeatMaster 900")
    assert candidate["source"] == "new_model_confirmation_required", candidate
    with agent.connect_db() as db:
        assert db.execute("SELECT COUNT(*) FROM toaster_models WHERE display_name = 'BrandX HeatMaster 900'").fetchone()[0] == 0
    confirmed_model = agent.process_user_message(new_user["session_id"], "Yes")
    assert confirmed_model["source"] == "model_selected", confirmed_model
    assert confirmed_model["toaster_model_id"] in agent.TOASTER_MODELS
    new_problem = agent.process_user_message(new_user["session_id"], "My toaster lever pops back up and will not stay down.")
    assert new_problem["source"] == "escalation", new_problem
    saved_new_model_fix = agent.process_user_message(new_user["session_id"], "That fixed it; it stays down now.")
    assert saved_new_model_fix["saved_solutions"] == 1, saved_new_model_fix
    with agent.connect_db() as db:
        assert db.execute("SELECT COUNT(*) FROM toaster_models WHERE display_name = 'BrandX HeatMaster 900'").fetchone()[0] == 1
        assert db.execute("SELECT toaster_model_id FROM sessions WHERE session_id = ?", (new_user["session_id"],)).fetchone()[0] == confirmed_model["toaster_model_id"]
        assert db.execute("SELECT toaster_model_id FROM solutions WHERE toaster_model_id = ?", (confirmed_model["toaster_model_id"],)).fetchone()[0] == confirmed_model["toaster_model_id"]

    selected_without_llm = agent.create_session(agent.CreateSessionRequest(user_id="direct-model-select"))
    picked_model = agent.process_user_message(selected_without_llm["session_id"], "model 5")
    assert picked_model["source"] == "model_selected", picked_model
    assert picked_model["toaster_model_id"] == "toaster-model-5"
    with agent.connect_db() as db:
        assert db.execute(
            "SELECT COUNT(*) FROM llm_calls WHERE session_id = ? AND call_type = 'model_detection'",
            (selected_without_llm["session_id"],),
        ).fetchone()[0] == 0
    print(f"PASS: model field, same-model reuse, cross-model isolation, PII removal, negative match, unconfirmed exclusion, memory isolation; tokens session1={first_cost}, session2={reuse_cost}")


def main() -> None:
    if ARGS.inspect:
        inspect_database()
    elif ARGS.self_test:
        run_self_test()
    else:
        interactive_chat()


if __name__ == "__main__":
    main()
