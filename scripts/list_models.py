"""List Gemini models available to the configured API key."""

from google import genai
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import GEMINI_API_KEY


if not GEMINI_API_KEY:
    print("No GEMINI_API_KEY configured. Set it in the environment or .env file.")
else:
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        for model in client.models.list():
            print(f"Name: {model.name}")
            actions = getattr(model, "supported_actions", None)
            if actions:
                print(f"Supported actions: {', '.join(actions)}")
            else:
                print("Supported actions: unavailable")
    except Exception as exc:
        print(f"Error listing models: {type(exc).__name__}: {exc}")
