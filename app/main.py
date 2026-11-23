"""FastAPI application entry point."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

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
