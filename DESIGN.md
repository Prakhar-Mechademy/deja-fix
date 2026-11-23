# Déjà Fix — Architecture & Design Document

## 1. Data Model (SQLite)
All state is persisted in `dejafix.db` so zero data is lost across server restarts:
- **`users`**: `user_id` (PK), `created_at`
- **`sessions`**: `session_id` (PK), `user_id` (FK), `status`, `created_at`
- **`messages`**: `id` (PK), `session_id` (FK), `user_id` (FK), `role`, `content`, `timestamp`
- **`solutions`**: `id` (PK), `source_session_id` (FK), `symptoms`, `environment`, `fix_steps`, `what_did_not_work`, `embedding` (JSON vector), `confirmed_count`, `created_at`
- **`token_logs`**: `id` (PK), `session_id`, `call_type`, `tokens_in`, `tokens_out`, `timestamp`

## 2. When the Extractor Runs and Why
The extractor runs **only when a customer explicitly confirms** that a fix worked in the conversation. It reads the full session transcript directly from the `messages` table in SQLite rather than from an in-memory variable so unconfirmed fixes are never saved, multiple solved issues in one session are captured independently, and PII is stripped while device/OS/version details are preserved.

## 3. How Reworded Problems Match Saved Solutions
1. **Semantic Vector Search (`text-embedding-004`):** Converts the user's problem into an embedding vector and computes cosine similarity against stored solution embeddings (`threshold >= 0.65`).
2. **Fault Verification Gate (`gemini-2.5-flash`):** Confirms both queries describe the exact same underlying fault to prevent false matches sharing surface keywords. Duplicate verified solutions (`similarity > 0.90`) increment `confirmed_count`.

## 4. How "Confirmed" Is Detected
Incoming user messages after the first turn are scanned for confirmation signals (e.g., *"worked"*, *"fixed"*, *"solved"*), triggering the Extractor LLM to verify confirmation against the full database transcript before saving.

## 5. What We Would Build Next (With One More Week)
- Persistent Redis sliding-window rate limiting across multiple workers.
- Explicit UI confirmation buttons alongside natural-language detection.
- Automated `pytest` suite covering prompt injection, negative matches, server restarts, and 429 limits.
