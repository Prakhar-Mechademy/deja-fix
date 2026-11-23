# Déjà Fix

Retrieval-grounded support chatbot for **NimbusSync** (cloud sync support) powered by a **Python 3.11** backend (`sqlite3` + standard library HTTP/REST Gemini client) and a React/Vite frontend. Starts with an empty SQLite Knowledge Hub, escalates unknown issues to an unrestricted backend Gemini model, extracts PII-free verified solutions upon customer confirmation, and reuses them for future customers at lower token cost.

## 5-Minute Quickstart

### Prerequisites
- Python `>= 3.11` (zero external `pip` packages required; uses Python standard library `sqlite3`, `urllib.request`, `http.server`)
- Node.js `>= 22` (for the Vite frontend build & port 3000 dev gateway)
- A Gemini API key (`GEMINI_API_KEY`)

### 1. Install frontend dependencies
```bash
npm install
```

### 2. Configure environment variables
Copy `.env.example` to `.env` (`.env` is gitignored; **never** place real keys in `.env.example`):
```bash
cp .env.example .env
# Add GEMINI_API_KEY="your-key-here" inside .env
```

Optional environment variables:
- `DATABASE_PATH="./data/dejafix.db"` (optional — defaults to `./data/dejafix.db` automatically)
- `GEMINI_TEXT_MODEL="gemini-2.5-flash"` (text-generation model)
- `GEMINI_EMBEDDING_MODEL="gemini-embedding-001"` (embedding model)
- `RATE_LIMIT_MESSAGES_PER_MIN="20"`
- `RATE_LIMIT_ESCALATIONS_PER_MIN="5"`
- `GEMINI_TIMEOUT_MS="25000"`

### 3. Run the 3-Session & Retrieval Evaluation Scripts
Run the 3-session scenario against a fresh empty SQLite database (`data/scenario-test.db`):
```bash
# Live Gemini API:
python3 scripts/run_scenario.py

# Deterministic offline CI / mock mode (no API quota spent):
python3 scripts/run_scenario.py --mock
```

Run the paraphrase & negative-pair retrieval benchmark suite:
```bash
npm run eval:retrieval
```

### 4. Start the Full-Stack App
```bash
npm run dev
```
Open `http://localhost:3000`. Use the top-right **Run 3-Session Demo** button or test manually in the Split Console.

For production build + direct Python static serving on port `3000`:
```bash
npm run build
python3 backend/main.py --host 0.0.0.0 --port 3000
```

### 5. Verify Restart Durability
Stop the server (`Ctrl+C`) after confirming a fix in Session 1, then restart it. All users, sessions, messages, verified Knowledge Hub solutions, confirmation counts, and token logs remain intact in SQLite.

## Assumptions
- **Support Domain**: Configured for *NimbusSync*, a cross-device cloud file sync application (tracking device, OS, and app version).
- **Empty Initial State**: The database initializes with zero seed solutions, users, or sessions.
- **Rate Limits**: Enforced per user in SQLite (`20` chat messages/minute and `5` LLM escalations/minute by default), returning HTTP `429` with `Retry-After`.
- **Single-User Local Identity**: Customer switching (`Customer A`, `Customer B`, or custom handle) models multi-customer isolation without requiring external OAuth setup.
