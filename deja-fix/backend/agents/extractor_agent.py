"""Extract customer-confirmed fixes from the persisted database transcript."""
import json, uuid
from datetime import datetime, timezone
from models.message import Message
from models.solution import Solution
from services.privacy_service import scrub_text, scrub_environment
from services.confirmation_service import is_confirmed
from services.embedding_service import embed_text
from services.deduplication_service import merge_or_create
from services.gemini_service import generate_json, ProviderError
from utils.token_tracker import record_usage

EXTRACTION_PROMPT = '''Read this support transcript and extract every distinct problem the customer explicitly confirmed as solved.
Return ONLY JSON: {"solutions":[{"symptoms":"...","environment":{"device":"...","operating_system":"...","app_version":"..."},"fix_steps":"...","failed_attempts":["..."]}]}
Do not include names, emails, URLs, order/account/ticket identifiers. Keep device, operating system and app version. If no confirmed fix exists, return an empty solutions array.\n\nTRANSCRIPT:\n'''

def _fallback_extract(messages):
    users = [m.content for m in messages if m.role == "user"]
    assistants = [m.content for m in messages if m.role == "assistant"]
    if not users or not assistants: return []
    # Conservative transcript-derived fallback when the provider is unavailable.
    problem = next((x for x in users if len(x) > 15), users[0])
    fix = next((x for x in reversed(assistants) if "try these steps" in x.lower() or "step" in x.lower()), assistants[-1])
    return [{"symptoms": problem, "environment": {}, "fix_steps": fix, "failed_attempts": []}]

def extract_confirmed_solutions(db, session_id: str):
    messages = db.query(Message).filter(Message.session_id == session_id).order_by(Message.id.asc()).all()
    if len(messages) < 3 or messages[-1].role != "user" or not is_confirmed(messages[-1].content):
        return []
    if messages[-2].role != "assistant" or messages[-2].source != "llm":
        return []
    transcript = "\n".join(f"{m.role.upper()}: {m.content}" for m in messages)
    try:
        result = generate_json(EXTRACTION_PROMPT + transcript, "extract")
        record_usage(db, session_id, result, "extract")
        items = result["data"].get("solutions", [])
        if not isinstance(items, list):
            items = []
    except ProviderError as exc:
        if getattr(exc, "usage", None):
            record_usage(db, session_id, exc.usage, "extract")
        items = _fallback_extract(messages)
    saved = []
    for item in items:
        if not isinstance(item, dict):
            continue
        symptoms = scrub_text(str(item.get("symptoms", "")).strip())
        steps = scrub_text(str(item.get("fix_steps", "")).strip())
        if not symptoms or not steps: continue
        raw_environment = item.get("environment")
        environment = scrub_environment(raw_environment if isinstance(raw_environment, dict) else {})
        raw_failed = item.get("failed_attempts", [])
        failed = [scrub_text(str(x)) for x in raw_failed] if isinstance(raw_failed, list) else []
        vector = embed_text(symptoms, db=db, session_id=session_id)
        solution = Solution(
            id=str(uuid.uuid4()), source_session_id=session_id,
            symptoms=symptoms, environment=json.dumps(environment), fix_steps=steps,
            failed_attempts=json.dumps(failed), verified=True, occurrence_count=1,
            embedding=json.dumps(vector) if vector else None,
            last_confirmed_at=datetime.now(timezone.utc))
        saved_solution, action = merge_or_create(db, solution)
        saved.append({"solution_id": saved_solution.id, "action": action})
    return saved
