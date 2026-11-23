"""Knowledge hub: retrieval, duplicate merging and confirmation bookkeeping.

Retrieval = fault-type gate + environment gate + weighted lexical similarity, with an optional cheap LLM
"same fault?" judge for borderline scores. See DESIGN.md for why.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from .config import Settings
from .db import connect, tx, utcnow
from .llm import LLMError, LLMService, extract_json
from .textutil import bag, detect_faults, env_conflict, env_text, parse_environment, similarity, text_sim

JUDGE_SYSTEM = (
    "You compare two customer-support problems. Reply with JSON only: "
    '{"same_fault": true|false, "reason": "<short>"}. '
    "same_fault is true only if both describe the same underlying fault in the same product, so that the same fix "
    "would apply. Sharing words, the same app, or the same device is NOT enough."
)


@dataclass
class Match:
    solution: dict
    score: float
    band: str                  # high | medium
    judged: bool | None = None


def row_to_solution(row) -> dict:
    d = dict(row)
    for k in ("symptoms", "environment", "fix_steps", "did_not_work", "alt_fixes", "fault_types"):
        d[k] = json.loads(d[k])
    return d


def _merge_list(existing: list[str], new: list[str], cap: int) -> list[str]:
    out = list(existing)
    for n in new or []:
        if not n or any(n.strip().lower() == e.strip().lower() or text_sim(n, e) >= 0.8 for e in out):
            continue
        out.append(n)
    return out[:cap]


def _merge_env(old: dict, new: dict) -> dict:
    env = dict(old)
    also = list(env.get("also_seen_on", []))
    differs = {k: v for k, v in (new or {}).items() if k in ("device", "os", "app_version") and v}
    if differs and any(env.get(k) != v for k, v in differs.items()) and differs not in also:
        also.append(differs)
    for k in ("device", "os", "app", "app_version"):
        if not env.get(k) and (new or {}).get(k):
            env[k] = new[k]
    if also:
        env["also_seen_on"] = also[:5]
    return env


class Hub:
    def __init__(self, settings: Settings, llm: LLMService | None = None):
        self.s = settings
        self.db_path = settings.db_path
        self.llm = llm

    # ------------------------------------------------------------------ reads
    def get(self, conn, solution_id: int | None) -> dict | None:
        if not solution_id:
            return None
        row = conn.execute("SELECT * FROM solutions WHERE id=?", (solution_id,)).fetchone()
        return row_to_solution(row) if row else None

    def list(self, status: str | None = None) -> list[dict]:
        with connect(self.db_path) as conn:
            q, a = "SELECT * FROM solutions", ()
            if status:
                q, a = q + " WHERE status=?", (status,)
            return [row_to_solution(r) for r in conn.execute(q + " ORDER BY id", a)]

    @staticmethod
    def doc_text(sol: dict) -> str:
        return " ".join([sol["title"], *sol["symptoms"], env_text(sol["environment"])])

    def score(self, qf: set, qb, qenv: dict, sol: dict) -> float | None:
        """None = hard-gated out (different fault, different app, different platform)."""
        sf = set(sol["fault_types"])
        if qf and sf and not (qf & sf):
            return None
        if env_conflict(qenv, sol["environment"]):
            return None
        qa, sa = (qenv.get("app") or "").lower(), (sol["environment"].get("app") or "").lower()
        if qa and sa and qa != sa:
            return None
        app = 1.0 if qa and qa == sa else (0.5 if not qa else 0.0)
        lex = similarity(qb, bag(self.doc_text(sol)))
        if qf and sf:
            return 0.45 + 0.2 * app + 0.35 * min(1.0, lex * 2.5)
        return 0.2 * app + 0.8 * min(1.0, lex * 1.5)       # no fault signal: lexical evidence only

    def find(self, query: str, env: dict | None = None, session_id: str | None = None,
             user_id: str | None = None, use_judge: bool = True, conn=None) -> Match | None:
        """Best VERIFIED solution for `query`, or None. Unverified rows are never returned."""
        def run(c):
            qf, qb, qenv = detect_faults(query), bag(query), env or parse_environment(query)
            best = None
            for r in c.execute("SELECT * FROM solutions WHERE status='verified'"):
                sol = row_to_solution(r)
                sc = self.score(qf, qb, qenv, sol)
                if sc is not None and (best is None or sc > best[0]):
                    best = (sc, sol)
            return best
        if conn is None:
            with connect(self.db_path) as c:
                best = run(c)
        else:
            best = run(conn)
        if not best or best[0] < self.s.match_accept:
            return None
        sc, sol = best
        m = Match(sol, round(sc, 3), "high" if sc >= self.s.match_high else "medium")
        mode = self.s.judge_mode
        if use_judge and self.llm and mode != "never" and (m.band == "medium" or mode == "always"):
            m.judged = self.judge(sol, query, session_id)
            if m.judged is False:
                return None
        return m

    def judge(self, sol: dict, query: str, session_id: str | None) -> bool | None:
        prompt = (f"PROBLEM A (saved): {sol['title']}. {' '.join(sol['symptoms'][:3])}\n"
                  f"PROBLEM B (new): {query[:800]}")
        try:
            res = self.llm.call(session_id, "judge", JUDGE_SYSTEM, [{"role": "user", "content": prompt}], max_tokens=120)
            return bool(extract_json(res.text).get("same_fault"))
        except LLMError:
            return None          # judge unavailable: fall back to the lexical decision

    # ------------------------------------------------------------------ writes
    def add_solution(self, *, title: str, symptoms: list, environment: dict, fix_steps: list, did_not_work: list,
                     fault_types: list, session_id: str) -> tuple[int, bool]:
        """Insert a verified solution, or merge into an existing duplicate. Returns (solution_id, merged)."""
        now = utcnow()
        ft = set(fault_types)
        with connect(self.db_path) as conn, tx(conn):
            dup = None
            for r in conn.execute("SELECT * FROM solutions WHERE status='verified' ORDER BY id"):
                s = row_to_solution(r)
                sa, na = (s["environment"].get("app") or "").lower(), (environment.get("app") or "").lower()
                if sa and na and sa != na or env_conflict(s["environment"], environment):
                    continue
                if ft and s["fault_types"]:
                    if ft & set(s["fault_types"]):
                        dup = s
                        break
                elif text_sim(title + " " + " ".join(symptoms), self.doc_text(s)) >= 0.6:
                    dup = s
                    break
            if dup:
                same_fix = text_sim(" ".join(fix_steps), " ".join(dup["fix_steps"])) >= 0.5
                alts = dup["alt_fixes"]
                if not same_fix and fix_steps and fix_steps not in alts:
                    alts = alts + [fix_steps]
                conn.execute(
                    "UPDATE solutions SET symptoms=?, environment=?, did_not_work=?, alt_fixes=?, confirm_count=confirm_count+1, "
                    "updated_at=? WHERE id=?",
                    (json.dumps(_merge_list(dup["symptoms"], symptoms, 8)), json.dumps(_merge_env(dup["environment"], environment)),
                     json.dumps(_merge_list(dup["did_not_work"], did_not_work, 10)), json.dumps(alts[:3]), now, dup["id"]))
                conn.execute("INSERT OR IGNORE INTO solution_sources VALUES (?,?,?,?)", (dup["id"], session_id, "confirmation", now))
                return dup["id"], True
            cur = conn.execute(
                "INSERT INTO solutions(title,symptoms,environment,fix_steps,did_not_work,alt_fixes,fault_types,status,"
                "confirm_count,fail_count,created_session_id,created_at,updated_at) VALUES (?,?,?,?,?,?,?,'verified',1,0,?,?,?)",
                (title, json.dumps(symptoms), json.dumps(environment), json.dumps(fix_steps), json.dumps(did_not_work),
                 "[]", json.dumps(sorted(ft)), session_id, now, now))
            conn.execute("INSERT INTO solution_sources VALUES (?,?,?,?)", (cur.lastrowid, session_id, "origin", now))
            return cur.lastrowid, False

    def add_confirmation(self, solution_id: int, session_id: str, env: dict, symptoms: list[str]) -> bool:
        """A customer confirmed a hub-served fix. Counted once per session."""
        now = utcnow()
        with connect(self.db_path) as conn, tx(conn):
            if conn.execute("INSERT OR IGNORE INTO solution_sources VALUES (?,?,?,?)",
                            (solution_id, session_id, "confirmation", now)).rowcount == 0:
                return False
            sol = self.get(conn, solution_id)
            conn.execute("UPDATE solutions SET confirm_count=confirm_count+1, symptoms=?, environment=?, updated_at=? WHERE id=?",
                         (json.dumps(_merge_list(sol["symptoms"], symptoms, 8)), json.dumps(_merge_env(sol["environment"], env)),
                          now, solution_id))
            return True

    def record_failure(self, solution_id: int | None) -> None:
        """The customer said a served fix did not work. Repeated failures demote it to 'unverified' (never served)."""
        if not solution_id:
            return
        with connect(self.db_path) as conn, tx(conn):
            conn.execute("UPDATE solutions SET fail_count=fail_count+1, updated_at=? WHERE id=?", (utcnow(), solution_id))
            conn.execute("UPDATE solutions SET status='unverified' WHERE id=? AND fail_count>=3 AND fail_count>confirm_count",
                         (solution_id,))
