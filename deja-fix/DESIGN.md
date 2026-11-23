# Déjà Fix — Backend Design

## Data model

SQLite + SQLAlchemy stores `users`, `sessions`, `messages`, `solutions`, and `llm_logs`. A user owns many sessions; a session owns its transcript; each solution references its source session. LLM usage records are tagged by session. Shared solutions contain generalized problem symptoms, technical environment, fix steps, failed attempts, verification state, occurrence count, and an optional embedding. Customer transcripts remain private and are never used as another customer's memory.

## Orchestration and extraction

Every incoming message is persisted before orchestration. The persona searches verified knowledge only. A safe match is returned in a fixed support tone; uncertain matches trigger clarification; a miss escalates the complete stored transcript to the solver. The solver's answer is explicitly a proposal. Extraction runs only after an explicit positive customer confirmation following a proposed fix. The extractor queries the transcript back from SQLite, structures every confirmed problem, removes PII, retains device/OS/app version, generates an embedding, and deduplicates before saving. A negative or ambiguous response does not verify a fix.

## Retrieval and negative-match handling

When Gemini embeddings are available, cosine similarity ranks verified solutions. The initial strong-match threshold is configurable (default 0.82); a lower semantic band requests clarification. A match below threshold escalates. Without an embedding provider, a conservative token-overlap fallback supports local testing but is not a substitute for semantic retrieval. Similarity is only candidate generation: fault-specific clarification and thresholding reduce the risk of reusing a solution for a different fault. Thresholds must be calibrated against a labelled test set before production.

## Confirmation

Only explicit positive phrases (for example, “that worked” or “problem solved”) count. Explicit negative language takes precedence. “Okay”, “thanks”, and “I'll try” are ambiguous and never cause extraction. Confirmation is tied to the immediately preceding assistant response that supplied a fix.

## Privacy and reliability

The shared knowledge hub strips emails, URLs, common order/account/ticket identifiers, phone-like strings, and common self-introductions. Environment fields are allow-listed. API keys are read from environment variables. SQLite-backed sliding-window limits return HTTP 429. Provider failures and malformed structured extraction responses are handled without crashing; token counts are persisted for generation and embedding calls whenever the provider returns usage metadata.

## One more week

Add authenticated customer identity (JWT/OAuth), migrations, background extraction jobs, stronger PII detection with review, hybrid BM25 + vector retrieval with a dedicated reranker, labelled retrieval evaluation (including hard negative faults), threshold calibration, richer observability/cost dashboards, and integration tests against a real Gemini staging key. Add tenant-aware authorization for administrative solution endpoints before production exposure.
