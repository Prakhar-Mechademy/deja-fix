import json
import math
import re
from typing import Any

from backend.db import (
    add_message,
    create_session,
    get_or_create_user,
    get_prior_user_history,
    get_session,
    get_session_messages,
    get_user_by_id,
    insert_solution,
    list_solutions,
    list_solutions_by_session,
    merge_solution_duplicate,
    update_session,
)
from backend.gemini_client import call_gemini_json, call_gemini_text, embed_text
from backend.pii import assert_pii_free, redact_pii, redact_solution_fields
from backend.rate_limit import enforce_escalation_rate_limit, enforce_message_rate_limit

# Tuned retrieval and deduplication thresholds
STAGE1_RECALL_THRESHOLD = 0.50
DEDUP_MERGE_THRESHOLD = 0.78


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def build_isolated_greeting(user: dict[str, Any], current_session_id: int | None = None) -> str:
    prior = get_prior_user_history(int(user["id"]), current_session_id)
    if not prior:
        return (
            "Hello. I am the NimbusSync support specialist. Describe the issue you are seeing "
            "along with your device, operating system, and app version."
        )

    latest_with_context = next(
        (
            entry
            for entry in prior
            if entry["session"].get("memory_summary")
            or entry.get("linked_solution_symptoms")
            or entry.get("first_user_message")
        ),
        None,
    )
    if not latest_with_context:
        return "Welcome back to NimbusSync support. What can I help you troubleshoot today?"

    raw_topic = (
        latest_with_context["session"].get("memory_summary")
        or latest_with_context.get("linked_solution_symptoms")
        or latest_with_context.get("first_user_message")
        or "your earlier support request"
    )
    clean_topic = redact_pii(str(raw_topic), [user["display_name"], user["handle"]])
    clean_topic = re.sub(r"\s+", " ", clean_topic).strip()
    short_topic = f"{clean_topic[:87].strip()}..." if len(clean_topic) > 90 else clean_topic

    return (
        f'Welcome back. Last time you reached out about: "{short_topic}". '
        "Is this follow-up related to that, or are you seeing a new NimbusSync issue today?"
    )


def start_user_session(
    user_handle: str,
    display_name: str | None = None,
    title: str | None = None,
) -> dict[str, Any]:
    user = get_or_create_user(user_handle, display_name)
    session = create_session(int(user["id"]), title or "Support session")
    greeting = build_isolated_greeting(user, int(session["id"]))
    add_message(int(session["id"]), int(user["id"]), "assistant", "greeting", greeting)
    return {
        "user": user,
        "session": get_session(int(session["id"])),
        "messages": get_session_messages(int(session["id"])),
    }


_BYPASS_PATTERNS = [
    re.compile(r"forget\s+(?:all\s+)?your\s+rules", re.IGNORECASE),
    re.compile(r"ignore\s+(?:all\s+)?(?:previous|prior|your)\s+(?:rules|instructions|guidelines)", re.IGNORECASE),
    re.compile(r"answer\s+from\s+your\s+own\s+knowledge", re.IGNORECASE),
    re.compile(r"bypass\s+(?:the\s+)?knowledge\s+hub", re.IGNORECASE),
    re.compile(r"disregard\s+your\s+system\s+prompt", re.IGNORECASE),
    re.compile(r"do\s+not\s+use\s+(?:the\s+)?knowledge\s+hub", re.IGNORECASE),
]


def is_persona_bypass_attempt(text: str) -> bool:
    return any(rx.search(text) for rx in _BYPASS_PATTERNS)


CONFIRMATION_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "outcome": {
            "type": "STRING",
            "description": "One of: confirmed, failed, ongoing",
        },
        "reason": {
            "type": "STRING",
            "description": "Short rationale.",
        },
    },
    "required": ["outcome", "reason"],
}


