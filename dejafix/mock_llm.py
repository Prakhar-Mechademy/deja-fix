"""Offline stand-in for Gemini (used only when no GEMINI_API_KEY is set).
Deterministic keyword rules, so the full flow and the scenario script can run without internet.
It reads the same prompts the real model gets and returns the same JSON shapes."""
import json
import re

from .confirm import classify as classify_confirmation

FAULTS = [  # order matters: 'crashes when I sync' is a crash, not a sync fault
    ("app crash", ["crash", "closes", "force close", "freez", "shuts down", "keeps stopping"]),
    ("note sync", ["sync", "not updating", "isn't updating", "aren't updating", "don't update", "doesn't update",
                   "not showing up", "don't show up", "doesn't show up", "old version", "across devices",
                   "other device", "not appear", "don't appear", "not reflecting"]),
    ("sign-in", ["log in", "login", "sign in", "password", "otp", "logged out"]),
    ("battery drain", ["battery", "drain"]),
    ("notifications", ["notification"]),
]

FIXES = {
    "note sync": [
        "Open the app's Settings > Sync and make sure 'Background sync' is switched on.",
        "On the phone, open system Settings > Apps > the notes app > Battery and set it to 'Unrestricted' "
        "so the operating system stops killing sync in the background.",
        "Go back to the notes list and pull down to force a fresh sync.",
        "If edits still do not appear, clear the app cache (Settings > Apps > the notes app > Storage > "
        "Clear cache). Notes are stored on the server, so nothing is lost.",
    ],
    "app crash": [
        "Update the app to the latest version from the app store.",
        "Clear the app cache (Settings > Apps > the app > Storage > Clear cache).",
        "Restart the phone and open the note again.",
    ],
    "sign-in": [
        "Use 'Forgot password' on the sign-in screen to reset the password.",
        "Check that the phone's date and time are set to automatic.",
        "Sign in again with the new password.",
    ],
}
GENERIC_FIX = ["Update the app to the latest version.", "Restart the device.",
               "If the problem continues, uninstall and reinstall the app."]


def section(prompt: str, name: str) -> str:
    m = re.search(rf"### {name}\n(.*?)(?=\n### |\Z)", prompt, flags=re.S)
    return m.group(1).strip() if m else ""


def fault_of(text: str):
    t = (text or "").lower()
    for label, words in FAULTS:
        if any(w in t for w in words):
            return label
    return None


def environment_of(text: str) -> dict:
    t = text or ""
    device = re.search(r"\b(Pixel \d+\w*|iPhone \d+\w*|Samsung Galaxy \w+|Galaxy \w+|OnePlus \w+|iPad\w*|"
                       r"MacBook\w*|Redmi \w+)", t, re.I)
    os_ = re.search(r"\b(Android \d+|iOS \d+(?:\.\d+)*|iPadOS \d+|Windows \d+|macOS \w+)", t, re.I)
    ver = re.search(r"\b(?:app )?(?:version|ver\.?|v)\s?(\d+(?:\.\d+)+)", t, re.I)
    if not ver and re.search(r"latest (app|version)", t, re.I):
        ver_val = "latest"
    else:
        ver_val = ver.group(1) if ver else None
    return {"device": device.group(1) if device else None, "os": os_.group(1) if os_ else None,
            "app_version": ver_val}


def _triage(prompt: str) -> dict:
    text = section(prompt, "CUSTOMER_TEXT")
    cands = json.loads(section(prompt, "CANDIDATES") or "[]")
    allow = section(prompt, "ALLOW_CLARIFY").lower().startswith("yes")
    fault = fault_of(text)
    is_problem = bool(fault) or bool(re.search(r"not working|error|broken|bug|won'?t|can'?t|doesn'?t work", text, re.I))
    env = environment_of(text)
    match = None
    for c in cands:
        if fault and fault_of(c.get("label", "") + " " + c.get("symptoms", "")) == fault:
            match = c["id"]
            break
    needs = bool(allow and match and (not env["os"] or not env["app_version"]))
    return {
        "is_support_problem": is_problem,
        "label": fault or ("app problem" if is_problem else None),
        "summary": f"Customer reports a {fault or 'technical'} problem." if is_problem else None,
        "environment": env,
        "match_id": match,
        "needs_clarification": needs,
        "clarifying_question": "Which device, operating system and app version are you on, and is it edits made "
                               "on one device not showing up on the others?" if needs else None,
    }


def _escalate(prompt: str) -> str:
    convo = section(prompt, "CONVERSATION")
    customer = " ".join(l for l in convo.splitlines() if l.startswith("Customer:"))
    steps = FIXES.get(fault_of(customer), GENERIC_FIX)
    return "Here is what should fix it:\n" + "\n".join(f"{i}. {s}" for i, s in enumerate(steps, 1))


def _extract(prompt: str) -> dict:
    lines = section(prompt, "TRANSCRIPT").splitlines()
    msgs = []
    for line in lines:
        m = re.match(r"\[(\d+)\] (Customer|Support): (.*)", line)
        if m:
            msgs.append((int(m.group(1)), m.group(2), m.group(3)))
        elif msgs:
            n, who, txt = msgs[-1]
            msgs[-1] = (n, who, txt + "\n" + line)
    solutions, problem_text, steps, tried = [], None, None, []
    for n, who, txt in msgs:
        if who == "Customer":
            verdict = classify_confirmation(txt) if steps else "unclear"
            if verdict == "yes" and problem_text:
                solutions.append(_solution(problem_text, steps, tried, True, n))
                problem_text, steps, tried = None, None, []
            elif verdict == "no" and steps:
                tried.append("; ".join(steps[:2]))
                steps = None
            elif fault_of(txt) and not problem_text:
                problem_text = txt
                tried += [re.split(r",|\bbut\b", t)[0].strip(" .")
                          for t in re.findall(r"(?:already )?tried ([^.!?]+)", txt, re.I)]
            elif problem_text:
                problem_text += " " + txt
        else:
            found = re.findall(r"^\s*\d+\.\s+(.*)$", txt, re.M)
            if found:
                steps = found
    if problem_text and steps:
        solutions.append(_solution(problem_text, steps, tried, False, None))
    return {"solutions": solutions}


def _solution(problem_text, steps, tried, confirmed, msg_no):
    fault = fault_of(problem_text) or "app problem"
    sentences = re.split(r"(?<=[.!?])\s+", problem_text)
    symptoms = " ".join(s for s in sentences if fault_of(s) or re.search(r"edit|show|appear", s, re.I)
                        and not re.search(r"tried", s, re.I)) or problem_text
    return {
        "label": fault, "title": f"Fix for {fault}",
        "symptoms": symptoms, "environment": environment_of(problem_text),
        "fix_steps": steps, "did_not_work": tried, "confirmed": confirmed, "confirmation_message": msg_no,
    }


def _dedupe(prompt: str) -> dict:
    new = json.loads(section(prompt, "NEW") or "{}")
    existing = json.loads(section(prompt, "EXISTING") or "[]")
    f = fault_of(new.get("label", "") + " " + new.get("symptoms", ""))
    for e in existing:
        if f and fault_of(e.get("label", "") + " " + e.get("symptoms", "")) == f:
            return {"same_as": e["id"]}
    return {"same_as": None}


def generate(prompt: str, purpose: str) -> str:
    if purpose == "triage":
        return json.dumps(_triage(prompt))
    if purpose == "escalation":
        return _escalate(prompt)
    if purpose == "extraction":
        return json.dumps(_extract(prompt))
    if purpose == "dedupe":
        return json.dumps(_dedupe(prompt))
    return "{}"
