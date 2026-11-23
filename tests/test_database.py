from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
import app.db.models  # noqa: F401 - register all models
from app.db.models import LLMUsage, Message, Session, Solution, SolutionSource, User


def make_database():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine)


def test_users_sessions_messages_and_isolation():
    engine, SessionLocal = make_database()

    with SessionLocal() as db:
        customer_a = User(external_id="customer-a")
        customer_b = User(external_id="customer-b")
        db.add_all([customer_a, customer_b])
        db.flush()

        session_a = Session(user_id=customer_a.id, title="Customer A session")
        session_b = Session(user_id=customer_b.id, title="Customer B session")
        db.add_all([session_a, session_b])
        db.flush()

        db.add_all([
            Message(session_id=session_a.id, user_id=customer_a.id, role="user", content="A private message"),
            Message(session_id=session_b.id, user_id=customer_b.id, role="user", content="B private message"),
        ])
        db.commit()

        customer_a_sessions = db.query(Session).filter_by(user_id=customer_a.id).all()
        customer_b_messages = (
            db.query(Message)
            .join(Session)
            .filter(Session.user_id == customer_b.id)
            .all()
        )

        assert customer_a_sessions == [session_a]
        assert len(customer_b_messages) == 1
        assert customer_b_messages[0].content == "B private message"

    engine.dispose()


def test_verified_solution_and_multiple_confirmations():
    engine, SessionLocal = make_database()

    with SessionLocal() as db:
        user_a = User(external_id="customer-a")
        user_b = User(external_id="customer-b")
        db.add_all([user_a, user_b])
        db.flush()

        session_a = Session(user_id=user_a.id)
        session_b = Session(user_id=user_b.id)
        db.add_all([session_a, session_b])
        db.flush()

        confirmation_a = Message(session_id=session_a.id, user_id=user_a.id, role="user", content="It worked")
        confirmation_b = Message(session_id=session_b.id, user_id=user_b.id, role="user", content="Fixed")
        db.add_all([confirmation_a, confirmation_b])
        db.flush()

        solution = Solution(
            problem="Application cannot synchronise files",
            symptoms=["Sync stays pending"],
            fix_steps=["Clear the sync cache", "Restart the application"],
            status="VERIFIED",
        )
        db.add(solution)
        db.flush()

        db.add_all([
            SolutionSource(solution_id=solution.id, session_id=session_a.id, confirmation_message_id=confirmation_a.id),
            SolutionSource(solution_id=solution.id, session_id=session_b.id, confirmation_message_id=confirmation_b.id),
        ])
        db.commit()

        db.refresh(solution)
        assert solution.verified is True
        assert solution.confirmation_count == 2

        db.add(LLMUsage(
            session_id=session_a.id,
            model="gemini-3.5-flash",
            purpose="escalation",
            input_tokens=100,
            output_tokens=50,
            total_tokens=150,
        ))
        db.commit()

        usage = db.query(LLMUsage).one()
        assert usage.total_tokens == 150

    engine.dispose()


def test_persistence_across_database_reopen(tmp_path):
    database_path = tmp_path / "persistence.db"
    database_url = f"sqlite:///{database_path}"

    def create_engine_and_session():
        engine = create_engine(database_url, connect_args={"check_same_thread": False})

        @event.listens_for(engine, "connect")
        def enable_foreign_keys(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        Base.metadata.create_all(engine)
        return engine, sessionmaker(bind=engine)

    engine, SessionLocal = create_engine_and_session()
    with SessionLocal() as db:
        customer = User(external_id="persistent-customer")
        db.add(customer)
        db.flush()

        session = Session(user_id=customer.id, title="Persistent session")
        db.add(session)
        db.flush()

        db.add_all([
            Message(session_id=session.id, user_id=customer.id, role="user", content="My first message"),
            Message(session_id=session.id, user_id=customer.id, role="assistant", content="My assistant reply"),
        ])
        db.commit()
        user_id = customer.id
        session_id = session.id

    engine.dispose()

    engine, SessionLocal = create_engine_and_session()
    with SessionLocal() as db:
        persisted_user = db.query(User).filter_by(id=user_id).one()
        persisted_session = db.query(Session).filter_by(id=session_id).one()
        persisted_messages = (
            db.query(Message)
            .filter_by(session_id=session_id)
            .order_by(Message.created_at)
            .all()
        )

        assert persisted_user.external_id == "persistent-customer"
        assert persisted_session.title == "Persistent session"
        assert [message.content for message in persisted_messages] == [
            "My first message",
            "My assistant reply",
        ]

    engine.dispose()


def test_solution_provenance_without_duplication():
    engine, SessionLocal = make_database()

    with SessionLocal() as db:
        customer_a = User(external_id="customer-a-provenance")
        customer_b = User(external_id="customer-b-provenance")
        db.add_all([customer_a, customer_b])
        db.flush()

        session_a = Session(user_id=customer_a.id)
        session_b = Session(user_id=customer_b.id)
        db.add_all([session_a, session_b])
        db.flush()

        confirmation_a = Message(
            session_id=session_a.id,
            user_id=customer_a.id,
            role="user",
            content="The fix worked",
        )
        confirmation_b = Message(
            session_id=session_b.id,
            user_id=customer_b.id,
            role="user",
            content="Confirmed fixed",
        )
        db.add_all([confirmation_a, confirmation_b])
        db.flush()

        solution = Solution(
            problem="Files fail to synchronise",
            fix_steps=["Clear cache", "Restart app", "Synchronise again"],
            status="VERIFIED",
        )
        db.add(solution)
        db.flush()

        db.add_all([
            SolutionSource(
                solution_id=solution.id,
                session_id=session_a.id,
                confirmation_message_id=confirmation_a.id,
            ),
            SolutionSource(
                solution_id=solution.id,
                session_id=session_b.id,
                confirmation_message_id=confirmation_b.id,
            ),
        ])
        db.commit()

        solution_count = db.query(Solution).filter_by(id=solution.id).count()
        source_records = db.query(SolutionSource).filter_by(solution_id=solution.id).all()

        assert solution_count == 1
        assert len(source_records) == 2
        assert {record.session_id for record in source_records} == {session_a.id, session_b.id}
        assert solution.confirmation_count == 2

    engine.dispose()


def test_customer_history_isolation():
    engine, SessionLocal = make_database()

    with SessionLocal() as db:
        customer_a = User(external_id="isolated-customer-a")
        customer_b = User(external_id="isolated-customer-b")
        db.add_all([customer_a, customer_b])
        db.flush()

        session_a = Session(user_id=customer_a.id, title="A's private session")
        session_b = Session(user_id=customer_b.id, title="B's private session")
        db.add_all([session_a, session_b])
        db.flush()

        db.add_all([
            Message(session_id=session_a.id, user_id=customer_a.id, role="user", content="A's private message"),
            Message(session_id=session_a.id, user_id=customer_a.id, role="assistant", content="A's private reply"),
            Message(session_id=session_b.id, user_id=customer_b.id, role="user", content="B's private message"),
        ])
        db.commit()

        customer_a_history = (
            db.query(Message)
            .join(Session, Message.session_id == Session.id)
            .filter(Session.user_id == customer_a.id)
            .order_by(Message.created_at)
            .all()
        )

        contents = [message.content for message in customer_a_history]
        assert contents == ["A's private message", "A's private reply"]
        assert "B's private message" not in contents

    engine.dispose()
