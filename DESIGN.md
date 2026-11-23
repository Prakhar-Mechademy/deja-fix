# Déjà Fix: Design

**Data model (SQLite, WAL).** `users`; `sessions` (status, `served_from` = hub or escalation,
`extracted_at`); `messages` (every message with its session and user, plus a `source` tag);
`solutions` (title, symptoms, environment JSON of device/OS/app version, fix steps, failed
attempts, embedding, `status` verified/unverified, `confirmed_count`, `source_session_id`);
`solution_sessions` (origin, merge or reuse link between a session and a solution);
`llm_calls` (purpose, tokens in/out, latency, status, tagged with the session);
`rate_events` (so limits survive restarts). Solutions carry no `user_id`: they are global and
anonymised, and provenance lives in the session link. Nothing is held in memory, so a restart
loses nothing. The hub only ever reads `status = 'verified'` rows.

**When the extractor runs, and why.** Once per session, after it is closed (explicitly, or
automatically after `IDLE_CLOSE_MINUTES` of silence, so a customer who confirms and leaves is
still learned from). It reads the transcript from the database, never from a variable. Running
at close rather than per message means it sees the whole story: it can tell what failed before
what finally worked, produce several solutions from one session, and costs one call per
session. Cost is kept down: a session served from the hub runs only a small confirm-and-merge
check; a session that never had a fix served skips the LLM entirely. A failed extraction
(outage) leaves the session unprocessed so `/admin/extract-pending` can retry it.

**How a reworded problem finds its solution, and why.** Two stages. (1) Embeddings
(`gemini-embedding-001`) shortlist the closest verified solutions with cosine similarity: cheap,
and it finds matches described in different words. (2) An LLM judge decides whether the
customer's problem is the same underlying fault as each shortlisted solution; only a clear "yes"
counts, and "unsure" or a malformed answer means no match, so the persona escalates. We chose
this because embeddings alone cannot separate "same fault, different words" from "different
fault, similar words". On real data a reworded sync problem scored 0.90 while "app won't open"
scored 0.91 against a "won't sync" fix, so no similarity threshold gets both cases right. The
judge also checks that the customer's stated device/OS does not conflict with the saved
environment. The same search detects duplicates when the extractor saves, so a duplicate merges
(count +1, new failed attempts added) instead of creating a copy. With an empty hub no
embedding or judge call is made at all.

**How "confirmed" is detected.** Three layers. The persona recognises plain confirmations
("that fixed it", "working again") with a small rule set and replies without an LLM call;
confirmations that also raise something else or say the fix did not last ("...but now", "for a
minute") are not treated as the end. The extractor asks the LLM for solutions only where the
customer explicitly said the fix worked, and the LLM must copy the customer's own confirming
words in `confirmation_quote`. Code then checks that quote actually appears in a stored customer
message; no genuine quote, no save. Anything unconfirmed is skipped and never served. Personal
data is removed by the LLM prompt, then a regex pass strips emails, links, phone numbers,
order/account numbers and names (the user's display name plus names the customer introduced
themselves with or put in an email address) from every saved field. Device, OS and app version
are kept.

**Other choices.** Persona answers and clarifying questions are templated, not generated, so
the persona cannot invent a fix and its tone is fixed. Jailbreak phrases are declined before any
search or LLM call. A clarifying question is asked on the customer's first message (a
confirm-your-device question when a solution matched, a generic one when the message is vague).
The escalation LLM gets no persona and the whole conversation. Provider errors, timeouts and
malformed responses become a plain message; nothing crashes. Rate limits are per user, in the
database; the escalation limit is tighter.

**Assumptions.** Auth is out of scope (the caller supplies `user_id`; sessions are
ownership-checked). Admin endpoints are open for the demo. Embedding token counts are estimated.

**What I would build next with a week.** Real authentication and per-tenant hubs; a vector
index (sqlite-vec or pgvector) instead of scanning all embeddings; a small LLM or classifier
for confirmation detection so the persona does not depend on phrases; human review and a
"stale solution" signal when a served fix is later reported as not working; ranking by
`confirmed_count` and recency; a retry queue for extraction with backoff; streaming replies and
a small web chat UI; and an evaluation set to tune the match judge and measure false matches.
