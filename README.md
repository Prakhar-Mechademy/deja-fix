# Déjà Fix

A support chatbot that remembers every fix a customer confirms and reuses it for the next customer with the
same problem. The knowledge hub starts empty and fills up as the system is used.

**Stack:** Python 3.10+, FastAPI, SQLite, Google Gemini (REST API, no SDK).

## Run it in 5 minutes

```bash
# 1. Install (Windows: use "python" instead of "python3")
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 2. Add your Gemini key (free key: https://aistudio.google.com/apikey)
cp .env.example .env               # Windows: copy .env.example .env
#    then open .env and set GEMINI_API_KEY=...   (.env is git-ignored)

# 3. Start the server
python3 -m uvicorn dejafix.app:app --port 8000
```

Open http://127.0.0.1:8000 for the chat page, or http://127.0.0.1:8000/docs for the API.

No key? The app still runs with an offline **mock** model (deterministic keyword rules) so the flow can be
demonstrated without internet. `GET /health` shows which provider is active.

## Demo scenario (three sessions, empty database, server restarted between sessions)

```bash
python3 scripts/run_scenario.py      # session 1 escalation + extraction, session 2 reuse, session 3 memory
python3 scripts/test_behaviors.py    # negative match, unconfirmed stays out, containment, isolation, restart, 429s
```

Both scripts start and stop the server themselves, use their own database file in `data/`, print the
conversation, the per-session token log and PASS/FAIL checks.

## API

| Method | Path | What it does |
|---|---|---|
| POST | `/sessions` `{user_id, name?}` | Start a session; returns a greeting that uses only this user's history |
| POST | `/sessions/{id}/messages` `{user_id, text}` | Send a message; returns `reply`, `source` (hub / llm / persona), `state` |
| POST | `/sessions/{id}/end` | End the session and run the extractor |
| GET | `/sessions/{id}?user_id=` | Read a session back in full (resume) |
| GET | `/users/{user_id}/sessions` | List a user's sessions |
| GET | `/solutions` | Verified solutions (`?include_unverified=true` for all) |
| GET | `/usage` | Tokens in/out per session (`?session_id=` for every call) |

Rate limits return HTTP **429** with a plain message and a `Retry-After` header.

## Configuration (environment variables or `.env`)

| Variable | Default | |
|---|---|---|
| `GEMINI_API_KEY` | – | Required for the real model |
| `GEMINI_MODEL` | `gemini-2.5-flash` | Falls back to `gemini-flash-latest`, then `gemini-2.0-flash` |
| `GEMINI_EMBED_MODEL` | `gemini-embedding-001` | |
| `LLM_PROVIDER` | `auto` | `gemini`, `mock` or `auto` |
| `LLM_TIMEOUT_SECONDS` | `30` | |
| `DEJA_DB` | `data/dejafix.db` | SQLite file |
| `MSG_LIMIT_PER_MINUTE` | `20` | Per user |
| `ESCALATION_LIMIT_PER_HOUR` | `6` | Per user, tighter than messages |
| `SESSION_IDLE_MINUTES` | `30` | Idle sessions are closed and extracted |

## Project layout

```
dejafix/
  app.py        HTTP API, 429s, error handling, idle-session sweep
  persona.py    'Deja' persona: state machine, clarifying questions, hub answers, escalation
  hub.py        retrieval: embeddings for recall + LLM check for "same fault?"
  extractor.py  reads the transcript from the DB, saves confirmed fixes, merges duplicates
  privacy.py    removes names, emails, links, order numbers, phone numbers
  confirm.py    detects "that worked" vs "didn't work"
  llm.py        Gemini client, timeouts, retries, JSON parsing, token logging
  mock_llm.py   offline stand-in for Gemini
  db.py         SQLite schema and queries
scripts/        run_scenario.py, test_behaviors.py
DESIGN.md       one-page design
```

## Assumptions

- The product is a generic consumer app (the demo uses a notes app with sync). Nothing is product-specific.
- `user_id` is sent by the client; real authentication is out of scope. Sessions are still owner-checked (403).
- A session ends when the client calls `/end`, or after `SESSION_IDLE_MINUTES` without messages.
- Token counts come from Gemini's `usageMetadata`; embedding calls and the mock model use an estimate
  (characters / 4), flagged as `estimated=1` in the log.
