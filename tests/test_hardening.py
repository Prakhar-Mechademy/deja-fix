import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from api import create_app
from app.models import Base, Solution, FixAttempt, RateEvent
from app.repositories import (
    add_message, create_chat_session, create_fix_attempt, get_or_create_user,
    update_fix_attempt_status,
)
from app.services.chat_service import ChatService
from app.services.confirmation import ConfirmationDecision, classify_confirmation
from app.services.extractor import SolutionExtractor, redact_personal_data
from app.services.knowledge_service import KnowledgeService, compatible_problems
from app.services.rate_limit import RateLimiter, RateLimits


class ConstantEmbedder:
    def embed(self, text):
        return [1.0, 0.0]

    def cosine_similarity(self, a, b):
        return 1.0


class FakeLLM:
    model = "mock-llm"

    def __init__(self):
        self.calls = 0

    def generate(self, prompt):
        self.calls += 1
        return "1. Restart Google Drive.\n2. Sign in again.", {"input_tokens": 120, "output_tokens": 30}


class HardeningTests(unittest.TestCase):
    def setUp(self):
        self.db_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.db_dir.name) / "test.db"
        self.engine = create_engine(f"sqlite:///{self.db_path}", connect_args={"check_same_thread": False})

        @event.listens_for(self.engine, "connect")
        def fk(connection, record):
            connection.execute("PRAGMA foreign_keys=ON")

        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)

    def tearDown(self):
        self.engine.dispose()
        self.db_dir.cleanup()

    def test_partial_and_conflicting_yes_are_not_confirmed(self):
        cases = {
            "Yes but it only partly works": ConfirmationDecision.AMBIGUOUS,
            "Yes, but it still crashes when I open the app": ConfirmationDecision.REJECTED,
            "The first step failed, but clearing the cache fixed it": ConfirmationDecision.CONFIRMED,
            "No, it didn't fix the problem": ConfirmationDecision.REJECTED,
            "Maybe it is fine now": ConfirmationDecision.AMBIGUOUS,
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(classify_confirmation(text), expected)

    def test_customer_correction_overrides_wrong_llm_fix_and_retains_failed_attempt(self):
        with self.Session.begin() as db:
            user = get_or_create_user(db)
            session = create_chat_session(db, user.id)
            question = add_message(db, session.id, user.id, "user", "Google Drive sync is stuck on Windows 11 laptop.")
            assistant = add_message(db, session.id, user.id, "assistant", "1. Restart Google Drive.\n2. Reinstall Google Drive.")
            attempt = create_fix_attempt(db, session.id, user.id, question.id, assistant.id)
            confirmation = add_message(
                db, session.id, user.id, "user",
                "Restarting did not work, but clearing the cache fixed it.",
            )
            update_fix_attempt_status(db, attempt.id, user.id, "confirmed", confirmation.id)
            result = SolutionExtractor(ConstantEmbedder()).extract_confirmed_attempt(db, user.id, attempt.id)
            self.assertEqual(result.status, "created")
            saved = db.get(Solution, result.solution_id)
            self.assertEqual(json.loads(saved.fix_steps), ["clearing the cache"])
            self.assertIn("Restarting did not work", json.loads(saved.what_did_not_work)[0])
            self.assertNotIn("Reinstall Google Drive", " ".join(json.loads(saved.fix_steps)))

    def test_all_solution_fields_redact_identifiers_but_keep_environment(self):
        problem = (
            "Alice Smith here. My name is Alice Smith. Google Drive version 4.2.1 "
            "on Windows 11 laptop cannot sync. My order number is ABC1234. "
            "Contact alice@example.com, https://bad.example/private."
        )
        with self.Session.begin() as db:
            user = get_or_create_user(db)
            session = create_chat_session(db, user.id)
            q = add_message(db, session.id, user.id, "user", problem)
            a = add_message(db, session.id, user.id, "assistant", "1. Restart Google Drive.")
            attempt = create_fix_attempt(db, session.id, user.id, q.id, a.id)
            outcome = add_message(db, session.id, user.id, "user", "Yes, fixed completely.")
            update_fix_attempt_status(db, attempt.id, user.id, "confirmed", outcome.id)
            extracted = SolutionExtractor(ConstantEmbedder()).extract_confirmed_attempt(db, user.id, attempt.id)
            saved = db.get(Solution, extracted.solution_id)
            fields = " ".join((saved.problem, saved.symptoms, saved.environment, saved.fix_steps, saved.what_did_not_work))
            for private in ("Alice", "Smith", "ABC1234", "alice@example.com", "bad.example"):
                self.assertNotIn(private, fields)
            self.assertIn("Windows 11", saved.problem)
            self.assertEqual(json.loads(saved.environment)["app_version"], "4.2.1")

    def test_similar_words_different_fault_and_error_code_are_rejected(self):
        saved = Solution(
            problem="Google Drive sync stops because authentication token expired, error 401",
            symptoms='["authentication token expired error 401"]',
            environment='{"os":"Windows 11","app":"Google Drive"}',
            fix_steps='["Sign in again"]',
            embedding='[1.0,0.0]', verified=True,
        )
        kb = KnowledgeService(ConstantEmbedder())
        self.assertIsNone(kb.find_match(
            "Google Drive sync stops because disk full error 507 on Windows 11", [saved],
        )[0])
        self.assertIsNone(kb.find_match(
            "Google Drive sync stops because authentication token expired error 403 on Windows 11", [saved],
        )[0])
        self.assertIsNotNone(kb.find_match(
            "Google Drive won't synchronize, token expired error 401 on Windows 11", [saved],
        )[0])

    def test_http_api_message_429_and_token_isolation(self):
        llm = FakeLLM()
        app = create_app(
            ChatService(llm, ConstantEmbedder()), engine=self.engine,
            rate_limiter=RateLimiter(RateLimits(messages_per_minute=2, escalations_per_minute=1)),
        )
        client = TestClient(app)
        a, b = client.post("/users").json(), client.post("/users").json()
        h_a = {"Authorization": f"Bearer {a['token']}"}
        h_b = {"Authorization": f"Bearer {b['token']}"}
        s = client.post("/sessions", headers=h_a).json()["session_id"]
        self.assertEqual(client.get(f"/sessions/{s}/messages", headers=h_b).status_code, 404)
        self.assertEqual(client.get(f"/sessions/{s}/messages").status_code, 401)
        self.assertEqual(client.post(f"/sessions/{s}/messages", json={"content":"hello"}, headers=h_b).status_code, 404)
        first = client.post(f"/sessions/{s}/messages", json={"content":"Google Drive sync is stuck on Windows 11."}, headers=h_a)
        self.assertEqual(first.status_code, 200)
        confirm = client.post(f"/sessions/{s}/messages", json={"content":"yes fixed"}, headers=h_a)
        self.assertEqual(confirm.status_code, 200)
        third = client.post(f"/sessions/{s}/messages", json={"content":"something else"}, headers=h_a)
        self.assertEqual(third.status_code, 429)
        self.assertIn("Retry-After", third.headers)
        self.assertEqual(len(client.get(f"/sessions/{s}/messages", headers=h_a).json()["messages"]), 5)
        self.assertEqual(llm.calls, 1)

    def test_escalation_429_before_provider_and_transaction_rollback(self):
        llm = FakeLLM()
        app = create_app(
            ChatService(llm, ConstantEmbedder()), engine=self.engine,
            rate_limiter=RateLimiter(RateLimits(messages_per_minute=20, escalations_per_minute=1)),
        )
        client = TestClient(app)
        a = client.post("/users").json()
        header = {"Authorization": f"Bearer {a['token']}"}
        s = client.post("/sessions", headers=header).json()["session_id"]
        post = lambda text: client.post(f"/sessions/{s}/messages", headers=header, json={"content":text})
        self.assertEqual(post("Google Drive sync is stuck on Windows 11").status_code, 200)
        self.assertEqual(post("No, it did not work").status_code, 200)
        # No confirmed solution was stored; attempting a second backend call gets 429.
        limit_response = post("The Dropbox app keeps crashing on Windows 11")
        self.assertEqual(limit_response.status_code, 429)
        self.assertEqual(llm.calls, 1)
        # The rejected request was rolled back entirely.
        messages = client.get(f"/sessions/{s}/messages", headers=header).json()["messages"]
        self.assertEqual(len(messages), 5)
        with self.Session() as db:
            self.assertEqual(len(list(db.scalars(select(RateEvent).where(RateEvent.kind == "escalation")))), 1)
            self.assertEqual(len(list(db.scalars(select(Solution)))), 0)


if __name__ == "__main__":
    unittest.main()
