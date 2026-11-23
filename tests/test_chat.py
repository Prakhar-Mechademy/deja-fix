"""Tests for chat behavior."""

import tempfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.db.database import Base, get_db
import app.db.models  # noqa: F401
from app.main import app


@pytest.fixture
def api_database():
    database_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    database_file.close()
    engine = create_engine(
        f"sqlite:///{database_file.name}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    yield engine, TestingSessionLocal

    app.dependency_overrides.clear()
    engine.dispose()


def test_create_customers_sessions_and_messages(api_database):
    engine, SessionLocal = api_database

    with TestClient(app) as client:
        response_a = client.post("/users", json={"external_id": "customer-a-api"})
        response_b = client.post("/users", json={"external_id": "customer-b-api"})
        assert response_a.status_code == 200
        assert response_b.status_code == 200

        user_a = response_a.json()
        user_b = response_b.json()
        session_a = client.post("/sessions", json={"user_id": user_a["id"], "title": "A's session"})
        session_b = client.post("/sessions", json={"user_id": user_b["id"], "title": "B's session"})
        assert session_a.status_code == 201
        assert session_b.status_code == 201

        session_a_id = session_a.json()["id"]
        message = client.post("/chat/message", json={
            "session_id": session_a_id,
            "user_id": user_a["id"],
            "role": "user",
            "content": "Sync is not working",
        })
        assert message.status_code == 201

        reply = message.json()
        assert set(reply) == {"reply", "source"}
        assert "solution_id" not in reply
        assert "solution" not in reply

        history = client.get(
            f"/sessions/{session_a_id}/messages",
            params={"user_id": user_a["id"]},
        )
        assert history.status_code == 200
        assert [item["content"] for item in history.json()] == ["Sync is not working"]


def test_resume_session_and_list_customer_sessions(api_database):
    engine, SessionLocal = api_database

    with TestClient(app) as client:
        user = client.post("/users", json={"external_id": "resume-user"}).json()
        created = client.post("/sessions", json={"user_id": user["id"], "title": "Resume me"})
        session_id = created.json()["id"]

        client.post("/chat/message", json={
            "session_id": session_id,
            "user_id": user["id"],
            "role": "assistant",
            "content": "Try clearing the cache",
        })

        resumed = client.get(f"/sessions/{session_id}", params={"user_id": user["id"]})
        listed = client.get(f"/users/{user['id']}/sessions")

        assert resumed.status_code == 200
        assert resumed.json()["title"] == "Resume me"
        assert listed.status_code == 200
        assert any(item["id"] == session_id for item in listed.json())


def test_cross_customer_access_and_validation_errors(api_database):
    engine, SessionLocal = api_database

    with TestClient(app) as client:
        user_a = client.post("/users", json={"external_id": "isolated-a"}).json()
        user_b = client.post("/users", json={"external_id": "isolated-b"}).json()
        session_a = client.post("/sessions", json={"user_id": user_a["id"]}).json()

        forbidden_history = client.get(
            f"/sessions/{session_a['id']}/messages",
            params={"user_id": user_b["id"]},
        )
        assert forbidden_history.status_code == 404

        forbidden_session = client.get(
            f"/sessions/{session_a['id']}",
            params={"user_id": user_b["id"]},
        )
        assert forbidden_session.status_code == 404

        missing = client.get("/users/missing-user")
        assert missing.status_code == 404

        invalid = client.post("/users", json={"external_id": ""})
        assert invalid.status_code == 422


def test_admin_usage_fail_closed_and_safe_comparison(api_database):
    original_token = settings.ADMIN_TOKEN

    try:
        settings.ADMIN_TOKEN = None
        with TestClient(app) as client:
            assert client.get("/admin/usage").status_code == 401
            assert client.get("/admin/usage", headers={"X-Admin-Token": ""}).status_code == 401

        settings.ADMIN_TOKEN = "correct-admin-token"
        with TestClient(app) as client:
            assert client.get("/admin/usage").status_code == 401
            assert client.get("/admin/usage", headers={"X-Admin-Token": "wrong-token"}).status_code == 403

            usage = client.get("/admin/usage", headers={"X-Admin-Token": "correct-admin-token"})
            assert usage.status_code == 200
            assert set(usage.json()) == {"call_count", "input_tokens", "output_tokens", "total_tokens", "by_purpose"}
    finally:
        settings.ADMIN_TOKEN = original_token


def test_persistence_after_opening_a_new_api_session(api_database):
    engine, SessionLocal = api_database

    with TestClient(app) as client:
        user = client.post("/users", json={"external_id": "persisted-api-user"}).json()
        session = client.post("/sessions", json={"user_id": user["id"]}).json()
        client.post("/chat/message", json={
            "session_id": session["id"],
            "user_id": user["id"],
            "role": "user",
            "content": "Persist this message",
        })

    engine.dispose()

    engine, SessionLocal = api_database
    with TestClient(app) as client:
        history = client.get(
            f"/sessions/{session['id']}/messages",
            params={"user_id": user["id"]},
        )
        assert history.status_code == 200
        assert history.json()[0]["content"] == "Persist this message"
