"""FastAPI app. Run with:  uvicorn dejafix.api:create_app --factory   (or `python -m dejafix`)

Auth is out of scope for the hackathon: the caller names itself with the X-User-Id header (assumption).
"""
from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from .chat import ChatService
from .config import Settings, load_dotenv
from .db import init_db
from .errors import DejaError, RateLimited
from .extractor import Extractor
from .hub import Hub
from .llm import LLMService, build_provider
from .ratelimit import RateLimiter

log = logging.getLogger("dejafix.api")
STATIC = Path(__file__).parent / "static"


class UserIn(BaseModel):
    display_name: str


class MessageIn(BaseModel):
    content: str


def create_app(settings: Settings | None = None, provider=None) -> FastAPI:
    load_dotenv()
    s = settings or Settings.from_env()
    init_db(s.db_path)
    llm = LLMService(provider or build_provider(s), s.db_path)
    hub = Hub(s, llm)
    svc = ChatService(s, llm, hub, Extractor(s, llm, hub), RateLimiter(s.rate_messages, s.rate_escalations))

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        threading.Thread(target=svc.sweep_pending, daemon=True).start()   # retry extractions lost to an outage
        yield

    app = FastAPI(title="Déjà Fix", version="1.0.0", lifespan=lifespan)
    app.state.svc, app.state.hub, app.state.settings = svc, hub, s

    # ------------------------------------------------------------ error handling
    @app.exception_handler(RateLimited)
    async def _rate(_: Request, e: RateLimited):
        return JSONResponse(status_code=429, headers={"Retry-After": str(e.retry_after)},
                            content={"error": e.code, "scope": e.scope, "detail": e.detail,
                                     "retry_after_seconds": e.retry_after})

    @app.exception_handler(DejaError)
    async def _deja(_: Request, e: DejaError):
        return JSONResponse(status_code=e.status, content={"error": e.code, "detail": e.detail})

    @app.exception_handler(Exception)
    async def _boom(_: Request, e: Exception):
        log.exception("unhandled error")
        return JSONResponse(status_code=500, content={"error": "internal_error",
                            "detail": "Something went wrong on our side. Please try again."})

    # ------------------------------------------------------------ routes
    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/health")
    def health():
        return {"status": "ok", "llm_provider": svc.llm.provider.name}

    @app.post("/users")
    def create_user(body: UserIn):
        return svc.create_user(body.display_name)

    @app.post("/sessions")
    def start_session(x_user_id: str = Header(default="")):
        return svc.start_session(x_user_id)

    @app.get("/sessions")
    def list_sessions(x_user_id: str = Header(default="")):
        return svc.list_sessions(x_user_id)

    @app.get("/sessions/{session_id}")
    def read_session(session_id: str, x_user_id: str = Header(default="")):
        return svc.get_session(x_user_id, session_id)

    @app.post("/sessions/{session_id}/messages")
    def post_message(session_id: str, body: MessageIn, x_user_id: str = Header(default="")):
        return svc.post_message(x_user_id, session_id, body.content)

    @app.post("/sessions/{session_id}/close")
    def close_session(session_id: str, x_user_id: str = Header(default="")):
        return svc.close_session(x_user_id, session_id)

    @app.get("/solutions")
    def solutions(status: str | None = None):
        return hub.list(status)

    @app.get("/usage")
    def usage():
        return llm.usage()

    @app.get("/usage/calls")
    def usage_calls(session_id: str | None = None):
        return llm.calls(session_id)

    return app
