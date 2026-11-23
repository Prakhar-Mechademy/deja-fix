"""Run the required 3-session scenario against a freshly empty SQLite DB.

Deterministic fake Gemini/embeddings are intentional: run offline with no API key.
For the live Gemini + MiniLM model use `uvicorn api:app` / Streamlit.
Run: python scripts/demo.py (from project root)
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event

from api import create_app
from app.models import Base
from app.services.chat_service import ChatService


class DemoEmbedder:
    def embed(self, text):
        text = text.lower()
        return [0.0, 1.0] if any(w in text for w in ("printer", "print spooler")) else [1.0, 0.0]

    def cosine_similarity(self, left, right):
        return sum(x * y for x, y in zip(left, right))


class DemoGemini:
    model = "offline-demo-llm"

    def __init__(self):
        self.calls = 0

    def generate(self, prompt):
        self.calls += 1
        return (
            "1. Open Task Manager and quit the Google Drive sync process.\n"
            "2. Restart Google Drive and sign in again.",
            {"input_tokens": 160, "output_tokens": 42},
        )


def run():
    with tempfile.TemporaryDirectory(prefix="deja-fix-demo-") as root:
        db_path = Path(root) / "empty.db"
        engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})

        @event.listens_for(engine, "connect")
        def fk(connection, record):
            connection.execute("PRAGMA foreign_keys=ON")

        Base.metadata.create_all(engine)
        gemini = DemoGemini()
        client = TestClient(create_app(ChatService(gemini, DemoEmbedder()), engine=engine))

        def customer():
            response = client.post("/users")
            assert response.status_code == 201, response.text
            return response.json()

        def new_session(who):
            response = client.post("/sessions", headers=auth(who))
            assert response.status_code == 201, response.text
            return response.json()

        def auth(who):
            return {"Authorization": "Bearer " + who["token"]}

        def send(who, session, content):
            response = client.post(
                f"/sessions/{session}/messages", headers=auth(who), json={"content": content},
            )
            assert response.status_code == 200, response.text
            return response.json()

        def usage(who, session):
            return client.get(f"/sessions/{session}/usage", headers=auth(who)).json()

        a, b = customer(), customer()
        assert a["user_id"] != b["user_id"]

        print("[1] Customer A, empty hub -> LLM escalation -> verified extraction")
        s1 = new_session(a)["session_id"]
        first = send(a, s1, "Google Drive won't finish syncing files on my Windows 11 laptop.")
        assert first["source"] == "gemini_escalation", first
        saved = send(a, s1, "Yes, that fixed it completely.")
        assert saved["source"] == "confirmation" and saved["solution_id"], saved
        print("    Saved solution:", saved["solution_id"])

        print("[2] Customer B, paraphrase -> clarification -> hub retrieval, zero LLM calls")
        s2 = new_session(b)["session_id"]
        clarifying = send(b, s2, "Files never finish uploading in Google Drive on my Windows 11 notebook.")
        assert clarifying["source"] == "clarification", clarifying
        hub = send(b, s2, "Yes, Google Drive uploads just stay pending on Windows 11.")
        assert hub["source"] == "knowledge_hub", hub
        assert hub["solution_id"] == saved["solution_id"]
        print("    Reused verified solution:", hub["solution_id"])

        print("[3] Returning customer A -> own history, never B's")
        returned = new_session(a)
        assert "Google Drive" in (returned["greeting"] or ""), returned
        assert "Files never finish uploading" not in returned["greeting"]
        own = client.get("/sessions", headers=auth(a)).json()["sessions"]
        assert len(own) == 2
        cross = client.get(f"/sessions/{s2}/messages", headers=auth(a))
        assert cross.status_code == 404, cross.text
        print("    Greeting:", returned["greeting"])

        usage1, usage2 = usage(a, s1), usage(b, s2)
        assert usage1["total_tokens"] > usage2["total_tokens"]
        assert gemini.calls == 1, gemini.calls
        print("    Token usage: Session 1 =", usage1, "Session 2 =", usage2)
        print("PASS: all three sessions, verified reuse, tenant isolation and token savings")
        engine.dispose()


if __name__ == "__main__":
    run()
