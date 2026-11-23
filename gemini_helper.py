from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
import json, os, sys, time
from google import genai
from google.genai import types
import database

MODELS = ["gemini-3.1-flash-lite", "gemini-3.5-flash-lite"]
_CLIENT = None

def _get_client():
    global _CLIENT
    if _CLIENT is None:
        if not os.environ.get("GEMINI_API_KEY") and os.path.exists(".env"):
            for line in open(".env", encoding="utf-8"):
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.strip().split("=", 1)
                    os.environ[k.strip()] = v.strip().strip('"').strip("'")
        _CLIENT = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    return _CLIENT

def _generate(contents: str, config=None, timeout_sec: int = 20):
    client = _get_client()
    last_err = None
    for model in MODELS:
        for attempt in range(3):
            pool = ThreadPoolExecutor(max_workers=1)
            fut = pool.submit(lambda m=model: client.models.generate_content(model=m, contents=contents, config=config))
            try:
                return fut.result(timeout=timeout_sec)
            except FuturesTimeout:
                last_err = TimeoutError("Timed out")
                break
            except Exception as e:
                last_err = e
                if any(x in str(e) for x in ("503", "429", "UNAVAILABLE", "RESOURCE_EXHAUSTED")):
                    time.sleep(2.0 * (attempt + 1))
                    continue
                break
            finally:
                pool.shutdown(wait=False)
    print(f"[API Error] {last_err}", file=sys.stderr)
    raise last_err

def ask_text(session_id: str, step: str, prompt: str) -> str:
    res = _generate(prompt)
    u = getattr(res, "usage_metadata", None)
    database.log_tokens(session_id, step, getattr(u, "prompt_token_count", None) or len(prompt)//4, getattr(u, "candidates_token_count", None) or len(res.text or "")//4)
    return res.text.strip()

def ask_json(session_id: str, step: str, prompt: str) -> dict:
    res = _generate(prompt, types.GenerateContentConfig(response_mime_type="application/json", temperature=0.0))
    u = getattr(res, "usage_metadata", None)
    database.log_tokens(session_id, step, getattr(u, "prompt_token_count", None) or len(prompt)//4, getattr(u, "candidates_token_count", None) or len(res.text or "")//4)
    text = (res.text or "").strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(text)
