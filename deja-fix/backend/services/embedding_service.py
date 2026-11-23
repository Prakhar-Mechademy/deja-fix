"""Embedding generation with a deterministic local fallback for offline operation."""
import hashlib, re
from services.gemini_service import embed_text as gemini_embed, ProviderError
from config import EMBEDDING_MODEL

def embed_text(text: str, db=None, session_id: str | None = None) -> list[float] | None:
    try:
        vector, input_tokens = gemini_embed(text)
        if db is not None and session_id is not None:
            from utils.token_tracker import record_usage
            record_usage(db, session_id, {"model": EMBEDDING_MODEL, "input_tokens": input_tokens,
                                          "output_tokens": 0}, "embedding")
        return vector
    except ProviderError:
        return None

def lexical_vector(text: str, dimensions: int = 256) -> list[float]:
    """Stable hashed-token vector; fallback is intentionally conservative."""
    import math
    vector = [0.0] * dimensions
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    for token in tokens:
        index = int(hashlib.sha256(token.encode()).hexdigest()[:8], 16) % dimensions
        vector[index] += 1.0
    norm = math.sqrt(sum(x*x for x in vector)) or 1.0
    return [x / norm for x in vector]
