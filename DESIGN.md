# Déjà Fix — Architecture & Design Document

## 1. Data Model

All state is persisted by the Python 3.11 backend (`backend/db.py` using Python `sqlite3`, WAL mode, foreign keys enforced) in a disk-backed SQLite database initialized completely empty with zero seed data:

- **`users`**: `id`, `handle`, `display_name`, `created_at` — isolates customer identities and memory boundaries.
- **`sessions`**: `id`, `user_id` (FK), `title`, `status` (`active | resolved | escalated | unconfirmed`), `memory_summary`, `last_extracted_message_id`, `created_at`, `updated_at`.
- **`messages`**: `id`, `session_id` (FK), `user_id` (FK), `role` (`user | assistant`), `route` (`user | greeting | clarify | hub | escalation | declined | error`), `content`, `created_at`. Every session can be listed, resumed, and read back in full across restarts.
- **`solutions`**: `id`, `source_session_id` (FK), `verified` (integer boolean; retrieval strictly filters `verified = 1`), `symptoms`, `device`, `os`, `app_version`, `fix_steps` (JSON), `what_did_not_work` (JSON), `embedding` (JSON vector), `confirmation_count`, `created_at`, `updated_at`.
- **`solution_confirmations`**: `(solution_id, session_id)` composite primary key enforcing that any single session can increment a solution's `confirmation_count` at most once.
- **`token_logs`**: `id`, `session_id` (FK), `user_id` (FK), `call_type`, `model`, `tokens_in`, `tokens_out`, `estimated_cost_usd`, `created_at`.
- **`rate_limit_events`**: `id`, `user_id` (FK), `event_type` (`message | escalation`), `created_at_ms` — persists per-user sliding-window limits across server restarts.

## 2. When the Extractor Runs, Idempotency & PII Scrubbing

The Extractor runs **only after a customer confirms** that a delivered fix solved their problem, never on unconfirmed suggestions or failed attempts.

- **Store-first & idempotent extraction**: Reads the full transcript from SQLite `messages` (`ORDER BY id ASC`) and checks `sessions.last_extracted_message_id` plus `list_solutions_by_session(session_id)` so multi-problem sessions never re-extract earlier already-saved problems on subsequent confirmations.
- **Deterministic PII scrubbing + post-write assertion**: Extracted entries pass through the LLM generalization prompt, a deterministic regex sanitizer (`redact_pii` removing emails, URLs, order/ticket/account IDs, phone numbers, and customer names while preserving `device`, `os`, and `app_version`), AND a mandatory `assert_pii_free` check before any SQLite write.
- **Dedup & merge-quality policy**: Candidate duplicates require both cosine similarity `>= 0.78` and Stage 2 verifier agreement (`isSameProblemAndFix == true`). Same-session matches (`source_session_id == session_id`) are ignored so a session cannot inflate its own `confirmation_count`, and `solution_confirmations(solution_id, session_id)` prevents double counting. On cross-session merges, `keep_existing` preserves canonical `fix_steps` unless the verifier explicitly scores `use_new` as strictly clearer/more complete, while `what_did_not_work` is union-merged.

## 3. How a Reworded Problem Finds a Solution (and Why)

Déjà Fix uses a **two-stage retrieval pipeline** paired with **structural persona containment**:

1. **Stage 1 — Dense Vector Recall (`gemini-embedding-001`)**: User turns are embedded and ranked against verified `solutions` embeddings (`>= 0.50` cosine floor, top 3 candidates). This captures reworded queries where lexical matching fails.
2. **Stage 2 — Negative-Match & Ambiguity Gate (`gemini-2.5-flash`)**: Evaluates top candidates against the conversation to reject **negative matches** (queries sharing domain words like *"sync"* that are different faults such as `401` 2FA expiry, launch crash, `ENOSPC`, or SSL proxy errors) and request one clarifying question when OS/device/version context is ambiguous.
3. **Structural Persona Containment**: The Persona LLM call (`generate_persona_grounded_reply`) **only ever receives a matched `SolutionRecord`**. When there is no match, the server returns a templated disclosure (`0` Persona LLM tokens) and escalates; rule-override injections (*"forget your rules"*) short-circuit before any LLM call (`0` tokens).

## 4. How "Confirmed" Is Detected (Token-Efficient Gating)

Confirmation detection runs **only after a fix has been delivered** (`route in ('hub', 'escalation')`) and skips turns immediately following a clarifying question (`route == 'clarify'`), preserving Session 2's token savings:

1. **Explicit UI Feedback**: `Confirm Fix Worked` / `Did Not Work` controls bound to `/api/sessions/:id/feedback`.
2. **Conversational Detection**: Fast regex matching for unambiguous confirmations/rejections (`0` LLM tokens), falling back to a compact `gemini-2.5-flash` JSON classifier when natural-language intent is nuanced.

## 5. What We Would Build Next With a Week

1. **Indexed Hybrid Vector + FTS5 Retrieval (`sqlite-vec`)**: Replace linear cosine scan with indexed hybrid search for sub-10ms recall at 100k+ solutions.
2. **Expanded Live Adversarial Eval Set**: Grow `scripts/eval_retrieval.py` to 100+ paraphrase/negative-match/PII/injection cases run automatically in CI.
3. **Version-Aware Solution Decay**: Flag fixes tied to deprecated `app_version` releases for re-confirmation when newer desktop/mobile versions roll out.
4. **Streaming Escalation & Multi-Session Compression**: Stream escalation responses over SSE and compact long user histories while maintaining strict `user_id` isolation.
