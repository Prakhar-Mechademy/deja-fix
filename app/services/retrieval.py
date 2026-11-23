"""Embedding-based retrieval for verified knowledge-base solutions."""

import json

import numpy as np
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import Solution
from app.services.embedding import (
    deserialize_embedding,
    embed_text,
    serialize_embedding,
)

HIGH_CONFIDENCE_THRESHOLD = 0.78
UNCERTAIN_THRESHOLD = 0.55
MIN_CANDIDATE_MARGIN = 0.08


def cosine_similarity(left: np.ndarray, right: np.ndarray) -> float:
    left = np.asarray(left, dtype=np.float32).reshape(-1)
    right = np.asarray(right, dtype=np.float32).reshape(-1)

    if left.size == 0 or right.size != left.size:
        raise ValueError("Embeddings must be non-empty vectors of the same size")

    denominator = float(np.linalg.norm(left) * np.linalg.norm(right))

    if denominator == 0:
        return 0.0

    return float(np.dot(left, right) / denominator)


def store_solution_embedding(db: Session, solution: Solution) -> np.ndarray:
    if not solution.problem or not solution.problem.strip():
        raise ValueError("A solution problem description is required")

    vector = embed_text(solution.problem)
    solution.embedding = serialize_embedding(vector)
    solution.embedding_model = settings.EMBEDDING_MODEL
    db.flush()
    return vector


def classify_retrieval_confidence(score: float, margin: float = 1.0) -> str:
    if score >= HIGH_CONFIDENCE_THRESHOLD and margin >= MIN_CANDIDATE_MARGIN:
        return "high"
    if score >= UNCERTAIN_THRESHOLD:
        return "uncertain"
    return "low"


def retrieve_solutions(
    db: Session,
    problem_description: str,
    limit: int = 5,
) -> list[dict]:
    description = problem_description.strip()

    if not description:
        return []

    query_vector = embed_text(description)
    candidates = db.query(Solution).filter(Solution.status == "VERIFIED").all()

    ranked: list[dict] = []

    for solution in candidates:
        if not solution.embedding:
            continue

        try:
            solution_vector = deserialize_embedding(solution.embedding)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue

        if solution.embedding_model and solution.embedding_model != settings.EMBEDDING_MODEL:
            continue

        if solution_vector.size != query_vector.size:
            continue

        score = cosine_similarity(query_vector, solution_vector)
        ranked.append({
            "solution_id": solution.id,
            "problem": solution.problem,
            "symptoms": solution.symptoms,
            "device": solution.device,
            "operating_system": solution.operating_system,
            "app_version": solution.app_version,
            "fix_steps": solution.fix_steps,
            "failed_attempts": solution.failed_attempts,
            "embedding_model": solution.embedding_model,
            "confirmation_count": solution.confirmation_count,
            "score": score,
        })

    ranked.sort(key=lambda item: item["score"], reverse=True)
    ranked = ranked[: max(limit, 0)]

    if not ranked:
        return []

    top_score = ranked[0]["score"]
    runner_up_score = ranked[1]["score"] if len(ranked) > 1 else 0.0
    margin = top_score - runner_up_score

    for candidate in ranked:
        candidate["confidence"] = classify_retrieval_confidence(candidate["score"], margin)

    return ranked


def get_retrieval_decision(result: list[dict]) -> str:
    if not result:
        return "low"

    return result[0]["confidence"]
