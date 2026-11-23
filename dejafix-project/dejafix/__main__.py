"""`python -m dejafix` starts the server (HOST / PORT env vars, default 127.0.0.1:8000)."""
import os

import uvicorn

from .config import load_dotenv

if __name__ == "__main__":
    load_dotenv()
    uvicorn.run("dejafix.api:create_app", factory=True, host=os.environ.get("HOST", "127.0.0.1"),
                port=int(os.environ.get("PORT", "8000")))
