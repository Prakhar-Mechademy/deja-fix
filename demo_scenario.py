from dotenv import load_dotenv; load_dotenv(override=True)
import os
import time
import subprocess
import requests

DB_FILE = "dejafix.db"
BASE_URL = "http://127.0.0.1:8000"

def reset_db():
    if os.path.exists(DB_FILE):
        os.remove(DB_FILE)
        print("[Setup] Removed existing database to start completely empty.")

def wait_for_server():
    for _ in range(20):
        try:
            r = requests.get(f"{BASE_URL}/solutions", timeout=1)
            if r.status_code == 200:
                return
        except Exception:
            time.sleep(0.5)
    raise RuntimeError("Server failed to start.")

def send_chat(user_id: str, session_id: str, message: str):
    print(f"\n[{user_id} | {session_id}] USER: {message}")
    res = requests.post(f"{BASE_URL}/chat", json={
        "user_id": user_id,
        "session_id": session_id,
        "message": message
    })
    data = res.json()
    print(f"[{user_id} | {session_id}] BOT ({data.get('source')}): {data.get('reply')}")
    return data

def main():
    if not os.environ.get("GEMINI_API_KEY"):
        print("ERROR: Please set GEMINI_API_KEY in your terminal first.")
        return

    reset_db()
    server = subprocess.Popen(["uvicorn", "app:app", "--host", "127.0.0.1", "--port", "8000"])
    try:
        wait_for_server()

        print("\n=== SESSION 1: Escalation & Extraction (Customer A) ===")
        send_chat(
            "customer_A",
            "session_1",
            "Hi, I'm Rahul Sharma (rahul@example.com, Order #99812). My CloudSync app v4.2 on Windows 11 keeps freezing on 'Syncing files' even after I reinstalled it."
        )
        s1_final = send_chat(
            "customer_A",
            "session_1",
            "Clearing the local cache folder worked! Everything is syncing smoothly now, thanks."
        )

        sols = requests.get(f"{BASE_URL}/solutions").json()["solutions"]
        print("\n[DB Check - Generalized Solution Saved]:", sols)

        print("\n=== SESSION 2: Reuse & Lower Token Cost (Customer B) ===")
        send_chat(
            "customer_B",
            "session_2",
            "My desktop sync client is stuck and won't finish uploading my folders."
        )
        s2_final = send_chat(
            "customer_B",
            "session_2",
            "I'm on Windows 11 using CloudSync v4.2."
        )

        print("\n=== TOKEN COMPARISON ===")
        print(f"Session 1 (Escalated) Tokens: {s1_final.get('tokens')}")
        print(f"Session 2 (Hub Reuse) Tokens: {s2_final.get('tokens')}")

        print("\n=== SESSION 3: Memory Isolation (Customer A Returns) ===")
        send_chat(
            "customer_A",
            "session_3",
            "Hey, I'm back! Now my mobile notifications aren't showing up on iOS 18."
        )

    finally:
        server.terminate()
        server.wait()
        print("\n[Done] Demo completed.")

if __name__ == "__main__":
    main()
