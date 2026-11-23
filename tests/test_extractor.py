"""Tests for information extraction."""

import asyncio

import numpy as np
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
import app.db.models  # noqa: F401
from app.db.models import LLMUsage, Message, Session, Solution, SolutionSource, User
from app.schemas.solutions import ExtractedSolution, ExtractedSolutionSet
from app.services.confirmation import is_explicit_confirmation
import app.services.extractor as extractor


@pytest.fixture
def extractor_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    yield SessionLocal
    engine.dispose()


def add_conversation(db, external_id, messages):
    user = User(external_id=external_id)
    db.add(user)
    db.flush()

    session = Session(user_id=user.id, title="Extraction session")
    db.add(session)
    db.flush()

    for role, content in messages:
        db.add(Message(session_id=session.id, user_id=user.id, role=role, content=content))

    db.commit()
    return session


def test_explicit_confirmation_is_required():
    assert is_explicit_confirmation("Yes, it worked. The problem is resolved") is True
    assert is_explicit_confirmation("Please try clearing the cache") is False
    assert is_explicit_confirmation("The assistant said this might fix it") is False


def test_confirmed_fix_is_saved_verified_and_pii_is_removed(extractor_db, monkeypatch):
    monkeypatch.setattr(extractor, "embed_text", lambda text: np.array([1.0, 0.0], dtype=np.float32))

    with extractor_db() as db:
        session = add_conversation(db, "confirmed-user", [
            ("user", "My name is Alice and my iPhone 15 on iOS 18 cannot upload files. Email alice@example.com"),
            ("assistant", "Clear the local sync cache and restart the app"),
            ("user", "Yes, it worked and the problem is resolved"),
        ])

        saved = asyncio.run(extractor.extract_solutions(db, session.id, use_gemini=False))

        assert len(saved) == 1
        assert saved[0].status == "VERIFIED"
        assert "alice@example.com" not in saved[0].problem
        assert "My name is Alice" not in saved[0].problem


def test_unconfirmed_fix_is_not_retrievable(extractor_db, monkeypatch):
    monkeypatch.setattr(extractor, "embed_text", lambda text: np.array([1.0, 0.0], dtype=np.float32))

    with extractor_db() as db:
        session = add_conversation(db, "unconfirmed-user", [
            ("user", "My laptop on Windows 11 cannot sync files"),
            ("assistant", "Clear the sync cache"),
        ])

        saved = asyncio.run(extractor.extract_solutions(db, session.id, use_gemini=False))
        assert saved == []
        assert db.query(Solution).count() == 0


def test_technical_environment_is_preserved(extractor_db, monkeypatch):
    monkeypatch.setattr(extractor, "embed_text", lambda text: np.array([1.0, 0.0], dtype=np.float32))

    with extractor_db() as db:
        session = add_conversation(db, "technical-user", [
            ("user", "My Windows 11 laptop running app 3.2.1 cannot sync files"),
            ("assistant", "Restart the app and clear the cache"),
            ("user", "The issue is resolved now"),
        ])

        saved = asyncio.run(extractor.extract_solutions(db, session.id, use_gemini=False))
        assert len(saved) == 1
        assert saved[0].device == "Laptop"
        assert saved[0].operating_system == "Windows"


def test_duplicate_solution_is_merged(extractor_db, monkeypatch):
    monkeypatch.setattr(extractor, "embed_text", lambda text: np.array([1.0, 0.0], dtype=np.float32))
    monkeypatch.setattr(extractor, "cosine_similarity", lambda left, right: 1.0)

    with extractor_db() as db:
        first = add_conversation(db, "duplicate-a", [
            ("user", "Cloud sync is pending"),
            ("assistant", "Clear the sync cache"),
            ("user", "It worked"),
        ])
        second = add_conversation(db, "duplicate-b", [
            ("user", "Cloud sync remains pending"),
            ("assistant", "Restart the app"),
            ("user", "It is fixed now"),
        ])

        first_saved = asyncio.run(extractor.extract_solutions(db, first.id, use_gemini=False))
        second_saved = asyncio.run(extractor.extract_solutions(db, second.id, use_gemini=False))

        assert first_saved[0].id == second_saved[0].id
        assert db.query(Solution).count() == 1


def test_multiple_solutions_from_one_conversation(extractor_db, monkeypatch):
    async def fake_gemini(messages):
        payload = ExtractedSolutionSet(solutions=[
            ExtractedSolution(problem="Cloud sync fails", fix_steps=["Clear sync cache"]),
            ExtractedSolution(problem="Printer is offline", fix_steps=["Reconnect printer"]),
        ])
        return payload, {"input_tokens": 80, "output_tokens": 20, "total_tokens": 100}

    monkeypatch.setattr(extractor, "_extract_with_gemini", fake_gemini)
    monkeypatch.setattr(extractor, "embed_text", lambda text: np.array([1.0, 0.0], dtype=np.float32))
    monkeypatch.setattr(extractor, "cosine_similarity", lambda left, right: 0.0)

    with extractor_db() as db:
        session = add_conversation(db, "multiple-user", [
            ("user", "Sync fails and my printer is offline"),
            ("assistant", "Clear sync cache. Reconnect the printer."),
            ("user", "Both fixes worked"),
        ])

        saved = asyncio.run(extractor.extract_solutions(db, session.id, use_gemini=True))
        assert len(saved) == 2


def test_provenance_and_token_usage(extractor_db, monkeypatch):
    async def fake_gemini(messages):
        payload = ExtractedSolutionSet(solutions=[
            ExtractedSolution(problem="Printer is offline", fix_steps=["Reconnect printer"]),
        ])
        return payload, {"input_tokens": 100, "output_tokens": 40, "total_tokens": 140}

    monkeypatch.setattr(extractor, "_extract_with_gemini", fake_gemini)
    monkeypatch.setattr(extractor, "embed_text", lambda text: np.array([1.0, 0.0], dtype=np.float32))

    with extractor_db() as db:
        session = add_conversation(db, "provenance-user", [
            ("user", "Printer is offline"),
            ("assistant", "Reconnect the printer"),
            ("user", "That fixed the issue"),
        ])

        saved = asyncio.run(extractor.extract_solutions(db, session.id, use_gemini=True))
        sources = db.query(SolutionSource).filter_by(solution_id=saved[0].id).all()

        assert len(sources) == 1
        assert sources[0].session_id == session.id
        assert sources[0].confirmation_message_id is not None

        usage = db.query(LLMUsage).filter_by(session_id=session.id).one()
        assert usage.purpose == "extraction"
        assert usage.total_tokens == 140


def test_extractor_reads_persisted_transcript(extractor_db, monkeypatch):
    monkeypatch.setattr(extractor, "embed_text", lambda text: np.array([1.0, 0.0], dtype=np.float32))

    with extractor_db() as db:
        session = add_conversation(db, "persisted-user", [
            ("user", "Network is disconnected"),
            ("assistant", "Reconnect to WiFi"),
            ("user", "The problem is solved"),
        ])

        messages = extractor.read_transcript(db, session.id)
        assert [message.role for message in messages] == ["user", "assistant", "user"]

        saved = asyncio.run(extractor.extract_solutions(db, session.id, use_gemini=False))
        assert len(saved) == 1
