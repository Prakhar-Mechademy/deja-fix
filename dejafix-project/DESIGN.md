# Déjà Fix: design note

**Data model (SQLite).** `users` -> `sessions` (state machine: gathering, clarifying, fix_offered_hub, escalated, resolved)
-> `messages` (every message with session, user, role, source: user/persona/hub/llm/system). `session_problems` tracks each
problem in a session (who served it, which message confirmed it, whether it was extracted). `solutions` holds title,
symptoms, environment (device / OS / app / version), fix steps, did-not-work, alternative fixes, fault types, status
(verified / unverified), confirm and fail counts, and `created_session_id`; `solution_sources` links every confirming session.
`llm_calls` logs tokens in/out, purpose, status and latency for **every** call, tagged with its session. Nothing is held in
memory except rate-limit counters, so a restart loses nothing.

**When the extractor runs, and why.** Once, at the moment the customer confirms a fix the *backend LLM* gave (not on every
turn, which would cost tokens, and not at session end, which loses data if the customer just leaves). It reads the
transcript from the database, not from a variable. The LLM must cite the confirmation message id, so it cannot invent a
confirmation; its output is scrubbed again in code. One session can yield several solutions (one per confirmed problem).
If the provider fails, the session is flagged and the extraction is retried on close and at startup.

**How a reworded problem finds its solution.** Embeddings were the obvious alternative, but they need a second paid model
and give soft scores that confuse "same words, different fault". Instead: (1) a deterministic **fault-type gate** (sync,
crash, login, wifi, ... detected by rules, so "never turns up on the other device" is a *sync* fault); a different fault type,
a different app, or a different platform (iOS vs Android) is a hard "no match", which makes "CloudNotes keeps crashing"
escalate instead of getting the sync fix; (2) a weighted score of fault match, app match and token cosine similarity;
(3) borderline scores (0.55-0.80) go to a cheap small-model "same fault?" judge (the mock offline). A medium match, or a
match with unknown device/OS, triggers a clarifying question first. Trade-off: new fault families need a rule; see below.

**How "confirmed" is detected.** Rules on the customer's own message, only while a fix is on the table. Positive phrases
("fixed", "did the trick", "syncing again") count, negative ones ("didn't help", "still not working", "worked for a minute
but stopped") count against, and **whichever comes last wins**, so "that didn't help but signing out fixed it" is a confirmation.
"I'll try it and tell you" and a bare "thanks" are *not* confirmations. Nothing is saved on a guess.

**Generalisation and safety.** Emails, links, IPs, phone numbers, order/account ids and names (the user's registered
name plus self-introductions) become placeholders; device, OS and app version are kept. The persona is template-based,
so it can only say what the hub holds; jailbreak phrases are declined and never forwarded to the backend LLM.

**What I'd build next with a week.** (1) Embeddings + a vector index as a second retrieval signal next to the fault gate,
and an LLM-assisted open-ended fault classifier instead of fixed rules; (2) an LLM-based confirmation classifier for
ambiguous messages (rules stay as the cheap first pass); (3) per-solution decay and auto-demotion from fail counts, with a
review queue for human agents; (4) real auth, a shared rate-limit store (Redis) and streaming replies; (5) an evaluation set
of paraphrased problems to measure precision/recall of matching instead of eyeballing.