def detect_confirmation_status(
    session_id: int,
    user_id: int,
    messages: list[dict[str, Any]],
) -> str:
    """
    Runs ONLY after a fix has actually been delivered (route in ('hub', 'escalation'))
    and skips turns immediately following a clarifying question so Session 2 pays zero
    confirmation-check overhead during clarification.
    """
    assistant_msgs = [m for m in messages if m["role"] == "assistant" and m["route"] != "greeting"]
    if not assistant_msgs:
        return "ongoing"

    last_assistant = assistant_msgs[-1]
    if last_assistant["route"] == "clarify":
        return "ongoing"

    has_delivered_fix = any(m["route"] in ("hub", "escalation") for m in assistant_msgs)
    if not has_delivered_fix:
        return "ongoing"

    last_user = next((m for m in reversed(messages) if m["role"] == "user"), None)
    if not last_user:
        return "ongoing"

    lower = last_user["content"].strip().lower()
    if re.match(
        r"^(?:yes|yep|yeah)?[,!.\s]*(?:that\s+(?:fixed|solved|worked)|it\s+worked|it's\s+working|working\s+now|problem\s+solved|issue\s+resolved|all\s+good|fixed\s+it)[!.]*$",
        lower,
    ):
        return "confirmed"
    if re.match(
        r"^(?:no|nope)?[,!.\s]*(?:that\s+did\s*not\s+work|didn't\s+work|still\s+broken|not\s+working|no\s+luck|didn't\s+fix|still\s+stuck)[!.]*$",
        lower,
    ):
        return "failed"

    recent_snippet = "\n\n".join(
        f"{m['role'].upper()} ({m['route']}): {m['content']}" for m in messages[-4:]
    )
    res = call_gemini_json(
        session_id=session_id,
        user_id=user_id,
        call_type="confirmation_check",
        low_thinking=True,
        system_instruction=(
            "Classify whether the customer's latest message confirms that the delivered fix "
            'solved their problem ("confirmed"), states that the fix did not work ("failed"), '
            'or continues troubleshooting a new/ongoing question ("ongoing"). Return JSON only.'
        ),
        contents=f"Recent conversation:\n{recent_snippet}",
        response_schema=CONFIRMATION_SCHEMA,
    )
    outcome = res["data"].get("outcome")
    if outcome in ("confirmed", "failed"):
        return str(outcome)
    return "ongoing"


EXTRACTOR_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "session_topic_summary": {
            "type": "STRING",
            "description": "Short 5-10 word PII-free summary of the issue(s) for future returning-user greetings.",
        },
        "solutions": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "symptoms": {
                        "type": "STRING",
                        "description": "Generalized, PII-free description of the problem symptoms and root cause.",
                    },
                    "device": {
                        "type": "STRING",
                        "description": 'Device model or type if mentioned, otherwise "Unspecified device".',
                    },
                    "os": {
                        "type": "STRING",
                        "description": 'Operating system and version if mentioned, otherwise "Unspecified OS".',
                    },
                    "app_version": {
                        "type": "STRING",
                        "description": 'NimbusSync app version if mentioned, otherwise "Unspecified version".',
                    },
                    "fix_steps": {
                        "type": "ARRAY",
                        "items": {"type": "STRING"},
                        "description": "Ordered, concrete troubleshooting steps that resolved the problem.",
                    },
                    "what_did_not_work": {
                        "type": "ARRAY",
                        "items": {"type": "STRING"},
                        "description": "Steps or workarounds mentioned in the transcript that failed or did not help.",
                    },
                },
                "required": [
                    "symptoms",
                    "device",
                    "os",
                    "app_version",
                    "fix_steps",
                    "what_did_not_work",
                ],
            },
        },
    },
    "required": ["session_topic_summary", "solutions"],
}

DUPLICATE_CHECK_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "isSameProblemAndFix": {"type": "BOOLEAN"},
        "stepPolicy": {
            "type": "STRING",
            "description": 'Either "keep_existing" (canonical steps remain best) or "use_new" (new steps are clearly more complete/accurate).',
        },
        "reason": {"type": "STRING"},
    },
    "required": ["isSameProblemAndFix", "stepPolicy", "reason"],
}


