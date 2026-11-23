# I asked GPT to summarize all the tests and the responses:
# All the usage scenarios were tested and debugged. 

**Project:** Déjà Fix\
**Document:** Tests completed to date\
**Date:** 23 November 2026\
**Environment:** Local backend running from the VS Code integrated
terminal

------------------------------------------------------------------------

## 1. Purpose

This document records the Déjà Fix backend tests that have actually been
performed so far, the steps used to perform them, the observed results,
and the current status.

The tests focus on the three-session demonstration:

1.  Customer A reports a problem, receives an escalated troubleshooting
    suggestion, confirms the fix, and the extractor saves it.
2.  Customer B reports the same problem in different words and receives
    the previously verified solution from the knowledge hub.
3.  Customer A returns and receives a greeting based only on Customer
    A's own previous history.

The technical requirements specifically call for confirmed-fix
extraction, reworded-problem reuse, duplicate merging, persistent
session/message storage, and customer memory isolation.

------------------------------------------------------------------------

## 2. Test Environment and Database

### 2.1 Backend environment

The backend was run locally from:

``` text
/home/csuser/Documents/Mechademy_Ribence/deja-fix/backend
```

The Python virtual environment was activated with:

``` bash
source ../../.venv/bin/activate
```

The backend was served locally at:

``` text
http://127.0.0.1:8000
```

API requests were made with `curl` from the VS Code integrated terminal.

### 2.2 Database used

The tests used the preserved SQLite backup database:

``` text
backend/data/deja_fix_backup.db
```

The terminal was configured to use it with:

``` bash
export DATABASE_URL="sqlite:////home/csuser/Documents/Mechademy_Ribence/deja-fix/backend/data/deja_fix_backup.db"
```

The original database was backed up before the test database was reset.
The backup database contained an existing verified Windows
synchronization solution. The new Android photo-upload solution was
created during the tests below.

**Important:** The database was not deleted or reset during the tests
described in this report. The backup database was retained.

### 2.3 Relevant configuration

The Gemini model used for the successful chat tests was:

``` text
GEMINI_MODEL=gemini-3.1-flash-lite
EMBEDDING_MODEL=gemini-embedding-001
```

The retrieval thresholds were adjusted during testing to:

``` text
RETRIEVAL_THRESHOLD=0.78
AMBIGUOUS_THRESHOLD=0.68
```

The retrieval threshold was initially `0.82`. The reworded Customer B
query scored approximately `0.7915`, so it did not pass the original
threshold. After changing the retrieval threshold to `0.78` and
restarting the server, the same query retrieved the verified solution.

API credentials were loaded through the backend environment
configuration. No API key is included in this document.

------------------------------------------------------------------------

## 3. Test 1 --- Customer A: Escalation and Solution Extraction

**Status: PASS**

### Objective

Verify that when the knowledge hub does not contain a matching verified
fix, the chatbot escalates the issue to the backend LLM. After the
customer confirms that the proposed fix worked, the extractor should
save the solution.

### Customer and session

  Field         Value
  ------------- ----------------------------------------
  Customer      Customer A
  User ID       `50c5e095-3363-494c-9498-ad5017d3d4f4`
  External ID   `customer_a_001`
  Session ID    `6bfd9a81-4a78-491e-8351-e28cc53047e7`

### Steps performed

1.  Created Customer A using the user API.

2.  Created a chat session for Customer A.

3.  Sent this problem through `POST /api/chat/message`:

    > My Samsung Galaxy S24 running Android 15 crashes whenever I upload
    > a photo. My app version is 6.1. Restarting the phone did not help.

4.  Checked the API response.

5.  Customer A tried the proposed troubleshooting steps and sent an
    explicit confirmation:

    > That worked! I checked the photo permissions, cleared the app
    > cache, and tried uploading a new photo. The application is now
    > working perfectly.

6.  Checked the response and the saved solution record.

### Observed results

The initial problem was escalated successfully:

-   Response source: `llm`
-   `solution_id`: `null`
-   `requires_confirmation`: `true`

