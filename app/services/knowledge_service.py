"""Conservative semantic retrieval with explicit technical incompatibility guards."""
import json
import logging
import time
import re
from collections.abc import Iterable

from app.config import settings

logger = logging.getLogger(__name__)

_OS_ALIASES = {
    "windows": "windows", "macos": "macos", "mac os": "macos", "os x": "macos",
    "linux": "linux", "ubuntu": "linux", "android": "android", "ios": "ios",
    "ipados": "ipados", "chromeos": "chromeos", "chrome os": "chromeos",
}
_APP_ALIASES = {
    "google drive": "drive", "drive client": "drive", "onedrive": "onedrive",
    "dropbox": "dropbox", "slack": "slack", "outlook": "outlook", "zoom": "zoom",
    "whatsapp": "whatsapp", "chrome": "chrome", "firefox": "firefox",
    "printer": "printer", "print spooler": "printer", "steam": "steam",
}
# We don't treat a generic word like 'problem' as a fault signal.
_FAULTS = {
    "sync": r"\b(?:sync|synchroniz|upload|pending transfer|files? not updating)\w*\b",
    "print": r"\b(?:print|spool|queue|paper jam)\w*\b",
    "login": r"\b(?:log(?:ging)?\s*in|sign(?:ing)?\s*in|authenticat|credential|password|2fa|login)\w*\b",
    "crash": r"\b(?:crash|freez|hang|force.?clos|won't open|will not open|fails? to launch)\w*\b",
    "network": r"\b(?:offline|disconnect|no internet|network|dns|timed? out|connection reset)\b",
    "storage": r"\b(?:disk full|out of (?:space|storage)|no storage|storage full|quota exceeded)\b",
    "permission": r"\b(?:access denied|permission denied|forbidden|unauthori[sz]ed)\b",
}
# Distinguish *causes* within otherwise identical symptoms (e.g. Drive doesn't sync).
_CAUSES = {
    "auth": r"\b(?:token expired|expired session|wrong password|password incorrect|login failed|"
            r"authentication failed|invalid credentials|sign.?in error)\b",
    "space": r"\b(?:disk full|out of (?:space|storage)|storage full|quota exceeded)\b",
    "connectivity": r"\b(?:no internet|network disconnected|dns failure|connection reset|offline mode)\b",
    "permissions": r"\b(?:permission denied|access denied|forbidden|not authorized)\b",
    "corrupt": r"\b(?:corrupt(?:ed)? (?:file|cache|database)|cache corruption)\b",
    "paper": r"\b(?:paper jam|jammed paper|out of paper|paper tray empty)\b",
    "ink": r"\b(?:out of ink|low ink|toner empty|out of toner|blank pages?|faded print)\b",
    "print_queue": r"\b(?:print queue|spooler|stuck print job|jobs? stuck in queue)\b",
    "driver": r"\b(?:printer driver|driver missing|driver not installed|corrupt driver)\b",
}
_ERROR_CODE = re.compile(r"\b(?:error|code|errno|0x)\s*[:#-]?\s*(0x[a-f0-9]{3,}|[a-z]{1,5}-?\d{3,}|\d{3,5})\b", re.I)


def _normalise_os(value: str) -> str | None:
    text = (value or "").lower().strip()
    for alias, canonical in _OS_ALIASES.items():
        if re.search(rf"\b{re.escape(alias)}\b", text):
            return canonical
    return None


