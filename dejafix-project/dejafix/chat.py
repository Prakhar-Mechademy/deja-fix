"""Chat service: users, sessions, and the per-message state machine.

Session states
  gathering        no active offer; a new problem (or a vague one awaiting detail)
  clarifying       persona asked a clarifying question about a candidate hub fix
  fix_offered_hub  a verified fix from the hub was given; waiting for confirm / fail
  escalated        the backend LLM is helping; its suggestions are labelled unverified
  resolved         customer confirmed; next message starts a new problem

Order of work for every message: ownership -> message rate limit -> route (read-only) -> escalation rate limit
-> persist -> act. Rate-limited requests therefore leave no trace in the transcript.
"""
from __future__ import annotations

import json
import logging
import threading
import uuid
from collections import defaultdict
from dataclasses import dataclass, field

from . import persona
from .config import Settings
from .db import connect, utcnow
from .errors import BadRequest, NotFound
from .extractor import Extractor
from .hub import Hub, Match
from .llm import LLMError, LLMService
from .ratelimit import RateLimiter
from .textutil import (classify_feedback, clean_sentences, content_tokens, detect_faults, env_conflict, is_jailbreak,
                       is_negative_answer, is_smalltalk, is_thanks_only, is_try_later, label_for, parse_environment, scrub)

log = logging.getLogger("dejafix.chat")
ESCALATION_SYSTEM = ("Suggest a fix for the technical problem described in this conversation. "
                     "Give concrete numbered steps.")


@dataclass
class Plan:
    kind: str                       # decline|smalltalk|nudge|clarify_vague|clarify_match|serve_hub|confirm|escalate
    reason: str | None = None
    match: Match | None = None
    solution: dict | None = None
    missing: list = field(default_factory=list)
    abandon: bool = False


