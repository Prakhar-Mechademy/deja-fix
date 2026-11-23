"""Verified-only retrieval with semantic embeddings and cautious lexical fallback."""
import json, math, re
from sqlalchemy.orm import Session
from models.solution import Solution
from services.embedding_service import embed_text, lexical_vector
from config import RETRIEVAL_THRESHOLD, AMBIGUOUS_THRESHOLD

def cosine_similarity(a, b):
    if len(a) != len(b): return 0.0
    denom = math.sqrt(sum(x*x for x in a)) * math.sqrt(sum(y*y for y in b))
    return sum(x*y for x, y in zip(a, b)) / denom if denom else 0.0

def _tokens(text):
    return set(re.findall(r"[a-z0-9]+", text.lower())) - {"the", "and", "for", "with", "this", "that", "from", "have", "has", "when", "into", "after", "before"}

def _lexical_score(query, candidate):
    a, b = _tokens(query), _tokens(candidate)
    if not a or not b: return 0.0
    overlap = len(a & b) / len(a | b)
    # A weak fallback should not confidently match on a few shared generic terms.
    return overlap

CAUSE_MARKERS = {
    "connectivity": {"network", "wifi", "internet", "offline", "connectivity", "connection"},
    "authentication": {"authentication", "login", "password", "credential", "token", "signin", "sign-in"},
    "permission": {"permission", "permissions", "privilege", "access-denied", "forbidden"},
    "server": {"server", "backend", "outage", "http500", "http503", "503", "500"},
    "storage": {"storage", "disk", "space", "capacity", "full"},
    "billing": {"billing", "payment", "charge", "invoice", "subscription"},
}

def _cause_families(text):
    words = _tokens(text)
    return {family for family, markers in CAUSE_MARKERS.items() if words & markers}

def has_conflicting_fault(query: str, candidate: str) -> bool:
    query_causes, candidate_causes = _cause_families(query), _cause_families(candidate)
    # If both descriptions name a cause but identify disjoint cause families,
    # shared symptom vocabulary (e.g. "cannot sync") must not override that conflict.
    return bool(query_causes and candidate_causes and query_causes.isdisjoint(candidate_causes))

def find_best_match(db: Session, problem: str, session_id: str | None = None):
    rows = db.query(Solution).filter(Solution.verified.is_(True)).all()
    if not rows: return None, 0.0, "none"
    query_embedding = embed_text(problem, db=db, session_id=session_id) if session_id else embed_text(problem)
    scored = []
    for solution in rows:
        candidate = f"{solution.symptoms} {solution.environment}"
        if has_conflicting_fault(problem, solution.symptoms):
            continue
        if query_embedding is not None and solution.embedding:
            try: score = cosine_similarity(query_embedding, json.loads(solution.embedding)); method = "semantic"
            except Exception: score = _lexical_score(problem, candidate); method = "lexical_fallback"
        else:
            score = _lexical_score(problem, candidate); method = "lexical_fallback"
        scored.append((score, solution, method))
    if not scored:
        return None, 0.0, "conflicting_fault"
    scored.sort(key=lambda x: x[0], reverse=True)
    score, solution, method = scored[0]
    # Semantic score uses the configured threshold. Lexical fallback deliberately requires
    # a high token overlap to reduce false positives in environments without an API key.
    threshold = RETRIEVAL_THRESHOLD if method == "semantic" else 0.72
    if score >= threshold: return solution, score, method
    if score >= AMBIGUOUS_THRESHOLD and method == "semantic": return solution, score, "ambiguous"
    return None, score, method
