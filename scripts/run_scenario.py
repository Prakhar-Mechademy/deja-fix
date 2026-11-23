"""Runs the three-session demo scenario against an EMPTY database, restarting the server between sessions.

    python scripts/run_scenario.py

Uses GEMINI_API_KEY from the environment / .env if present, otherwise the offline mock model."""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = int(os.environ.get("SCENARIO_PORT", "8765"))
BASE = f"http://127.0.0.1:{PORT}"
DB = ROOT / "data" / "scenario.db"


def http(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


class Server:
    def __init__(self, db=DB, **extra_env):
        self.proc, self.db, self.extra = None, db, {k: str(v) for k, v in extra_env.items()}

    def start(self):
        env = dict(os.environ, DEJA_DB=str(self.db), PYTHONUNBUFFERED="1", **self.extra)
        self.proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "dejafix.app:app", "--port", str(PORT),
                                      "--log-level", "warning"], cwd=ROOT, env=env)
        for _ in range(60):
            try:
                if http("GET", "/health")[0] == 200:
                    print(f"   [server started, provider={http('GET', '/health')[1]['provider']}]")
                    return
            except Exception:
                time.sleep(0.5)
        raise SystemExit("server did not start")

    def stop(self):
        if self.proc:
            self.proc.terminate()
            self.proc.wait(timeout=15)
            print("   [server stopped]")


def chat(user, name, turns, end=True):
    _, s = http("POST", "/sessions", {"user_id": user, "name": name})
    sid = s["session_id"]
    print(f"\n   Deja: {s['greeting']}")
    replies = []
    for text in turns:
        print(f"\n   {name}: {text}")
        code, r = http("POST", f"/sessions/{sid}/messages", {"user_id": user, "text": text})
        reply = r.get("reply") or r.get("message")
        print(f"   Deja [{r.get('source', code)}]: " + (reply or "").replace("\n", "\n         "))
        replies.append(r)
    extraction = http("POST", f"/sessions/{sid}/end?user_id={user}")[1] if end else None
    if extraction:
        print(f"\n   [session ended, extractor: {json.dumps(extraction.get('extraction'))}]")
    return sid, s["greeting"], replies


def reset_db(path):
    for f in (path, Path(str(path) + "-wal"), Path(str(path) + "-shm")):
        if f.exists():
            f.unlink()


def main():
    reset_db(DB)
    print("Empty database:", DB)
    server = Server()
    checks = []

    print("\n=== SESSION 1: Customer A, escalation and extraction ===")
    server.start()
    s1, _, r1 = chat("customer-a", "Rahul Sharma", [
        "Hi, I'm Rahul Sharma (rahul.sharma@example.com), order number ORD-58213. Since yesterday my notes stopped "
        "syncing. Edits I make on my Pixel 7 (Android 14, app version 5.2.1) don't show up on my laptop. "
        "I already tried logging out and back in, it didn't help.",
        "That worked! It's syncing again, thanks.",
    ])
    sols = http("GET", "/solutions")[1]
    checks.append(("Session 1 escalated to the LLM", r1[0].get("source") == "llm"))
    checks.append(("Solution 1 saved after confirmation", len(sols) == 1))
    blob = json.dumps(sols).lower()
    checks.append(("Saved solution has no name / email / order number",
                   not any(x in blob for x in ("rahul", "sharma", "example.com", "58213"))))
    checks.append(("Saved solution keeps device and OS", "pixel 7" in blob and "android 14" in blob))
    server.stop()

    print("\n=== SESSION 2: Customer B, same problem in different words (reuse) ===")
    server.start()
    s2, _, r2 = chat("customer-b", "Priya", [
        "My notes aren't updating across devices. I edit something on my phone but my computer still shows "
        "the old version.",
        "It's a Samsung Galaxy S23 on Android 14, latest app version. Yes, edits from the phone never reach "
        "the computer.",
        "Yes, that fixed it. Everything is updating now.",
    ])
    sols = http("GET", "/solutions")[1]
    checks.append(("Session 2 asked a clarifying question first", r2[0].get("state") == "clarifying"))
    checks.append(("Session 2 served the fix from the hub", r2[1].get("source") == "hub"))
    checks.append(("No escalation in session 2", all(r.get("source") != "llm" for r in r2)))
    checks.append(("Duplicate merged: still 1 solution, count 2", len(sols) == 1 and sols[0]["count"] == 2))
    server.stop()

    print("\n=== SESSION 3: Customer A returns (memory) ===")
    server.start()
    s3, greet, _ = chat("customer-a", "Rahul Sharma", [], end=True)
    checks.append(("Greeting remembers A's sync problem", "sync" in greet.lower()))
    checks.append(("Greeting shows nothing from B", not any(x in greet.lower() for x in ("samsung", "galaxy", "priya"))))

    usage = {u["session_id"]: u for u in http("GET", "/usage")[1]}
    print("\n=== TOKEN LOG (per session) ===")
    print(f"   {'session':<14}{'calls':>6}{'tokens in':>11}{'tokens out':>12}{'total':>8}{'escalations':>13}")
    for label, sid in (("1 (escalated)", s1), ("2 (from hub)", s2), ("3 (greeting)", s3)):
        u = usage.get(sid, {"calls": 0, "tokens_in": 0, "tokens_out": 0, "total_tokens": 0, "escalations": 0})
        print(f"   {label:<14}{u['calls']:>6}{u['tokens_in']:>11}{u['tokens_out']:>12}{u['total_tokens']:>8}"
              f"{u['escalations']:>13}")
    t1 = usage.get(s1, {}).get("total_tokens", 0)
    t2 = usage.get(s2, {}).get("total_tokens", 0)
    checks.append(("Session 2 (hub) cost fewer tokens than session 1 (escalation)", 0 < t2 < t1 or (t1 > 0 and t2 == 0)))
    server.stop()

    print("\n=== CHECKS ===")
    for name, ok in checks:
        print(f"   [{'PASS' if ok else 'FAIL'}] {name}")
    print("\nSaved solutions:\n" + json.dumps(sols, indent=2))
    sys.exit(0 if all(ok for _, ok in checks) else 1)


if __name__ == "__main__":
    main()
