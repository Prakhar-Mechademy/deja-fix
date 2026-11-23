import pytest
from conftest import PRIYA_1, PRIYA_2, PRIYA_3, Harness
from dejafix.llm import MockProvider


@pytest.mark.parametrize("mode", ["timeout", "unavailable", "malformed"])
def test_llm_failures_give_plain_message_not_a_crash(tmp_path, mode):
    h = Harness(tmp_path / "f.db", MockProvider(fail=mode))
    uid = h.user("Priya Sharma")
    sid = h.session(uid)
    r = h.say(uid, sid, PRIYA_1)                       # HTTP 200, plain message
    assert r["source"] == "system" and "Traceback" not in r["reply"] and len(r["reply"]) > 20
    calls = h.client.get("/usage/calls", params={"session_id": sid}).json()
    assert calls and calls[0]["status"] != "ok"
    assert h.solutions() == []


def test_extraction_outage_is_retried_and_solution_saved_later(tmp_path):
    prov = MockProvider()
    h = Harness(tmp_path / "x.db", prov)
    uid = h.user("Priya Sharma")
    sid = h.session(uid)
    h.say(uid, sid, PRIYA_1)
    h.say(uid, sid, PRIYA_2)
    prov.fail, prov.fail_purposes = "garbage", {"extract"}        # malformed extraction output
    r = h.say(uid, sid, PRIYA_3)
    assert r["state"] == "resolved" and "retry" in r["reply"] and h.solutions() == []
    prov.fail = None
    h.restart()
    h.svc.sweep_pending()
    assert len(h.solutions()) == 1


def test_rate_limit_messages_and_tighter_escalation_limit_return_429(tmp_path):
    h = Harness(tmp_path / "r.db", rate_messages=(4, 60.0), rate_escalations=(2, 60.0))
    uid = h.user("Priya Sharma")
    sid = h.session(uid)
    h.say(uid, sid, "My toaster burns everything.")
    h.say(uid, sid, "It happens with every slice of bread.")       # 2nd escalation
    r = h.client.post(f"/sessions/{sid}/messages", json={"content": "Still burning toast."}, headers={"X-User-Id": uid})
    assert r.status_code == 429 and r.json()["scope"] == "escalations" and "Retry-After" in r.headers
    n = h.client.get(f"/sessions/{sid}", headers={"X-User-Id": uid}).json()["messages"]
    h.say(uid, sid, "thanks")
    h.say(uid, sid, "hi", expect=429)                              # message limit (4) reached
    assert len(n) == 5                                            # 429s left no trace in the transcript


def test_bad_requests(h):
    assert h.client.post("/users", json={"display_name": "  "}).status_code == 400
    assert h.client.post("/sessions", headers={"X-User-Id": "nobody"}).status_code == 404
    uid = h.user("A B")
    sid = h.session(uid)
    assert h.client.post(f"/sessions/{sid}/messages", json={"content": " "}, headers={"X-User-Id": uid}).status_code == 400


def test_closed_session_cannot_be_written_but_can_be_read(h):
    uid = h.user("A B")
    sid = h.session(uid)
    h.client.post(f"/sessions/{sid}/close", headers={"X-User-Id": uid})
    h.say(uid, sid, "hello", expect=400)
    assert h.client.get(f"/sessions/{sid}", headers={"X-User-Id": uid}).status_code == 200
