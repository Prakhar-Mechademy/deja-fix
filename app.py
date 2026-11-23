import time
from collections import defaultdict
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

load_dotenv()

from database import (
    init_db,
    ensure_user_and_session,
    save_message,
    get_session_messages,
    get_user_past_sessions,
    get_all_solutions,
    get_session_token_summary,
)
from llm_service import (
    search_knowledge_hub,
    persona_respond,
    escalate_to_backend_llm,
    check_and_extract_solutions,
)

app = FastAPI(title="Déjà Fix Support Assistant")

USER_MSG_TIMESTAMPS = defaultdict(list)
USER_ESC_TIMESTAMPS = defaultdict(list)

MSG_LIMIT_PER_MIN = 15
ESC_LIMIT_PER_MIN = 5

@app.on_event("startup")
def startup():
    init_db()

class ChatRequest(BaseModel):
    user_id: str
    session_id: str
    message: str

def enforce_rate_limit(user_id: str, is_escalation: bool = False):
    now = time.time()
    window = 60.0

    USER_MSG_TIMESTAMPS[user_id] = [t for t in USER_MSG_TIMESTAMPS[user_id] if now - t < window]
    USER_ESC_TIMESTAMPS[user_id] = [t for t in USER_ESC_TIMESTAMPS[user_id] if now - t < window]

    if not is_escalation:
        if len(USER_MSG_TIMESTAMPS[user_id]) >= MSG_LIMIT_PER_MIN:
            raise HTTPException(status_code=429, detail="Rate limit exceeded: Too many messages per minute.")
        USER_MSG_TIMESTAMPS[user_id].append(now)
    else:
        if len(USER_ESC_TIMESTAMPS[user_id]) >= ESC_LIMIT_PER_MIN:
            raise HTTPException(status_code=429, detail="Escalation rate limit exceeded: Too many LLM escalations per minute.")
        USER_ESC_TIMESTAMPS[user_id].append(now)

@app.get("/", response_class=HTMLResponse)
def chat_ui():
    return """
    <!DOCTYPE html>
    <html>
    <head>
      <title>Déjà Fix Support</title>
      <style>
        body { font-family: system-ui, sans-serif; max-width: 680px; margin: 40px auto; padding: 0 16px; }
        .controls { display: flex; gap: 12px; margin-bottom: 12px; }
        .controls input { padding: 6px 10px; border: 1px solid #ccc; border-radius: 4px; }
        #chat { border: 1px solid #ddd; border-radius: 6px; height: 380px; overflow-y: auto; padding: 14px; background: #fafafa; margin-bottom: 12px; }
        .input-row { display: flex; gap: 8px; }
        #msg { flex: 1; padding: 10px; border: 1px solid #ccc; border-radius: 4px; }
        button { padding: 10px 18px; background: #1a73e8; color: white; border: none; border-radius: 4px; cursor: pointer; }
        button:hover { background: #1557b0; }
        .meta { font-size: 12px; color: #666; margin-top: 8px; }
      </style>
    </head>
    <body>
      <h2>Déjà Fix — Support Assistant</h2>
      <div class="controls">
        <label>User ID: <input id="uid" value="customer_A" /></label>
        <label>Session ID: <input id="sid" value="session_1" /></label>
      </div>
      <div id="chat"></div>
      <div class="input-row">
        <input id="msg" placeholder="Describe your issue..." onkeydown="if(event.key==='Enter') send()" />
        <button onclick="send()">Send</button>
      </div>
      <div class="meta">
        API Docs: <a href="/docs" target="_blank">/docs</a> |
        Knowledge Hub: <a href="/solutions" target="_blank">/solutions</a>
      </div>
      <script>
        async function send() {
          const input = document.getElementById('msg');
          const msg = input.value.trim();
          if (!msg) return;
          const chat = document.getElementById('chat');
          chat.innerHTML += `<p><strong>You:</strong> ${msg}</p>`;
          input.value = '';
          chat.scrollTop = chat.scrollHeight;

          const res = await fetch('/chat', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
              user_id: document.getElementById('uid').value,
              session_id: document.getElementById('sid').value,
              message: msg
            })
          });
          const data = await res.json();
          if (!res.ok) {
            chat.innerHTML += `<p style="color:red;"><strong>Error (${res.status}):</strong> ${data.detail}</p>`;
          } else {
            chat.innerHTML += `<p><strong>Déjà Fix [${data.source}]:</strong> ${data.reply}</p>`;
          }
          chat.scrollTop = chat.scrollHeight;
        }
      </script>
    </body>
    </html>
    """

@app.post("/chat")
def chat_endpoint(req: ChatRequest):
    enforce_rate_limit(req.user_id, is_escalation=False)
    ensure_user_and_session(req.user_id, req.session_id)

    save_message(req.session_id, req.user_id, "user", req.message)
    transcript = get_session_messages(req.session_id)
    user_history = get_user_past_sessions(req.user_id, req.session_id)

    try:
        confirmation_keywords = ["worked", "fixed", "solved", "working now", "that did it", "problem resolved"]
        if len(transcript) >= 2 and any(k in req.message.lower() for k in confirmation_keywords):
            extracted = check_and_extract_solutions(req.session_id)
            reply = "I'm glad that resolved your issue! Let me know if you need help with anything else."
            if extracted > 0:
                reply += " (Verified solution saved to Knowledge Hub.)"
            save_message(req.session_id, req.user_id, "assistant", reply)
            return {
                "reply": reply,
                "source": "confirmation_handler",
                "extracted_solutions": extracted,
                "tokens": get_session_token_summary(req.session_id)
            }

        combined_user_query = " ".join([m["content"] for m in transcript if m["role"] == "user"])
        matched_solution = search_knowledge_hub(req.session_id, combined_user_query)

        if matched_solution:
            reply, needs_escalation = persona_respond(req.session_id, req.message, matched_solution, user_history)
            if not needs_escalation:
                save_message(req.session_id, req.user_id, "assistant", reply)
                return {
                    "reply": reply,
                    "source": "knowledge_hub",
                    "solution_id": matched_solution["id"],
                    "tokens": get_session_token_summary(req.session_id)
                }

        enforce_rate_limit(req.user_id, is_escalation=True)

        greeting_prefix = ""
        if len(transcript) == 1 and user_history:
            first_past = next((m["content"] for m in user_history if m["role"] == "user"), "")
            greeting_prefix = f"Welcome back! Last time you reached out about: '{first_past[:60]}...'. Regarding your new issue: "

        esc_reply = escalate_to_backend_llm(req.session_id, transcript)
        full_reply = greeting_prefix + esc_reply
        save_message(req.session_id, req.user_id, "assistant", full_reply)

        return {
            "reply": full_reply,
            "source": "backend_escalation",
            "tokens": get_session_token_summary(req.session_id)
        }

    except HTTPException:
        raise
    except Exception as e:
        print(f"\n[DEBUG LLM ERROR]: {type(e).__name__}: {e}\n")
        fallback = "I'm temporarily unable to process your request due to a service interruption. Please try again shortly."
        save_message(req.session_id, req.user_id, "assistant", fallback)
        return {"reply": fallback, "source": "error_fallback"}

@app.get("/sessions/{session_id}")
def read_session(session_id: str):
    return {"session_id": session_id, "messages": get_session_messages(session_id)}

@app.get("/solutions")
def list_solutions():
    sols = get_all_solutions()
    for s in sols:
        s.pop("embedding", None)
    return {"solutions": sols}
