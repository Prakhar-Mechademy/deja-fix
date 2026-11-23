import json
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.models import Base, ChatSession, LLMCallLog, Message, Solution, User
from app.repositories import (
    add_message,
    create_chat_session,
    get_chat_session,
    get_or_create_user,
    list_chat_sessions,
    list_messages,
    log_llm_call,
)
from app.services.chat_service import ChatService


class FakeEmbedder:
    def embed(self, _text):
        return [1.0, 0.0]

    @staticmethod
    def cosine_similarity(_left, _right):
        return 0.91


class FakeGemini:
    model = "gemini-test"

    def __init__(self, response=None, error=None):
        self.response = response or ("Try restarting the sync service.", {
            "input_tokens": 123,
            "output_tokens": 45,
        })
        self.error = error
        self.prompts = []

    def generate(self, prompt):
        self.prompts.append(prompt)
        if self.error:
            raise self.error
        return self.response


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})

        @event.listens_for(self.engine, "connect")
        def enable_foreign_keys(connection, _record):
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)

    def tearDown(self):
        self.engine.dispose()

    def test_users_are_created_independently_and_sessions_are_scoped(self):
        with self.Session.begin() as db:
            user_a = get_or_create_user(db)
            user_b = get_or_create_user(db)
            session_a = create_chat_session(db, user_a.id)
            session_b = create_chat_session(db, user_b.id)
            add_message(db, session_a.id, user_a.id, "user", "Sync is stuck")
            add_message(db, session_b.id, user_b.id, "user", "Printer is offline")

            self.assertNotEqual(user_a.id, user_b.id)
            self.assertEqual([s.id for s in list_chat_sessions(db, user_a.id)], [session_a.id])
            self.assertEqual([s.id for s in list_chat_sessions(db, user_b.id)], [session_b.id])
            self.assertIsNone(get_chat_session(db, session_a.id, user_id=user_b.id))
            self.assertEqual(len(list_messages(db, session_a.id, user_id=user_a.id)), 1)

    def test_message_cannot_be_attached_to_another_users_session(self):
        with self.Session.begin() as db:
            user_a = get_or_create_user(db)
            user_b = get_or_create_user(db)
            session_a = create_chat_session(db, user_a.id)
            with self.assertRaises(ValueError):
                add_message(db, session_a.id, user_b.id, "user", "cross-user message")

    def test_database_composite_foreign_key_blocks_owner_mismatch(self):
        with self.Session.begin() as db:
            user_a = get_or_create_user(db)
            user_b = get_or_create_user(db)
            session_a = create_chat_session(db, user_a.id)
            db.add(Message(
                session_id=session_a.id,
                user_id=user_b.id,
                role="user",
                content="should be rejected",
            ))
            with self.assertRaises(IntegrityError):
                db.flush()

    def test_messages_and_sessions_survive_a_new_database_session(self):
        with self.Session.begin() as db:
            user = get_or_create_user(db)
            chat = create_chat_session(db, user.id)
            add_message(db, chat.id, user.id, "user", "My app will not sync")
            add_message(db, chat.id, user.id, "assistant", "What operating system?")
            user_id, session_id = user.id, chat.id

        with self.Session() as db:
            saved_session = get_chat_session(db, session_id, user_id=user_id)
            messages = list_messages(db, session_id, user_id=user_id)
            self.assertIsNotNone(saved_session)
            self.assertEqual([m.content for m in messages], [
                "My app will not sync", "What operating system?"
            ])

    def test_file_database_survives_engine_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "restart-test.db"
            first_engine = create_engine(f"sqlite:///{database_path}")
            Base.metadata.create_all(first_engine)
            FirstSession = sessionmaker(bind=first_engine, expire_on_commit=False)
            with FirstSession.begin() as db:
                user = get_or_create_user(db)
                chat = create_chat_session(db, user.id)
                add_message(db, chat.id, user.id, "user", "The desktop app will not open")
                user_id, session_id = user.id, chat.id
            first_engine.dispose()

            second_engine = create_engine(f"sqlite:///{database_path}")
            SecondSession = sessionmaker(bind=second_engine, expire_on_commit=False)
            with SecondSession() as db:
                restored = list_messages(db, session_id, user_id=user_id)
                self.assertEqual(len(restored), 1)
                self.assertEqual(restored[0].content, "The desktop app will not open")
            second_engine.dispose()

    def test_llm_usage_is_tagged_to_the_session(self):
        with self.Session.begin() as db:
            user = get_or_create_user(db)
            chat = create_chat_session(db, user.id)
            log_llm_call(db, chat.id, "gemini-test", 17, 9)
            session_id = chat.id

        with self.Session() as db:
            log = db.scalar(select(LLMCallLog).where(LLMCallLog.session_id == session_id))
            self.assertEqual((log.input_tokens, log.output_tokens), (17, 9))


