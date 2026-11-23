"""Optional live embedding smoke test. Requires `pip install -r requirements.txt`.

Run `python scripts/evaluate_retrieval.py` after the first MiniLM model download.
Cases and cosine scores print for manual calibration; no real customers involved.
"""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings
from app.models import Solution
from app.services.embeddings import EmbeddingService
from app.services.knowledge_service import KnowledgeService


def main():
    embedder = EmbeddingService(settings.embedding_model)
    kb = KnowledgeService(embedder)
    cases = [
        ("Google Drive sync is stuck on Windows 11", "My Google Drive files never finish uploading on Windows 11", True),
        ("The printer queue is stuck on Windows 11", "My printer's jobs sit in queue and never print on Windows 11", True),
        ("Google Drive sync is stuck on Windows 11", "The Google Drive desktop application crashes immediately on Windows 11", False),
        ("The printer queue is stuck on Windows 11", "The printer is out of ink on Windows 11", False),
        ("Google Drive sync failed because authentication token expired on Windows 11", "Google Drive sync failed because the disk is full on Windows 11", False),
        ("Google Drive sync is stuck on Windows 11", "Google Drive sync is stuck on macOS 15", False),
    ]
    passed = 0
    print(f"Model: {settings.embedding_model}, similarity threshold: {settings.similarity_threshold}")
    for problem, paraphrase, expected in cases:
        saved = Solution(
            problem=problem, symptoms=json.dumps([problem]),
            environment=json.dumps({"os": "Windows 11", "app": "printer" if "printer" in problem.lower() else "Google Drive"}),
            fix_steps='["Example verified fix"]', embedding=json.dumps(embedder.embed(problem)), verified=True,
        )
        found, cosine = kb.find_match(paraphrase, [saved])
        good = (found is not None) == expected
        passed += good
        print(f"{'PASS' if good else 'FAIL'} | {'should match' if expected else 'should reject'} | score={cosine:.3f} | {paraphrase}")
    print(f"{passed}/{len(cases)} cases passed")
    if passed != len(cases):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
