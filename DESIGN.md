# Déjà Fix — Design

## Data model

Déjà Fix uses six SQLAlchemy tables:

1. `users` — stable external customer identifiers and account status.
2. `sessions` — customer-owned support sessions.
3. `messages` — ordered persisted transcripts for each session.
4. `solutions` — shared canonical knowledge, including JSON symptoms/steps, text embeddings, model identifier, status and environment metadata.
5. `solution_sources` — provenance linking each solution to confirming sessions and confirmation messages.
6. `llm_usage` — Gemini purpose, session, model and token accounting.

Foreign keys enforce session, message and provenance relationships. SQLite foreign-key enforcement and UTC timestamps are enabled. Verified solutions alone are eligible for normal retrieval.

## Extraction timing

The extractor runs after a customer explicitly confirms resolution. It reads the complete session transcript from SQLite, rather than in-memory state, so extraction survives application restarts. Gemini is requested to return one or more structured solutions, with PII sanitisation and local heuristics available as a fallback.

## Reworded-problem retrieval

The embedding service uses `all-MiniLM-L6-v2` and NumPy normalised vectors. Retrieval compares the customer text with stored solutions using cosine similarity, ranks only `VERIFIED` candidates and adds application metadata checks before merging or serving knowledge.

This implementation uses three bands:

- High confidence: score at least 0.78 with adequate separation between candidates
- Uncertain: score at least 0.55
- Low confidence: below 0.55

Semantic embeddings handle differently worded descriptions better than keyword matching, which would miss paraphrases and over-match unrelated faults containing the same terms.

## Confirmation detection

The confirmation service recognises explicit customer language such as “it worked”, “the issue is resolved”, or “both fixes worked”. Assistant suggestions alone do not count. `solution_sources` preserves the exact confirming message and session.

## Knowledge generalisation and quality

The generalisation pipeline redacts PII and credentials, standardises steps and problem wording, preserves technical environment information and validates that a confirmed solution has actionable steps and no unresolved PII. Duplicate detection combines semantic similarity with technical metadata; it does not merge solely on cosine score.

## Known limitations

- Thresholds have not been calibrated on a large labelled dataset.
- Retrieval is currently a SQLite scan, without an ANN/vector index.
- The demo's realistic reworded problem can score uncertain until it supplies the exact error and app version.
- Failed Gemini calls count through a separate attempt record, with operational tuning still needed.
- Confirmation remains text-pattern based and would benefit from review or structured confirmation signals.

## What to build next with a week

1. Calibrate high/uncertain/low thresholds on a larger positive and negative dataset.
2. Add an ANN vector index or managed vector search for knowledge scaling.
3. Add human review and editorial approval before solution verification.
4. Add token-bucket or sliding-window rate limiting with attempts and failures counted consistently.
5. Add a customer-facing UI with session history and confirmation controls.
