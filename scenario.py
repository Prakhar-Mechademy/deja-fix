"""Run the required three-session demo end-to-end using a deterministic fake provider.

This never uses a real API key and starts with a fresh temporary SQLite database.
"""

from __future__ import annotations

import json
import os
import tempfile


_temp_dir = tempfile.TemporaryDirectory(prefix="deja-fix-demo-")
os.environ["DATABASE_PATH"] = os.path.join(_temp_dir.name, "scenario.sqlite3")
os.environ.pop("OPENAI_API_KEY", None)
os.environ.pop("MAIN_API_KEY", None)

import agent  # noqa: E402


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
        system_prompt = messages[0]["content"]
        if "Extract every distinct problem" in system_prompt:
            result = {
                "solutions": [
                    {
                        "problem": "Google Photos stops syncing photos from an Android phone to cloud storage",
                        "symptoms": "Photos remain on the device and do not appear in the cloud. Reported by Alex Morgan; email alex.morgan@example.test; order #ZX-48291.",
                        "environment": "Google Pixel phone, Android 14, Google Photos app 6.1",
                        "fix_steps": "1. Open Google Photos. 2. Check that Backup is enabled. 3. Confirm the account has available storage. 4. Retry backup while connected to Wi-Fi.",
                        "tried_unsuccessfully": "Restarting the phone did not help. Contact: Alex Morgan, alex.morgan@example.test, order #ZX-48291.",
                    }
                ]
            }
            return json.dumps(result), {"input_tokens": 320, "output_tokens": 90}
        return (
            "Let's check the Photos backup setting and available cloud storage first. What Android and app versions are you using?",
            {"input_tokens": 110, "output_tokens": 28},
        )


def assert_no_pii(solution: dict) -> None:
    saved_text = json.dumps(solution).lower()
    for private_value in ("alex", "morgan", "example.test", "zx-48291"):
        assert private_value not in saved_text, f"PII remained in saved solution: {private_value}"


def main() -> None:
    agent.provider_override = DemoProvider()
    with agent.connect_db() as db:
        assert db.execute("SELECT COUNT(*) FROM solutions").fetchone()[0] == 0

    # Session 1: miss -> backend advice -> explicit confirmation -> extraction from persisted transcript.
    a1 = agent.create_session(agent.CreateSessionRequest(user_id="customer-a"))
    session_a1 = a1["session_id"]
    first = agent.process_user_message(
        session_a1,
        "I'm Alex Morgan (alex.morgan@example.test). My Google Photos on a Pixel running Android 14 won't sync; "
        "the pictures stay on my phone. Order #ZX-48291. The app is version 6.1.",
    )
    assert first["source"] == "escalation", first
    confirmed = agent.process_user_message(session_a1, "That fixed it; the photos are syncing now.")
    assert confirmed["saved_solutions"] == 1, confirmed
    with agent.connect_db() as db:
        saved = dict(db.execute("SELECT * FROM solutions").fetchone())
    assert_no_pii(saved)
    assert "Android 14" in saved["environment"]
    assert saved["verified"] == 1

    # Session 2: paraphrase -> verified hub match and clarification -> no chat escalation.
    b1 = agent.create_session(agent.CreateSessionRequest(user_id="customer-b"))
    session_b1 = b1["session_id"]
    match = agent.process_user_message(session_b1, "Pictures on my phone never show up in the cloud backup.")
    assert match["source"] == "knowledge_hub_clarification", match
    answer = agent.process_user_message(session_b1, "Yes.")
    assert answer["source"] == "knowledge_hub", answer
    assert "Backup is enabled" in answer["reply"]

    # A different fault sharing the word "phone" must be escalated, not given the photo fix.
    c1 = agent.create_session(agent.CreateSessionRequest(user_id="customer-c"))
    negative = agent.process_user_message(c1["session_id"], "My phone cannot connect to the Wi-Fi network.")
    assert negative["source"] == "escalation", negative
    assert "Backup is enabled" not in negative["reply"]

    # An unconfirmed escalation never becomes a knowledge entry.
    d1 = agent.create_session(agent.CreateSessionRequest(user_id="customer-d"))
    agent.process_user_message(d1["session_id"], "My laptop battery drains unusually quickly.")
    with agent.connect_db() as db:
        assert db.execute("SELECT COUNT(*) FROM solutions").fetchone()[0] == 1

    # Session 3: customer A sees their own past issue; B's transcript is not included.
    a2 = agent.create_session(agent.CreateSessionRequest(user_id="customer-a"))
    assert "Google Photos" in a2["greeting"]
    assert "customer-b" not in a2["greeting"].lower()
    with agent.connect_db() as db:
        first_usage = db.execute(
            "SELECT COALESCE(SUM(input_tokens + output_tokens), 0) FROM llm_calls WHERE session_id = ?", (session_a1,)
        ).fetchone()[0]
        reused_usage = db.execute(
            "SELECT COALESCE(SUM(input_tokens + output_tokens), 0) FROM llm_calls WHERE session_id = ?", (session_b1,)
        ).fetchone()[0]
        assert first_usage > reused_usage, (first_usage, reused_usage)
        assert db.execute("SELECT COUNT(*) FROM solution_sessions").fetchone()[0] == 1

    print("PASS: escalation, confirmed extraction, PII removal, paraphrase reuse, negative match, unconfirmed exclusion, memory isolation, and lower reuse token count")
    print(f"Session 1 token total: {first_usage}; session 2 token total: {reused_usage}")
    print("Demo database was temporary and has been discarded.")


if __name__ == "__main__":
    main()
