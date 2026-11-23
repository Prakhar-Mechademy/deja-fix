"""Offline tests for app/llm.py using a fake client (no network, no API key needed).

    python tests/test_llm.py        (or: pytest -s)
"""
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["DEJAFIX_DB"] = str(Path(tempfile.mkdtemp()) / "test_llm.db")

from app import config, db, llm  # noqa: E402


class FakeModels:
    def __init__(self, behaviours):
        self.behaviours = list(behaviours)

    def generate_content(self, **kwargs):
        item = self.behaviours.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeClient:
    def __init__(self, behaviours):
        self.models = FakeModels(behaviours)


def reply(text, tin=100, tout=40):
    usage = SimpleNamespace(prompt_token_count=tin, candidates_token_count=tout, thoughts_token_count=0)
    return SimpleNamespace(text=text, usage_metadata=usage)


MSGS = [{"role": "user", "content": "My app won't sync"}]


def setup():
    db.init_db()
    db.get_or_create_user("u1")
    sid = db.create_session("u1")
    llm.time.sleep = lambda s: None  # don't actually wait during retries
    config.LLM_MAX_RETRIES = 1
    return sid


def calls(sid):
    with db.get_conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM llm_calls WHERE session_id = ? ORDER BY id", (sid,))]


def test_all():
    sid = setup()

    # 1. Success: tokens are logged against the session
    llm._client = FakeClient([reply("Try re-linking.", 120, 30)])
    assert llm.generate(MSGS, session_id=sid, user_id="u1", purpose="escalation") == "Try re-linking."
    row = calls(sid)[-1]
    assert (row["tokens_in"], row["tokens_out"], row["status"]) == (120, 30, "ok")

    # 2. Timeout then success: retried, both attempts logged
    llm._client = FakeClient([TimeoutError("request timed out"), reply("ok")])
    assert llm.generate(MSGS, session_id=sid, user_id="u1", purpose="escalation") == "ok"
    assert [c["status"] for c in calls(sid)[-2:]] == ["timeout", "ok"]

    # 3. Persistent outage: raises LLMError with a plain user message, no crash
    class Unavailable(Exception):
        code = 503

    llm._client = FakeClient([Unavailable("503 unavailable"), Unavailable("503 unavailable")])
    try:
        llm.generate(MSGS, session_id=sid, user_id="u1", purpose="escalation")
        raise AssertionError("expected LLMError")
    except llm.LLMError as e:
        assert e.kind == "outage"
        assert "try again" in e.user_message.lower()

    # 4. Malformed JSON
    llm._client = FakeClient([reply("not json at all")])
    try:
        llm.generate_json(MSGS, session_id=sid, user_id="u1", purpose="extraction")
        raise AssertionError("expected LLMError")
    except llm.LLMError as e:
        assert e.kind == "malformed"

    # 5. JSON wrapped in code fences still parses
    llm._client = FakeClient([reply('```json\n{"a": 1}\n```')])
    assert llm.generate_json(MSGS, session_id=sid, user_id="u1", purpose="extraction") == {"a": 1}

    # 6. Empty response counts as malformed
    llm._client = FakeClient([SimpleNamespace(text=None, usage_metadata=None)])
    try:
        llm.generate(MSGS, session_id=sid, user_id="u1", purpose="persona")
        raise AssertionError("expected LLMError")
    except llm.LLMError as e:
        assert e.kind == "malformed"

    # 7. Missing API key is reported cleanly (no crash)
    llm._client = None
    os.environ.pop("GEMINI_API_KEY", None)
    try:
        llm.generate(MSGS, session_id=sid, user_id="u1", purpose="persona")
        raise AssertionError("expected LLMError")
    except llm.LLMError as e:
        assert e.kind == "config"

    print("All LLM checks passed.")


if __name__ == "__main__":
    test_all()