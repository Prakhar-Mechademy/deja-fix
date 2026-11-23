# Design notes

## Data model and persistence



SQLite stores users, sessions, ordered messages, the toaster-model catalog, verified solutions, solution provenance, model-call token usage, and rate-limit events. The problems and solutions are checked for similarity by converting them into vectors using the gemini-embedding-1 model, and the running cosine similarity with a threshold of 0.76. On a fresh database, all customer/chat/solution/token tables start empty. Only the five starter toaster-model names are inserted into the reference catalog. Each session and verified solution records one model. `DATABASE_PATH` selects the persistent SQLite file.

## Model selection and retrieval

The terminal asks the customer for their toaster model. The LLM compares the name against the catalog, identifies a possible new toaster model, and asks the user to confirm it. A new catalog entry is written only after confirmation. *Every retrieval and duplicate check filters by the selected model*, preventing a fix for one model from being offered for another. Embeddings use the configured provider, with a local feature-hash fallback for offline development. Weak or ambiguous matches escalate rather than being presented as verified.

## Confirmation and extraction

Only explicit customer confirmation starts extraction and new solutions being added to the database. The model will confirm with the user after every presented solution if the fix worked and only if they answer yes, will the save happen.  The extractor reads the persisted transcript from SQLite and asks the LLM to produce separate original-problem, solution, symptoms, unsuccessful-fixes, and model fields. PII redaction runs before saving. Entries are linked to the confirming session and user; repeated verified fixes merge within the same model. Silence, abandonment, negative confirmation, provider failure, or malformed extraction does not add a solution.

## Tokens and safety

Every model/embedding call is recorded with its session, model, input/output token counts, and outcome. Totals and per-call details are printed on terminal exit. Message and escalation limits apply per user. System instructions direct the assistant to stay on toaster troubleshooting and provide basic electrical safety guidance. No commands are executed.