The assistant proposed troubleshooting steps involving photo
permissions, clearing the application cache, and trying another image.

After Customer A confirmed success, the extractor recorded a verified
solution.

  ----------------------------------------------------------------------------
  Extracted field                     Stored value
  ----------------------------------- ----------------------------------------
  Solution ID                         `18b1a9b3-8b25-48f0-a1f8-ce6ed6f0bb33`

  Symptoms                            Application crashes immediately when
                                      uploading a photo

  Device                              Samsung Galaxy S24

  Operating system                    Android 15

  App version                         6.1

  Fix steps                           Verified app permissions to access
                                      storage/photos, cleared app cache, and
                                      tested with a new, simple photo.

  Failed attempt                      Restarted the phone

  Verified                            `true`

  Initial occurrence count            `1`
  ----------------------------------------------------------------------------

### Result

**PASS.** The issue was escalated when no matching verified solution was
available. The fix was saved as verified only after the customer
explicitly confirmed that it worked. The extracted record retained the
device and software environment, the symptoms, the fix steps, and the
failed attempt.

------------------------------------------------------------------------

## 4. Test 2 --- Customer B: Reworded Problem Retrieval

**Status: PASS**

### Objective

Verify that a second customer can describe the same underlying issue in
different words and receive the existing verified knowledge-hub solution
without another LLM troubleshooting escalation.

### Customer and session

  Field         Value
  ------------- ----------------------------------------
  Customer      Customer B
  User ID       `f6b87b89-8832-4949-91a9-8fce7446d39d`
  External ID   `customer_b_001`
  Session ID    `44597251-06cb-4060-bb73-6ba7faf6b92e`

### Steps performed

1.  Created Customer B.

2.  Created a new chat session for Customer B.

3.  Sent a reworded version of Customer A's issue through
    `POST /api/chat/message`:

    > Whenever I try to upload pictures from my Samsung phone, the app
    > suddenly closes. I am using Android 15 and version 6.1 of the app.
    > How can I fix this?

4.  Inspected the retrieval behavior and similarity score.

5.  Adjusted the retrieval threshold from `0.82` to `0.78` in the
    backend environment configuration.

6.  Restarted the backend so the updated configuration would be loaded.

7.  Repeated the API request.

8.  Confirmed that Customer B received the existing verified solution.

9.  Customer B explicitly confirmed success:

    > Yes, that fixed the problem! I enabled the photo permissions,
    > cleared the cache, and uploaded another picture. It works
    > perfectly now.

10. Inspected the database record to verify that the existing solution
    was updated rather than duplicated.

### Retrieval threshold investigation

With the original retrieval threshold of `0.82`, the reworded query had
a similarity score of approximately:

``` text
0.7914924371
```

This score was below `0.82`, so the solution was not automatically
accepted as a match. It was in the ambiguous range above the configured
ambiguity threshold.

The retrieval threshold was then changed to:

``` text
RETRIEVAL_THRESHOLD=0.78
AMBIGUOUS_THRESHOLD=0.68
```

After the backend restart, the same query retrieved the saved solution.

### Observed API result

The successful response contained:

``` json
{
  "session_id": "44597251-06cb-4060-bb73-6ba7faf6b92e",
  "source": "knowledge_hub",
  "solution_id": "18b1a9b3-8b25-48f0-a1f8-ce6ed6f0bb33",
  "requires_confirmation": true
}
```

The response provided the verified photo-upload troubleshooting steps
and asked Customer B whether they resolved the issue.

After Customer B confirmed success, the system acknowledged the
confirmation.

### Database verification

The database was inspected after Customer B's confirmation.

  Field                       Observed result
  --------------------------- ----------------------------------------
  Matching solution records   1
  Solution ID                 `18b1a9b3-8b25-48f0-a1f8-ce6ed6f0bb33`
  Verified                    `true`
  Occurrence count            `2`
  Last confirmed timestamp    Updated

The occurrence count increased from `1` to `2`. No second copy of the
same solution was created.

