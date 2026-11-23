"""Conservative, local confirmation detection; ambiguous/partial success is not verified."""
import re
from enum import Enum


class ConfirmationDecision(str, Enum):
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    AMBIGUOUS = "ambiguous"


_POSITIVE = re.compile(
    r"\b(?:yes|yep|yeah|yup|fixed|solved|resolved|works now|working now|"
    r"it works|that worked|it worked|worked for me|issue is gone|problem is gone|all good|"
    r"back to normal|working again|no longer (?:fails|crashes|freezes|gets? the error|shows? the error))\b",
    re.I,
)
_NEGATIVE = re.compile(
    r"\b(?:no(?!\s+longer)|nope|not really|not yet|did not work|didn't work|does not work|"
    r"doesn't work|still broken|still failing|still not working|not working|"
    r"not fixed|hasn't fixed|has not fixed|didn't fix|did not fix|"
    r"doesn't seem to work|does not seem to work|don't think (?:it|that) work(?:s|ed)?|"
    r"do not think (?:it|that) work(?:s|ed)?|no change|failed|failure|worse|same issue|"
    r"issue persists|problem persists|still happens|still crashes|still freezes|"
    r"(?:still|again) (?:fails?|crashes?|breaks?)|(?:it|the app|the issue) (?:crashes?|fails?|breaks?) when)\b",
    re.I,
)
_UNCERTAIN = re.compile(
    r"\b(?:not sure|unsure|maybe|perhaps|hard to tell|can't tell|cannot tell|"
    r"don't know whether|do not know whether|sort of|kind of|partly|partially|"
    r"sometimes|intermittently|temporarily|for a while|for now|not completely|"
    r"not fully|only half|half works)\b",
    re.I,
)


def classify_confirmation(message: str) -> ConfirmationDecision:
    """Verify only clear *complete* resolution; evaluate the final contrast clause.

    Examples: 'first step failed, but clearing cache fixed it' => confirmed.
    'yes but it only partly works' => ambiguous, never saved as verified.
    'yes but it still crashes' => rejected.
    """
    text = " ".join((message or "").strip().lower().split())
    if not text:
        return ConfirmationDecision.AMBIGUOUS

    contrast = re.split(r"\b(?:but|however|except that)\b", text)
    decisive = contrast[-1].strip() if len(contrast) > 1 else text
    if _UNCERTAIN.search(decisive):
        return ConfirmationDecision.AMBIGUOUS
    if _NEGATIVE.search(decisive):
        return ConfirmationDecision.REJECTED
    if _POSITIVE.search(decisive):
        return ConfirmationDecision.CONFIRMED
    # Don't verify a merely proposed or partial fix.
    if len(contrast) > 1:
        return ConfirmationDecision.AMBIGUOUS
    if _NEGATIVE.search(text):
        return ConfirmationDecision.REJECTED
    return ConfirmationDecision.AMBIGUOUS


CONFIRMATION_QUESTION = (
    "Did those steps solve the problem completely? Reply **yes** if fixed, "
    "or **no** if not. I only save confirmed, working fixes."
)
AMBIGUOUS_CONFIRMATION_PROMPT = (
    "Did the problem get resolved completely? Please reply **yes** or **no**. "
    "I won't save a partial or uncertain result as a verified fix."
)
REJECTED_CONFIRMATION_MESSAGE = (
    "Thanks for the feedback. That attempt won't be saved as verified. "
    "Tell me which step failed or what happened, and I'll try another approach."
)


def confirmed_message(saved_count: int, merged: bool = False) -> str:
    if saved_count <= 0:
        return (
            "Thanks for confirming. I couldn't reliably extract a reusable fix "
            "from the stored conversation, so nothing new was saved."
        )
    if merged:
        return "Thanks! This verified occurrence has been added to the existing solution."
    return "Thanks! I've saved a generalized, verified fix for future customers."