def run_extractor_from_store(session_id: int) -> dict[str, Any]:
    """
    Idempotent, store-backed Extractor:
    - Reads the full transcript from SQLite `messages` table (not an in-memory variable).
    - Tracks `last_extracted_message_id` and prior `solutions` for `session_id` so re-running
      on later confirmations in the same session never re-extracts earlier solved issues.
    - Runs deterministic regex PII scrubbing AND post-redaction `assert_pii_free` verification.
    - Deduplicates via similarity threshold (>= 0.78) + Stage 2 verifier agreement, never
      incrementing `confirmation_count` from the same `source_session_id`.
    """
    session = get_session(session_id)
    if not session:
        raise ValueError(f"Session {session_id} not found")
    user = get_user_by_id(int(session["user_id"]))
    if not user:
        raise ValueError(f"User {session['user_id']} not found")

    transcript_rows = get_session_messages(session_id)
    last_extracted_id = int(session.get("last_extracted_message_id") or 0)
    unprocessed_rows = [
        m for m in transcript_rows if int(m["id"]) > last_extracted_id and m["route"] != "greeting"
    ]
    if not unprocessed_rows:
        return {
            "saved_solutions": [],
            "merged_count": 0,
            "created_count": 0,
            "memory_summary": session.get("memory_summary") or "",
        }

    transcript_text = "\n\n".join(
        f"[{m['role'].upper()} / {m['route']}]: {m['content']}"
        for m in transcript_rows
        if m["route"] != "greeting"
    )
    already_in_session = list_solutions_by_session(session_id)
    already_summary = (
        "; ".join(s["symptoms"] for s in already_in_session) if already_in_session else "None"
    )

    res = call_gemini_json(
        session_id=session_id,
        user_id=int(user["id"]),
        call_type="extractor",
        low_thinking=True,
        system_instruction=(
            "You are the Déjà Fix Knowledge Extractor.\n"
            "Read the persisted support transcript and extract ONLY NEWLY confirmed solved problems "
            "that were NOT already extracted in this session.\n"
            "Rules:\n"
            "1. Only include problems explicitly confirmed as solved by the customer. Never extract unconfirmed or failed attempts.\n"
            "2. Do NOT re-extract problems listed under 'Already extracted in this session'.\n"
            "3. A single session can contain multiple distinct confirmed problems—extract each newly solved one separately.\n"
            "4. GENERALIZE BEFORE SAVING: Remove ALL personal data—names, emails, URLs/links, order numbers, account IDs, ticket numbers, and phone numbers.\n"
            "5. KEEP THE ENVIRONMENT: Preserve device, operating system, and app version accurately.\n"
            '6. Include concrete "fix_steps" and any "what_did_not_work" steps mentioned (empty array if none failed).'
        ),
        contents=(
            f"Already extracted in this session: {already_summary}\n\n"
            f"Transcript for session #{session_id}:\n\n{transcript_text}"
        ),
        response_schema=EXTRACTOR_SCHEMA,
    )

    data = res["data"]
    extra_names = [user["display_name"], user["handle"]]
    clean_summary = redact_pii(
        str(data.get("session_topic_summary") or session.get("memory_summary") or "NimbusSync support issue"),
        extra_names,
    )

    saved_solutions: list[dict[str, Any]] = []
    merged_count = 0
    created_count = 0

    for raw_sol in data.get("solutions") or []:
        if not raw_sol.get("symptoms") or not raw_sol.get("fix_steps"):
            continue

        clean_sol = redact_solution_fields(raw_sol, extra_names)
        assert_pii_free(clean_sol, extra_names)

        embed_input = (
            f"{clean_sol['symptoms']}\n"
            f"Environment: {clean_sol['device']}, {clean_sol['os']}, {clean_sol['app_version']}\n"
            f"Fix: {' '.join(clean_sol['fix_steps'])}"
        )
        vector = embed_text(session_id, int(user["id"]), embed_input)

        # Deduplication: similarity threshold >= 0.78 PLUS Stage 2 verifier agreement
        existing_verified = list_solutions(include_unverified=False)
        best_candidate: dict[str, Any] | None = None
        best_sim = 0.0
        for candidate in existing_verified:
            sim = _cosine_similarity(vector, candidate["embedding"])
            if sim > best_sim:
                best_sim = sim
                best_candidate = candidate

        should_merge = False
        replacement_steps: list[str] | None = None

        if best_candidate is not None and best_sim >= DEDUP_MERGE_THRESHOLD:
            # Guard: if matched against the SAME session, treat as idempotent no-op without calling verifier or incrementing count
            if int(best_candidate["source_session_id"]) == int(session_id):
                continue

            dup_res = call_gemini_json(
                session_id=session_id,
                user_id=int(user["id"]),
                call_type="duplicate_verify",
                low_thinking=True,
                system_instruction=(
                    "Determine whether the newly extracted solution solves the same underlying fault "
                    "as the existing verified solution. If yes, choose stepPolicy: 'keep_existing' to "
                    "preserve the canonical steps, or 'use_new' ONLY if the new steps are strictly clearer and more complete."
                ),
                contents=(
                    f"Existing Solution:\nSymptoms: {best_candidate['symptoms']}\n"
                    f"Fix: {'; '.join(best_candidate['fix_steps'])}\n\n"
                    f"New Solution:\nSymptoms: {clean_sol['symptoms']}\n"
                    f"Fix: {'; '.join(clean_sol['fix_steps'])}"
                ),
                response_schema=DUPLICATE_CHECK_SCHEMA,
            )
            should_merge = bool(dup_res["data"].get("isSameProblemAndFix"))
            if should_merge and dup_res["data"].get("stepPolicy") == "use_new":
                replacement_steps = clean_sol["fix_steps"]

        if should_merge and best_candidate is not None:
            merged, incremented = merge_solution_duplicate(
                existing_id=int(best_candidate["id"]),
                confirming_session_id=session_id,
                extra_what_did_not_work=clean_sol["what_did_not_work"],
                replacement_fix_steps=replacement_steps,
            )
            if incremented:
                saved_solutions.append(merged)
                merged_count += 1
        else:
            created = insert_solution(
                source_session_id=session_id,
                verified=True,
                symptoms=clean_sol["symptoms"],
                device=clean_sol["device"] or "Unspecified device",
                os_name=clean_sol["os"] or "Unspecified OS",
                app_version=clean_sol["app_version"] or "Unspecified version",
                fix_steps=clean_sol["fix_steps"],
                what_did_not_work=clean_sol["what_did_not_work"],
                embedding=vector,
            )
            saved_solutions.append(created)
            created_count += 1

    max_msg_id = int(transcript_rows[-1]["id"])
    update_session(
        session_id,
        title=clean_summary or session["title"],
        status="resolved",
        memory_summary=clean_summary,
        update_memory_summary=True,
        last_extracted_message_id=max_msg_id,
    )

    return {
        "saved_solutions": saved_solutions,
        "merged_count": merged_count,
        "created_count": created_count,
        "memory_summary": clean_summary,
    }


