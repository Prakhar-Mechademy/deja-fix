"""Extractor: turns confirmed problems in a stored transcript into generalized solutions.

* Reads the transcript FROM THE DATABASE (never from a variable passed in by the chat code).
* Only problems whose confirmation message was detected on the customer's turn are eligible; the LLM must cite
  that message id, so it cannot "confirm" something the customer never confirmed.
* Everything it returns is scrubbed again deterministically before it touches the hub.
* One session can yield several solutions (one per confirmed problem).
"""
from __future__ import annotations

import logging

from .config import Settings
from .db import connect, utcnow
from .hub import Hub
from .llm import LLMError, LLMService, extract_json
from .textutil import detect_faults, parse_environment, scrub_obj, title_for

log = logging.getLogger("dejafix.extractor")

EXTRACT_SYSTEM = (
    "You extract resolved support problems from a transcript. Reply with JSON only:\n"
    '{"solutions":[{"confirmation_message_id":<int>,"title":"<short generic title>","symptoms":["..."],'
    '"environment":{"device":"","os":"","app":"","app_version":""},"fix_steps":["..."],"did_not_work":["..."]}]}\n'
    "Rules: produce exactly one solution for each id in CONFIRMATION_IDS, using the problem the customer confirmed "
    "fixed at that message (PROBLEM_STARTS says which message that problem began at; ignore earlier problems). "
    "fix_steps are the steps that actually worked. did_not_work lists things the customer tried or were suggested that "
    "did not help. Generalize: remove names, emails, links, order/account numbers and any other personal data "
    "(names in KNOWN_NAMES must not appear). KEEP the device, operating system and app version."
)


def _strs(v) -> list[str]:
    return [str(x).strip() for x in v if str(x).strip()] if isinstance(v, list) else []


class Extractor:
    def __init__(self, settings: Settings, llm: LLMService, hub: Hub):
        self.s, self.llm, self.hub = settings, llm, hub
        self.db_path = settings.db_path

    def transcript(self, conn, session_id: str) -> str:
        rows = conn.execute("SELECT id, role, content, meta, source FROM messages WHERE session_id=? AND source!='greeting' "
                            "ORDER BY id", (session_id,)).fetchall()
        return "\n".join(f"[{r['id']}] {'CUSTOMER' if r['role'] == 'user' else 'SUPPORT'}: "
                         f"{r['content'][:2000]}"
                         for r in rows)

    def run(self, session_id: str) -> list[int]:
        """Extract every confirmed, not-yet-extracted LLM-sourced problem. Returns saved solution ids.
        Raises LLMError when the provider fails (the caller marks the session for retry)."""
        with connect(self.db_path) as conn:
            sess = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
            if not sess:
                return []
            user = conn.execute("SELECT display_name FROM users WHERE id=?", (sess["user_id"],)).fetchone()
            names = (user["display_name"],) if user else ()
            pending = conn.execute(
                "SELECT * FROM session_problems WHERE session_id=? AND status='resolved' AND served_by='llm' "
                "AND extracted=0 AND confirm_message_id IS NOT NULL ORDER BY confirm_message_id", (session_id,)).fetchall()
            if not pending:
                return []
            transcript = self.transcript(conn, session_id)
        ids = [p["confirm_message_id"] for p in pending]
        starts = ", ".join(f"{p['confirm_message_id']}:{p['start_message_id'] or 0}" for p in pending)
        prompt = (f"{transcript}\n\nCONFIRMATION_IDS: {', '.join(map(str, ids))}\n"
                  f"PROBLEM_STARTS (confirmation_id:first_message_id): {starts}\nKNOWN_NAMES: {', '.join(names)}")
        res = self.llm.call(session_id, "extract", EXTRACT_SYSTEM, [{"role": "user", "content": prompt}], max_tokens=1500)
        items = extract_json(res.text).get("solutions")
        if not isinstance(items, list):
            raise LLMError("extraction payload has no 'solutions' list")
        by_conf = {p["confirm_message_id"]: p for p in pending}
        saved = []
        for it in items:
            if not isinstance(it, dict):
                continue
            prob = by_conf.get(it.get("confirmation_message_id"))
            if not prob:
                continue                                    # cites a message the customer never confirmed
            it = scrub_obj(it, names)
            steps, symptoms = _strs(it.get("fix_steps")), _strs(it.get("symptoms"))
            if not steps or not symptoms:
                log.info("skipping extraction without steps/symptoms for problem %s", prob["id"])
                continue
            env = {k: v for k, v in (it.get("environment") or {}).items() if k in ("device", "os", "app", "app_version") and v}
            for k, v in parse_environment(prob["problem_text"]).items():     # deterministic parse wins
                env[k] = v
            faults = detect_faults(" ".join(symptoms) + " " + str(it.get("title", "")))
            title = str(it.get("title") or "").strip() or title_for(faults, env.get("app"))
            sid, _ = self.hub.add_solution(title=title, symptoms=symptoms, environment=env, fix_steps=steps,
                                           did_not_work=_strs(it.get("did_not_work")), fault_types=sorted(faults),
                                           session_id=session_id)
            with connect(self.db_path) as conn:
                conn.execute("UPDATE session_problems SET extracted=1, solution_id=?, updated_at=? WHERE id=?",
                             (sid, utcnow(), prob["id"]))
            saved.append(sid)
        return saved