def _json_dict(value) -> dict:
    if isinstance(value, dict):
        return value
    try:
        decoded = json.loads(value or "{}")
        return decoded if isinstance(decoded, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def _app(value: str) -> str | None:
    lowered = (value or "").lower()
    for phrase, canonical in sorted(_APP_ALIASES.items(), key=lambda x: -len(x[0])):
        if re.search(rf"\b{re.escape(phrase)}\b", lowered):
            return canonical
    return None


def _labels(patterns: dict, text: str) -> set[str]:
    return {name for name, pattern in patterns.items() if re.search(pattern, text, re.I)}


def compatible_problems(query: str, solution) -> bool:
    """Hard-negative filter, independent of embedding score.

    When evidence is absent, don't invent it. Reject only known contradictions.
    Ambiguity is resolved through a clarifying question before serving a hit.
    """
    env = _json_dict(getattr(solution, "environment", None))
    saved_text = (getattr(solution, "problem", "") or "")
    try:
        symptoms = json.loads(getattr(solution, "symptoms", "[]") or "[]")
        if isinstance(symptoms, list):
            saved_text += " " + " ".join(str(s) for s in symptoms)
    except (TypeError, json.JSONDecodeError):
        pass
    query_os = _normalise_os(query)
    saved_os = _normalise_os(str(env.get("os", ""))) or _normalise_os(saved_text)
    if query_os and saved_os and query_os != saved_os:
        return False

    query_app = _app(query)
    saved_app = _app(str(env.get("app", ""))) or _app(saved_text)
    if query_app and saved_app and query_app != saved_app:
        return False

    query_faults = _labels(_FAULTS, query)
    saved_faults = _labels(_FAULTS, saved_text)
    if query_faults and saved_faults and not query_faults.issubset(saved_faults):
        return False

    query_causes = _labels(_CAUSES, query)
    saved_causes = _labels(_CAUSES, saved_text)
    # A specific new fault is not safely interchangeable with a generic hit.
    if query_causes and (not saved_causes or not query_causes.issubset(saved_causes)):
        return False
    query_errors = {code.lower() for code in _ERROR_CODE.findall(query)}
    saved_errors = {code.lower() for code in _ERROR_CODE.findall(saved_text)}
    if query_errors and saved_errors and query_errors.isdisjoint(saved_errors):
        return False
    return True


class KnowledgeService:
    def __init__(self, embedder):
        self.embedder = embedder

    def find_match(self, problem: str, verified_solutions: Iterable) -> tuple[object | None, float]:
        started = time.perf_counter()
        candidates = list(verified_solutions)
        if not candidates or not problem.strip():
            logger.info("knowledge.search_skipped reason=%s candidates=%d", "empty_query" if not problem.strip() else "empty_hub", len(candidates))
            return None, 0.0
        logger.debug("knowledge.search_started candidates=%d query_chars=%d", len(candidates), len(problem))
        try:
            vector = self.embedder.embed(problem)
        except Exception:
            logger.exception("knowledge.query_embedding_failed")
            raise
        best, highest = None, 0.0
        rejected_incompatible = 0
        invalid_embeddings = 0
        for solution in candidates:
            if not getattr(solution, "verified", False) or not solution.embedding:
                continue
            if not compatible_problems(problem, solution):
                rejected_incompatible += 1
                logger.debug("knowledge.candidate_rejected_hard_negative solution_id=%s", getattr(solution, "id", None))
                continue
            try:
                saved = json.loads(solution.embedding)
                score = self.embedder.cosine_similarity(vector, saved)
            except (TypeError, ValueError, json.JSONDecodeError, ArithmeticError):
                invalid_embeddings += 1
                logger.warning("knowledge.candidate_embedding_invalid solution_id=%s", getattr(solution, "id", None))
                continue
            logger.debug("knowledge.candidate_scored solution_id=%s score=%.4f", getattr(solution, "id", None), score)
            if score > highest:
                best, highest = solution, score
        accepted = bool(best and highest >= settings.similarity_threshold)
        logger.info("knowledge.search_completed accepted=%s solution_id=%s best_score=%.4f threshold=%.4f candidates=%d hard_negative_rejects=%d invalid_embeddings=%d elapsed_ms=%.1f", accepted, getattr(best, "id", None) if accepted else None, highest, settings.similarity_threshold, len(candidates), rejected_incompatible, invalid_embeddings, (time.perf_counter() - started) * 1000)
        return (best, highest) if accepted else (None, highest)