### Result

**PASS.** Customer B's differently worded issue retrieved the verified
solution from the knowledge hub. The successful response used
`source: knowledge_hub` and referenced the original solution ID. After
confirmation, the existing solution's occurrence count increased to two
instead of creating a duplicate.

------------------------------------------------------------------------

## 5. Test 3 --- Customer A: Returning-Customer Memory

**Status: PASS**

### Objective

Verify that a returning customer is greeted with information from their
own previous conversation and that the greeting does not expose another
customer's conversation.

### Implementation change

Inspection of the existing memory service showed that it had user-scoped
history functions, including:

``` python
latest_user_problem(db, user_id)
```

The function searches sessions belonging to the supplied user ID and
retrieves that customer's latest available problem.

However, the session creation route was not using this function to
create a greeting. The session creation implementation was updated in
`api/sessions.py`.

The updated route:

1.  Validates that the customer exists.
2.  Calls `latest_user_problem(db, payload.user_id)`.
3.  Creates a new chat session.
4.  Adds an assistant greeting as the first message in that session.
5.  Commits the session and greeting to SQLite.
6.  Returns the greeting in the API response.

The greeting uses the previous problem, truncated to 250 characters:

``` python
greeting = (
    "Welcome back! Last time, you reported: "
    f"{previous_problem[:250]}. How can I help you today?"
)
```

If the customer has no previous problem, the route uses a general
welcome message.

### 5.1 Customer A greeting

A new session was created for Customer A.

  Field            Value
  ---------------- ----------------------------------------
  Customer         Customer A
  User ID          `50c5e095-3363-494c-9498-ad5017d3d4f4`
  New session ID   `fca4f3e8-6bc3-4f2b-92fd-0ba6e20e5306`

Request:

``` bash
curl -X POST http://127.0.0.1:8000/api/sessions \
-H "Content-Type: application/json" \
-d '{
  "user_id": "50c5e095-3363-494c-9498-ad5017d3d4f4"
}'
```

Observed response included:

``` json
{
  "id": "fca4f3e8-6bc3-4f2b-92fd-0ba6e20e5306",
  "user_id": "50c5e095-3363-494c-9498-ad5017d3d4f4",
  "greeting": "Welcome back! Last time, you reported: Hi, I am using your application on my Samsung Galaxy S24 running Android 15, app version 6.1. Every time I try uploading a photo, the application immediately crashes. I have restarte"
}
```

The actual response showed Customer A's earlier photo-upload problem.
The displayed greeting was truncated as intended.

### 5.2 Customer B greeting and isolation check

A new session was then created for Customer B.

  Field            Value
  ---------------- ----------------------------------------
  Customer         Customer B
  User ID          `f6b87b89-8832-4949-91a9-8fce7446d39d`
  New session ID   `72b85529-785b-4ea0-a500-91a394774bfc`

Request:

``` bash
curl -X POST http://127.0.0.1:8000/api/sessions \
-H "Content-Type: application/json" \
-d '{
  "user_id": "f6b87b89-8832-4949-91a9-8fce7446d39d"
}'
```

Observed response included:

``` json
{
  "id": "72b85529-785b-4ea0-a500-91a394774bfc",
  "user_id": "f6b87b89-8832-4949-91a9-8fce7446d39d",
  "greeting": "Welcome back! Last time, you reported: Whenever I try to upload pictures from my Samsung phone, the app suddenly closes."
}
```

The greeting contained Customer B's own earlier wording, rather than
Customer A's original message.

### Result

**PASS.** Customer A received a greeting based on Customer A's own
previous problem. Customer B received a greeting based on Customer B's
own previous problem. The memory lookup is scoped by `user_id`, and the
returned greetings demonstrated that one customer's history was not used
for the other customer's greeting.

------------------------------------------------------------------------

