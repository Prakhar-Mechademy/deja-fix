"""Déjà Fix FastAPI application."""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from database import init_db
from api.chat import router as chat_router
from api.users import router as users_router
from api.sessions import router as sessions_router
from api.solutions import router as solutions_router
from api.analytics import router as analytics_router

app = FastAPI(title="Déjà Fix API", version="1.0.0", description="Retrieval-grounded customer support assistant")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=False, allow_methods=["*"], allow_headers=["*"])

@app.on_event("startup")
def startup(): init_db()

app.include_router(chat_router, prefix="/api")
app.include_router(users_router, prefix="/api")
app.include_router(sessions_router, prefix="/api")
app.include_router(solutions_router, prefix="/api")
app.include_router(analytics_router, prefix="/api")

@app.get("/health")
def health(): return {"status": "ok", "service": "deja-fix"}
