"""Delete and recreate the local SQLite database. Run from backend: python ../scripts/reset_database.py"""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from database import Base, engine, init_db

Base.metadata.drop_all(bind=engine)
init_db()
print("Database reset. The knowledge hub is empty.")