HUB_VERIFY_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "matchedSolutionId": {
            "type": "INTEGER",
            "description": "ID of the matching verified solution, or 0 if no candidate matches the same root problem.",
        },
        "needsClarification": {
            "type": "BOOLEAN",
            "description": (
                "True if the problem matches a candidate solution, but key environment (device/OS/app version) "
                "or symptom details are ambiguous and have not yet been clarified in this session."
            ),
        },
        "clarifyingQuestion": {
            "type": "STRING",
            "description": "One concise, calm clarifying question if needsClarification is true; otherwise empty string.",
        },
        "reason": {
            "type": "STRING",
            "description": "Brief explanation for why the candidate matched or was rejected as a negative match.",
        },
    },
    "required": ["matchedSolutionId", "needsClarification", "clarifyingQuestion", "reason"],
}


def search_knowledge_hub(
    session_id: int,
    user_id: int,
    messages: list[dict[str, Any]],
) -> dict[str, Any]:
    # Strictly retrieve ONLY verified solutions
    verified_solutions = list_solutions(include_unverified=False)
    if not verified_solutions:
        return {"matched_solution": None, "needs_clarification": False, "clarifying_question": None}

    user_turns = "\n".join(m["content"] for m in messages if m["role"] == "user")
    query_vector = embed_text(session_id, user_id, user_turns)

    ranked = sorted(
        (
            (sol, _cosine_similarity(query_vector, sol["embedding"]))
            for sol in verified_solutions
        ),
        key=lambda item: item[1],
        reverse=True,
    )
    top_candidates = [(sol, sim) for sol, sim in ranked if sim >= STAGE1_RECALL_THRESHOLD][:3]
    if not top_candidates:
        return {"matched_solution": None, "needs_clarification": False, "clarifying_question": None}

    already_clarified = any(m["role"] == "assistant" and m["route"] == "clarify" for m in messages)

    candidate_text = "\n\n".join(
        f"Candidate ID {sol['id']} (similarity {sim:.3f}):\n"
        f"- Symptoms: {sol['symptoms']}\n"
        f"- Environment: device={sol['device']}, os={sol['os']}, app_version={sol['app_version']}\n"
        f"- Fix summary: {' -> '.join(sol['fix_steps'])}"
        for sol, sim in top_candidates
    )

    recent_dialogue = "\n".join(
        f"{m['role'].upper()}: {m['content']}"
        for m in [msg for msg in messages if msg["route"] != "greeting"][-6:]
    )

    res = call_gemini_json(
        session_id=session_id,
        user_id=user_id,
        call_type="hub_verify",
        low_thinking=True,
        system_instruction=(
            "You are the Knowledge Hub Retrieval Gate for NimbusSync support.\n"
            "Compare the customer's reported issue against the candidate verified solutions.\n"
            "CRITICAL RULES:\n"
            "1. Match problems described in DIFFERENT words to the right solution if the underlying technical fault is the same.\n"
            '2. NEGATIVE MATCH REJECTION: If the customer\'s issue merely shares keywords (like "sync", "NimbusSync", "app", "file") '
            "with a candidate but describes a DIFFERENT root fault or error (e.g., 401 auth/2FA expiry vs. stuck local index queue, "
            "launch crash vs. upload stall, disk full vs. SSL proxy), you MUST return matchedSolutionId: 0.\n"
            "3. CLARIFYING QUESTION: If a candidate matches AND the customer has NOT yet provided key details "
            f"(device/OS/app version or exact symptom variant) AND alreadyClarified={str(already_clarified).lower()} is false, "
            "set needsClarification: true and ask one calm, concise clarifying question. "
            "If alreadyClarified is true or the user already gave sufficient context, set needsClarification: false."
        ),
        contents=(
            f"alreadyClarified={str(already_clarified).lower()}\n\n"
            f"Customer conversation:\n{recent_dialogue}\n\n"
            f"Candidate Verified Solutions:\n{candidate_text}"
        ),
        response_schema=HUB_VERIFY_SCHEMA,
    )

    data = res["data"]
    matched_id = int(data.get("matchedSolutionId") or 0)
    if matched_id == 0:
        return {"matched_solution": None, "needs_clarification": False, "clarifying_question": None}

    matched = next((s for s in verified_solutions if int(s["id"]) == matched_id), None)
    if matched is None:
        return {"matched_solution": None, "needs_clarification": False, "clarifying_question": None}

    needs_clarify = (
        bool(data.get("needsClarification"))
        and (not already_clarified)
        and bool(data.get("clarifyingQuestion"))
    )
    clarifying_q = str(data.get("clarifyingQuestion") or "").strip() or None
    return {
        "matched_solution": matched,
        "needs_clarification": needs_clarify,
        "clarifying_question": clarifying_q,
    }


