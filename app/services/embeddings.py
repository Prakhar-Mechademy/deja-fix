import logging
import time

import numpy as np
from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

class EmbeddingService:
    def __init__(self, model_name: str):
        logger.info("embeddings.model_loading model=%s", model_name)
        started = time.perf_counter()
        self.model = SentenceTransformer(model_name)
        logger.info("embeddings.model_loaded model=%s elapsed_ms=%.1f", model_name, (time.perf_counter() - started) * 1000)

    def embed(self, text: str) -> list[float]:
        started = time.perf_counter()
        vector = self.model.encode(text, normalize_embeddings=True)
        result = vector.astype(float).tolist()
        logger.debug("embeddings.vector_created input_chars=%d dimensions=%d elapsed_ms=%.1f", len(text or ""), len(result), (time.perf_counter() - started) * 1000)
        return result

    @staticmethod
    def cosine_similarity(a: list[float], b: list[float]) -> float:
        a_vec = np.asarray(a, dtype=np.float32)
        b_vec = np.asarray(b, dtype=np.float32)
        denom = np.linalg.norm(a_vec) * np.linalg.norm(b_vec)
        if denom == 0:
            return 0.0
        return float(np.dot(a_vec, b_vec) / denom)
