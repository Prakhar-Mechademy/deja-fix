import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dejafix.api import create_app          # noqa: E402
from dejafix.config import Settings         # noqa: E402
from dejafix.llm import MockProvider        # noqa: E402

PRIYA_1 = ("Hi, I'm Priya Sharma, email priya.sharma@example.com, order #ORD-48213. My CloudNotes app (v4.2.1) on my "
           "iPhone 14 with iOS 17.2 has stopped syncing. Notes I write on my iPad never show up on my phone.")
PRIYA_2 = "I already tried restarting the phone and turning Background App Refresh on, but it didn't help."
PRIYA_3 = "Signing out and back in did the trick, it's syncing again. Thanks!"
RAHUL_1 = "Anything I type in CloudNotes on one device never turns up on the other. They just don't match anymore."
RAHUL_2 = "Yes, it's an iPhone 13 on iOS 17.1."
RAHUL_3 = "That worked, thanks!"


class Harness:
    """A running app on a temp DB. `restart()` builds a brand-new app on the SAME database file."""

    def __init__(self, path, provider=None, **overrides):
        self.settings = Settings(db_path=str(path), **overrides)
        self.provider = provider or MockProvider()
        self.restart()

    def restart(self):
        self.app = create_app(self.settings, self.provider)
        self.client = TestClient(self.app)
        self.svc = self.app.state.svc

    def user(self, name):
        return self.client.post("/users", json={"display_name": name}).json()["user_id"]

    def session(self, uid):
        return self.client.post("/sessions", headers={"X-User-Id": uid}).json()["session_id"]

    def say(self, uid, sid, text, expect=200):
        r = self.client.post(f"/sessions/{sid}/messages", json={"content": text}, headers={"X-User-Id": uid})
        assert r.status_code == expect, r.text
        return r.json()

    def solutions(self):
        return self.client.get("/solutions").json()

    def usage(self):
        return {r["session_id"]: r for r in self.client.get("/usage").json()}

    def priya_session_1(self, name="Priya Sharma"):
        uid = self.user(name)
        sid = self.session(uid)
        self.say(uid, sid, PRIYA_1)
        self.say(uid, sid, PRIYA_2)
        self.say(uid, sid, PRIYA_3)
        return uid, sid


@pytest.fixture
def h(tmp_path):
    return Harness(tmp_path / "t.db")
