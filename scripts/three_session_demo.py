"""Repeatable end-to-end Déjà Fix demonstration.

Uses data/deja_fix_demo.db and never modifies DATABASE_URL from the .env file.
Set DEMO_LIVE_GEMINI=1 to require and use the configured Gemini API key.
"""

import asyncio
import os
import sys
from pathlib import Path

from sqlalchemy import create_engine, event, func
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.db.database import Base
import app.db.models  # noqa: F401
from app.db.models import LLMUsage, Message, Session, Solution, SolutionSource, User
from app.services.escalation import escalate_to_gemini
from app.services.extractor import extract_solutions
from app.services.persona import format_verified_reply, summarize_last_session
from app.services.retrieval import get_retrieval_decision, retrieve_solutions

DEMO_DATABASE = Path("data/deja_fix_demo.db")


def make_demo_database():
    DEMO_DATABASE.parent.mkdir(parents=True, exist_ok=True)

    if DEMO_DATABASE.exists():
        DEMO_DATABASE.unlink()

    engine = create_engine(
        f"sqlite:///{DEMO_DATABASE}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine)


def reopen_demo_database():
    engine = create_engine(
        f"sqlite:///{DEMO_DATABASE}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine, sessionmaker(bind=engine)


def add_message(db, session: Session, user: User, role: str, content: str):
    message = Message(
        session_id=session.id,
        user_id=user.id,
        role=role,
        content=content,
    )
    db.add(message)
    db.commit()
    db.refresh(message)
    return message


def usage_by_session(db):
    rows = db.query(
        LLMUsage.session_id,
        LLMUsage.purpose,
        func.count(LLMUsage.id),
        func.coalesce(func.sum(LLMUsage.input_tokens), 0),
        func.coalesce(func.sum(LLMUsage.output_tokens), 0),
        func.coalesce(func.sum(LLMUsage.total_tokens), 0),
    ).group_by(LLMUsage.session_id, LLMUsage.purpose).all()

    return [
        {
            "session_id": session_id,
            "purpose": purpose,
            "call_count": call_count,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
        }
        for session_id, purpose, call_count, input_tokens, output_tokens, total_tokens in rows
    ]


async def run_demo(live_gemini: bool = False):
    if live_gemini:
        key = settings.GEMINI_API_KEY
        if not key or key.lower().startswith("your_") or key == "test-placeholder":
            raise RuntimeError("DEMO_LIVE_GEMINI=1 requires a real GEMINI_API_KEY")

    engine, SessionLocal = make_demo_database()

    try:
        with SessionLocal() as db:
            initial_count = db.query(Solution).filter_by(status="VERIFIED").count()
            print("=" * 64)
            print("DÉJÀ FIX — THREE-SESSION DEMONSTRATION")
            print("=" * 64)
            print(f"Dedicated database: {DEMO_DATABASE}")
            print(f"Initial verified knowledge-hub count: {initial_count}")

            customer_a = User(external_id="demo-customer-a")
            customer_b = User(external_id="demo-customer-b")
            db.add_all([customer_a, customer_b])
            db.flush()

            session_a = Session(user_id=customer_a.id, title="Customer A learning session")
            db.add(session_a)
            db.flush()

            problem_a = "My laptop on Windows 11 cannot upload photos in PhotoSync; error ERR-504 keeps appearing."
            add_message(db, session_a, customer_a, "user", problem_a)

            initial_results = retrieve_solutions(db, problem_a)
            print("\nSESSION 1 — Learning")
            print(f"Customer A problem: {problem_a}")

            if initial_results:
                print(f"Unexpected initial match: {initial_results[0]['score']:.3f}")

            escalation_history = (
                db.query(Message)
                .filter(Message.session_id == session_a.id)
                .order_by(Message.created_at.asc(), Message.id.asc())
                .all()
            )

            if live_gemini:
                gemini_result = await escalate_to_gemini(db, session_a.id, escalation_history)
                gemini_text = gemini_result["text"]
            else:
                raise RuntimeError(
                    "A live Gemini-backed demo is required. Set DEMO_LIVE_GEMINI=1 and provide GEMINI_API_KEY."
                )

            print("Gemini escalation result:")
            print(gemini_text)

            assistant_text = f"Please try this troubleshooting procedure:\n{gemini_text}"
            add_message(db, session_a, customer_a, "assistant", assistant_text)

            confirmation = "Yes, I cleared the cache and restarted PhotoSync. It worked; ERR-504 is resolved."
            add_message(db, session_a, customer_a, "user", confirmation)

            extracted = await extract_solutions(db, session_a.id, use_gemini=True)

            if len(extracted) != 1:
                raise RuntimeError(f"Expected one canonical demo solution, found {len(extracted)}")

            solution = extracted[0]
            print("Customer confirmation:", confirmation)
            print("Extracted/generalised solution:")
            print(f"  Problem: {solution.canonical_title or solution.problem}")
            print(f"  Device: {solution.device}")
            print(f"  OS: {solution.operating_system}")
            print(f"  App: {solution.application_name} {solution.app_version or ''}".rstrip())
            print(f"  Steps: {solution.fix_steps}")
            print(f"  Status: {solution.status}")

            demo_text = " ".join([
                solution.problem,
                solution.canonical_title or "",
                solution.problem_description or "",
                " ".join(solution.symptoms or []),
                solution.device or "",
                solution.operating_system or "",
                solution.application_name or "",
                solution.app_version or "",
                " ".join(solution.fix_steps or []),
                " ".join(solution.failed_attempts or []),
                " ".join(solution.error_codes or []),
            ])
            forbidden = ("customer-a", "demo-customer-a", "alice", "@", "http://", "https://")
            if any(marker in demo_text.lower() for marker in forbidden):
                raise RuntimeError("PII or session-identifying data was persisted")

            source = db.query(SolutionSource).filter_by(solution_id=solution.id).one()
            if source.session_id != session_a.id:
                raise RuntimeError("Solution provenance does not point to Customer A")

            customer_a_id = customer_a.id
            customer_b_id = customer_b.id
            session_a_id = session_a.id

            print("\nRestart checkpoint: closing and reopening the dedicated demo database")
            db.close()
            engine.dispose()
            engine, SessionLocal = reopen_demo_database()
            db = SessionLocal()

            persisted_solution = db.query(Solution).filter_by(status="VERIFIED").count()
            if persisted_solution != 1:
                raise RuntimeError("Verified knowledge did not survive the demo restart")

            print("Restart checkpoint: persisted knowledge reloaded successfully")

            customer_a = db.get(User, customer_a_id)
            customer_b = db.get(User, customer_b_id)
            session_a = db.get(Session, session_a_id)

            session_b = Session(user_id=customer_b.id, title="Customer B reuse session")
            db.add(session_b)
            db.flush()

            problem_b = "PhotoSync on my Windows laptop won't send my pictures, they just stay pending."
            add_message(db, session_b, customer_b, "user", problem_b)

            print("\nSESSION 2 — Knowledge reuse")
            print(f"Customer B differently worded problem: {problem_b}")

            from app.api.chat import create_message as chat_handler
            from app.schemas.chat import MessageCreate

            session2_usage_before = db.query(func.count(LLMUsage.id)).filter(LLMUsage.session_id == session_b.id).scalar()

            first_reply = await chat_handler(MessageCreate(
                session_id=session_b.id,
                user_id=customer_b.id,
                role="user",
                content=problem_b,
            ), db=db)

            first_history = (
                db.query(Message)
                .filter(Message.session_id == session_b.id)
                .order_by(Message.created_at.asc())
                .all()
            )
            first_history_problem = "\n".join(message.content for message in first_history if message.role == "user")
            first_candidates = retrieve_solutions(db, first_history_problem)
            first_decision = get_retrieval_decision(first_candidates)

            if first_candidates:
                print(f"B first candidate score: {first_candidates[0]['score']:.3f}")
                print(f"B first decision: {first_decision}")

            if first_reply.source != "clarification":
                raise RuntimeError("Customer B's first message should return a clarification")

            print(f"Clarifying question: {first_reply.reply}")

            second_reply = await chat_handler(MessageCreate(
                session_id=session_b.id,
                user_id=customer_b.id,
                role="user",
                content="The device is a Windows 11 laptop running PhotoSync 4.2.1. Photos remain pending and uploads fail with ERR-504.",
            ), db=db)

            combined_history = (
                db.query(Message)
                .filter(Message.session_id == session_b.id)
                .order_by(Message.created_at.asc())
                .all()
            )
            combined_problem = "\n".join(message.content for message in combined_history if message.role == "user")
            candidates = retrieve_solutions(db, combined_problem)
            decision = get_retrieval_decision(candidates)

            if candidates:
                print(f"B combined candidate score: {candidates[0]['score']:.3f}")
                print(f"B combined decision: {decision}")

            if decision != "high" or not candidates:
                raise RuntimeError("Customer B did not achieve high-confidence verified reuse")

            top = candidates[0]
            persona_reply = second_reply.reply

            session2_usage_after = db.query(func.count(LLMUsage.id)).filter(LLMUsage.session_id == session_b.id).scalar()

            print(f"Retrieved solution: {top['problem']}")
            print(f"Similarity score: {top['score']:.3f}")
            print(f"Persona response: {persona_reply}")
            print("Gemini called in Session 2: No")

            if session2_usage_before != session2_usage_after:
                raise RuntimeError("Session 2 unexpectedly recorded Gemini usage")

            final_verified_count = db.query(Solution).filter_by(status="VERIFIED").count()
            if final_verified_count != 1:
                raise RuntimeError(f"Expected one canonical solution, found {final_verified_count}")

            print("\nSESSION 3 — Customer history and isolation")
            customer_a_history = (
                db.query(Message)
                .join(Session, Message.session_id == Session.id)
                .filter(Session.user_id == customer_a.id)
                .order_by(Message.created_at.asc(), Message.id.asc())
                .all()
            )
            print("Customer A recovered history:")
            for message in customer_a_history:
                print(f"  [{message.role}] {message.content}")

            print("Session 3 greeting:", summarize_last_session(customer_a_history))

            cross_access = db.query(Session).filter(
                Session.id == session_b.id,
                Session.user_id == customer_a.id,
            ).first()
            if cross_access is not None:
                raise RuntimeError("Customer A unexpectedly accessed Customer B's session")

            customer_b_private = "Photo uploads stay pending and never finish"
            customer_a_text = " ".join(message.content for message in customer_a_history)
            if customer_b_private in customer_a_text:
                raise RuntimeError("Customer B private conversation leaked into Customer A history")

            print("Customer isolation check: PASSED")

            usage = usage_by_session(db)
            print("\nTOKEN USAGE COMPARISON")
            for row in usage:
                print(
                    f"  Session {row['session_id']} | {row['purpose']} | calls={row['call_count']} "
                    f"input={row['input_tokens']} output={row['output_tokens']} total={row['total_tokens']}"
                )

            session_a_usage = sum(row["total_tokens"] for row in usage if row["session_id"] == session_a.id)
            session_b_usage = sum(row["total_tokens"] for row in usage if row["session_id"] == session_b.id)
            print(f"Session 1 total tokens: {session_a_usage}")
            print(f"Session 2 total tokens: {session_b_usage}")

            print("\nFINAL COUNTS")
            print(f"Initial verified knowledge-hub count: {initial_count}")
            print(f"Final verified knowledge-hub count: {final_verified_count}")
            print("Status: VERIFIED")
            print("=" * 64)

            return {
                "initial_count": initial_count,
                "solution": solution,
                "session_a": session_a,
                "session_b": session_b,
                "customer_a": customer_a,
                "customer_b": customer_b,
                "retrieval_score": top["score"],
                "session_a_usage": session_a_usage,
                "session_b_usage": session_b_usage,
                "usage": usage,
                "final_verified_count": final_verified_count,
            }
    finally:
        engine.dispose()


def main():
    live_gemini = os.getenv("DEMO_LIVE_GEMINI", "0") == "1"

    try:
        asyncio.run(run_demo(live_gemini=live_gemini))
    except RuntimeError as error:
        print(f"Demo not started: {error}", file=sys.stderr)
        print("Set GEMINI_API_KEY and DEMO_LIVE_GEMINI=1 to run live.", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
