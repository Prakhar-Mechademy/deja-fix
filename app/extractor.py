"""Extract and merge confirmed fixes from ended sessions."""

import sys

import numpy as np

from app import config
from app import db
from app import llm
from app.retrieval import embed_solution_text
from app.scrub import scrub_text, scrub_solution


EXTRACTOR_SYSTEM = (
    "You extract resolved support problems from a chat transcript. Return ONLY JSON: {\"topic_summary\": string, \"solutions\": [ ... ]}. "
    "topic_summary is a 3-8 word lowercase description of the customer's main problem, e.g. 'your notes app not syncing'. It must contain no names or personal data. "
    "Each solution object: {\"title\": short generic title, \"symptoms\": [strings], \"environment\": {\"device\": string|null, \"os\": string|null, \"app_version\": string|null}, \"fix_steps\": [ordered, generic, imperative steps that actually fixed it], \"failed_attempts\": [things tried that did NOT work], \"confirmed\": boolean, \"confirmation_message_id\": integer|null}. "
    "RULES: Include one solution per distinct problem; a session may contain several. 'confirmed' is true ONLY if the CUSTOMER (role USER) clearly states after the fix was suggested that it now works / is resolved. 'I'll try it later', 'not sure', 'didn't work', no reply, or an assistant claim are NOT confirmation: set confirmed=false. "
    "confirmation_message_id must be the id in brackets of the customer's confirming message. GENERALIZE: remove names, email addresses, links, phone numbers, order/ticket/account numbers and any other personal data from every field. "
    "KEEP the technical environment (device model, operating system and version, app version). Write fix steps so they work for any customer with the same environment. "
    "If there are no solved problems return an empty solutions list."
)


def _string_list(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if isinstance(item, (str, int, float)) and str(item).strip()]


def _normalize_solution(raw: dict) -> dict | None:
    if not isinstance(raw, dict):
        return None
    title = str(raw.get("title") or "").strip()
    fix_steps = _string_list(raw.get("fix_steps"))
    if not title or not fix_steps:
        return None

    source_environment = raw.get("environment")
    if not isinstance(source_environment, dict):
        source_environment = {}
    environment = {
        "device": source_environment.get("device"),
        "os": source_environment.get("os"),
        "app_version": source_environment.get("app_version"),
    }
    for key, value in list(environment.items()):
        if value is not None:
            environment[key] = str(value)

    return {
        "title": title,
        "symptoms": _string_list(raw.get("symptoms")),
        "environment": environment,
        "fix_steps": fix_steps,
        "failed_attempts": _string_list(raw.get("failed_attempts")),
        "confirmed": raw.get("confirmed") is True,
        "confirmation_message_id": raw.get("confirmation_message_id"),
    }


def _judge_same_fault(session_id: int, user_id: int, first: dict, second: dict) -> bool:
    payload = {
        "first": {
            "title": first.get("title", ""),
            "symptoms": first.get("symptoms", []),
            "environment": first.get("environment", {}),
        },
        "second": {
            "title": second.get("title", ""),
            "symptoms": second.get("symptoms", []),
            "environment": second.get("environment", {}),
        },
    }
    result = llm.generate(
        session_id,
        user_id,
        "judge",
        [{"role": "user", "text": "Do these describe the same underlying fault? " + str(payload)}],
        system="Return ONLY JSON: {\"same_fault\": boolean}. Ignore OS/device differences and compare the underlying fault.",
        json_mode=True,
        max_output_tokens=128,
    )
    if not result.ok or not isinstance(result.data, dict):
        return False
    return result.data.get("same_fault") is True


def run_extraction(session_id: int) -> list[int]:
    """Extract only customer-confirmed solutions and merge duplicates."""
    messages = db.get_messages(session_id)
    session = db.get_session(session_id)
    if not session:
        return []

    user_messages = [message for message in messages if message["role"] == "user"]
    if len(messages) < 2 or not user_messages:
        return []

    with db.get_conn() as conn:
        user = conn.execute(
            "SELECT id, name FROM users WHERE id=?", (session["user_id"],)
        ).fetchone()
    user_id = int(session["user_id"])
    user_name = user["name"] if user else ""

    transcript = "\n".join(
        f"[{message['id']}] {message['role'].upper()}: {message['content']}"
        for message in messages
    )
    extracted = llm.generate(
        session_id,
        user_id,
        "extractor",
        [{"role": "user", "text": transcript}],
        system=EXTRACTOR_SYSTEM,
        json_mode=True,
        max_output_tokens=2048,
    )
    if not extracted.ok or not isinstance(extracted.data, dict):
        raise RuntimeError("Solution extraction failed")

    topic_summary = extracted.data.get("topic_summary")
    if isinstance(topic_summary, str) and topic_summary.strip():
        scrubbed_topic = scrub_text(topic_summary.strip(), [user_name])
        db.update_session(session_id, topic_summary=scrubbed_topic)

    raw_solutions = extracted.data.get("solutions")
    if not isinstance(raw_solutions, list):
        return []

    message_by_id = {int(message["id"]): message for message in messages}
    returned_ids: list[int] = []

    for raw in raw_solutions:
        sol = _normalize_solution(raw)
        if not sol or sol["confirmed"] is not True:
            continue

        confirmation_id = sol["confirmation_message_id"]
        try:
            confirmation_id = int(confirmation_id)
        except (TypeError, ValueError):
            continue
        confirmation = message_by_id.get(confirmation_id)
        if not confirmation or confirmation["role"] != "user":
            continue
        if not any(
            message["role"] == "assistant" and int(message["id"]) < confirmation_id
            for message in messages
        ):
            continue

        sol = scrub_solution(sol, known_names=[user_name])
        sol.pop("confirmed", None)
        sol.pop("confirmation_message_id", None)

        text = embed_solution_text(sol)
        vectors = llm.embed([text], "RETRIEVAL_DOCUMENT", session_id, user_id)
        if vectors is None:
            raise RuntimeError("Embedding failed during solution extraction")

        vector = np.asarray(vectors[0], dtype=np.float32)
        existing = db.get_all_solutions()
        best_id = None
        best_score = -1.0
        for candidate in existing:
            candidate_vector = np.asarray(candidate["embedding"], dtype=np.float32)
            if candidate_vector.size == 0 or candidate_vector.size != vector.size:
                continue
            score = float(np.dot(vector, candidate_vector))
            if score > best_score:
                best_score = score
                best_id = int(candidate["id"])

        if best_score >= config.MERGE_THRESHOLD:
            db.merge_into_solution(best_id, sol, session_id, confirmation_id)
            returned_ids.append(best_id)
        elif best_score >= config.MERGE_THRESHOLD - 0.10:
            best_candidate = next((item for item in existing if int(item["id"]) == best_id), None)
            same_fault = bool(best_candidate and _judge_same_fault(session_id, user_id, sol, best_candidate))
            if same_fault:
                db.merge_into_solution(best_id, sol, session_id, confirmation_id)
                returned_ids.append(best_id)
            else:
                inserted_id = db.insert_solution(sol, vector.tolist(), session_id, confirmation_id)
                returned_ids.append(inserted_id)
        else:
            inserted_id = db.insert_solution(sol, vector.tolist(), session_id, confirmation_id)
            returned_ids.append(inserted_id)

    return list(dict.fromkeys(returned_ids))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python -m app.extractor <session_id>")
    else:
        print(run_extraction(int(sys.argv[1])))
