import os
import chatbot
import database

if os.path.exists(database.DB_PATH):
    os.remove(database.DB_PATH)
database.init_db()

print("=== SESSION 1: Escalation & Extraction (Customer A) ===")
s1 = chatbot.start_session("customer_a", "session_1")
print("Bot:", s1["greeting"])
r1 = chatbot.handle_message(
    "session_1",
    "customer_a",
    "Hi, I'm Alice (alice@test.com, order #9821). Cloud sync fails with Error 503 on MacBook Pro macOS 15.1 app v4.2. Restarting failed.",
)
print(f"Bot [{r1['route']}]:", r1["reply"])
r2 = chatbot.handle_message("session_1", "customer_a", "That worked! Clearing the sync cache fixed it.")
print(f"Bot [{r2['route']}]:", r2["reply"])
print("Saved Solutions in DB:", database.get_all_solutions())

print("\n=== SESSION 2: Reuse from Hub (Customer B - Reworded) ===")
s2 = chatbot.start_session("customer_b", "session_2")
print("Bot:", s2["greeting"])
b1 = chatbot.handle_message("session_2", "customer_b", "My files won't upload to the cloud, getting a 503 sync failure.")
print(f"Bot [{b1['route']}]:", b1["reply"])
if b1["route"] == "clarify":
    b2 = chatbot.handle_message("session_2", "customer_b", "MacBook Pro, macOS 15.1, app v4.2.")
    print(f"Bot [{b2['route']}]:", b2["reply"])
b3 = chatbot.handle_message("session_2", "customer_b", "Awesome, that fixed it!")
print(f"Bot [{b3['route']}]:", b3["reply"])

print("\n--- SESSION 3: Memory Isolation (Customer A Returns) ---")
s3 = chatbot.start_session("customer_a", "session_3")
print("Bot:", s3["greeting"])

print("\n=== TOKEN USAGE COMPARISON ===")
print("Session 1 (Escalated):", database.get_token_totals("session_1"))
print("Session 2 (Hub Reuse):", database.get_token_totals("session_2"))