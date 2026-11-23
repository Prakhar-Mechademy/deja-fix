"""Transcript-backed extraction of customer-confirmed fixes.

This extractor is deliberately deterministic: it only consumes content already
stored in SQLite, applies local PII redaction, and never asks an LLM to invent
missing symptoms, environment details, or fix steps.
"""

import json
import logging
import re
import time
from dataclasses import dataclass

from app.models import Solution
from app.repositories import (
    add_solution_source,
    get_fix_attempt,
    get_solution_source_for_attempt,
    list_fix_attempts,
    list_messages,
    list_verified_solutions,
)
from app.services.knowledge_service import KnowledgeService
from app.services.confirmation import classify_confirmation, ConfirmationDecision


logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_URL_RE = re.compile(r"\b(?:https?://|www\.)\S+", re.I)
# Require an explicit identifier label; preserve OS/app version and error numbers.
_ACCOUNT_RE = re.compile(
    r"\b(?:order|account|ticket|case|invoice|reference|ref|customer|user|serial|"
    r"license|subscription)\s*(?:number|no\.?|id|#)?\s*(?:is|:|=|#|-)?\s*"
    r"(?=[A-Z0-9-]{2,}\b)(?=[A-Z0-9-]*\d)[A-Z0-9-]{2,}\b", re.I,
)
_PHONE_RE = re.compile(r"(?<!\w)(?:\+?\d[\d .()/-]{7,}\d)(?!\w)")
_LONG_NUMBER_RE = re.compile(r"(?<!\w)\d{8,}(?!\w)")
_NAME_LABEL_RE = re.compile(
    r"\b(?i:my name is|customer name\s*[:=]?|name\s*[:=])\s*"
    r"[A-Z][a-z]{1,}(?:\s+[A-Z][a-z]{1,}){0,2}",
)
_NAME_INTRO_RE = re.compile(
    r"\b(?:I'm|I am)\s+[A-Z][a-z]{1,}(?:\s+[A-Z][a-z]{1,}){0,2}"
)
_NAME_HERE_RE = re.compile(
    r"\b[A-Z][a-z]{1,}\s+[A-Z][a-z]{1,}\s+here\b"
)

_OS_PATTERNS = (
    ("Windows", re.compile(r"\bWindows(?:\s+(?:10|11))?\b", re.I)),
    ("macOS", re.compile(r"\b(?:macOS|Mac\s+OS\s+X)\s*\d*(?:\.\d+)*\b", re.I)),
    ("Ubuntu", re.compile(r"\bUbuntu\s*\d*(?:\.\d+)*\b", re.I)),
    ("Android", re.compile(r"\bAndroid\s*\d*(?:\.\d+)*\b", re.I)),
    ("iPadOS", re.compile(r"\biPadOS\s*\d*(?:\.\d+)*\b", re.I)),
    ("iOS", re.compile(r"\biOS\s*\d*(?:\.\d+)*\b", re.I)),
    ("ChromeOS", re.compile(r"\bChromeOS\s*\d*(?:\.\d+)*\b", re.I)),
    ("Linux", re.compile(r"\bLinux\b", re.I)),
)
_DEVICE_PATTERNS = (
    re.compile(r"\b(?:MacBook(?:\s+(?:Air|Pro))?|iPhone(?:\s+\d+)?|iPad(?:\s+Pro)?)\b", re.I),
    re.compile(r"\b(?:Windows\s+PC|desktop|laptop|tablet|Android\s+phone)\b", re.I),
)
_KNOWN_APPS = (
    "Google Drive", "Drive Client", "OneDrive", "Dropbox", "Slack",
    "Microsoft Teams", "Teams", "Zoom", "Google Chrome", "Chrome",
    "Firefox", "Outlook", "Discord", "Notion", "WhatsApp", "Steam",
    "Visual Studio Code", "VS Code", "Microsoft 365", "Excel", "Word",
)
_FAILED_CUES = re.compile(
    r"\b(?:didn't work|did not work|doesn't work|does not work|failed|"
    r"no change|still not working|still broken|not fixed|didn't help|"
    r"did not help|made no difference|same issue)\b",
    re.I,
)
_BULLET_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+(.+?)\s*$")