def generate_persona_grounded_reply(
    session_id: int,
    user_id: int,
    solution: dict[str, Any],
) -> str:
    """
    Structural Persona Containment:
    The Persona LLM call ONLY ever receives a matched verified `solution` object—never raw
    ungrounded user instructions or open-ended troubleshooting prompts. If there is no match,
    this function is never called.
    """
    steps_formatted = "\n".join(f"{idx + 1}. {step}" for idx, step in enumerate(solution["fix_steps"]))
    failed_line = (
        f"What did not work: {'; '.join(solution['what_did_not_work'])}"
        if solution.get("what_did_not_work")
        else ""
    )

    res = call_gemini_text(
        session_id=session_id,
        user_id=user_id,
        call_type="persona_reply",
        low_thinking=True,
        system_instruction=(
            "You are the Déjà Fix Persona Agent for NimbusSync support.\n"
            "Fixed tone: Calm, concise, matter-of-fact support specialist.\n"
            "STRICT GROUNDING RULES:\n"
            "- Format and present ONLY the verified Knowledge Hub solution below.\n"
            "- Do NOT invent additional troubleshooting steps or draw from outside knowledge.\n"
            "- Ask the customer to confirm whether these steps resolve their issue."
        ),
        contents=(
            f"Verified Knowledge Hub Solution #{solution['id']}:\n"
            f"Symptoms: {solution['symptoms']}\n"
            f"Environment: {solution['device']} | {solution['os']} | {solution['app_version']}\n"
            f"Verified Fix Steps:\n{steps_formatted}\n"
            f"{failed_line}"
        ),
    )
    return res["text"]


