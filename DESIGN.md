# Design notes

## Data model and persistence

SQLite stores users, sessions, ordered messages, verified solutions, solution-to-session provenance, model-call token usage, and rolling per-user rate events. Each session belongs to one user; each solution links to every confirming session that contributed to it. `DATABASE_PATH` selects the durable file. The knowledge store starts empty; there is no seed data.

## Retrieval and answer policy

Each new report is embedded and compared against verified solution vectors using cosine similarity. Provider embeddings are used when configured; a normalized hashed-feature vectorizer keeps local development usable offline. A conservative similarity floor and a top-result separation check reject weak or ambiguous matches. The customer is asked whether the candidate describes the same problem before the stored fix is shown. Unmatched/uncertain cases escalate with the complete transcript read back from SQLite. The hub response is constructed only from a verified stored record; unverified records are never returned.

## Confirmation and extraction

Only an explicit positive confirmation (or the confirmation endpoint) starts extraction. Clear negative language never saves. The extractor reads the ordered conversation from the database, not an in-memory chat buffer, and asks the model for one or more distinct confirmed problems with symptoms, environment, fix steps, and failed attempts. It is not called before confirmation. If the provider is down or the response is malformed, the entry is not stored. Duplicate embeddings merge into the existing solution and increment its confirmation count while adding session provenance.

PII handling uses an extractor instruction plus deterministic redaction for email addresses, URLs, labeled order/account references, self-identified names, and names copied from the transcript. Device, operating system, and application/version fields are retained. This is a practical demo safeguard rather than a guarantee against every possible identifier; production should use a tested PII detector and human review.

## Tokens, limits, and error handling

Every chat and embedding request is logged with its session, model, input/output token counts, and outcome. A hub reuse can incur embedding tokens but avoids the much larger escalation and extraction calls; the included scenario asserts this ordering. Message and escalation limits are stored in SQLite and return HTTP 429 with `Retry-After`. Provider errors, timeouts, empty responses, and malformed extraction output produce plain user messages and do not crash the API.

## Assumptions and next week

A support fix is considered confirmed only when the customer explicitly says it worked; silence, abandonment, and ambiguous replies do not count. The demo uses a customer-supplied stable ID and is not an authentication system. Generated shell commands are never executed; any diagnostic is presented for a person to review and run. With another week, add authenticated accounts and authorization, a front end, a PII scrubber evaluated against a test corpus, retrieval evaluation and threshold calibration, human approval for high-impact fixes, migrations/backups, and provider-specific retry/cost handling.
