import uuid, json
from models.user import User
from models.session import ChatSession
from models.message import Message
from models.solution import Solution
from agents import extractor_agent

def test_extractor_reads_persisted_transcript_and_scrubs_pii(db, monkeypatch):
    user = User(id=str(uuid.uuid4()), external_id="customer"); db.add(user)
    session = ChatSession(id=str(uuid.uuid4()), user_id=user.id); db.add(session); db.commit()
    db.add_all([
        Message(session_id=session.id,user_id=user.id,role="user",content="My name is Ribence and my email is ribence@example.com. Desktop app sync fails on Windows 11."),
        Message(session_id=session.id,user_id=user.id,role="assistant",content="Try enabling background sync in Settings.",source="llm"),
        Message(session_id=session.id,user_id=user.id,role="user",content="That worked; it syncs now.")])
    db.commit()
    payload = {"solutions": [{"symptoms":"Desktop app sync fails on Windows 11 for Ribence ribence@example.com", "environment":{"device":"Laptop","operating_system":"Windows 11"}, "fix_steps":"Enable background sync", "failed_attempts":[]}]}
    monkeypatch.setattr(extractor_agent, "generate_json", lambda *a, **k: {"data": payload, "text":"{}", "input_tokens":10, "output_tokens":5, "model":"test"})
    monkeypatch.setattr(extractor_agent, "embed_text", lambda text, **kwargs: None)
    saved = extractor_agent.extract_confirmed_solutions(db, session.id)
    assert len(saved) == 1
    row = db.query(Solution).one()
    assert row.verified
    assert "ribence@example.com" not in row.symptoms.lower()
    assert "Windows 11" in row.environment

def test_unconfirmed_transcript_is_never_extracted(db, monkeypatch):
    user = User(id=str(uuid.uuid4()), external_id="customer-negative"); db.add(user)
    session = ChatSession(id=str(uuid.uuid4()), user_id=user.id); db.add(session); db.commit()
    db.add_all([
        Message(session_id=session.id,user_id=user.id,role="user",content="Desktop sync is broken on Windows 11."),
        Message(session_id=session.id,user_id=user.id,role="assistant",content="Try enabling sync.",source="llm"),
        Message(session_id=session.id,user_id=user.id,role="user",content="That did not work; still broken.")])
    db.commit()
    assert extractor_agent.extract_confirmed_solutions(db, session.id) == []
    assert db.query(Solution).count() == 0
