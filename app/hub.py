"""Knowledge hub retrieval.

Embeddings alone cannot tell "same fault, different words" from "different fault, similar
words" (measured on real data: reworded problem 0.904, look-alike fault 0.913). So search
is two-stage:

  1. Embeddings shortlist the closest VERIFIED solutions (cheap, finds reworded matches).
  2. An LLM judge decides whether the customer's problem is the SAME fault as each
     shortlisted solution. Only a clear "yes" counts as a match.

Anything else returns None, and the caller escalates. When the hub is empty, no LLM or
embedding call is made at all.
"""
from __future__ import annotations

import math
from typing import Optional

from . import config, db, llm

TOP_K = 3  # at most this many judge calls per search

JUDGE_SYSTEM = (
    "You compare a customer's problem with a saved support solution. "
    'Reply with ONLY JSON: {"same_fault": true or false, "reason": "<one short sentence>"}. '
    "same_fault is true only if both describe the same underlying fault: the same failing "
    "behaviour with the same likely root cause, even if worded very differently. "
    "Answer false if they merely share words, apps, devices or features but the failing "
    "behaviour differs (for example 'won't sync' versus 'won't open'). "
    "Answer false if the customer's stated device, OS or app version clearly conflicts with "
    "the saved solution's environment. If the customer has not mentioned their environment, "
    "do not penalise that. When unsure, answer false."
)


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def solution_text(title: str, symptoms: str) -> str:
    """The text that gets embedded for a solution (environment is left to the judge)."""
    return f"{title}. {symptoms}"


def embed_solution(title: str, symptoms: str, *, session_id: Optional[str], user_id: Optional[str]) -> list[float]:
    """Used by the extractor when saving a new solution."""
    return llm.embed(solution_text(title, symptoms), session_id=session_id, user_id=user_id)


def _judge(query_text: str, sol: dict, session_id: Optional[str], user_id: Optional[str]) -> Optional[dict]:
    env = ", ".join(f"{k}: {v}" for k, v in (sol.get("environment") or {}).items() if v) or "not recorded"
    prompt = (
        f"Customer's problem:\n{query_text}\n\n"
        f"Saved solution:\nTitle: {sol['title']}\nSymptoms: {sol['symptoms']}\nEnvironment: {env}\n\n"
        "Is the customer's problem the same underlying fault as the saved solution?"
    )
    try:
        out = llm.generate_json(
            [{"role": "user", "content": prompt}],
            session_id=session_id,
            user_id=user_id,
            purpose="match_check",
            system=JUDGE_SYSTEM,
            temperature=0.0,
        )
    except llm.LLMError as e:
        if e.kind == "malformed":
            return None  # be conservative: treat as "not the same", the caller will escalate
        raise
    return out if isinstance(out, dict) else None


def find_match(
    query_text: str,
    *,
    session_id: Optional[str],
    user_id: Optional[str],
) -> Optional[dict]:
    """Return {"solution": dict, "score": float, "reason": str} or None.

    Used both to serve a customer (persona) and to detect duplicates (extractor).
    Raises llm.LLMError if the embedding call or the judge call fails outright.
    """
    candidates = [s for s in db.get_verified_solutions() if s.get("embedding")]
    if not candidates:
        return None  # empty hub: no embedding or judge call needed

    qvec = llm.embed(query_text, session_id=session_id, user_id=user_id)
    scored = sorted(((_cosine(qvec, s["embedding"]), s) for s in candidates), key=lambda t: -t[0])
    shortlist = [(sc, s) for sc, s in scored if sc >= config.BORDERLINE_THRESHOLD][:TOP_K]

    for score, sol in shortlist:
        verdict = _judge(query_text, sol, session_id, user_id)
        if verdict and verdict.get("same_fault") is True:
            return {"solution": sol, "score": score, "reason": str(verdict.get("reason", ""))}
    return None