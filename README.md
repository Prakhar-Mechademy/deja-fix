# Déjà Fix

A support chatbot that remembers every fix a customer confirms and reuses it when a later
customer reports the same problem. The knowledge hub starts empty and fills as it is used.

Customer message -> persona -> hub search (embeddings + LLM judge)
  - match: templated fix from the hub (no escalation, cheap)
  - no match: full conversation goes to the backend Gemini model (escalation)
Session closed -> extractor saves confirmed, anonymised fixes (or merges duplicates).

## Quickstart (5 minutes)

Requires Python 3.10+ and a Gemini API key (https://aistudio.google.com/apikey).

```bash
git clone <your-repo-url> && cd deja-fix
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                 # then edit .env and set GEMINI_API_KEY
```

Check the install (offline, no key needed):

```bash
python -m pytest -q tests
```

Run the full three-session demo against an empty database (uses your key):

```bash
python script/run_demo.py
```

It prints each chat, the extractor result, the token log and a PASS/FAIL line for every
behaviour in the spec (reuse, negative match, unconfirmed stays out, generalization, memory
isolation, persona containment, rate limit, restart).

Run the real server:

```bash
uvicorn app.main:app --reload        # interactive docs: http://127.0.0.1:8000/docs
```

## Try it by hand (and prove restart safety)

```bash
curl -s -X POST localhost:8000/sessions -H 'content-type: application/json' -d '{"user_id":"alice"}'
# -> {"session_id":"<SID>","greeting":null}
curl -s -X POST localhost:8000/sessions/<SID>/messages -H 'content-type: application/json' \
     -d '{"user_id":"alice","text":"My app will not sync on my iPhone 14, iOS 17.2. It just spins."}'
curl -s -X POST localhost:8000/sessions/<SID>/messages -H 'content-type: application/json' \
     -d '{"user_id":"alice","text":"That fixed it, thank you! Sync is working again."}'
curl -s -X POST localhost:8000/sessions/<SID>/close -H 'content-type: application/json' -d '{"user_id":"alice"}'
curl -s localhost:8000/solutions        # the saved, anonymised solution
```

Now stop uvicorn (Ctrl+C), start it again, and `curl localhost:8000/solutions` - it is still there.
`curl localhost:8000/token-report` shows tokens in/out per session and whether it was served
from the hub or by escalation.

## API

| Method and path | Purpose |
|---|---|
| `POST /users` | create or update a user (`user_id`, optional `display_name`) |
| `POST /sessions` | start a session; returns `session_id` and a greeting from that user's own history (or null) |
| `POST /sessions/{id}/messages` | send a customer message; returns `reply`, `source` (`hub`, `persona`, `persona_clarify`, `llm`) |
| `GET /users/{user_id}/sessions` | list a user's sessions |
| `GET /sessions/{id}?user_id=` | read a session back in full |
| `POST /sessions/{id}/resume` | reopen a closed session |
| `POST /sessions/{id}/close` | close a session and run the extractor on its stored transcript |
| `POST /admin/extract-pending` | retry extraction for closed sessions that failed (e.g. provider outage) |
| `POST /admin/close-idle` | close idle sessions now and extract them (also runs every minute) |
| `GET /solutions` | verified, anonymised solutions in the hub |
| `GET /token-report` | tokens per session, hub vs escalation |
| `GET /health` | liveness |

Rate limits return HTTP 429 with a `Retry-After` header: `MESSAGES_PER_MINUTE` (default 20)
and a tighter `ESCALATIONS_PER_HOUR` (default 5), per user, stored in the database.

## Configuration

All settings come from environment variables (`.env` is loaded automatically). See
`.env.example`. The API key is only ever read from `GEMINI_API_KEY`. If your key cannot use
the default model, set `GEMINI_MODEL` to one it can (Google AI Studio lists them).

## Assumptions

- Authentication is out of scope: the caller supplies `user_id`, and every session read or
  write is checked against it (someone else's session looks like it does not exist).
- `/admin/*` and `/token-report` are unauthenticated, for demo purposes only.
- Embedding calls are logged with an estimated token count (about 4 characters per token);
  chat, escalation, extraction and judge calls use the provider's real counts.
- A session is "done" when it is closed explicitly or has been idle for `IDLE_CLOSE_MINUTES`.

## Layout

```
app/        main.py (API) persona.py hub.py extractor.py llm.py db.py rate_limit.py config.py schema.sql
tests/      offline tests with a fake LLM (no network)
script/     run_demo.py (the three-session scenario), embed_check.py (similarity probe)
DESIGN.md   one-page design
```