def run_backend_escalation(session_id: int, user_id: int, messages: list[dict[str, Any]]) -> str:
    enforce_escalation_rate_limit(user_id)
    conversation_contents = [
        {
            "role": "user" if m["role"] == "user" else "model",
            "parts": [{"text": m["content"]}],
        }
        for m in messages
        if m["route"] != "greeting"
    ]
    res = call_gemini_text(
        session_id=session_id,
        user_id=user_id,
        call_type="escalation",
        contents=conversation_contents,
    )
    update_session(session_id, status="escalated")
    return res["text"]


def handle_chat_turn(
    user_handle: str,
    message: str,
    display_name: str | None = None,
    session_id: int | None = None,
) -> dict[str, Any]:
    trimmed = message.strip()
    if not trimmed:
        raise ValueError("Message cannot be empty.")

    user = get_or_create_user(user_handle, display_name)
    user_id = int(user["id"])

    enforce_message_rate_limit(user_id)

    session = get_session(session_id) if session_id is not None else None
    if session is not None and int(session["user_id"]) != user_id:
        raise ValueError("Session does not belong to the active user.")

    if session is None:
        title_seed = redact_pii(trimmed, [user["display_name"], user["handle"]])[:48]
        session = create_session(user_id, title_seed or "Support session")
        greeting = build_isolated_greeting(user, int(session["id"]))
        add_message(int(session["id"]), user_id, "assistant", "greeting", greeting)

    sid = int(session["id"])
    add_message(sid, user_id, "user", "user", trimmed)
    full_history = get_session_messages(sid)

    # 1. Structural Persona Containment: short-circuit rule-override injections BEFORE any LLM call
    if is_persona_bypass_attempt(trimmed):
        decline_msg = (
            "I cannot forget my support rules or answer from unverified knowledge. "
            "I only provide verified solutions from the Knowledge Hub, or escalate genuine "
            "NimbusSync support issues to the backend model."
        )
        add_message(sid, user_id, "assistant", "declined", decline_msg)
        return {
            "user": user,
            "session": get_session(sid),
            "messages": get_session_messages(sid),
            "outcome": "declined",
        }

    # Short-circuit greetings and trivial conversational turns without invoking Gemini.
    normalized = re.sub(r"[^a-z0-9]+", " ", trimmed.lower()).strip()
    if normalized in {"yo", "hi", "hey", "hello", "hiya", "good morning", "good afternoon", "good evening", "thanks", "thank you"}:
        preset = (
            "Hello. Please describe the NimbusSync issue you're seeing, including your device, "
            "operating system, app version, and the exact symptom or error."
            if normalized not in {"thanks", "thank you"}
            else "You're welcome. Let me know if you have another NimbusSync issue to troubleshoot."
        )
        add_message(sid, user_id, "assistant", "greeting", preset)
        return {
            "user": user,
            "session": get_session(sid),
            "messages": get_session_messages(sid),
            "outcome": "greeting",
        }

    # 2. Confirmation check (runs ONLY after a fix has actually been delivered)
    confirmation = detect_confirmation_status(sid, user_id, full_history)
    if confirmation == "confirmed":
        extracted = run_extractor_from_store(sid)
        ack_msg = (
            "Glad that resolved it. I have saved this verified fix to the Knowledge Hub "
            "(with personal details removed) so future sessions can resolve it directly."
            if extracted["saved_solutions"]
            else "Glad to hear your issue is resolved. Let me know if anything else comes up."
        )
        add_message(sid, user_id, "assistant", "hub", ack_msg)
        return {
            "user": user,
            "session": get_session(sid),
            "messages": get_session_messages(sid),
            "outcome": "confirmed",
            "extractedSolutionsCount": len(extracted["saved_solutions"]),
        }

    if confirmation == "failed":
        update_session(sid, status="unconfirmed")
        add_message(
            sid,
            user_id,
            "assistant",
            "hub",
            "Understood—that fix did not resolve the issue, so I will not mark it as verified. "
            "Let me escalate the full conversation to try a different troubleshooting approach.",
        )
        escalation_reply = run_backend_escalation(sid, user_id, get_session_messages(sid))
        add_message(sid, user_id, "assistant", "escalation", escalation_reply)
        return {
            "user": user,
            "session": get_session(sid),
            "messages": get_session_messages(sid),
            "outcome": "failed_re_escalated",
        }

    # 3. Search Knowledge Hub (verified solutions only)
    hub_result = search_knowledge_hub(sid, user_id, full_history)
    if hub_result["matched_solution"] is not None:
        if hub_result["needs_clarification"] and hub_result["clarifying_question"]:
            add_message(sid, user_id, "assistant", "clarify", hub_result["clarifying_question"])
            return {
                "user": user,
                "session": get_session(sid),
                "messages": get_session_messages(sid),
                "outcome": "clarify",
            }

        grounded_reply = generate_persona_grounded_reply(
            sid,
            user_id,
            hub_result["matched_solution"],
        )
        add_message(sid, user_id, "assistant", "hub", grounded_reply)
        return {
            "user": user,
            "session": get_session(sid),
            "messages": get_session_messages(sid),
            "outcome": "hub",
        }

    # 4. No verified Hub match -> templated disclosure (0 Persona LLM tokens) + Escalation
    add_message(
        sid,
        user_id,
        "assistant",
        "hub",
        "I do not have a verified solution for this issue in the Knowledge Hub yet. Escalating the conversation now.",
    )
    escalation_reply = run_backend_escalation(sid, user_id, get_session_messages(sid))
    add_message(sid, user_id, "assistant", "escalation", escalation_reply)

    return {
        "user": user,
        "session": get_session(sid),
        "messages": get_session_messages(sid),
        "outcome": "escalation",
    }


