"""Runs the three-session demo scenario against an EMPTY database (it deletes the DB file first).

    python scripts/run_scenario.py                 # offline MockProvider, no API key needed
    LLM_PROVIDER=anthropic python scripts/run_scenario.py   # real provider (needs ANTHROPIC_API_KEY)

The server is "restarted" (a brand-new app object on the same SQLite file) between the sessions.
Exit code 0 = every check passed.
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from dejafix.api import create_app  # noqa: E402
from dejafix.config import Settings, load_dotenv  # noqa: E402
from dejafix.llm import MockProvider, build_provider  # noqa: E402

A1 = ("Hi, I'm Priya Sharma, email priya.sharma@example.com, order #ORD-48213. My CloudNotes app (v4.2.1) on my iPhone 14 "
      "with iOS 17.2 has stopped syncing. Notes I write on my iPad never show up on my phone.")
A2 = "I already tried restarting the phone and turning Background App Refresh on, but it didn't help."
A3 = "Signing out and back in did the trick, it's syncing again. Thanks!"
B1 = "Anything I type in CloudNotes on one device never turns up on the other. They just don't match anymore."
B2 = "Yes, it's an iPhone 13 on iOS 17.1."
B3 = "That worked, thanks!"
results = []


def check(label, ok):
    results.append(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")


def main():
    load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="scenario.db")
    ap.add_argument("--mock", action="store_true", help="force the offline MockProvider")
    args = ap.parse_args()
    for suffix in ("", "-wal", "-shm"):
        Path(args.db + suffix).unlink(missing_ok=True)
    settings = Settings.from_env()
    settings.db_path = args.db
    provider = MockProvider() if args.mock else build_provider(settings)
    print(f"Provider: {provider.name}   Database: {args.db} (fresh)\n")

    def boot():
        return TestClient(create_app(settings, provider))

    def user(c, n):
        return c.post("/users", json={"display_name": n}).json()["user_id"]

    def say(c, u, sid, text):
        j = c.post(f"/sessions/{sid}/messages", json={"content": text}, headers={"X-User-Id": u}).json()
        print(f"\n> {text}\n[{j.get('source')}/{j.get('state')}] {j.get('reply') or j}")
        return j

    c = boot()
    print("=== Session 1: escalation + extraction (Customer A) ===")
    check("database starts empty", c.get("/solutions").json() == [])
    a = user(c, "Priya Sharma")
    s1 = c.post("/sessions", headers={"X-User-Id": a}).json()["session_id"]
    r1 = say(c, a, s1, A1)
    check("hub empty -> escalated to the backend LLM", r1["escalated"] and r1["source"] == "llm")
    say(c, a, s1, A2)
    say(c, a, s1, A3)
    sols = c.get("/solutions").json()
    check("exactly one solution saved after the customer confirmed", len(sols) == 1)
    if sols:
        s = sols[0]
        blob = str(s).lower()
        check("PII removed (name, email, order number)", not any(x in blob for x in ("priya", "sharma", "example.com", "48213")))
        check("environment kept (device, OS, app version)",
              (s["environment"].get("device"), s["environment"].get("os"), s["environment"].get("app_version")) == ("iPhone 14", "iOS 17.2", "4.2.1"))
        check("symptoms, fix steps and 'did not work' saved", bool(s["symptoms"] and s["fix_steps"] and s["did_not_work"]))
        check("solution linked to its session", s["created_session_id"] == s1)

    print("\n--- server restart ---")
    c = boot()
    check("solution survives the restart", len(c.get("/solutions").json()) == 1)

    print("\n=== Session 2: reuse (Customer B, reworded problem) ===")
    b = user(c, "Rahul Verma")
    s2 = c.post("/sessions", headers={"X-User-Id": b}).json()["session_id"]
    r = say(c, b, s2, B1)
    check("asks a clarifying question first", r["state"] == "clarifying" and "?" in r["reply"] and not r["escalated"])
    r = say(c, b, s2, B2)
    check("serves the fix from the hub, no escalation", r["source"] == "hub" and not r["escalated"])
    say(c, b, s2, B3)
    check("duplicate merged (still 1 solution, confirm_count 2)",
          [x["confirm_count"] for x in c.get("/solutions").json()] == [2])

    print("\n=== Token log ===")
    u = {x["session_id"]: x for x in c.get("/usage").json()}
    for name, sid in (("session 1 (escalation)", s1), ("session 2 (hub)", s2)):
        x = u[sid]
        print(f"  {name:24} in={x['tokens_in']:5} out={x['tokens_out']:5} total={x['tokens_total']:5} "
              f"calls={x['llm_calls']} served_from={x['served_from']}")
    check("hub session cost fewer tokens than the escalated session", u[s2]["tokens_total"] < u[s1]["tokens_total"])

    print("\n=== Session 3: memory (Customer A returns) ===")
    c = boot()
    g = c.post("/sessions", headers={"X-User-Id": a}).json()["greeting"]
    print("  GREETING:", g)
    check("greets with A's own history", "Welcome back" in g and "sync" in g.lower())
    check("shows nothing from Customer B", not any(x in g.lower() for x in ("rahul", "verma")))

    print(f"\n{sum(results)}/{len(results)} checks passed")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
