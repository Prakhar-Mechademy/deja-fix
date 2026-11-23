# Déjà Fix — confirmed-fix support assistant

A retrieval-first technical support chatbot that **starts with an empty knowledge hub**, escalates unknown problems to Gemini, and saves reusable solutions **only after explicit customer confirmation**. The existing Streamlit app is preserved; an optional FastAPI interface and a three-session offline demo are included.

## Quick start (offline demo, no API key needed)

Use **Python 3.10+** from the project directory:

```bash
python -m venv .venv
source .venv/bin/activate             # Windows: .venv\Scripts\activate
pip install -r requirements-demo.txt
python scripts/demo.py
python -m unittest discover -s tests -v
```

The demo uses a fresh **temporary** SQLite database and deterministic fake embeddings/Gemini. It does **not** call Gemini or download MiniLM. You should see a successful three-session scenario and token use for escalation versus retrieval. These are simulated token counts, not a measured live provider bill.

## Run the real chatbot

```bash
pip install -r requirements.txt
cp .env.example .env                 # Windows: copy .env.example .env
# Edit .env and set GEMINI_API_KEY
streamlit run app.py
```

The first real run downloads the `all-MiniLM-L6-v2` embedding model. This may take longer than five minutes depending on network speed. SQLite is created locally as `deja_fix.db`. No knowledge articles are seeded. Set `DATABASE_URL` to change the database location.

The sidebar allows **Customer 1 → create Customer 2 → switch back to Customer 1**, plus creation/resumption of sessions. A new session for a returning customer displays that customer's previous issue, without exposing other customers' conversations. Streamlit customers are **browser-session demo identities**, not production authentication.

## HTTP API (optional; provides real 429 responses)

Start the API with the same `.env`:

```bash
uvicorn api:app --reload
```

Example calls (each customer gets a **secret bearer token** on creation):

```bash
curl -X POST http://127.0.0.1:8000/users
# Save the returned token; never put it in public logs.
TOKEN='<token from response>'
curl -X POST http://127.0.0.1:8000/sessions -H "Authorization: Bearer $TOKEN"
# Save the session_id from the response.
SESSION='<session_id from response>'
curl -X POST "http://127.0.0.1:8000/sessions/$SESSION/messages" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"content":"Google Drive sync is stuck on Windows 11"}'
curl "http://127.0.0.1:8000/sessions/$SESSION/usage" \
  -H "Authorization: Bearer $TOKEN"
```

`GET /sessions` lists a customer's sessions. `GET /sessions/{id}/messages` returns the full session transcript. Cross-user access returns `404`; missing/invalid bearer tokens return `401`. Message and escalation quotas are stored in SQLite and return **HTTP 429** with a `Retry-After` header when exceeded. The Streamlit UI shows a corresponding clear rate-limit message.

## Confirmation, retrieval and storage behavior

1. A new report searches **verified** solution embeddings. A semantic hit is screened for OS, application, cause, fault category and error-code contradictions. The persona asks an issue-specific clarifying question before showing verified steps.
2. A miss escalates the *full stored transcript* to Gemini. Its output is explicitly labelled **unverified** and a proposed fix is tracked by a `fix_attempts` record.
3. `yes`, `that fixed it`, etc., confirm success; `no`, partial progress and uncertain statements never verify. The extractor **rereads SQLite**, including the customer's outcome, and captures an explicit successful correction rather than blindly storing unsuccessful AI steps. Solutions are generalized and PII is redacted with deterministic heuristics.
4. Verified solutions track source sessions and merge compatible duplicates, incrementing `occurrence_count` only once per confirmed attempt. Each LLM call logs input and output tokens in its session. Failed provider calls are logged with zero usage if the provider offers no token metadata.
5. A returning customer gets a greeting from **their own** prior fix attempt, not another customer's history. Sessions, messages and solutions survive restarts.

## Testing and repository layout

```bash
python scripts/demo.py
python -m unittest discover -s tests -v
# Optional after installing full requirements; downloads real MiniLM embeddings:
python scripts/evaluate_retrieval.py
```

