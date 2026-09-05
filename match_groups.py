from pathlib import Path
import json
import numpy as np
import pandas as pd

REFERENCE_FILE = Path("output/reference_embeddings.json")
FACE_FILE = Path("output/photo_faces.json")
OUTPUT_FILE = Path("output/results.csv")
TOP_N = 3


def normalize(v):
    v = np.asarray(v, dtype=np.float32)
    n = np.linalg.norm(v)
    return v if n == 0 else v / n


def similarity(a, b):
    return float(np.dot(normalize(a), normalize(b)))

with open(REFERENCE_FILE, "r", encoding="utf-8") as f:
    references = json.load(f)
with open(FACE_FILE, "r", encoding="utf-8") as f:
    records = json.load(f)

reference_vectors = {name: normalize(data["embedding"]) for name, data in references.items()}
groups = {}
for record in records:
    groups.setdefault(record["cluster"], []).append(np.asarray(record["embedding"], dtype=np.float32))

rows = []
for cluster, embeddings in groups.items():
    centroid = normalize(np.mean(np.vstack(embeddings), axis=0))
    candidates = sorted(((name, similarity(centroid, ref)) for name, ref in reference_vectors.items()), key=lambda x: x[1], reverse=True)[:TOP_N]
    row = {"group": f"person_{int(cluster):03d}"}
    for i, (name, score) in enumerate(candidates, 1):
        row[f"candidate_{i}"] = name
        row[f"score_{i}"] = round(score, 4)
    rows.append(row)

df = pd.DataFrame(rows)
df.to_csv(OUTPUT_FILE, index=False)
print(df.to_string(index=False))
print(f"Saved to {OUTPUT_FILE}")
