# Déjà Fix

Déjà Fix is a retrieval-grounded customer-support backend built with FastAPI, SQLite, SQLAlchemy, Pydantic, Sentence Transformers and Google Gemini.

It separates customer conversation history from the shared verified knowledge hub. Gemini is used only for unresolved or low-confidence troubleshooting and for extracting confirmed resolutions.

## Features

- Persistent users, sessions and messages
- Customer-history isolation
- Verified-solution retrieval using embeddings and cosine similarity
- Gemini escalation with token usage logging
- Explicit-confirmation-based solution extraction
- PII sanitisation and knowledge generalisation
- Router-level admin authentication
- A repeatable three-session demo database

## Setup

```bash
python -m venv mechvenv
source mechvenv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Configure `.env`:

```env
APP_NAME=Deja Fix
DATABASE_URL=sqlite:///./data/deja_fix.db
GEMINI_API_KEY=
GEMINI_MODEL=gemini-3.5-flash
ADMIN_TOKEN=
EMBEDDING_MODEL=all-MiniLM-L6-v2
RATE_LIMIT_MESSAGES=30
RATE_LIMIT_ESCALATIONS=5
ESCALATION_WINDOW_SECONDS=3600
```

Never commit `.env`. Its values and actual database files are intentionally ignored.

## Run

```bash
source mechvenv/bin/activate
python -m uvicorn app.main:app --reload
```

Open:

- `http://127.0.0.1:8000/`
- `http://127.0.0.1:8000/docs`

## API endpoints

### Customers and sessions

- `POST /users` — create or retrieve a customer
- `GET /users/{user_id}` — retrieve customer details
- `POST /sessions` — create a session
- `GET /sessions/{session_id}?user_id=...` — retrieve an owned session
- `GET /users/{user_id}/sessions` — list a customer's sessions

### Messages

- `POST /chat/message` — persist and orchestrate a message
- `GET /sessions/{session_id}/messages?user_id=...` — retrieve owned session history

Cross-customer session and message access returns `404`.

### Admin

Send `X-Admin-Token` on admin requests:

- `GET /admin/usage` — aggregate Gemini call and token counts

Missing or unconfigured admin tokens return `401`; an incorrect token returns `403`. The response never includes message content or customer identifiers.

## Tests

```bash
GEMINI_API_KEY=test-placeholder ADMIN_TOKEN=test-admin \
mechvenv/bin/python -m pytest -q
```

## Three-session demo

The demo uses `data/deja_fix_demo.db`, deletes only that dedicated database before starting, and never modifies `DATABASE_URL` from `.env`.

```bash
DEMO_LIVE_GEMINI=1 mechvenv/bin/python -m scripts.three_session_demo
```

A real Gemini API key is required for this live mode. Integration tests mock Gemini where external calls would otherwise make results nondeterministic.

## Assumptions and known limitations

- The 0.78 high-confidence and 0.55 uncertain thresholds have only been tested against limited examples.
- The demo's realistic Session 2 reworded score may require a clarifying answer containing the error code or app version.
- Retrieval uses a full SQLite scan rather than an ANN/vector index.
- Gemini calls are not isolated from provider outages; chat failures return a plain `source: "error"` response.
- Extracted solutions require explicit textual confirmation and are not yet reviewed by a human.
- The limiter is time-windowed and counts `escalation_attempt` rows; threshold and window settings still need operational tuning.
- There is no production UI yet.