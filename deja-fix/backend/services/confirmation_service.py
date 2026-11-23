"""Only explicit positive customer language verifies a proposed fix."""
import re
POSITIVE = [r"\bthat (?:worked|fixed it)\b", r"\bit worked\b", r"\bproblem (?:is )?solved\b", r"\bworking now\b", r"\bit works now\b", r"\bit is working\b", r"\bit's working\b", r"\bnow it works\b", r"\bfixed\b", r"\bissue is resolved\b"]
NEGATIVE = [r"\b(?:did not|didn't|doesn't|does not) work\b", r"\bnot working\b", r"\bnot fixed\b", r"\bdid not help\b", r"\bno change\b"]
AMBIGUOUS = {"ok", "okay", "thanks", "thank you", "great", "i will try", "i'll try", "let me try"}
def confirmation_state(message: str) -> str:
    text = message.lower().strip()
    if any(re.search(p, text) for p in NEGATIVE): return "negative"
    if any(re.search(p, text) for p in POSITIVE): return "positive"
    return "ambiguous"
def is_confirmed(message: str) -> bool:
    return confirmation_state(message) == "positive"
