# Déjà Fix

Déjà Fix is a terminal, LLM-based toaster-support assistant. It saves full chat transcripts and only adds fixes to its SQLite knowledge base after the customer confirms the fix worked. I am assuming that 5 known model types of the toaster are there and any new ones
are logged as well. 
## First run

Use Python 3.10+ from this project directory:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
```

Configure Gemini using the existing local `api_key.env` (`gemini_key=...`) or the `GEMINI_API_KEY` environment variable. The defaults are `gemini-2.5-flash` for chat and `gemini-embedding-001` for embeddings. Keys are read at runtime and must not be committed.

Start the chat:

```sh
python3 scenario.py
```

Enter a stable customer ID. If the ID has previous sessions, choose one to resume or choose `N` to start a new session. The assistant asks for the toaster model, verifies it against the five starter model names, and requests confirmation before adding a new model to the catalog. A fix is saved only after an explicit customer confirmation. The terminal prints the session's input/output token totals when you exit.

In chat, `/history` shows the transcript, `/sessions` lists sessions for that customer, `/solutions` displays fixes for the selected toaster model, `/model` starts a new session for another model, and `/quit` exits.

## Database

The default SQLite file is `deja_fix.sqlite3` in the project directory. Set `DATABASE_PATH` to use another location. A fresh database contains **no users, sessions, messages, solutions, or token logs**. It contains only the five starter toaster-model catalog rows; the solution knowledge base starts empty. Existing chat data persists across normal restarts.

Inspect all records:

```sh
python3 scenario.py --inspect
```

Inspect one customer, including complete messages and their verified fixes:

```sh
python3 scenario.py --inspect --user-id "customer-a"
```

Each LLM/embedding call is logged in `llm_calls` with its session, model, input/output tokens, and outcome. Verified solutions store separate `original_problem`, `solution`, `symptoms`, `fixes_that_did_not_work`, and `toaster_model_id` fields. Suggested fixes display the confirming user's ID and source session ID.

## Test

Run the deterministic checks in an isolated temporary database:

```sh
python3 scenario.py --self-test
```

The test covers the empty knowledge base, model-specific retrieval, dynamic model confirmation, PII removal, and confirmed-only saves. It does not call an external model.

## Notes

The initial model labels (`Toaster Model 1` through `Toaster Model 5`) are placeholders and can be replaced in `INITIAL_TOASTER_MODELS` in [agent.py](agent.py). When a configured LLM is unavailable, the assistant reports the problem rather than inventing or saving a fix. Toaster troubleshooting includes electrical safety guidance; the assistant does not execute commands.
