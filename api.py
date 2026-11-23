"""Optional HTTP API: bearer-scoped sessions, persisted rate limits, real HTTP 429."""
import hashlib
import logging
import secrets
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel as RequestModel, Field
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.db import engine as default_engine
from app.models import Base, LLMCallLog, UserCredential
from app.repositories import get_or_create_user, get_chat_session, list_chat_sessions, list_messages
from app.services.memory import start_session
from app.services.rate_limit import RateLimitExceeded, RateLimiter
from app.services.runtime import process_message

logger = logging.getLogger(__name__)


class NewMessage(RequestModel):
    content: str = Field(min_length=1, max_length=20000)


def create_app(chat_service=None, *, engine=None, rate_limiter=None):
    engine = engine or default_engine
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    limiter = rate_limiter or RateLimiter()

    @asynccontextmanager
    async def lifespan(_app):
        logger.info("api.startup_started")
        Base.metadata.create_all(engine)
        logger.info("api.startup_completed")
        yield
        logger.info("api.shutdown_started")

    app = FastAPI(title="Déjà Fix API", version="2.0", lifespan=lifespan)

    @app.middleware("http")
    async def request_logging(request, call_next):
        request_id = uuid.uuid4().hex[:12]
        request.state.request_id = request_id
        started = time.perf_counter()
        logger.info("http.request_started request_id=%s method=%s path=%s", request_id, request.method, request.url.path)
        try:
            response = await call_next(request)
            logger.info("http.request_completed request_id=%s method=%s path=%s status=%d elapsed_ms=%.1f", request_id, request.method, request.url.path, response.status_code, (time.perf_counter() - started) * 1000)
            response.headers["X-Request-ID"] = request_id
            return response
        except Exception:
            logger.exception("http.request_failed request_id=%s method=%s path=%s elapsed_ms=%.1f", request_id, request.method, request.url.path, (time.perf_counter() - started) * 1000)
            raise

    def service():
        nonlocal chat_service
        if chat_service is None:
            from app.config import settings
            from app.services.embeddings import EmbeddingService
            from app.services.gemini_client import GeminiClient
            from app.services.chat_service import ChatService
            logger.info("api.services_initializing embedding_model=%s gemini_model=%s gemini_key_configured=%s", settings.embedding_model, settings.gemini_model, bool(settings.gemini_api_key))
            chat_service = ChatService(
                GeminiClient(
                    settings.gemini_api_key,
                    settings.gemini_model,
                    settings.gemini_timeout_ms,
                    settings.gemini_fallback_models,
                ),
                EmbeddingService(settings.embedding_model),
            )
            logger.info("api.services_initialized")
        return chat_service

    def authenticated_user(authorization: str | None = Header(default=None)) -> str:
        if not authorization or not authorization.startswith("Bearer "):
            logger.warning("api.authentication_failed reason=missing_or_malformed_header")
            raise HTTPException(401, "Provide Authorization: Bearer <customer_token>")
        token = authorization[7:].strip()
        if not token:
            logger.warning("api.authentication_failed reason=empty_token")
            raise HTTPException(401, "Invalid customer token")
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        with Session() as db:
            cred = db.scalar(select(UserCredential).where(UserCredential.token_hash == token_hash))
            if cred is None:
                logger.warning("api.authentication_failed reason=unknown_token")
                raise HTTPException(401, "Invalid customer token")
            logger.debug("api.authentication_succeeded user_id=%s", cred.user_id)
            return cred.user_id

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.post("/users", status_code=201)
    def new_customer():
        """Return secret token only at creation; API never accepts arbitrary user IDs."""
        logger.info("api.customer_creation_requested")
        token = secrets.token_urlsafe(32)
        with Session.begin() as db:
            user = get_or_create_user(db)
            db.add(UserCredential(
                user_id=user.id,
                token_hash=hashlib.sha256(token.encode()).hexdigest(),
            ))
            db.flush()
            logger.info("api.customer_created user_id=%s", user.id)
            return {"user_id": user.id, "token": token}

    @app.post("/sessions", status_code=201)
    def new_session(user_id: str = Depends(authenticated_user)):
        logger.info("api.session_creation_requested user_id=%s", user_id)
        with Session.begin() as db:
            new = start_session(db, user_id)
            messages = list_messages(db, new.id, user_id)
            logger.info("api.session_created user_id=%s session_id=%s", user_id, new.id)
            return {
                "session_id": new.id,
                "greeting": messages[0].content if messages else None,
            }

    @app.get("/sessions")
    def sessions(user_id: str = Depends(authenticated_user)):
        with Session() as db:
            items = list_chat_sessions(db, user_id)
            logger.info("api.sessions_listed user_id=%s count=%d", user_id, len(items))
            return {"sessions": [
                {"session_id": s.id, "created_at": s.created_at.isoformat()}
                for s in items
            ]}

    @app.get("/sessions/{session_id}/messages")
    def messages(session_id: str, user_id: str = Depends(authenticated_user)):
        with Session() as db:
            if get_chat_session(db, session_id, user_id) is None:
                raise HTTPException(404, "Session not found")
            items = list_messages(db, session_id, user_id)
            logger.info("api.transcript_loaded user_id=%s session_id=%s messages=%d", user_id, session_id, len(items))
            return {"messages": [
                {"role": m.role, "content": m.content, "id": m.id}
                for m in items
            ]}

    @app.post("/sessions/{session_id}/messages")
    def send_message(session_id: str, message: NewMessage, user_id: str = Depends(authenticated_user)):
        logger.info("api.message_received user_id=%s session_id=%s chars=%d", user_id, session_id, len(message.content))
        try:
            with Session.begin() as db:
                if get_chat_session(db, session_id, user_id) is None:
                    raise HTTPException(404, "Session not found")
                outcome = process_message(
                    db, user_id, session_id, message.content, service(), rate_limiter=limiter,
                )
                logger.info("api.message_processed user_id=%s session_id=%s source=%s solution_id=%s", user_id, session_id, outcome.source, outcome.solution_id)
                return {
                    "answer": outcome.answer, "source": outcome.source,
                    "match_score": outcome.match_score, "solution_id": outcome.solution_id,
                }
        except RateLimitExceeded as exc:
            logger.warning("api.rate_limit_response user_id=%s session_id=%s kind=%s retry_after=%d", user_id, session_id, exc.kind, exc.retry_after)
            raise HTTPException(
                status_code=429,
                detail=str(exc),
                headers={"Retry-After": str(exc.retry_after)},
            ) from exc
        except ValueError as exc:
            logger.warning("api.bad_request user_id=%s session_id=%s error=%s", user_id, session_id, str(exc))
            raise HTTPException(400, str(exc)) from exc

    @app.get("/sessions/{session_id}/usage")
    def usage(session_id: str, user_id: str = Depends(authenticated_user)):
        with Session() as db:
            if get_chat_session(db, session_id, user_id) is None:
                raise HTTPException(404, "Session not found")
            logs = list(db.scalars(
                select(LLMCallLog).where(LLMCallLog.session_id == session_id)
            ))
            logger.info("api.usage_loaded user_id=%s session_id=%s calls=%d input_tokens=%d output_tokens=%d", user_id, session_id, len(logs), sum(call.input_tokens for call in logs), sum(call.output_tokens for call in logs))
            return {
                "calls": len(logs),
                "input_tokens": sum(call.input_tokens for call in logs),
                "output_tokens": sum(call.output_tokens for call in logs),
                "total_tokens": sum(call.input_tokens + call.output_tokens for call in logs),
            }

    return app


app = create_app()
