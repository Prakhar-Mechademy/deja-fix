"""Canonical knowledge generalisation and quality validation."""

import re

from app.core.config import settings
from app.schemas.solutions import ExtractedSolution
from app.services.embedding import embed_text, serialize_embedding
from app.utils.pii import contains_pii, redact_pii, sanitise_string_list

ERROR_CODE_PATTERN = re.compile(r"\b(?:ERR|E|0x)?[-_]?[A-Z]?\d{2,5}[A-Z]?\b|\b[A-Z]{2,}-\d{3,}\b")
CUSTOMER_LANGUAGE_PATTERN = re.compile(
    r"\b(?:my|mine|i|me|please|my name|customer|alice|bob)\b",
    re.I,
)


def extract_error_codes(text: str) -> list[str]:
    return sorted(set(match.group(0) for match in ERROR_CODE_PATTERN.finditer(text or "")))


def generalise_title(problem: str) -> str:
    title = redact_pii(problem).strip()

    title = re.sub(r"\b(?:please|kindly|can you|could you|help me|my|mine)\b", "", title, flags=re.I)
    title = re.sub(r"\s+", " ", title)
    title = title.strip(" ,.:")

    if not title:
        return "Unspecified technical problem"

    return title[0].upper() + title[1:].rstrip(".")


def normalise_steps(steps: list[str] | None) -> list[str]:
    normalised = []

    for step in sanitise_string_list(steps):
        cleaned = re.sub(r"^(?:please\s+|then\s+|first\s+|next\s+)", "", step, flags=re.I)
        cleaned = re.sub(r"\b(?:i would|you should)\b", "", cleaned, flags=re.I)
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,.:")

        if cleaned:
            normalised.append(cleaned[0].upper() + cleaned[1:])

    return list(dict.fromkeys(normalised))


def validate_canonical_solution(solution: dict) -> tuple[bool, list[str]]:
    reasons = []

    searchable = " ".join([
        solution.get("canonical_title", ""),
        solution.get("problem_description", ""),
        " ".join(solution.get("symptoms") or []),
        " ".join(solution.get("fix_steps") or []),
    ])

    if contains_pii(searchable):
        reasons.append("unresolved personal information")

    if not solution.get("fix_steps"):
        reasons.append("missing actionable troubleshooting steps")

    if not solution.get("confirmed"):
        reasons.append("missing explicit customer confirmation")

    return not reasons, reasons


def generalise_extracted_solution(item: ExtractedSolution, confirmed: bool = True) -> dict:
    source_text = " ".join([
        item.problem,
        " ".join(item.symptoms or []),
        " ".join(item.fix_steps or []),
        " ".join(item.error_codes or []),
    ])

    problem_description = redact_pii(item.problem).strip()
    problem_description = CUSTOMER_LANGUAGE_PATTERN.sub("", problem_description)
    problem_description = re.sub(r"\s+", " ", problem_description).strip(" ,.;:")

    if not problem_description:
        problem_description = "Unspecified technical problem"

    canonical = {
        "canonical_title": generalise_title(problem_description),
        "problem_description": problem_description,
        "symptoms": sanitise_string_list(item.symptoms),
        "device": redact_pii(item.device) if item.device else None,
        "operating_system": redact_pii(item.operating_system) if item.operating_system else None,
        "application_name": redact_pii(item.application_name) if item.application_name else None,
        "app_version": redact_pii(item.app_version) if item.app_version else None,
        "fix_steps": normalise_steps(item.fix_steps),
        "failed_attempts": normalise_steps(item.failed_attempts),
        "error_codes": sorted(set(extract_error_codes(source_text) + sanitise_string_list(item.error_codes))),
        "confirmed": confirmed,
    }

    embedding_text = " ".join([
        canonical["canonical_title"],
        canonical["problem_description"],
        " ".join(canonical["symptoms"]),
        canonical["device"] or "",
        canonical["operating_system"] or "",
        canonical["application_name"] or "",
        canonical["app_version"] or "",
        " ".join(canonical["fix_steps"]),
    ])

    vector = embed_text(embedding_text)
    canonical["embedding"] = serialize_embedding(vector)
    canonical["embedding_model"] = settings.EMBEDDING_MODEL

    valid, reasons = validate_canonical_solution(canonical)
    canonical["quality_valid"] = valid
    canonical["quality_reasons"] = reasons

    return canonical
