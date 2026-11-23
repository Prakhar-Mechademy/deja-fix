# Déjà Fix

Déjà Fix is a support-chat backend that reuses customer-confirmed fixes. SQLite persists users, sessions, messages, verified solutions, solution provenance, rate-limit events, and per-session model token usage.

## Run in five minutes

1. Use Python 3.10+ and install the dependencies:

	```sh
	python3 -m venv .venv
	. .venv/bin/activate
	python -m pip install -r requirements.txt
	```

2. Configure a Gemini key in the existing local `api_key.env` as `gemini_key=...`, or use the environment variable `GEMINI_API_KEY`. The app uses the Gemini `gemini-embedding-001` embedding model and `gemini-2.5-flash` chat model by default. Override with `GEMINI_EMBEDDING_MODEL` or `GEMINI_MODEL` if needed. OpenAI-compatible `OPENAI_API_KEY` configuration is also supported.

	```ini
	gemini_key=your-key
	# Optional overrides:
	# GEMINI_MODEL=gemini-2.5-flash
	# GEMINI_EMBEDDING_MODEL=gemini-embedding-001
	```

	API keys are read at runtime and must never be pasted into source code or committed. Without a key, a local hashed-feature vectorizer demonstrates retrieval, while escalation and extraction return a clear unavailable response; no fake solution is saved.

3. Start an interactive terminal chat using the persistent SQLite database:

	```sh
	python3 scenario.py
	```

	Enter a stable customer ID. Chat in the terminal; use `/history`, `/sessions`, and `/solutions` to inspect stored records. Type `/quit` to end. Confirm a fix explicitly in chat (for example, “That fixed it”) before it is saved.

4. Inspect the complete database—including every stored message and the separate verified-solution fields—from the terminal:

	```sh
	python3 scenario.py --inspect
	```

	To inspect only one customer's sessions, full messages, and linked verified fixes, run:

	```sh
	python3 scenario.py --inspect --user-id "customer-a"
	```

	For the optional HTTP API, run `uvicorn agent:app --reload` and open `http://127.0.0.1:8000/docs`.

The terminal and API use a local `deja_fix.sqlite3` database by default. Set `DATABASE_PATH` to change it. Restarting the process does not clear it. A verified row has distinct `original_problem`, `solution`, `symptoms`, `fixes_that_did_not_work`, and `environment` columns; complete raw messages are in the `messages` table. The assistant is configured for short, plain-language toaster troubleshooting, with basic electrical safety guidance.

## Demo

Run `python3 scenario.py --self-test` to execute the three-session story plus negative-match and unconfirmed-fix checks. It uses a fresh temporary database and deterministic fake provider; it makes no external API calls and does not use a real API key.

## Retrieval and safety notes

With a configured provider, the system stores provider embeddings and ranks verified solutions by cosine similarity, using a conservative threshold and a score-gap check. Otherwise, a small local hashed-feature vector is used for offline development; configure real embeddings and tune `RETRIEVAL_THRESHOLD` on representative, labeled support examples before production. A candidate is confirmed by the customer before its fix is shown. A missing, weak, or ambiguous match escalates with the stored transcript.

The backend never runs generated commands. Escalated responses may suggest safe, read-only diagnostics for the user to review and run. API keys are loaded from environment files; never commit them. The sample API accepts a `user_id` directly and is intended for a local demo: a deployed service must authenticate users and authorize every session endpoint, use TLS, and add retention/privacy controls.