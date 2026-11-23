"""Smoke test Gemini text generation and embeddings."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import db
from app.llm import embed, generate


try:
    db.init_db()
    result = generate(
        None,
        None,
        "smoke",
        [{"role": "user", "text": "Reply with the word OK"}],
    )
    print(f"Response text: {result.text if result.ok else result.user_message}")
    print(f"Generate ok: {result.ok}")
    print(f"Usage metadata: prompt={result.tokens_in}, candidates_and_thoughts={result.tokens_out}")

    vectors = embed(["hello"], "RETRIEVAL_DOCUMENT", None, None)
    print(f"Embedding vector length: {len(vectors[0]) if vectors else 0}")
except Exception as exc:
    print(f"Smoke test error: {type(exc).__name__}: {exc}")
