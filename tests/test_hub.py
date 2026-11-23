"""Offline tests for app/hub.py (fake embeddings and a fake judge, no network).

    python tests/test_hub.py
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["DEJAFIX_DB"] = str(Path(tempfile.mkdtemp()) / "test_hub.db")

from app import config, db, hub, llm  # noqa: E402

VECS = {
    "reworded": [0.90, 0.436, 0.0],       # cosine ~0.90 to solution 1
    "lookalike": [0.91, 0.415, 0.0],      # cosine ~0.91: HIGHER than reworded, but a different fault
    "unrelated": [0.0, 1.0, 0.0],         # cosine 0
}

embed_calls = []
judge_calls = []
judge_answer = {"value": {"same_fault": True, "reason": "ok"}}


def fake_embed(text, **kw):
    embed_calls.append(text)
    return VECS[text]


def fake_judge(messages, **kw):
    judge_calls.append(messages[0]["content"])
    ans = judge_answer["value"]
    if isinstance(ans, Exception):
        raise ans
    return ans


llm.embed = fake_embed
llm.generate_json = fake_judge


def reset(answer):
    embed_calls.clear()
    judge_calls.clear()
    judge_answer["value"] = answer


def test_all():
    db.init_db()
    db.get_or_create_user("u1")
    sid = db.create_session("u1")
    find = lambda q: hub.find_match(q, session_id=sid, user_id="u1")  # noqa: E731

    # 1. Empty hub: nothing found, and no embedding/judge call is made
    reset({"same_fault": True})
    assert find("reworded") is None
    assert embed_calls == [] and judge_calls == []

    sol1 = db.add_solution(sid, "App sync failure", "Data stalls between devices",
                           {"device": "iPhone", "os": "iOS 17"}, ["Sign out", "Sign in"],
                           embedding=[1.0, 0.0, 0.0])

    # 2. Reworded problem: judge says same fault -> match
    reset({"same_fault": True, "reason": "same sync stall"})
    m = find("reworded")
    assert m and m["solution"]["id"] == sol1 and m["score"] > 0.85

    # 3. Look-alike scores HIGHER than the reworded one, but judge says different fault -> no match
    reset({"same_fault": False, "reason": "different failure"})
    assert find("lookalike") is None
    assert len(judge_calls) == 1

    # 4. Unrelated problem: below the similarity floor, judge never called
    reset({"same_fault": True})
    assert find("unrelated") is None
    assert judge_calls == []

    # 5. Unverified solutions are never matched
    db.add_solution(sid, "Unverified thing", "x", {}, ["y"], embedding=[0.0, 1.0, 0.0], status="unverified")
    reset({"same_fault": True})
    assert find("unrelated") is None
    assert judge_calls == []

    # 6. Malformed judge output -> treated as no match (caller escalates)
    reset(llm.LLMError("malformed", "bad json"))
    assert find("reworded") is None

    # 7. A provider outage in the judge propagates so the caller can show a plain message
    reset(llm.LLMError("outage", "503"))
    try:
        find("reworded")
        raise AssertionError("expected LLMError")
    except llm.LLMError as e:
        assert e.kind == "outage"

    # 8. At most TOP_K judge calls per search
    for i in range(5):
        db.add_solution(sid, f"Similar {i}", "stalls", {}, ["z"], embedding=[1.0, 0.0, 0.0])
    reset({"same_fault": False})
    assert find("reworded") is None
    assert len(judge_calls) == hub.TOP_K

    # 9. The judge sees the saved solution's environment
    reset({"same_fault": False})
    find("reworded")
    assert any("iOS 17" in c for c in judge_calls)

    print("All hub checks passed.")


if __name__ == "__main__":
    test_all()