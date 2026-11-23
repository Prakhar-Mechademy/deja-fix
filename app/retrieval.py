"""Embedding-based solution retrieval."""

import numpy as np

from app import config
from app.db import get_all_solutions
from app.llm import embed


def embed_solution_text(sol: dict) -> str:
    """Build the embedding text used for a solution."""
    symptoms = " ".join(str(value) for value in (sol.get("symptoms") or []))
    if symptoms:
        return f"{sol.get('title', '')}. {symptoms}".strip()
    return str(sol.get("title", "")).strip()


def debug_scores(query_text: str) -> list[tuple[int, float]]:
    """Return every solution score, ignoring the retrieval threshold."""
    solutions = get_all_solutions()
    if not solutions:
        return []
    vectors = embed([query_text], "RETRIEVAL_QUERY", None, None)
    if not vectors:
        return []
    query = np.asarray(vectors[0], dtype=np.float32)
    scores = []
    for solution in solutions:
        solution_vector = np.asarray(solution["embedding"], dtype=np.float32)
        if solution_vector.size == 0 or solution_vector.size != query.size:
            score = 0.0
        else:
            score = float(np.dot(query, solution_vector))
        scores.append((int(solution["id"]), score))
    return sorted(scores, key=lambda item: item[1], reverse=True)


def search(query_text: str, session_id: int, user_id: int, top_k: int = 3) -> list[dict]:
    """Search verified solutions by cosine similarity."""
    solutions = get_all_solutions()
    if not solutions:
        return []

    vectors = embed([query_text], "RETRIEVAL_QUERY", session_id, user_id)
    if not vectors:
        return []
    query = np.asarray(vectors[0], dtype=np.float32)

    matches = []
    for solution in solutions:
        solution_vector = np.asarray(solution["embedding"], dtype=np.float32)
        if solution_vector.size == 0 or solution_vector.size != query.size:
            continue
        score = float(np.dot(query, solution_vector))
        if score >= config.RETRIEVAL_THRESHOLD:
            result = dict(solution)
            result.pop("embedding", None)
            result["score"] = score
            matches.append(result)

    matches.sort(key=lambda item: item["score"], reverse=True)
    return matches[:top_k]