@dataclass
class ExtractionResult:
    status: str
    solution_id: str | None = None
    problem: str | None = None
    reason: str | None = None

    @property
    def saved(self) -> bool:
        return self.status in {"created", "merged", "already_processed"}

    @property
    def merged(self) -> bool:
        return self.status in {"merged", "already_processed"}


class SolutionExtractor:
    def __init__(self, embedder):
        self.embedder = embedder
        self.knowledge = KnowledgeService(embedder)

    def extract_confirmed_attempt(self, db, user_id: str, attempt_id: str) -> ExtractionResult:
        """Extract exactly one confirmed attempt from its persisted transcript."""
        started = time.perf_counter()
        logger.info("extractor.started user_id=%s attempt_id=%s", user_id, attempt_id)
        attempt = get_fix_attempt(db, attempt_id, user_id)
        if attempt is None:
            logger.warning("extractor.attempt_not_found_or_not_owned user_id=%s attempt_id=%s", user_id, attempt_id)
            raise ValueError("The fix attempt does not exist or is not owned by this user.")
        logger.info("extractor.attempt_loaded user_id=%s session_id=%s attempt_id=%s status=%s source=%s", user_id, attempt.session_id, attempt_id, attempt.status, attempt.source)
        if attempt.status != "confirmed":
            logger.warning("extractor.skipped reason=attempt_not_confirmed attempt_id=%s status=%s", attempt_id, attempt.status)
            return ExtractionResult(
                status="not_confirmed",
                reason="Only explicitly confirmed fixes can be extracted.",
            )

        existing_source = get_solution_source_for_attempt(db, attempt.id)
        if existing_source is not None:
            solution = db.get(Solution, existing_source.solution_id)
            logger.info("extractor.idempotent_skip attempt_id=%s solution_id=%s", attempt_id, existing_source.solution_id)
            return ExtractionResult(
                status="already_processed",
                solution_id=existing_source.solution_id,
                problem=solution.problem if solution else None,
            )

        # The transcript is deliberately loaded from SQLite here. No Streamlit
        # state, prompt variable, or in-memory chat history is passed to extractor.
        transcript = list_messages(db, attempt.session_id, user_id)
        logger.info("extractor.transcript_loaded user_id=%s session_id=%s attempt_id=%s messages=%d", user_id, attempt.session_id, attempt_id, len(transcript))
        message_positions = {message.id: index for index, message in enumerate(transcript)}
        if attempt.response_message_id not in message_positions:
            logger.warning("extractor.no_solution attempt_id=%s reason=assistant_response_missing", attempt_id)
            return ExtractionResult(status="no_solution", reason="Stored assistant response is missing.")
        if attempt.confirmation_message_id not in message_positions:
            logger.warning("extractor.no_solution attempt_id=%s reason=confirmation_message_missing", attempt_id)
            return ExtractionResult(status="no_solution", reason="A stored customer confirmation is required.")
        confirmation_index = message_positions[attempt.confirmation_message_id]
        confirmation_text = transcript[confirmation_index].content
        if (
            confirmation_index <= message_positions[attempt.response_message_id]
            or classify_confirmation(confirmation_text) != ConfirmationDecision.CONFIRMED
        ):
            logger.warning("extractor.skipped reason=stored_confirmation_not_positive attempt_id=%s", attempt_id)
            return ExtractionResult(status="not_confirmed", reason="The stored outcome does not confirm a full fix.")
        response_index = message_positions[attempt.response_message_id]
        trigger_index = message_positions.get(attempt.trigger_message_id)
        if trigger_index is None or trigger_index > response_index:
            logger.warning("extractor.no_solution attempt_id=%s reason=problem_context_missing", attempt_id)
            return ExtractionResult(status="no_solution", reason="Stored problem context is missing.")

        # A solution returned from the hub is already verified. Confirmation
        # records another real-world occurrence rather than extracting a copy.
        if attempt.solution_id:
            logger.info("extractor.confirmed_existing_hub_solution attempt_id=%s solution_id=%s", attempt_id, attempt.solution_id)
            solution = db.get(Solution, attempt.solution_id)
            if solution is None or not solution.verified:
                logger.warning("extractor.no_solution attempt_id=%s reason=referenced_solution_unavailable solution_id=%s", attempt_id, attempt.solution_id)
                return ExtractionResult(status="no_solution", reason="The referenced solution is unavailable.")
            _, created_source = add_solution_source(
                db, solution.id, attempt.session_id, attempt.id
            )
            if created_source:
                solution.occurrence_count = (solution.occurrence_count or 0) + 1
                db.flush()
            status = "merged" if created_source else "already_processed"
            logger.info("extractor.completed attempt_id=%s status=%s solution_id=%s elapsed_ms=%.1f", attempt_id, status, solution.id, (time.perf_counter() - started) * 1000)
            return ExtractionResult(
                status=status,
                solution_id=solution.id,
                problem=solution.problem,
            )

        # Keep each issue's transcript segment independent when a customer solves
        # multiple issues in one session. A successful prior attempt starts the
        # next issue segment; rejected attempts remain context for a retry.
        start_index = 0
        attempts = list_fix_attempts(db, attempt.session_id, user_id)
        for previous in attempts:
            # Rejected suggestions remain part of the active issue: include
            # their attempted steps and the customer's failure report when a
            # later suggestion is eventually confirmed. A prior successful fix
            # starts a fresh issue boundary.
            if (
                previous.id == attempt.id
                or previous.status != "confirmed"
                or not previous.confirmation_message_id
            ):
                continue
            previous_position = message_positions.get(previous.confirmation_message_id)
            if previous_position is not None and previous_position < trigger_index:
                start_index = max(start_index, previous_position + 1)

        segment = transcript[start_index : response_index + 1]
        user_messages = [
            message.content.strip()
            for message in segment
            if message.role == "user" and message.content.strip()
        ]
        assistant_response = transcript[response_index].content
        rejected_steps = []
        for old in attempts:
            if old.status != "rejected":
                continue
            old_index = message_positions.get(old.response_message_id)
            if old_index is not None and start_index <= old_index < response_index:
                rejected_steps.extend(self._extract_fix_steps(transcript[old_index].content))
        logger.info("extractor.transcript_segment_ready attempt_id=%s user_messages=%d rejected_steps=%d segment_messages=%d", attempt_id, len(user_messages), len(rejected_steps), len(segment))
        candidate = self._build_candidate(
            user_messages, assistant_response, confirmation_text, rejected_steps
        )
        if candidate is None:
            logger.warning("extractor.no_solution attempt_id=%s reason=missing_problem_or_actionable_steps", attempt_id)
            return ExtractionResult(
                status="no_solution",
                reason="The stored conversation did not contain both a problem and actionable fix steps.",
            )

        problem, symptoms, environment, fix_steps, what_did_not_work = candidate
        logger.info("extractor.candidate_built attempt_id=%s problem_chars=%d symptoms=%d environment_fields=%s fix_steps=%d failed_attempts=%d", attempt_id, len(problem), len(symptoms), sorted(environment.keys()), len(fix_steps), len(what_did_not_work))
        try:
            embedding = self.embedder.embed(problem)
        except Exception as exc:
            logger.exception("extractor.embedding_failed attempt_id=%s", attempt_id)
            return ExtractionResult(
                status="no_solution",
                reason=f"Could not embed the generalized problem: {type(exc).__name__}.",
            )

        verified_solutions = list_verified_solutions(db)
        duplicate, _score = self.knowledge.find_match(problem, verified_solutions)
        if duplicate is not None and not self._compatible_fix_steps(duplicate.fix_steps, fix_steps):
            duplicate = None
        if duplicate is not None:
            logger.info("extractor.duplicate_found attempt_id=%s solution_id=%s score=%.4f", attempt_id, duplicate.id, _score)
            duplicate.occurrence_count = (duplicate.occurrence_count or 0) + 1
            # Retain useful additional observations while keeping the original
            # generalized problem and verified fix canonical.
            duplicate.symptoms = self._merge_json_lists(duplicate.symptoms, symptoms)
            duplicate.what_did_not_work = self._merge_json_lists(
                duplicate.what_did_not_work, what_did_not_work
            )
            add_solution_source(db, duplicate.id, attempt.session_id, attempt.id)
            db.flush()
            logger.info("extractor.completed attempt_id=%s status=merged solution_id=%s occurrence_count=%d elapsed_ms=%.1f", attempt_id, duplicate.id, duplicate.occurrence_count, (time.perf_counter() - started) * 1000)
            return ExtractionResult(
                status="merged",
                solution_id=duplicate.id,
                problem=duplicate.problem,
            )

        solution = Solution(
            problem=problem,
            symptoms=json.dumps(symptoms, ensure_ascii=False),
            environment=json.dumps(environment, ensure_ascii=False),
            fix_steps=json.dumps(fix_steps, ensure_ascii=False),
            what_did_not_work=json.dumps(what_did_not_work, ensure_ascii=False),
            embedding=json.dumps(embedding),
            verified=True,
            occurrence_count=1,
            source_session_id=attempt.session_id,
        )
        db.add(solution)
        db.flush()
        add_solution_source(db, solution.id, attempt.session_id, attempt.id)
        db.flush()
        logger.info("extractor.completed attempt_id=%s status=created solution_id=%s symptoms=%d fix_steps=%d environment_fields=%s elapsed_ms=%.1f", attempt_id, solution.id, len(symptoms), len(fix_steps), sorted(environment.keys()), (time.perf_counter() - started) * 1000)
        return ExtractionResult(
            status="created",
            solution_id=solution.id,
            problem=solution.problem,
        )

    def _build_candidate(
        self, user_messages: list[str], assistant_response: str,
        confirmation_text: str = "", rejected_steps: list[str] | None = None,
    ):
        if not user_messages:
            return None
        problem = redact_personal_data("\n".join(user_messages)).strip()
        if not problem:
            return None

        steps = self._extract_fix_steps(assistant_response)
        # If the customer explicitly says a *different* action fixed it, their
        # verified outcome supersedes the backend's proposed unsuccessful steps.
        corrected = self._customer_successful_steps(confirmation_text)
        if corrected:
            logger.info("extractor.customer_corrected_steps_detected steps=%d", len(corrected))
            steps = corrected
        if not steps:
            return None

        raw_user_text = "\n".join(user_messages)
        environment = extract_environment(raw_user_text)
        symptoms = _unique(
            redact_personal_data(item).strip()
            for message in user_messages
            for item in _sentences(message)
            if item.strip() and not _FAILED_CUES.search(item)
        )
        failed = _unique(
            redact_personal_data(item).strip()
            for message in user_messages
            for item in _sentences(message)
            if _FAILED_CUES.search(item)
        )
        for item in _sentences(confirmation_text):
            if _FAILED_CUES.search(item):
                # A clause following 'but' may describe the actual success;
                # keep only the unsuccessful clause.
                preceding = re.split(r"\b(?:but|however)\b", item, maxsplit=1, flags=re.I)[0]
                failed.append(redact_personal_data(preceding))
        if rejected_steps:
            failed.extend("Previously tried without success: " + step for step in rejected_steps)
        return (
            problem, symptoms or [problem], environment, _unique(steps),
            _unique(redact_personal_data(v) for v in failed),
        )

    @staticmethod
    def _customer_successful_steps(confirmation_text: str) -> list[str]:
        """Extract explicit customer correction, rather than inventing a fix.

        For ordinary 'yes' replies, use the assistant's steps as confirmed.
        """
        text = confirmation_text.strip()
        contrast = re.split(r'\b(?:but|however)\b', text, flags=re.I)
        if len(contrast) > 1:
            text = contrast[-1].strip()
        # 'restart failed, but clearing the cache fixed it'
        endings = r"(?:fixed (?:it|the (?:issue|problem))|solved (?:it|the problem)|worked(?: for me)?)"
        matches = re.findall(
            rf"(?:^|\bbut\s+|\bhowever\s+|[.;]\s*)"
            rf"((?:(?:clearing|restarting|resetting|reinstalling|updating|disabling|enabling|"
            rf"removing|deleting|signing|changing|turning|running|setting|uninstalling|"
            rf"closing|opening|installing)\b)[^.!?;]{{0,100}}?)\s+{endings}",
            text, flags=re.I,
        )
        if not matches:
            matches = re.findall(
                r"\bfixed (?:it|the issue|the problem) by\s+"
                r"((?:clearing|restarting|resetting|reinstalling|updating|disabling|"
                r"enabling|removing|deleting|changing|signing|running)\b[^.!?;]{1,100})",
                text, flags=re.I,
            )
        return _unique(redact_personal_data(step.strip(" ,.-")) for step in matches)

    @staticmethod
    def _compatible_fix_steps(existing_json: str, new_steps: list[str]) -> bool:
        """Avoid silently merging genuinely different repairs into one canonical fix."""
        try:
            existing = json.loads(existing_json or "[]")
        except (TypeError, json.JSONDecodeError):
            existing = []
        if not isinstance(existing, list):
            return False
        action = re.compile(
            r"\b(restart|reboot|reinstall|install|update|clear|reset|sign|log|"
            r"remove|delete|disable|enable|repair|free|check|open|close|run)\w*\b", re.I
        )
        old_actions = {m.lower()[:5] for line in existing for m in action.findall(str(line))}
        new_actions = {m.lower()[:5] for line in new_steps for m in action.findall(str(line))}
        return not (old_actions and new_actions and old_actions.isdisjoint(new_actions))

    @staticmethod
    def _extract_fix_steps(assistant_response: str) -> list[str]:
        text = (assistant_response or "").strip()
        text = re.sub(
            r"^I don't have a verified fix for this issue in the knowledge hub yet\.\s*"
            r"Here is an unverified troubleshooting suggestion from the backend:\s*",
            "",
            text,
            flags=re.I,
        )
        text = re.split(
            r"\n?Did those steps solve the problem\?.*$",
            text,
            maxsplit=1,
            flags=re.I | re.S,
        )[0].strip()

        bullets = []
        for line in text.splitlines():
            match = _BULLET_RE.match(line)
            if match:
                item = match.group(1).strip()
                if item:
                    bullets.append(redact_personal_data(item))
        if bullets:
            return _unique(bullets)[:12]

        # Handle prose-only Gemini responses as a sequence of actionable steps.
        sentences = _sentences(text)
        cleaned = []
        for sentence in sentences:
            sentence = sentence.strip().strip("-•* ")
            if not sentence or re.match(r"^(here('?s| is)|try these steps|possible solution)\b", sentence, re.I):
                continue
            if re.match(r"^(note|if that doesn't work|let me know)\b", sentence, re.I):
                continue
            cleaned.append(redact_personal_data(sentence))
        return _unique(cleaned)[:12]

    @staticmethod
    def _merge_json_lists(existing_json: str | None, additions: list[str]) -> str:
        try:
            current = json.loads(existing_json or "[]")
        except (TypeError, json.JSONDecodeError):
            current = []
        if not isinstance(current, list):
            current = []
        return json.dumps(_unique([*current, *additions]), ensure_ascii=False)