## 6. Test Summary

  -----------------------------------------------------------------------
  Test                    What was verified       Status
  ----------------------- ----------------------- -----------------------
  Test 1 --- Escalation   New issue was sent to   PASS
                          the backend LLM when no 
                          matching verified fix   
                          was available           

  Test 1 --- Confirmation Explicit customer       PASS
  and extraction          confirmation triggered  
                          extraction and creation 
                          of a verified solution  

  Test 2 --- Reworded     A differently worded    PASS
  retrieval               issue retrieved the     
                          existing verified       
                          solution                

  Test 2 --- No duplicate Confirmation updated    PASS
                          the existing solution's 
                          occurrence count from 1 
                          to 2                    

  Test 3 --- Customer A   Customer A's new        PASS
  memory                  session greeting        
                          referenced Customer A's 
                          previous problem        

  Test 3 --- Customer B   Customer B's new        PASS
  memory isolation        session greeting        
                          referenced Customer B's 
                          own previous problem    
  -----------------------------------------------------------------------

------------------------------------------------------------------------

## 7. Tests Not Yet Performed

The following requirements have **not** been verified in the tests
documented above. They should not be treated as passing until they have
been run and their results recorded.

  -----------------------------------------------------------------------
  Pending test                        What still needs to be verified
  ----------------------------------- -----------------------------------
  Negative match                      A problem that shares words with a
                                      saved solution but represents a
                                      different fault must be escalated
                                      rather than answered with the
                                      unrelated solution.

  Unconfirmed fix                     A customer says the proposed fix
                                      did not work, leaves, and no new
                                      verified solution is created or
                                      served.

  Generalization and personal-data    A test conversation containing a
  removal                             name, email address, and order
                                      number produces a saved solution
                                      without those personal details
                                      while retaining device and
                                      operating-system information.

  Persona containment                 Requests outside the knowledge hub,
                                      including attempts to override the
                                      persona's rules, do not cause it to
                                      invent a verified fix.

  Restart persistence                 Stop and restart the backend
                                      between sessions and verify that
                                      users, sessions, messages, and
                                      saved solutions remain available.

  Token logging                       Verify input and output token
                                      records for each LLM call and
                                      compare the cost of a knowledge-hub
                                      response with an escalated
                                      response.

  Rate limiting                       Verify per-user message limits,
                                      tighter escalation limits, and
                                      clear HTTP 429 responses.

  Provider failure handling           Verify that malformed responses,
                                      timeouts, and provider outages are
                                      handled without crashing the
                                      backend.

  Session read/resume                 Verify listing sessions, retrieving
                                      a full transcript, and resuming an
                                      existing session.
  -----------------------------------------------------------------------

------------------------------------------------------------------------

## 8. Notes and Known Observations

1.  **Retrieval threshold:** The threshold was changed from `0.82` to
    `0.78` because the tested reworded query scored approximately
    `0.7915`. This is a test configuration choice, not proof that `0.78`
    is optimal for all issue types.
2.  **Greeting length:** The previous problem is truncated to 250
    characters in the greeting. The observed Customer A greeting ended
    mid-word because of this truncation. This does not affect the
    memory-isolation result, but the presentation could be improved by
    truncating at a word boundary.
3.  **Existing database content:** The tests used the preserved backup
    database, which already contained a verified Windows synchronization
    solution. Therefore, the test environment was not a completely empty
    knowledge hub at the start.
4.  **Scope of evidence:** The PASS statuses above refer only to the
    specific test inputs and observed outputs documented in this report.
    They do not establish that every edge case or every requirement has
    been tested.

------------------------------------------------------------------------

## 9. Conclusion

Three major parts of the Déjà Fix demonstration have been exercised
successfully:

-   A new customer issue was escalated, explicitly confirmed, and
    extracted into a verified knowledge-hub solution.
-   A second customer described the same issue differently and received
    the saved solution. The existing record was updated rather than
    duplicated.
-   Returning-customer greetings used each customer's own previous
    issue, demonstrating memory isolation for the two tested customers.

The remaining robustness, privacy, persistence, token-logging, and
rate-limiting tests are listed as pending and should be completed before
declaring the full requirements test suite complete.
