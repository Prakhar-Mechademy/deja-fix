# Déjà Fix

Déjà Fix is a retrieval-grounded support assistant. It reuses only customer-confirmed, anonymized fixes. The SQLite knowledge hub starts empty and grows as verified resolutions are collected.

## Quick start (about five minutes)

Requirements: Python 3.10+.

```bash
cd backend
python -m venv .venv
# macOS/Linux
source .venv/bin/activate
# Windows PowerShell: .venv\\Scripts\\Activate.ps1
pip install -r requirements.txt
cp .env.example .env       # Windows: copy .env.example .env
```

Add a Gemini API key to `backend/.env`:

```env
GEMINI_API_KEY=your_key_here
GEMINI_MODEL=gemini-2.5-flash
```

Start the API from the `backend` directory:

```bash
uvicorn main:app --reload
```

- API docs: http://127.0.0.1:8000/docs
- Health: http://127.0.0.1:8000/health
- SQLite file: `backend/data/deja_fix.db`

Without a Gemini key, the API still starts and the knowledge hub can be browsed. New escalations return a plain provider-unavailable message. The local demo and tests use mocked/deterministic components and do not need a key.

## Run tests and demo

From the repository root:

```bash
pip install -r backend/requirements.txt
pytest -q
python scripts/three_session_demo.py
```

The demo resets the local database and runs the three-session scenario with deterministic mocked LLM responses. To use Gemini instead:

```bash
python scripts/three_session_demo.py --live
```

`--live` requires `GEMINI_API_KEY` in `backend/.env` and uses real provider calls.

To reset the database manually:

```bash
cd backend
python ../scripts/reset_database.py
```

## Typical API flow

1. `POST /api/users` with `{"external_id":"customer-123","display_name":"Customer"}`.
2. `POST /api/sessions` with the returned `user_id`.
3. `POST /api/chat/message` with `user_id`, `session_id`, and `message`.
4. If a proposed fix works, send an explicit confirmation such as `That worked; it is fixed now.`
5. Read the transcript with `GET /api/sessions/{session_id}/messages?user_id=...` and list sessions with `GET /api/sessions?user_id=...`.

All session reads are scoped to the owning user. The current scaffold uses UUID identity references, not production-grade authentication; deploy behind authenticated identity before exposing it publicly.

## Design notes

See [`DESIGN.md`](DESIGN.md) for the data model, retrieval strategy, confirmation policy, privacy handling, and next steps.