def redact_personal_data(text: str) -> str:
    """Remove common direct identifiers while retaining technical context."""
    value = text or ""
    original = value
    for pattern, replacement in (
        (_URL_RE, "[redacted link]"), (_EMAIL_RE, "[redacted email]"),
        (_ACCOUNT_RE, "[redacted identifier]"), (_PHONE_RE, "[redacted phone]"),
        (_LONG_NUMBER_RE, "[redacted number]"), (_NAME_LABEL_RE, "[redacted name]"),
        (_NAME_INTRO_RE, "[redacted name]"), (_NAME_HERE_RE, "[redacted name]"),
    ):
        value = pattern.sub(replacement, value)
    if value != original:
        logger.debug("extractor.personal_data_redacted input_chars=%d output_chars=%d", len(original), len(value))
    return re.sub(r"\s+", " ", value).strip()


def extract_environment(text: str) -> dict[str, str]:
    environment: dict[str, str] = {}
    for key, pattern in _OS_PATTERNS:
        match = pattern.search(text or "")
        if match:
            environment["os"] = match.group(0).strip()
            break

    for pattern in _DEVICE_PATTERNS:
        match = pattern.search(text or "")
        if match:
            environment["device"] = match.group(0).strip()
            break

    lowered = (text or "").lower()
    for app_name in sorted(_KNOWN_APPS, key=len, reverse=True):
        if app_name.lower() in lowered:
            environment["app"] = app_name
            break

    app_version = re.search(
        r"(?:app|application|version)\s*(?:version)?\s*[:=v]?\s*(\d+(?:\.\d+){1,3})\b",
        text or "",
        re.I,
    )
    if app_version:
        environment["app_version"] = app_version.group(1)
    return environment


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+|\n+", text or "") if part.strip()]


def _unique(items) -> list[str]:
    result = []
    seen = set()
    for item in items:
        if not isinstance(item, str):
            continue
        cleaned = item.strip()
        key = cleaned.casefold()
        if cleaned and key not in seen:
            seen.add(key)
            result.append(cleaned)
    return result
