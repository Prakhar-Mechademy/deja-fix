"""Detects whether a customer message confirms or rejects the fix they were given.
Rule-based on purpose: it is free, fast and predictable. Negative phrases are checked first,
so 'not fixed' or 'still not working' never count as a confirmation."""
import re

NEGATIVE = [
    r"\b(didn'?t|did not|doesn'?t|does not|isn'?t|is not|wasn'?t|was not|hasn'?t|has not|won'?t|not)\s+"
    r"(work|working|help|helped|fix|fixed|solve|solved|resolve|resolved|change|changed)",
    r"\bstill\b", r"\bno luck\b", r"\bsame (problem|issue|error)\b", r"^\s*no\b", r"\bnope\b",
    r"\bnothing (changed|happened)\b", r"\bnot (fixed|solved|resolved|working)\b", r"\bgave up\b",
]
POSITIVE = [
    r"\b(that|it|this|which)\s+(worked|works|did it|did the trick|fixed it|helped|solved it)\b",
    r"\bworked\b", r"\b(it'?s|is|now|everything'?s?)\s+(working|fixed|syncing|solved|resolved|fine|ok|okay|good)\b",
    r"\b(fixed|solved|resolved|sorted)\b", r"^\s*(yes|yep|yeah|yup|yes!)\b", r"\bperfect\b", r"\ball good\b",
    r"\bworking (now|again|fine)\b", r"\bproblem('?s| is) gone\b",
]


def classify(text: str) -> str:
    """Returns 'yes', 'no' or 'unclear'."""
    t = (text or "").lower().strip()
    if any(re.search(p, t) for p in NEGATIVE):
        return "no"
    if any(re.search(p, t) for p in POSITIVE):
        return "yes"
    return "unclear"
