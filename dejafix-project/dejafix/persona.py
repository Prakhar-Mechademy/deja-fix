"""The persona: a fixed, friendly, plain-spoken support voice.

Replies are rendered from templates + stored solution data, so the persona can ONLY say what the hub holds
(containment by construction: there is no free-text generation to jailbreak). Text from the backend LLM is always
wrapped and labelled "not yet verified".

Tone: warm, concise, plain language, no jargon, no promises; always ends by inviting the customer to report back.
"""
from __future__ import annotations


def _env(env: dict) -> str:
    out = " on ".join(p for p in (env.get("device"), env.get("os")) if p)
    if env.get("app_version"):
        out += f" (app v{env['app_version']})"
    return out


def _first(name: str) -> str:
    return name.split()[0] if name.split() else name


def _steps(steps: list[str]) -> str:
    return "\n".join(f"{i}. {s}" for i, s in enumerate(steps, 1))


def greeting_new(name: str) -> str:
    return (f"Hi {_first(name)}! I'm the Déjà Fix support assistant. Tell me what's going wrong and I'll check our "
            "verified fixes. If I don't have one, I'll say so plainly.")


def greeting_returning(name: str, summary: str, resolved: bool) -> str:
    if resolved:
        return f"Welcome back, {_first(name)}! Last time it was your {summary}, and we got that sorted. How can I help today?"
    return (f"Welcome back, {_first(name)}! Last time you were dealing with your {summary}, and we hadn't confirmed a fix. "
            "Is that still going on, or is it something new?")


def smalltalk() -> str:
    return "Happy to help! What's going wrong? A sentence or two about the problem and the device you're using is plenty."


def nudge() -> str:
    return "No rush. Whenever you've tried it, let me know whether it worked or not."


def decline() -> str:
    return ("I can only share fixes that customers have confirmed, so I can't answer from my own knowledge or change how "
            "I work. Tell me what's going wrong and I'll check what we have on file. If there's nothing, I'll say so plainly.")


def clarify_vague() -> str:
    return ("Happy to help. I need a little more to go on: which app or device is this about, and what exactly happens "
            "when it goes wrong?")


def clarify_match(sol: dict, missing: list[str]) -> str:
    symptom = sol["symptoms"][0].rstrip(".")
    ask = ""
    if missing:
        what = " and ".join({"device": "device", "os": "OS version"}[m] for m in missing)
        ask = f" And which {what} are you using?"
    return (f"Thanks for explaining. I may have a verified fix for this. Quick check first so I don't send you down the "
            f"wrong path: is it similar to this: “{symptom}”?{ask} "
            f"(The fix I have was confirmed on: {_env(sol['environment']) or 'a range of setups'}.)")


def fix_from_hub(sol: dict) -> str:
    n = sol["confirm_count"]
    who = "1 customer" if n == 1 else f"{n} customers"
    out = [f"Thanks. Here's a verified fix, confirmed by {who} with this problem:", "", _steps(sol["fix_steps"])]
    if sol["did_not_work"]:
        out += ["", "These did not help others, so you can skip them: " + "; ".join(sol["did_not_work"][:4]) + "."]
    out += ["", "Give it a go and let me know if that sorts it out."]
    return "\n".join(out)


_INTRO = {
    "no_match": "I don't have a verified fix for this one yet, so I've asked our backend assistant. Here's its suggestion. "
                "It hasn't been verified, so treat it as a first attempt:",
    "hub_failed": "Sorry that didn't work. I don't have another verified fix for this, so I've asked our backend assistant. "
                  "Here's its suggestion (not yet verified):",
    "clarify_rejected": "Thanks. That's different from the fix I have on file, and I don't have a verified answer for your "
                        "case. I've asked our backend assistant. Here's its suggestion (not yet verified):",
    "env_mismatch": "Thanks. The fix I have on file was confirmed on a different kind of device, so I won't offer it. "
                    "I've asked our backend assistant. Here's its suggestion (not yet verified):",
    "followup": "Thanks for the update. Here's what our backend assistant suggests (not yet verified):",
    "continue": "Thanks for the update. Here's what our backend assistant suggests next (still not verified):",
}


def escalated(reason: str, raw: str) -> str:
    return f"{_INTRO.get(reason, _INTRO['no_match'])}\n\n{raw.strip()}\n\nTry it and tell me whether it worked."


def resolved(kind: str) -> str:
    tail = " Anything else I can help with?"
    return {
        "saved": "Wonderful, I'm glad that fixed it! I've saved the fix (with personal details removed) so it can help other customers." + tail,
        "hub": "Wonderful, I'm glad that sorted it! I've added your confirmation so this fix is even more trusted." + tail,
        "pending": "Wonderful, I'm glad that's sorted! I couldn't save the fix just now, but I'll retry shortly." + tail,
        "none": "Wonderful, I'm glad that's sorted!" + tail,
    }[kind]
