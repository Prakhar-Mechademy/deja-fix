"""Fixed-tone customer-facing persona; verified hub answers only, otherwise escalation."""
from services.retrieval_service import find_best_match
from agents.solver_agent import solve
from services.gemini_service import ProviderError
from utils.rate_limiter import enforce_limit

TONE = "I'm here to help you troubleshoot this step by step."

def respond(db, user_id: str, session_id: str, transcript: list[dict], latest_problem: str):
    match, score, method = find_best_match(db, latest_problem, session_id=session_id)
    if match and method == "ambiguous":
        return ("I found a potentially related fix, but I want to avoid giving you the wrong steps. "
                "Could you tell me the exact error message and your device and operating system?", "clarification", None, False)
    if match:
        return (f"I found a verified fix that helped in a similar situation. {TONE}\n\n"
                f"Try these steps:\n{match.fix_steps}\n\nPlease tell me whether this resolves the problem.",
                "knowledge_hub", match.id, True)
    enforce_limit(db, user_id, "escalation")
    try:
        proposed = solve(db, session_id, transcript)
        return (f"I couldn't find a verified solution in the knowledge hub, so I checked with our troubleshooting engine. "
                f"This is a proposed fix, not yet a verified knowledge-hub answer:\n\n{proposed}\n\n"
                "Please try the steps and tell me explicitly whether they fixed the problem.", "llm", None, True)
    except ProviderError as exc:
        return (str(exc), "system", None, False)