- `app.py` — Streamlit UI and customer switcher
- `api.py` — bearer-authenticated API and HTTP 429
- `app/models.py`, `app/repositories.py`, `app/db.py` — SQLite persistence
- `app/services/runtime.py` — shared chat and confirmation pipeline
- `app/services/knowledge_service.py` — embedding search + hard negatives
- `app/services/extractor.py`, `confirmation.py` — redacted, confirmed learning
- `app/services/rate_limit.py`, `memory.py` — durable quota and customer recall
- `scripts/demo.py` — required empty-database, three-session scenario
- `scripts/evaluate_retrieval.py` — optional real MiniLM paraphrase/hard-negative smoke test
- `DESIGN.md` — one-page design, trade-offs and next steps
- `tests/` — unit and HTTP integration tests using offline model stand-ins

## Console logging / troubleshooting

The application writes structured, timestamped logs to **stdout** (the terminal where Streamlit or Uvicorn is running). Logging is enabled by default at `INFO` level. Set `LOG_LEVEL=DEBUG` in `.env` for lower-level repository, embedding and retrieval-candidate details; use `WARNING` to reduce normal output. Restart the app after changing the setting.

Logs cover application startup, database initialization and transaction outcomes, customer/session/message persistence, API request timing and status codes, per-user rate-limit decisions, retrieval candidate scores and hard-negative rejections, Gemini request duration and token usage, confirmation classification, extractor steps, redaction events, and solution creation/merging. Each event includes relevant user/session/attempt/solution IDs where available. Request logs include a request ID returned in the `X-Request-ID` response header.

For privacy, routine logs do **not** print chat message text, Gemini prompts/responses, bearer tokens, or API keys. They report message/prompt lengths, IDs, decisions, counts and error details instead. Exception tracebacks are logged at application failure boundaries.

Example:

```text
2026-11-23 14:32:02 | INFO | app.services.knowledge_service | knowledge.search_completed accepted=True solution_id=... best_score=0.9123 threshold=0.7800 candidates=4 ...
2026-11-23 14:32:02 | INFO | app.services.chat_service | chat.escalation_succeeded model=gemini-2.5-flash input_tokens=... output_tokens=... elapsed_ms=...
```

## Configuration

| Setting | Default | Description |
|---|---|---|
| `GEMINI_API_KEY` | empty | Required only for real escalation |
| `GEMINI_MODEL` | `gemini-2.5-flash` | Provider model |
| `GEMINI_TIMEOUT_MS` | `30000` | Call timeout |
| `DATABASE_URL` | `sqlite:///./deja_fix.db` | Persistent storage |
| `EMBEDDING_MODEL` | `sentence-transformers/all-MiniLM-L6-v2` | Embeddings |
| `SIMILARITY_THRESHOLD` | `0.78` | Similarity cutoff |
| `RATE_MESSAGES_PER_MINUTE` | `30` | Per-user limit |
| `RATE_ESCALATIONS_PER_MINUTE` | `5` | Lower per-user escalation limit |
| `LOG_LEVEL` | `INFO` | Console logging verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |

## Assumptions and limitations

- The hub is **globally reusable** across customers, but customer sessions and greetings are isolated. Share only redacted solution fields, never cross-user transcripts.
- The extractor is deterministic rather than a second LLM call. It prioritizes confirmed results and catches common names, emails, URLs, phones, and labeled order/account IDs; **regex redaction cannot guarantee that all PII is removed**. For production, use stronger PII/NER tools and human review.
- Similarity + cause guards are conservative heuristics, not a measured accuracy guarantee. Real MiniLM behavior and a live Gemini call have **not** been validated by the offline test suite. Benchmark false matches against realistic customer reports before launch.
- Streamlit's browser identities are **demo-only**, while HTTP endpoints require bearer tokens. For production, add a proper login system, TLS, backups, migrations, concurrent-request load testing, and distributed quota storage.
- Public Git publication is not automated. To meet that deliverable, create and push this directory as a **public repository during your event's permitted commit window**; don't commit `.env`, tokens or your SQLite database.
