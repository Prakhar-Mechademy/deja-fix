import json
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app.models import Base, FixAttempt, Solution, SolutionSource
from app.repositories import (
    add_message,
    create_chat_session,
    create_fix_attempt,
    get_or_create_user,
    list_verified_solutions,
    update_fix_attempt_status,
)
from app.services.confirmation import (
    ConfirmationDecision,
    classify_confirmation,
)
from app.services.extractor import SolutionExtractor


class FakeEmbedder:
    """Deterministic embeddings: sync and printer issues stay distinct."""

    def embed(self, text):
        lowered = text.lower()
        return [0.0, 1.0] if "printer" in lowered else [1.0, 0.0]

    @staticmethod
    def cosine_similarity(left, right):
        return sum(a * b for a, b in zip(left, right))


class ConfirmationClassificationTests(unittest.TestCase):
    def test_explicit_positive_confirmation(self):
        self.assertEqual(classify_confirmation("Yes, it works now!"), ConfirmationDecision.CONFIRMED)
        self.assertEqual(classify_confirmation("That fixed it."), ConfirmationDecision.CONFIRMED)

    def test_explicit_negative_confirmation(self):
        self.assertEqual(classify_confirmation("No, it still doesn't work."), ConfirmationDecision.REJECTED)
        self.assertEqual(classify_confirmation("Still broken; no change."), ConfirmationDecision.REJECTED)
        self.assertEqual(classify_confirmation("I don't think it worked."), ConfirmationDecision.REJECTED)
        self.assertEqual(classify_confirmation("Not sure if it's fixed."), ConfirmationDecision.AMBIGUOUS)

    def test_ambiguous_reply_does_not_get_assumed_as_success(self):
        self.assertEqual(classify_confirmation("I'll try that later."), ConfirmationDecision.AMBIGUOUS)
        self.assertEqual(classify_confirmation("Maybe."), ConfirmationDecision.AMBIGUOUS)

    def test_later_success_after_but_takes_precedence(self):
        self.assertEqual(
            classify_confirmation("The first step failed, but clearing the cache fixed it."),
            ConfirmationDecision.CONFIRMED,
        )


class ExtractorDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:", connect_args={"check_same_thread": False}
        )

        @event.listens_for(self.engine, "connect")
        def enable_foreign_keys(connection, _record):
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.extractor = SolutionExtractor(FakeEmbedder())

    def tearDown(self):
        self.engine.dispose()

    def _add_pending_attempt(self, db, user_id, session_id, problem, answer, source="gemini_escalation"):
        user_message = add_message(db, session_id, user_id, "user", problem)
        assistant_message = add_message(db, session_id, user_id, "assistant", answer)
        return create_fix_attempt(
            db,
            session_id,
            user_id,
            user_message.id,
            assistant_message.id,
            source=source,
        )

    def _confirm(self, db, user_id, session_id, attempt, reply="Yes, it works now."):
        message = add_message(db, session_id, user_id, "user", reply)
        update_fix_attempt_status(
            db,
            attempt.id,
            user_id,
            "confirmed",
            confirmation_message_id=message.id,
        )
        return message

    def test_confirmed_attempt_extracts_redacted_verified_solution_from_database_transcript(self):
        with self.Session.begin() as db:
            user = get_or_create_user(db)
            chat = create_chat_session(db, user.id)
            attempt = self._add_pending_attempt(
                db,
                user.id,
                chat.id,
                (
                    "My name is Alice Smith. Google Drive app version 4.2.1 on my Windows 11 laptop "
                    "will not sync. Contact alice@example.com; order #ABCD-9876. "
                    "I tried clearing the cache but it did not work. See https://example.com/private."
                ),
                (
                    "I don't have a verified fix for this issue in the knowledge hub yet. "
                    "Here is an unverified troubleshooting suggestion from the backend:\n\n"
                    "1. Open Task Manager and close the Google Drive sync process.\n"
                    "2. Restart Google Drive and sign in again.\n\n"
                    "Did those steps solve the problem? Please reply yes or no."
                ),
            )
            self._confirm(db, user.id, chat.id, attempt)

            result = self.extractor.extract_confirmed_attempt(db, user.id, attempt.id)
            self.assertEqual(result.status, "created")
            self.assertTrue(result.saved)

            solution = db.get(Solution, result.solution_id)
            self.assertTrue(solution.verified)
            self.assertIn("Windows 11", solution.problem)
            self.assertNotIn("Alice", solution.problem)
            self.assertNotIn("alice@example.com", solution.problem)
            self.assertNotIn("ABCD-9876", solution.problem)
            self.assertNotIn("example.com/private", solution.problem)

            environment = json.loads(solution.environment)
            self.assertEqual(environment.get("os"), "Windows 11")
            self.assertEqual(environment.get("device"), "laptop")
            self.assertEqual(environment.get("app"), "Google Drive")
            self.assertEqual(environment.get("app_version"), "4.2.1")

            steps = json.loads(solution.fix_steps)
            self.assertEqual(len(steps), 2)
            self.assertIn("Task Manager", steps[0])
            self.assertIn("sign in again", steps[1].lower())

            failed = json.loads(solution.what_did_not_work)
            self.assertTrue(any("did not work" in item.lower() for item in failed))
            self.assertEqual(solution.source_session_id, chat.id)
            self.assertEqual(
                db.scalar(select(SolutionSource).where(SolutionSource.fix_attempt_id == attempt.id)).session_id,
                chat.id,
            )

    def test_rejected_attempt_is_never_extracted_or_served(self):
        with self.Session.begin() as db:
            user = get_or_create_user(db)
            chat = create_chat_session(db, user.id)
            attempt = self._add_pending_attempt(
                db,
                user.id,
                chat.id,
                "The desktop printer cannot print from Windows 11.",
                "1. Restart the print spooler.\n2. Clear the print queue.",
            )
            negative_reply = add_message(
                db, chat.id, user.id, "user", "No, it still doesn't work."
            )
            update_fix_attempt_status(
                db, attempt.id, user.id, "rejected",
                confirmation_message_id=negative_reply.id,
            )
            result = self.extractor.extract_confirmed_attempt(db, user.id, attempt.id)
            self.assertEqual(result.status, "not_confirmed")
            self.assertEqual(list_verified_solutions(db), [])

    def test_ambiguous_attempt_remains_pending_and_is_not_extracted(self):
        with self.Session.begin() as db:
            user = get_or_create_user(db)
            chat = create_chat_session(db, user.id)
            attempt = self._add_pending_attempt(
                db,
                user.id,
                chat.id,
                "The printer queue is stuck on Windows 11.",
                "1. Restart the print spooler.",
            )
            add_message(db, chat.id, user.id, "user", "I'll see if that helps.")
            # The app's confirmation classifier leaves ambiguous answers pending.
            self.assertEqual(
                classify_confirmation("I'll see if that helps."),
                ConfirmationDecision.AMBIGUOUS,
            )
            result = self.extractor.extract_confirmed_attempt(db, user.id, attempt.id)
            self.assertEqual(result.status, "not_confirmed")
            self.assertEqual(list_verified_solutions(db), [])

    def test_duplicate_confirmed_problem_merges_and_is_idempotent(self):
        with self.Session.begin() as db:
            user_a = get_or_create_user(db)
            session_a = create_chat_session(db, user_a.id)
            first = self._add_pending_attempt(
                db,
                user_a.id,
                session_a.id,
                "Google Drive sync is stuck on Windows 11 laptop.",
                "1. Restart Google Drive.\n2. Sign in again.",
            )
            self._confirm(db, user_a.id, session_a.id, first)
            first_result = self.extractor.extract_confirmed_attempt(db, user_a.id, first.id)
            self.assertEqual(first_result.status, "created")

            # Same fault in different words, from another customer/session.
            user_b = get_or_create_user(db)
            session_b = create_chat_session(db, user_b.id)
            second = self._add_pending_attempt(
                db,
                user_b.id,
                session_b.id,
                "My Windows 11 notebook's Google Drive client stays pending while syncing.",
                "1. Restart Google Drive.\n2. Sign in again.",
            )
            self._confirm(db, user_b.id, session_b.id, second)
            second_result = self.extractor.extract_confirmed_attempt(db, user_b.id, second.id)
            self.assertEqual(second_result.status, "merged")
            self.assertEqual(first_result.solution_id, second_result.solution_id)

            solution = db.get(Solution, first_result.solution_id)
            self.assertEqual(solution.occurrence_count, 2)
            self.assertEqual(
                db.scalar(select(SolutionSource).where(SolutionSource.fix_attempt_id == first.id)).session_id,
                session_a.id,
            )
            self.assertEqual(
                db.scalar(select(SolutionSource).where(SolutionSource.fix_attempt_id == second.id)).session_id,
                session_b.id,
            )

            # A retry after a UI rerun must not inflate the count again.
            repeated = self.extractor.extract_confirmed_attempt(db, user_b.id, second.id)
            self.assertEqual(repeated.status, "already_processed")
            self.assertEqual(solution.occurrence_count, 2)

    def test_one_session_can_learn_multiple_distinct_confirmed_fixes(self):
        with self.Session.begin() as db:
            user = get_or_create_user(db)
            chat = create_chat_session(db, user.id)

            sync_attempt = self._add_pending_attempt(
                db,
                user.id,
                chat.id,
                "Google Drive does not finish syncing on Windows 11.",
                "1. Restart Google Drive.\n2. Sign in again.",
            )
            self._confirm(db, user.id, chat.id, sync_attempt)
            sync_result = self.extractor.extract_confirmed_attempt(db, user.id, sync_attempt.id)
            self.assertEqual(sync_result.status, "created")

            add_message(db, chat.id, user.id, "assistant", "Thanks, I've recorded that confirmed fix.")
            printer_attempt = self._add_pending_attempt(
                db,
                user.id,
                chat.id,
                "The HP printer queue is stuck and jobs never print on Windows 11.",
                "1. Restart the print spooler.\n2. Remove the stuck queue and add the printer again.",
            )
            self._confirm(db, user.id, chat.id, printer_attempt)
            printer_result = self.extractor.extract_confirmed_attempt(db, user.id, printer_attempt.id)

            self.assertEqual(printer_result.status, "created")
            solutions = list_verified_solutions(db)
            self.assertEqual(len(solutions), 2)
            self.assertNotEqual(sync_result.solution_id, printer_result.solution_id)
            self.assertTrue(any("Google Drive" in item.problem for item in solutions))
            self.assertTrue(any("printer" in item.problem.lower() for item in solutions))

    def test_existing_hub_solution_confirmation_adds_source_and_increments_count(self):
        with self.Session.begin() as db:
            user = get_or_create_user(db)
            chat = create_chat_session(db, user.id)
            solution = Solution(
                problem="Google Drive sync is stuck",
                symptoms='["sync remains pending"]',
                environment='{"os":"Windows 11"}',
                fix_steps='["Restart Google Drive"]',
                what_did_not_work="[]",
                embedding=json.dumps([1.0, 0.0]),
                verified=True,
                occurrence_count=3,
                source_session_id=chat.id,
            )
            db.add(solution)
            db.flush()

            attempt = self._add_pending_attempt(
                db,
                user.id,
                chat.id,
                "Google Drive sync is stuck on Windows 11.",
                "I found a verified solution. Did those steps solve the problem?",
                source="knowledge_hub",
            )
            attempt.solution_id = solution.id
            self._confirm(db, user.id, chat.id, attempt)

            result = self.extractor.extract_confirmed_attempt(db, user.id, attempt.id)
            self.assertEqual(result.status, "merged")
            self.assertEqual(solution.occurrence_count, 4)
            self.assertIsNotNone(
                db.scalar(select(SolutionSource).where(SolutionSource.fix_attempt_id == attempt.id))
            )

    def test_new_tables_and_confirmation_status_survive_database_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "attempt-restart.db"
            first_engine = create_engine(f"sqlite:///{database_path}")
            Base.metadata.create_all(first_engine)
            FirstSession = sessionmaker(bind=first_engine, expire_on_commit=False)
            with FirstSession.begin() as db:
                user = get_or_create_user(db)
                chat = create_chat_session(db, user.id)
                attempt = self._add_pending_attempt(
                    db, user.id, chat.id,
                    "The app crashes on startup on Windows 11.",
                    "1. Reinstall the app.",
                )
                self._confirm(db, user.id, chat.id, attempt)
                user_id, attempt_id = user.id, attempt.id
            first_engine.dispose()

            second_engine = create_engine(f"sqlite:///{database_path}")
            SecondSession = sessionmaker(bind=second_engine, expire_on_commit=False)
            with SecondSession() as db:
                restored = db.get(FixAttempt, attempt_id)
                self.assertEqual(restored.user_id, user_id)
                self.assertEqual(restored.status, "confirmed")
                result = SolutionExtractor(FakeEmbedder()).extract_confirmed_attempt(
                    db, user_id, attempt_id
                )
                self.assertEqual(result.status, "created")
                db.commit()
            second_engine.dispose()


if __name__ == "__main__":
    unittest.main()
