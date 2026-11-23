"""Three-session acceptance demo. Uses deterministic mock LLM calls by default.
Run from repository root: python scripts/three_session_demo.py
Use --live to call Gemini (requires GEMINI_API_KEY in backend/.env).
"""
import argparse, json, sys, uuid
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from database import Base, engine, SessionLocal, init_db
from models.user import User
from models.session import ChatSession
from models.message import Message

parser = argparse.ArgumentParser()
parser.add_argument("--live", action="store_true", help="Use the configured Gemini provider instead of mocks")
args = parser.parse_args()

if not args.live:
    from services import embedding_service, retrieval_service
    from agents import persona_agent, extractor_agent
    from services.embedding_service import lexical_vector
    import re
    def demo_embedding(text):
        normalized = text.lower()
        for pattern, replacement in [(r"synchroniz(?:e|es|ed|ing)", "sync"), (r"sync(?:s|ed|ing)", "sync"),
                                      (r"application", "app"), (r"cannot", "not"), (r"aren't", "not")]:
            normalized = re.sub(pattern, replacement, normalized)
        return lexical_vector(normalized)
    embedding_service.embed_text = lambda text, **kwargs: demo_embedding(text)
    retrieval_service.embed_text = lambda text, **kwargs: demo_embedding(text)
    extractor_agent.embed_text = lambda text, **kwargs: demo_embedding(text)
    def mock_solve(db, sid, transcript):
        from utils.token_tracker import record_usage
        record_usage(db, sid, {"model": "mock-gemini", "input_tokens": 180, "output_tokens": 90}, "solve")
        return "1. Open Settings.\n2. Enable background sync.\n3. Restart the app and verify sync status."
    persona_agent.solve = mock_solve
    def mock_extract(prompt, purpose="extract"):
        data = {"solutions": [{
            "symptoms": "Desktop application does not synchronize files on Windows 11.",
            "environment": {"device": "Windows desktop", "operating_system": "Windows 11", "app_version": "5.2"},
            "fix_steps": "1. Open Settings. 2. Enable background sync. 3. Restart the app and verify sync status.",
            "failed_attempts": ["Restarting the app alone did not help."]
        }]}
        return {"data": data, "text": json.dumps(data), "input_tokens": 120, "output_tokens": 70, "model": "mock-gemini"}
    extractor_agent.generate_json = mock_extract

Base.metadata.drop_all(bind=engine)
init_db()
db = SessionLocal()
try:
    user_a = User(id=str(uuid.uuid4()), external_id="demo-customer-a", display_name="Customer A")
    user_b = User(id=str(uuid.uuid4()), external_id="demo-customer-b", display_name="Customer B")
    db.add_all([user_a, user_b]); db.commit()
    s1 = ChatSession(id=str(uuid.uuid4()), user_id=user_a.id, title="Sync issue - first contact")
    db.add(s1); db.commit()
    from services.chat_orchestrator import process_message
    first = process_message(db, user_a.id, s1.id,
        "My desktop app cannot sync files on Windows 11. My name is Ribence Kadel and my email is ribence@example.com. Order number ORD-92831.")
    print("SESSION 1 - escalation:", first["source"])
    confirmation = process_message(db, user_a.id, s1.id, "That worked, the files are syncing now.")
    print("SESSION 1 - confirmation:", confirmation["reply"])
    s2 = ChatSession(id=str(uuid.uuid4()), user_id=user_b.id, title="Sync issue - reuse")
    db.add(s2); db.commit()
    second = process_message(db, user_b.id, s2.id,
        "Files aren't syncing in my desktop application on Windows 11.")
    print("SESSION 2 - reuse:", second["source"])
    from models.llm_log import LLMLog
    for label, session_id in [("SESSION 1", s1.id), ("SESSION 2", s2.id)]:
        rows = db.query(LLMLog).filter(LLMLog.session_id == session_id).all()
        total = sum(x.input_tokens + x.output_tokens for x in rows)
        print(f"{label} - logged LLM tokens: {total}")
    s3 = ChatSession(id=str(uuid.uuid4()), user_id=user_a.id, title="Returning customer")
    db.add(s3); db.commit()
    from services.memory_service import latest_user_problem
    print("SESSION 3 - Customer A memory:", latest_user_problem(db, user_a.id))
    print("SESSION 3 - Customer B history isolated:", latest_user_problem(db, user_b.id) != latest_user_problem(db, user_a.id))
    solutions = db.query(__import__('models.solution', fromlist=['Solution']).Solution).all()
    print("Verified solutions:", len([x for x in solutions if x.verified]))
    if not args.live:
        print("DEMO MODE: deterministic mocked Gemini responses; no API key or network needed.")
finally:
    db.close()