class ChatPipelineTests(unittest.TestCase):
    def _solution(self, environment=None, verified=True):
        return Solution(
            problem="Cloud sync does not finish",
            symptoms='["sync remains pending"]',
            environment=json.dumps(environment or {"os": "Windows 11", "app": "Drive Client"}),
            fix_steps='["Restart the sync service", "Sign in again"]',
            embedding=json.dumps([1.0, 0.0]),
            verified=verified,
        )

    def test_verified_match_does_not_call_gemini(self):
        gemini = FakeGemini()
        service = ChatService(gemini, FakeEmbedder())
        result = service.respond(
            "My cloud sync remains pending on Windows 11",
            transcript=[],
            verified_solutions=[self._solution()],
        )
        # First semantic hit asks for issue-specific clarification, not the fix.
        self.assertEqual(result.source, "clarification")
        self.assertIn("Before I share", result.answer)
        self.assertNotIn("Verified fix steps", result.answer)
        self.assertFalse(result.llm_attempted)
        self.assertEqual(gemini.prompts, [])

    def test_environment_conflict_escalates_with_full_transcript(self):
        gemini = FakeGemini()
        service = ChatService(gemini, FakeEmbedder())
        transcript = [
            type("Msg", (), {"role": "user", "content": "Cloud sync remains pending on macOS"})(),
            type("Msg", (), {"role": "assistant", "content": "Which version are you using?"})(),
            type("Msg", (), {"role": "user", "content": "macOS 15"})(),
        ]
        result = service.respond(
            "Cloud sync remains pending on macOS 15",
            transcript=transcript,
            verified_solutions=[self._solution({"os": "Windows 11"})],
        )
        self.assertEqual(result.source, "gemini_escalation")
        self.assertTrue(result.llm_attempted)
        self.assertEqual(result.llm_usage, {"input_tokens": 123, "output_tokens": 45})
        self.assertIn("Which version are you using?", gemini.prompts[0])
        self.assertIn("macOS 15", gemini.prompts[0])
        self.assertNotIn("You are Déjà Fix", gemini.prompts[0])


    def test_followup_answer_uses_prior_problem_for_retrieval(self):
        gemini = FakeGemini()
        service = ChatService(gemini, FakeEmbedder())
        transcript = [
            type("Msg", (), {"role": "user", "content": "Cloud sync remains pending"})(),
            type("Msg", (), {"role": "assistant", "content": "Which operating system are you using?"})(),
            type("Msg", (), {"role": "user", "content": "Windows 11"})(),
        ]
        result = service.respond(
            "Windows 11",
            transcript=transcript,
            verified_solutions=[self._solution({"os": "Windows 11"})],
        )
        self.assertEqual(result.source, "knowledge_hub")
        self.assertEqual(gemini.prompts, [])

    def test_unverified_solution_is_never_served(self):
        gemini = FakeGemini()
        service = ChatService(gemini, FakeEmbedder())
        result = service.respond(
            "My cloud sync remains pending",
            transcript=[],
            verified_solutions=[self._solution(verified=False)],
        )
        self.assertEqual(result.source, "gemini_escalation")

    def test_ambiguous_message_requests_clarification_without_escalation(self):
        gemini = FakeGemini()
        service = ChatService(gemini, FakeEmbedder())
        result = service.respond("not working", transcript=[], verified_solutions=[])
        self.assertEqual(result.source, "clarification")
        self.assertIn("describe what is failing", result.answer)
        self.assertFalse(result.llm_attempted)
        self.assertEqual(gemini.prompts, [])

    def test_provider_failure_returns_plain_fallback_and_zero_token_log_values(self):
        service = ChatService(FakeGemini(error=TimeoutError("timed out")), FakeEmbedder())
        result = service.respond("Cloud sync remains pending", transcript=[], verified_solutions=[])
        self.assertEqual(result.source, "gemini_unavailable")
        self.assertTrue(result.llm_attempted)
        self.assertEqual(result.llm_usage, {"input_tokens": 0, "output_tokens": 0})
        self.assertIn("temporarily unavailable", result.answer)

    def test_malformed_gemini_response_is_handled(self):
        service = ChatService(FakeGemini(response=(None, None)), FakeEmbedder())
        result = service.respond("Cloud sync remains pending", transcript=[], verified_solutions=[])
        self.assertEqual(result.source, "gemini_unavailable")
        self.assertIn("temporarily unavailable", result.answer)


if __name__ == "__main__":
    unittest.main()
