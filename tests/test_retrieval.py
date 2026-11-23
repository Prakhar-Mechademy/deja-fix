"""Tests for retrieval behavior."""

import json

import numpy as np
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
import app.db.models  # noqa: F401
from app.db.models import Solution
import app.services.retrieval as retrieval


@pytest.fixture
def retrieval_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)

    yield SessionLocal

    engine.dispose()


def fake_embedding(text: str) -> np.ndarray:
    normalized = text.lower()
    vector = np.zeros(8, dtype=np.float32)

    if "sync" in normalized or "cloud" in normalized:
        vector[0] = 1.0
    if "document" in normalized or "file" in normalized:
        vector[1] = 1.0
    if "crash" in normalized:
        vector[2] = 1.0
    if "wifi" in normalized or "network" in normalized:
        vector[3] = 1.0
    if "printer" in normalized:
        vector[4] = 1.0
    if "export" in normalized:
        vector[5] = 1.0

    if not np.any(vector):
        vector[7] = 1.0

    norm = np.linalg.norm(vector)
    return vector / norm if norm else vector


def test_identical_descriptions_have_high_cosine_similarity(monkeypatch):
    monkeypatch.setattr(retrieval, "embed_text", fake_embedding)

    vector = fake_embedding("Files cannot sync to the cloud")
    score = retrieval.cosine_similarity(vector, vector)

    assert score == pytest.approx(1.0)


def test_paraphrased_description_retrieves_verified_solution(monkeypatch, retrieval_db):
    monkeypatch.setattr(retrieval, "embed_text", fake_embedding)

    with retrieval_db() as db:
        solution = Solution(
            problem="Files cannot sync to the cloud",
            fix_steps=["Reset cloud synchronisation"],
            status="VERIFIED",
        )
        vector = fake_embedding(solution.problem)
        solution.embedding = json.dumps(vector.tolist())
        solution.embedding_model = "all-MiniLM-L6-v2"
        db.add(solution)
        db.commit()

        results = retrieval.retrieve_solutions(db, "Cloud documents do not upload")

        assert len(results) == 1
        assert results[0]["solution_id"] == solution.id
        assert results[0]["score"] >= 0.55


def test_overlapping_words_do_not_match_different_fault(monkeypatch, retrieval_db):
    monkeypatch.setattr(retrieval, "embed_text", fake_embedding)

    with retrieval_db() as db:
        solution = Solution(
            problem="Application crashes when exporting PDF files",
            fix_steps=["Update the PDF exporter"],
            status="VERIFIED",
        )
        vector = fake_embedding(solution.problem)
        solution.embedding = json.dumps(vector.tolist())
        solution.embedding_model = "all-MiniLM-L6-v2"
        db.add(solution)
        db.commit()

        results = retrieval.retrieve_solutions(db, "Application crashes when opening")

        assert results
        assert results[0]["score"] < 0.78
        assert retrieval.get_retrieval_decision(results) != "high"


def test_unverified_solution_is_never_retrieved(monkeypatch, retrieval_db):
    monkeypatch.setattr(retrieval, "embed_text", fake_embedding)

    with retrieval_db() as db:
        solution = Solution(
            problem="Files cannot sync",
            fix_steps=["Try again"],
            status="UNVERIFIED",
        )
        vector = fake_embedding(solution.problem)
        solution.embedding = json.dumps(vector.tolist())
        solution.embedding_model = "all-MiniLM-L6-v2"
        db.add(solution)
        db.commit()

        assert retrieval.retrieve_solutions(db, "Files cannot sync") == []


def test_empty_knowledge_hub_returns_no_match(monkeypatch, retrieval_db):
    monkeypatch.setattr(retrieval, "embed_text", fake_embedding)

    with retrieval_db() as db:
        assert retrieval.retrieve_solutions(db, "Printer is offline") == []


def test_multiple_candidates_are_ranked(monkeypatch, retrieval_db):
    monkeypatch.setattr(retrieval, "embed_text", fake_embedding)

    with retrieval_db() as db:
        sync_solution = Solution(problem="Cloud file sync", fix_steps=["Reset sync"], status="VERIFIED")
        network_solution = Solution(problem="WiFi network connection", fix_steps=["Reconnect WiFi"], status="VERIFIED")

        for solution in (sync_solution, network_solution):
            vector = fake_embedding(solution.problem)
            solution.embedding = json.dumps(vector.tolist())
            solution.embedding_model = "all-MiniLM-L6-v2"
            db.add(solution)

        db.commit()

        results = retrieval.retrieve_solutions(db, "Cloud documents cannot sync", limit=5)

        assert len(results) == 2
        assert results[0]["solution_id"] == sync_solution.id
        assert results[0]["score"] >= results[1]["score"]
