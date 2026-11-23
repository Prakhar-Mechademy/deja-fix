"""FastAPI application entry point."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.admin import router as admin_router
from app.api.chat import router as chat_router
from app.api.sessions import router as sessions_router
from app.api.users import router as users_router
from app.core.config import settings
from app.db.database import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title=settings.APP_NAME,
    description="Retrieval-grounded customer support assistant",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(users_router)
app.include_router(sessions_router)
app.include_router(chat_router)
app.include_router(admin_router)


@app.get("/")
def root():
    return {
        "application": "Deja Fix",
        "status": "running",
    }


@app.get("/health")
def health():
    return {
        "status": "healthy",
    }