def record_explicit_session_feedback(session_id: int, outcome: str) -> dict[str, Any]:
    session = get_session(session_id)
    if not session:
        raise ValueError("Session not found.")
    user = get_user_by_id(int(session["user_id"]))
    if not user:
        raise ValueError("User not found.")
    user_id = int(user["id"])

    enforce_message_rate_limit(user_id)

    if outcome == "confirmed":
        add_message(
            session_id,
            user_id,
            "user",
            "user",
            "Confirmed: that fixed the problem and NimbusSync is working properly now.",
        )
        extracted = run_extractor_from_store(session_id)
        count = len(extracted["saved_solutions"])
        add_message(
            session_id,
            user_id,
            "assistant",
            "hub",
            f"Confirmed. Extracted {count} generalized solution(s) into the Knowledge Hub from this session transcript.",
        )
        return {
            "session": get_session(session_id),
            "messages": get_session_messages(session_id),
            "extractedSolutionsCount": count,
        }

    add_message(session_id, user_id, "user", "user", "The suggested fix did not work for me.")
    update_session(session_id, status="unconfirmed")
    add_message(
        session_id,
        user_id,
        "assistant",
        "hub",
        "Noted—nothing was saved to the Knowledge Hub since the fix did not work.",
    )
    return {
        "session": get_session(session_id),
        "messages": get_session_messages(session_id),
        "extractedSolutionsCount": 0,
    }


