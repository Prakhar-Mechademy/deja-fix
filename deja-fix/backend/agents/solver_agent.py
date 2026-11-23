"""Unrestricted backend problem-solving agent; output is a proposal, never verified knowledge."""
import json
from services.gemini_service import generate_text, ProviderError
from utils.token_tracker import record_usage

def solve(db, session_id: str, transcript: list[dict]) -> str:
    conversation = "\n".join(f"{m['role'].upper()}: {m['content']}" for m in transcript)
    prompt = (
        "You are a technical support problem-solving model. Analyze the entire conversation and suggest "
        "a safe, actionable troubleshooting plan. Do not claim certainty when details are missing. "
        "Ask for necessary missing information. Return concise numbered steps and explain how to verify.\n\n"
        f"FULL CONVERSATION:\n{conversation}"
    )
    result = generate_text(prompt, "solve")
    record_usage(db, session_id, result, "solve")
    return result["text"].strip()
