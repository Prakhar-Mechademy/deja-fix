HOW TO USE?

## 1. Start the Backend

Open a terminal in the `deja-fix/backend` directory.

### Activate the virtual environment

**Linux/macOS:**
```bash
source ../../.venv/bin/activate


pip install -r requirements.txt


Run the server:
From the backend directory:
uvicorn main:app --reload

2. Customer Workflow
The typical workflow involves creating a customer, starting a session, sending support messages, and confirming whether a proposed solution worked.
Step 1: Create a Customer
curl -X POST http://127.0.0.1:8000/api/users \
  -H "Content-Type: application/json" \
  -d '{
    "external_id": "customer-123",
    "display_name": "Customer"
  }'

Save the returned id. This is the customer's user_id.

If the same external_id already exists, the existing customer record is retrieved.
Step 2: Create a Chat Session
curl -X POST http://127.0.0.1:8000/api/sessions \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "YOUR_USER_ID"
  }'

Save the returned session id as your session_id.

The session response includes a greeting. If the customer has previously contacted support, the greeting can reference their previous issue.
Step 3: Send a Support Message
curl -X POST http://127.0.0.1:8000/api/chat/message \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "YOUR_USER_ID",
    "session_id": "YOUR_SESSION_ID",
    "message": "My application crashes whenever I upload a photo."
  }'

The backend processes the issue and returns a response.

Possible response sources:
Source	Meaning
knowledge_hub	A matching verified solution was found.
llm	No suitable verified solution was found, so the issue was escalated to the LLM.
clarification	More information is required from the customer.
system	A system-generated response.


Step 4: Confirm the Solution
If the backend suggests troubleshooting steps, the customer should try them and explicitly confirm whether the issue was resolved.

Example:
That worked! I enabled the photo permissions, cleared the cache, and uploaded another picture. It works perfectly now.

When a customer explicitly confirms an LLM-proposed solution, the extraction process can save the solution to the verified knowledge hub.

If the solution does not work, the customer should explicitly say so. An unsuccessful or unconfirmed solution should not be added to the verified knowledge hub.
3. Customer Sessions and History
The backend maintains customer-specific conversation history.
List a customer's sessions
curl "http://127.0.0.1:8000/api/sessions?user_id=YOUR_USER_ID"

Retrieve messages from a session
curl "http://127.0.0.1:8000/api/sessions/YOUR_SESSION_ID/messages?user_id=YOUR_USER_ID"

Resume a session
curl "http://127.0.0.1:8000/api/sessions/YOUR_SESSION_ID?user_id=YOUR_USER_ID"

Important: Always use the correct user_id when accessing a customer's sessions. Session access is scoped to its owning customer.
4. Access the Knowledge Hub
The knowledge hub stores verified solutions that can be reused for future customer problems.
Retrieve all verified solutions
curl "http://127.0.0.1:8000/api/solutions"

Retrieve a specific solution
curl "http://127.0.0.1:8000/api/solutions/YOUR_SOLUTION_ID"

Solutions include information such as:
- Problem symptoms.
- Device and operating system.
- Application version.
- Verified troubleshooting steps.
- Previously unsuccessful attempts.
- Number of times the solution has been confirmed.
Similar confirmed solutions can be merged rather than creating unnecessary duplicates.
5. Monitor Token Usage
The backend tracks LLM token usage.

Retrieve token usage for a particular session:
curl "http://127.0.0.1:8000/api/analytics/tokens/YOUR_SESSION_ID?user_id=YOUR_USER_ID"

The response provides token usage information, including input and output tokens.

This helps compare:
- Issues resolved through the knowledge hub.
- Issues escalated to the LLM.
- Token consumption across support sessions.
6. Important Development Notes
1. Keep customer IDs consistent: Use the same user_id for all sessions belonging to a customer.
2. Keep session IDs consistent: Use the same session_id throughout one conversation.
3. Confirm solutions explicitly: Only customer-confirmed fixes should enter the verified knowledge hub.
4. Preserve the database: Do not delete or reset the SQLite database unless you intentionally want to remove stored customer and support data.
5. Protect credentials: Never expose your Gemini API key or commit .env to Git.
6. Check the server: Use /health to verify that the backend is running.
7. Use Swagger: Visit /docs to inspect endpoint parameters, request schemas, and responses.
Note: The current backend is intended for development and testing. Before deploying it publicly, implement production-grade authentication and authorization. UUIDs alone are not a substitute for authentication.
Quick Reference
Start the server:
uvicorn main:app --reload

Open the API documentation:
http://127.0.0.1:8000/docs

Then:
1. Create a customer.
2. Create a chat session.
3. Send a support message.
4. Let the backend retrieve a verified solution or escalate the issue.
5. Ask the customer to confirm whether the suggested fix worked.
6. Check the knowledge hub for newly saved verified solutions.









The branch name is Ribence_Mechademy_1020231799
git push -u origin new-branch-name


The problem is basically that same customer  problem -> n number of LLM calls

customer->LLM call-> customer verifies that its true -> stores it in the knowledge base-> reuses for future use

The persona agent is a controlled interface around your knowledge base, not an unrestricted AI assistant.
Knowledge Hub (RAG system)

DB design:
| Table | Important fields | Purpose |

| users | id, external_id, created_at | Customer identity |
| sessions | id, user_id, title, status, timestamps | Individual conversations |
| messages | id, session_id, user_id, role, content, timestamp | Complete conversation history |
| solutions | id, source_session_id, symptoms, environment, fix_steps, failed_attempts, verified, occurrence_count, embedding |Knowledge hub |
| llm_logs | id, session_id, model, input_tokens, output_tokens, timestamp | LLM cost tracking |



Creating Customer A which sends a request to the API. 