def run_three_session_scenario() -> dict[str, Any]:
    # Session 1: Customer A escalation & extraction (realistic multi-detail report with PII)
    s1_start = start_user_session(
        user_handle="customer_a",
        display_name="Priya Sharma",
        title="Customer A — Sync queue stuck",
    )
    s1_id = int(s1_start["session"]["id"])

    handle_chat_turn(
        user_handle="customer_a",
        display_name="Priya Sharma",
        session_id=s1_id,
        message=(
            "Hi, I am Priya Sharma (priya.sharma@example.com, order #NS-984210, "
            "portal https://portal.nimbus.example/u/priya). My NimbusSync desktop app v4.2.1 "
            'on Windows 11 (Dell XPS 15) has been stuck at "Preparing sync..." for two hours and '
            "won't upload my design folder. I already tried restarting my Wi-Fi router and rebooting, "
            "and neither changed anything."
        ),
    )

    handle_chat_turn(
        user_handle="customer_a",
        display_name="Priya Sharma",
        session_id=s1_id,
        message=(
            "Thanks! Removing the stale local index lock file and restarting NimbusSync worked—"
            "all my files are syncing normally now."
        ),
    )

    # Session 2: Customer B reworded problem -> Clarifying question -> Hub reuse
    s2_start = start_user_session(
        user_handle="customer_b",
        display_name="Marcus Chen",
        title="Customer B — Files not uploading",
    )
    s2_id = int(s2_start["session"]["id"])

    s2_turn1 = handle_chat_turn(
        user_handle="customer_b",
        display_name="Marcus Chen",
        session_id=s2_id,
        message=(
            "Hey, my cloud drive client refuses to upload any new files and just hangs "
            "indefinitely before transferring anything."
        ),
    )

    if s2_turn1["outcome"] == "clarify":
        handle_chat_turn(
            user_handle="customer_b",
            display_name="Marcus Chen",
            session_id=s2_id,
            message=(
                "I am on a MacBook Pro running macOS 15.1 with NimbusSync v4.2.1, "
                "and the status indicator just stays stuck on preparing."
            ),
        )

    # Session 3: Customer A returns -> Memory isolation
    s3_start = start_user_session(
        user_handle="customer_a",
        display_name="Priya Sharma",
        title="Customer A — Returning check-in",
    )
    s3_id = int(s3_start["session"]["id"])

    s1_updated = get_session(s1_id) or {}
    s2_updated = get_session(s2_id) or {}
    s2_messages = get_session_messages(s2_id)
    s3_greeting = (s3_start["messages"][0]["content"]) if s3_start["messages"] else ""
    verified_solutions = list_solutions(include_unverified=False)

    s1_tokens = int(s1_updated.get("tokens_in_total") or 0) + int(s1_updated.get("tokens_out_total") or 0)
    s2_tokens = int(s2_updated.get("tokens_in_total") or 0) + int(s2_updated.get("tokens_out_total") or 0)

    # Post-write PII assertion across all persisted solutions
    for sol in verified_solutions:
        assert_pii_free(sol, ["Priya Sharma", "customer_a", "Marcus Chen", "customer_b"])

    serialized_solutions = json.dumps(verified_solutions).lower()
    pii_stripped = (
        "priya" not in serialized_solutions
        and "sharma" not in serialized_solutions
        and "priya.sharma@example.com" not in serialized_solutions
        and "ns-984210" not in serialized_solutions
        and "portal.nimbus.example" not in serialized_solutions
    )

    env_preserved = any(
        "windows" in s["os"].lower()
        and "4.2.1" in s["app_version"]
        and "dell" in s["device"].lower()
        for s in verified_solutions
    )

    s2_used_hub_no_escalation = (
        any(m["route"] == "hub" for m in s2_messages)
        and not any(m["route"] == "escalation" for m in s2_messages)
    )

    s3_isolated_to_a = (
        "marcus" not in s3_greeting.lower()
        and "macbook" not in s3_greeting.lower()
        and "macos" not in s3_greeting.lower()
    )

    assertions = [
        {
            "name": "Session 1 created verified Solution 1 via Escalation + Extractor",
            "passed": len(verified_solutions) >= 1,
            "detail": f"Verified solutions stored: {len(verified_solutions)}",
        },
        {
            "name": "Generalization stripped PII (name, email, order #, link) & kept device/OS/version",
            "passed": pii_stripped and env_preserved,
            "detail": f"piiStripped={pii_stripped}, envPreserved={env_preserved}",
        },
        {
            "name": "Session 2 reused Knowledge Hub with zero Escalation",
            "passed": s2_used_hub_no_escalation,
            "detail": "Routes in Session 2: " + ", ".join(m["route"] for m in s2_messages),
        },
        {
            "name": "Session 2 Hub Reuse cost fewer tokens than Session 1 Escalation",
            "passed": s2_tokens < s1_tokens,
            "detail": (
                f"Session 1: {s1_tokens} tokens (${float(s1_updated.get('cost_total_usd') or 0):.6f}) "
                f"vs Session 2: {s2_tokens} tokens (${float(s2_updated.get('cost_total_usd') or 0):.6f})"
            ),
        },
        {
            "name": "Session 3 greeted Customer A with isolated history only",
            "passed": s3_isolated_to_a and ("welcome back" in s3_greeting.lower()),
            "detail": f'Greeting: "{s3_greeting}"',
        },
    ]

    return {
        "session1": {
            "id": s1_id,
            "tokens": s1_tokens,
            "costUsd": float(s1_updated.get("cost_total_usd") or 0),
        },
        "session2": {
            "id": s2_id,
            "tokens": s2_tokens,
            "costUsd": float(s2_updated.get("cost_total_usd") or 0),
        },
        "session3": {
            "id": s3_id,
            "greeting": s3_greeting,
        },
        "solutionsCount": len(verified_solutions),
        "assertions": assertions,
    }
