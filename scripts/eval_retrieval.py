"""
Paraphrase & Negative-Pair Retrieval Evaluation Harness (CI-friendly).
Runs deterministically under DEJAFIX_MOCK_LLM=1 by default (or live API if --live is passed).
"""
import argparse
import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


PARAPHRASE_CASES = [
    (
        "Paraphrase 1: Upload hangs before transferring",
        "My NimbusSync app on macOS 15.1 v4.2.1 hangs indefinitely before transferring any new files.",
    ),
    (
        "Paraphrase 2: Queue stuck preparing",
        "Cloud drive client on Windows 11 v4.2.1 stays stuck preparing upload queue and never pushes changes.",
    ),
    (
        "Paraphrase 3: Ambiguous rewording triggers clarify or hub",
        "Hey, my cloud drive client refuses to upload any new files and sits stuck forever.",
    ),
]

NEGATIVE_MATCH_CASES = [
    (
        "Negative 1: 401 2FA auth failure",
        "NimbusSync on Windows 11 v4.2.1 won't sync because it throws Error 401 unauthorized after rotating 2FA credentials.",
    ),
    (
        "Negative 2: Startup segfault crash",
        "NimbusSync on Windows 11 v4.2.1 crashes immediately on launch with a startup segfault before opening.",
    ),
    (
        "Negative 3: Disk full ENOSPC",
        "NimbusSync sync fails with ENOSPC error because my local SSD storage disk is full.",
    ),
    (
        "Negative 4: SSL proxy handshake failure",
        "NimbusSync sync fails behind our corporate VPN proxy with an SSL certificate handshake error.",
    ),
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Déjà Fix Retrieval & Negative-Match Eval")
    parser.add_argument("--live", action="store_true", help="Use live Gemini API instead of mock mode.")
    args = parser.parse_args()
    if not args.live:
        os.environ["DEJAFIX_MOCK_LLM"] = "1"

    from backend.db import close_db, init_db, list_solutions
    from backend.engine import handle_chat_turn, start_user_session
    from backend.env_loader import load_dotenv
    from backend.rate_limit import RateLimitError, enforce_escalation_rate_limit

    load_dotenv(ROOT_DIR / ".env")
    eval_db = ROOT_DIR / "data" / "eval-retrieval.db"
    for suffix in ("", "-wal", "-shm"):
        p = Path(f"{eval_db}{suffix}")
        if p.exists():
            p.unlink()

    init_db(str(eval_db))

    # Seed Solution 1 legitimately via Session 1 escalation + confirmation
    s1 = start_user_session("seed_user", "Taylor Reed", "Seed sync stall")
    s1_id = int(s1["session"]["id"])
    handle_chat_turn(
        "seed_user",
        'My NimbusSync desktop app v4.2.1 on Windows 11 (Dell XPS 15) is stuck at "Preparing sync..." and won\'t upload files.',
        display_name="Taylor Reed",
        session_id=s1_id,
    )
    handle_chat_turn(
        "seed_user",
        "Clearing the local index lock and restarting worked!",
        display_name="Taylor Reed",
        session_id=s1_id,
    )
    assert len(list_solutions(False)) == 1, "Failed to populate initial verified solution"

    failures = 0
    print("=== Paraphrase Recall Suite (Expected: hub or clarify, never escalation) ===")
    for idx, (label, prompt) in enumerate(PARAPHRASE_CASES):
        sess = start_user_session(f"para_u_{idx}", f"Para User {idx}", label)
        res = handle_chat_turn(
            f"para_u_{idx}",
            prompt,
            display_name=f"Para User {idx}",
            session_id=int(sess["session"]["id"]),
        )
        ok = res["outcome"] in ("hub", "clarify")
        if not ok:
            failures += 1
        print(f"  [{'PASS' if ok else 'FAIL'}] {label} -> outcome={res['outcome']}")

    print("\n=== Negative-Match Precision Suite (Expected: escalation, never hub) ===")
    for idx, (label, prompt) in enumerate(NEGATIVE_MATCH_CASES):
        sess = start_user_session(f"neg_u_{idx}", f"Neg User {idx}", label)
        res = handle_chat_turn(
            f"neg_u_{idx}",
            prompt,
            display_name=f"Neg User {idx}",
            session_id=int(sess["session"]["id"]),
        )
        ok = res["outcome"] == "escalation"
        if not ok:
            failures += 1
        print(f"  [{'PASS' if ok else 'FAIL'}] {label} -> outcome={res['outcome']}")

    # Verify per-user 429 escalation rate limit
    rate_limit_hit = False
    try:
        for _ in range(10):
            enforce_escalation_rate_limit(int(s1["user"]["id"]))
    except RateLimitError as err:
        rate_limit_hit = err.status == 429
    if not rate_limit_hit:
        failures += 1
    print(f"\n=== Robustness ===")
    print(f"  [{'PASS' if rate_limit_hit else 'FAIL'}] Escalation rate limit returns 429")

    close_db()
    for suffix in ("", "-wal", "-shm"):
        p = Path(f"{eval_db}{suffix}")
        if p.exists():
            p.unlink()

    if failures > 0:
        print(f"\nEval FAILED ({failures} failing case(s)).")
        sys.exit(1)
    print("\nAll paraphrase, negative-match, and robustness checks PASSED.")


if __name__ == "__main__":
    main()
