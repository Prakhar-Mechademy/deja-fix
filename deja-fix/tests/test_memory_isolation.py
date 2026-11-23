import uuid
from models.user import User
from models.session import ChatSession
from models.message import Message
from services.memory_service import owned_session, latest_user_problem

def test_customer_history_is_isolated(db):
    a, b = User(id=str(uuid.uuid4()), external_id="a"), User(id=str(uuid.uuid4()), external_id="b")
    db.add_all([a,b]); db.commit()
    sa, sb = ChatSession(id=str(uuid.uuid4()), user_id=a.id), ChatSession(id=str(uuid.uuid4()), user_id=b.id)
    db.add_all([sa,sb]); db.commit()
    db.add_all([Message(session_id=sa.id,user_id=a.id,role="user",content="My sync is broken"),
                Message(session_id=sb.id,user_id=b.id,role="user",content="My printer is offline")]); db.commit()
    assert "sync" in latest_user_problem(db, a.id)
    assert "printer" in latest_user_problem(db, b.id)
    assert owned_session(db, a.id, sb.id) is None
