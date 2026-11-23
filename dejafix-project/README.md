# Déjà Fix

A support chatbot that **remembers every fix a customer confirms** and reuses it when the next customer reports the
same problem. The knowledge store starts empty and fills up as the system is used.

Stack: Python 3.10+, FastAPI, SQLite (stdlib), `httpx` for LLM calls. No API key is needed to try it (an offline
`MockProvider` stands in for the backend LLM).

## Run it in 5 minutes

```bash
python3 -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python scripts/run_scenario.py --mock     # 1. the 3-session demo on an empty DB (14 checks, exit 0 = all pass)
python -m pytest -q                        # 2. the test-suite (35 tests)
python -m dejafix                          # 3. the server -> http://127.0.0.1:8000  (chat UI at /, API docs at /docs)
```

### Using a real LLM
```bash
cp .env.example .env        # then set ONE key. Keys are read from the environment only, never from code.
export ANTHROPIC_API_KEY=...        # or OPENAI_API_KEY=...
python -m dejafix                    # LLM_PROVIDER=auto picks Anthropic, then OpenAI, else the mock
```
Models and limits are configurable (see `.env.example`).

## API (auth is out of scope: callers identify themselves with the `X-User-Id` header)

| Method & path | Purpose |
|---|---|
| `POST /users` `{display_name}` | create a customer -> `user_id` |
| `POST /sessions` | start a session -> `session_id` + greeting (remembers *this* customer's last problem) |
| `GET /sessions` | list the customer's sessions |
| `GET /sessions/{id}` | read a session back in full (and resume it by posting more messages) |
| `POST /sessions/{id}/messages` `{content}` | send a message -> `reply`, `source` (persona / hub / llm / system), `state` |
| `POST /sessions/{id}/close` | end a session |
| `GET /solutions` | saved solutions (`?status=verified`) |
| `GET /usage`, `GET /usage/calls?session_id=` | token log per session / per LLM call |

```bash
U=$(curl -s -X POST localhost:8000/users -H 'content-type: application/json' -d '{"display_name":"Priya"}' | python -c 'import sys,json;print(json.load(sys.stdin)["user_id"])')
S=$(curl -s -X POST localhost:8000/sessions -H "X-User-Id: $U" | python -c 'import sys,json;print(json.load(sys.stdin)["session_id"])')
curl -s -X POST localhost:8000/sessions/$S/messages -H "X-User-Id: $U" -H 'content-type: application/json' \
     -d '{"content":"My CloudNotes app on my iPhone 14 (iOS 17.2) stopped syncing."}'
```

## How it behaves (maps to the spec)

* **Persona** answers only from the hub, in a fixed template voice; asks a clarifying question when the match is
  uncertain or the device/OS is unknown; says plainly when it has no verified answer; declines "forget your rules" prompts.
* **Escalation**: no verified match -> the full stored conversation goes to the backend LLM (no persona, no restrictions).
  Its text is always labelled *not yet verified*.
* **Extractor** runs when the customer confirms an LLM-served fix. It reads the transcript **from SQLite**, saves
  symptoms / environment / fix steps / what did not work, one solution per confirmed problem, PII scrubbed.
* **Hub**: unconfirmed fixes are never saved; duplicates merge (`confirm_count` goes up); unverified rows are never served.
* **Robustness**: timeouts, outages and malformed LLM output give the customer a plain message (HTTP 200, `source: "system"`),
  are logged, and a failed extraction is retried (on session close and at startup). Rate limits return **429**
  (`Retry-After` header): 30 messages/min and a tighter 5 LLM escalations/min per user.

## Layout
```
dejafix/        api.py chat.py hub.py extractor.py persona.py llm.py textutil.py db.py ratelimit.py config.py errors.py static/
scripts/        run_scenario.py        tests/   conftest.py test_behaviors.py test_robustness.py test_textutil.py
DESIGN.md       one-page design note
```

## Assumptions
Auth is a header (no passwords). One customer message = one turn; the rate limiter is in-process (per server instance).
Confirmation is detected with rules on the customer's own words (see DESIGN.md). Environment parsing covers common
phones, tablets, laptops and OSes; unknown devices are simply not recorded.