class ChatService:
    def __init__(self, settings: Settings, llm: LLMService, hub: Hub, extractor: Extractor, limiter: RateLimiter):
        self.s, self.llm, self.hub, self.extractor, self.limiter = settings, llm, hub, extractor, limiter
        self.db_path = settings.db_path
        self._locks: dict[str, threading.Lock] = defaultdict(threading.Lock)
        self._guard = threading.Lock()

    def _lock(self, session_id: str) -> threading.Lock:
        with self._guard:
            return self._locks[session_id]

    # ------------------------------------------------------------------ users & sessions
    def create_user(self, display_name: str) -> dict:
        name = (display_name or "").strip()
        if not name or len(name) > 80:
            raise BadRequest("display_name must be 1-80 characters.")
        uid = uuid.uuid4().hex[:12]
        with connect(self.db_path) as conn:
            conn.execute("INSERT INTO users VALUES (?,?,?)", (uid, name, utcnow()))
        return {"user_id": uid, "display_name": name}

    def _user(self, conn, user_id: str):
        row = conn.execute("SELECT * FROM users WHERE id=?", (user_id or "",)).fetchone()
        if not row:
            raise NotFound("Unknown user. Create one with POST /users and send its id in the X-User-Id header.")
        return row

    def _session(self, conn, user_id: str, session_id: str):
        row = conn.execute("SELECT * FROM sessions WHERE id=? AND user_id=?", (session_id, user_id)).fetchone()
        if not row:                       # also hides other customers' sessions
            raise NotFound("Session not found.")
        return row

    def _problem(self, conn, pid):
        return conn.execute("SELECT * FROM session_problems WHERE id=?", (pid,)).fetchone() if pid else None

    def _add_message(self, conn, sid, uid, role, source, content, meta=None) -> int:
        return conn.execute("INSERT INTO messages(session_id,user_id,role,source,content,meta,created_at) VALUES (?,?,?,?,?,?,?)",
                            (sid, uid, role, source, content, json.dumps(meta) if meta else None, utcnow())).lastrowid

    def start_session(self, user_id: str) -> dict:
        with connect(self.db_path) as conn:
            user = self._user(conn, user_id)
            last = conn.execute("SELECT summary, status FROM session_problems WHERE user_id=? ORDER BY id DESC LIMIT 1",
                                (user_id,)).fetchone()          # ONLY this customer's own history
            greeting = (persona.greeting_returning(user["display_name"], last["summary"], last["status"] == "resolved")
                        if last else persona.greeting_new(user["display_name"]))
            sid, now = str(uuid.uuid4()), utcnow()
            conn.execute("INSERT INTO sessions(id,user_id,created_at,updated_at) VALUES (?,?,?,?)", (sid, user_id, now, now))
            self._add_message(conn, sid, user_id, "assistant", "greeting", greeting)
        return {"session_id": sid, "greeting": greeting}

    def list_sessions(self, user_id: str) -> list[dict]:
        with connect(self.db_path) as conn:
            self._user(conn, user_id)
            rows = conn.execute("SELECT s.id session_id, s.status, s.state, s.created_at, s.updated_at, "
                                "(SELECT COUNT(*) FROM messages m WHERE m.session_id=s.id) message_count "
                                "FROM sessions s WHERE s.user_id=? ORDER BY s.created_at", (user_id,)).fetchall()
            return [dict(r) for r in rows]

    def get_session(self, user_id: str, session_id: str) -> dict:
        with connect(self.db_path) as conn:
            self._user(conn, user_id)
            s = self._session(conn, user_id, session_id)
            msgs = conn.execute("SELECT id, role, source, content, created_at FROM messages WHERE session_id=? ORDER BY id",
                                (session_id,)).fetchall()
            return {"session_id": s["id"], "status": s["status"], "state": s["state"], "created_at": s["created_at"],
                    "messages": [dict(m) for m in msgs]}

    def close_session(self, user_id: str, session_id: str) -> dict:
        with connect(self.db_path) as conn:
            self._user(conn, user_id)
            self._session(conn, user_id, session_id)
            conn.execute("UPDATE sessions SET status='closed', updated_at=? WHERE id=?", (utcnow(), session_id))
        self._retry_extraction(session_id)
        return {"session_id": session_id, "status": "closed"}

    # ------------------------------------------------------------------ extraction retries
    def _retry_extraction(self, session_id: str) -> None:
        with connect(self.db_path) as conn:
            row = conn.execute("SELECT extraction_pending FROM sessions WHERE id=?", (session_id,)).fetchone()
        if row and row["extraction_pending"]:
            try:
                self.extractor.run(session_id)
                with connect(self.db_path) as conn:
                    conn.execute("UPDATE sessions SET extraction_pending=0 WHERE id=?", (session_id,))
            except LLMError as e:
                log.warning("extraction retry failed for %s: %s", session_id, e.code)

    def sweep_pending(self) -> None:
        """Retry extractions that were lost to an outage (runs once at startup)."""
        with connect(self.db_path) as conn:
            ids = [r["id"] for r in conn.execute("SELECT id FROM sessions WHERE extraction_pending=1")]
        for sid in ids:
            self._retry_extraction(sid)

    # ------------------------------------------------------------------ messages
    def post_message(self, user_id: str, session_id: str, content: str) -> dict:
        text = (content or "").strip()
        if not text:
            raise BadRequest("Message is empty.")
        if len(text) > self.s.max_message_chars:
            raise BadRequest(f"Message is too long (max {self.s.max_message_chars} characters).")
        with self._lock(session_id), connect(self.db_path) as conn:
            user = self._user(conn, user_id)
            sess = self._session(conn, user_id, session_id)
            if sess["status"] != "open":
                raise BadRequest("This session is closed. Start a new one with POST /sessions.")
            self.limiter.check(user_id, "messages")
            plan = self._plan(conn, user, sess, text)
            if plan.kind == "escalate":
                self.limiter.check(user_id, "escalations")
            return self._act(conn, user, sess, text, plan)

    # -- routing (read-only)
    def _plan(self, conn, user, sess, text: str) -> Plan:
        state = sess["state"]
        prob = self._problem(conn, sess["current_problem_id"]) if state != "resolved" else None
        if is_jailbreak(text):
            return Plan("decline")
        faults = detect_faults(text)

        if state in ("fix_offered_hub", "escalated") and prob:
            fb = classify_feedback(text)
            if fb == "confirmed":
                return Plan("confirm")
            if fb == "failed":
                return Plan("escalate", reason="hub_failed" if state == "fix_offered_hub" else "continue")
            if is_thanks_only(text) or is_try_later(text):
                return Plan("nudge")
            pf = detect_faults(prob["problem_text"])
            if faults and pf and not (faults & pf):          # a different problem altogether
                plan = self._plan_new(conn, user, sess, text, None)
                plan.abandon = True
                return plan
            return Plan("escalate", reason="followup" if state == "fix_offered_hub" else "continue")

        if state == "clarifying" and prob:
            cand = self.hub.get(conn, sess["pending_solution_id"])
            if cand is None or cand["status"] != "verified":
                return self._plan_new(conn, user, sess, text, prob)
            af = detect_faults(text)
            if is_negative_answer(text) or (af and not (af & set(cand["fault_types"]))):
                return Plan("escalate", reason="clarify_rejected")
            env = parse_environment(prob["problem_text"] + "\n" + text)
            if env_conflict(env, cand["environment"]):
                return Plan("escalate", reason="env_mismatch")
            return Plan("serve_hub", solution=cand)

        return self._plan_new(conn, user, sess, text, prob)

    def _plan_new(self, conn, user, sess, text: str, prob) -> Plan:
        if is_smalltalk(text):
            return Plan("smalltalk")
        problem_text = (prob["problem_text"] + "\n" + text) if prob and prob["status"] == "open" else text
        env = parse_environment(problem_text)
        match = self.hub.find(problem_text, env, session_id=sess["id"], user_id=user["id"], conn=conn)
        if match:
            sol = match.solution
            missing = [k for k in ("device", "os") if sol["environment"].get(k) and not env.get(k)]
            if match.band == "medium" or missing:
                return Plan("clarify_match", match=match, solution=sol, missing=missing)
            return Plan("serve_hub", match=match, solution=sol)
        vague = not detect_faults(problem_text) and len(content_tokens(problem_text)) < 3
        if vague and sess["vague_count"] < 2:
            return Plan("clarify_vague")
        return Plan("escalate", reason="no_match")

    # -- conversation handed to the backend LLM (full transcript, raw LLM text where we wrapped it)
    def _conversation(self, conn, sid: str) -> list[dict]:
        out: list[dict] = []
        for r in conn.execute("SELECT role, source, content, meta FROM messages WHERE session_id=? AND source NOT IN "
                              "('greeting','system') ORDER BY id", (sid,)):
            text, meta = r["content"], (json.loads(r["meta"]) if r["meta"] else {})
            if meta.get("declined") or meta.get("plan") == "decline":
                continue                                    # jailbreak attempts never reach the backend LLM
            if r["role"] == "assistant" and r["source"] == "llm":
                text = meta.get("raw", text)
            if out and out[-1]["role"] == r["role"]:
                out[-1]["content"] += "\n\n" + text
            else:
                out.append({"role": r["role"], "content": text})
        while out and out[0]["role"] != "user":
            out.pop(0)
        return out

    def _act(self, conn, user, sess, text: str, plan: Plan) -> dict:
        sid, uid, now, state = sess["id"], user["id"], utcnow(), sess["state"]
        umid = self._add_message(conn, sid, uid, "user", "user", text, {"declined": True} if plan.kind == "decline" else None)
        prob = self._problem(conn, sess["current_problem_id"])
        if plan.abandon and prob:
            conn.execute("UPDATE session_problems SET status='abandoned', updated_at=? WHERE id=?", (now, prob["id"]))
            prob, state = None, "gathering"
        if state == "resolved":
            prob = None
        if plan.kind in ("clarify_vague", "clarify_match", "serve_hub", "escalate") and prob is None:
            faults, env = detect_faults(text), parse_environment(text)
            cur = conn.execute(
                "INSERT INTO session_problems(session_id,user_id,problem_text,summary,status,start_message_id,created_at,updated_at)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (sid, uid, text, label_for(faults, env.get("app"), text), "open", umid, now, now))
            prob = self._problem(conn, cur.lastrowid)
            conn.execute("UPDATE sessions SET current_problem_id=? WHERE id=?", (prob["id"], sid))
        elif plan.kind in ("clarify_vague", "clarify_match", "serve_hub", "escalate") and state in ("gathering", "clarifying"):
            conn.execute("UPDATE session_problems SET problem_text=problem_text||char(10)||?, updated_at=? WHERE id=?",
                         (text, now, prob["id"]))
            prob = self._problem(conn, prob["id"])
            if state == "gathering":                       # refresh the greeting label with the extra detail
                full = prob["problem_text"]
                conn.execute("UPDATE session_problems SET summary=? WHERE id=?",
                             (label_for(detect_faults(full), parse_environment(full).get("app"), full), prob["id"]))

        new_state, source, meta, escalated = state, "persona", {"plan": plan.kind, "reason": plan.reason}, False
        vague_count = sess["vague_count"]
        pending = sess["pending_solution_id"]

        if plan.kind == "decline":
            reply = persona.decline()
        elif plan.kind == "smalltalk":
            reply = persona.smalltalk()
        elif plan.kind == "nudge":
            reply = persona.nudge()
        elif plan.kind == "clarify_vague":
            reply, new_state, vague_count = persona.clarify_vague(), "gathering", vague_count + 1
        elif plan.kind == "clarify_match":
            reply, new_state, pending = persona.clarify_match(plan.solution, plan.missing), "clarifying", plan.solution["id"]
            meta["match"] = {"solution_id": plan.solution["id"], "score": plan.match.score if plan.match else None}
        elif plan.kind == "serve_hub":
            sol = plan.solution
            reply, source, new_state = persona.fix_from_hub(sol), "hub", "fix_offered_hub"
            conn.execute("UPDATE session_problems SET served_by='hub', solution_id=?, updated_at=? WHERE id=?",
                         (sol["id"], now, prob["id"]))
            meta["match"] = {"solution_id": sol["id"], "score": plan.match.score if plan.match else None}
        elif plan.kind == "confirm":
            new_state, reply = "resolved", self._confirm(conn, sess, user, prob, umid)
        else:                                               # escalate
            escalated = True
            try:
                res = self.llm.call(sid, "escalation", ESCALATION_SYSTEM, self._conversation(conn, sid))
                reply, source, new_state, meta["raw"] = persona.escalated(plan.reason or "no_match", res.text), "llm", "escalated", res.text
                conn.execute("UPDATE session_problems SET served_by='llm', updated_at=? WHERE id=?", (now, prob["id"]))
                if plan.reason == "hub_failed":
                    self.hub.record_failure(prob["solution_id"])   # the customer said the served fix did not work
            except LLMError as e:
                reply, source, meta["error"] = e.user_message, "system", e.code

        rid = self._add_message(conn, sid, uid, "assistant", source, reply, meta)
        conn.execute("UPDATE sessions SET state=?, vague_count=?, pending_solution_id=?, updated_at=? WHERE id=?",
                     (new_state, vague_count, pending, utcnow(), sid))
        return {"session_id": sid, "message_id": rid, "reply": reply, "source": source, "state": new_state, "escalated": escalated}

    def _confirm(self, conn, sess, user, prob, umid: int) -> str:
        sid, now = sess["id"], utcnow()
        conn.execute("UPDATE session_problems SET status='resolved', confirm_message_id=?, updated_at=? WHERE id=?",
                     (umid, now, prob["id"]))
        if prob["served_by"] == "hub":
            names = (user["display_name"],)
            symptoms = clean_sentences(prob["problem_text"], names)[:3]
            self.hub.add_confirmation(prob["solution_id"], sid, parse_environment(prob["problem_text"]), symptoms)
            return persona.resolved("hub")
        try:
            return persona.resolved("saved" if self.extractor.run(sid) else "none")
        except LLMError as e:
            log.warning("extraction failed (%s); will retry", e.code)
            conn.execute("UPDATE sessions SET extraction_pending=1 WHERE id=?", (sid,))
            return persona.resolved("pending")
