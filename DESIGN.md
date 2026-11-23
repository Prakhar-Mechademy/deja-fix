# Déjà Fix: Design (one page)

## Data model (SQLite, nothing in memory)
- **users** (id, display_name)
- **sessions** (id, user_id, state, current_problem_id, extracted, timestamps). State is `open`, `clarifying`,
  `awaiting_confirmation` or `ended`. Because state lives in the DB, a session can be resumed after a restart.
- **messages** (session_id, user_id, role, source = customer/persona/hub/llm, content, time). Every message.
- **problems**: one row per problem raised in a session (label, environment, status, source of the fix tried,
  solution_id, confirm_message_id). A session can hold several problems. Used for memory and for extraction.
- **solutions** (symptoms, environment JSON, fix_steps, did_not_work, verified, count, fail_count,
  source_session_id, embedding). **solution_sessions** links every solution to each session that created,
  merged into or reused it.
- **llm_calls**: one row per LLM/embedding call: session, user, purpose, model, tokens in/out, latency, error.

## How a message is handled
1. Recall: the description is embedded and compared (cosine) with **verified** solutions only; the top 3 above a
   threshold become candidates. An empty hub skips this.
2. Triage: one small LLM call (JSON) gets the candidates and decides: is this a support problem, label,
   environment, and **is any candidate the same fault** (same symptom and failure mode, not just shared words).
   It may ask one clarifying question if details are missing or ambiguous.
3. Match → the persona serves the stored steps (template, no free generation). No match → escalation: the
   backend LLM (no persona, no restrictions) gets the full conversation, read from the DB.
4. "Didn't work" → escalate again with everything tried so far (max 3 attempts, then hand-off).

## How a reworded problem finds its solution, and why
Two stages: embeddings for recall (they handle different wording: "notes aren't updating across devices" ≈
"notes stopped syncing"), then an LLM check for precision. Embeddings alone would also match "app crashes when
I open a note on Android 14", which shares many words but is a different fault. The verify step rejects it,
so it is escalated. The check only costs a short prompt, which is much cheaper than an escalation.

## How "confirmed" is detected
Live, a rule-based classifier (`confirm.py`) reads the reply to a fix: negative phrases ("didn't work",
"still", "no") are checked first, then positive ("that worked", "fixed", "yes"). It is free, fast and predictable.
The extractor's LLM also marks each problem as confirmed or not, and a solution is **verified only if both agree**:
the model must point to a customer message that the classifier also reads as a yes, for an escalated fix in
that session. Anything else is stored as unverified and is never served.

## When the extractor runs, and why
When the session ends (`/end`), or automatically after 30 idle minutes (customers often just leave). Running at
the end means it sees the whole transcript, so it can save several problems from one session and record what did
not work. It reads the transcript from the database. Hub fixes that are confirmed live just add +1 to that
solution's count (no LLM call), which is why a reuse session costs fewer tokens than an escalation session.
Before saving, records are generalized: the LLM is told to remove personal data, then `privacy.py` strips emails,
links, phone and order numbers and any names seen in the chat, while keeping device, OS and app version.
Duplicates are merged: a new confirmed solution that matches an existing one (same two-stage check) adds to
its count and its "did not work" list instead of creating a copy.

## Robustness
Per-user limits on messages per minute and escalations per hour (HTTP 429 + Retry-After). Keys only from
environment/.env. Every LLM call has a timeout and one retry. Malformed JSON gets one retry and then a plain
"try again" message, and the server keeps running. Customer messages are saved before any LLM call.

## With one more week
A vector index (sqlite-vec/FAISS) for a large hub; real auth; a human-review queue for unverified and
frequently failing solutions (fail_count); solution versioning per app version; an admin dashboard for token
cost per session; async workers for extraction; an evaluation set of reworded and near-miss problems to tune
thresholds.
