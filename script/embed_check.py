import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import math
from app import db, llm


def cos(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


texts = {
    "A": "My app won't sync my data on my iPhone",
    "B_reworded": "Data isn't updating between my phone and laptop",
    "C_different_fault": "My app won't open on my iPhone after the update",
}

db.init_db()
vecs = {k: llm.embed(v, session_id=None, user_id=None) for k, v in texts.items()}
print("vector length:", len(vecs["A"]))
print("same problem, reworded :", round(cos(vecs["A"], vecs["B_reworded"]), 3))
print("different fault        :", round(cos(vecs["A"], vecs["C_different_fault"]), 3))