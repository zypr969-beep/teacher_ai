from pathlib import Path
import json
import cv2
import numpy as np
import pandas as pd
from insightface.app import FaceAnalysis

REF_CSV = "refs.csv"
OUTPUT = "output/reference_embeddings.json"


def normalize(v):
    v = np.asarray(v, dtype=np.float32)
    n = np.linalg.norm(v)
    return v if n == 0 else v / n


def largest_face(faces):
    return max(faces, key=lambda f: (f.bbox[2]-f.bbox[0])*(f.bbox[3]-f.bbox[1])) if faces else None


print("Loading face model...")
app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
app.prepare(ctx_id=0, det_size=(640, 640))

df = pd.read_csv(REF_CSV)
database = {}

for _, row in df.iterrows():
    name = str(row["name"]).strip()
    image_path = str(row["image"]).strip()
    print(f"Processing: {name}")
    img = cv2.imread(image_path)
    if img is None:
        print(f"  ERROR: Could not read {image_path}")
        continue
    face = largest_face(app.get(img))
    if face is None:
        print("  ERROR: No face found")
        continue
    database[name] = {"image": image_path, "embedding": normalize(face.embedding).tolist()}
    print("  OK")

Path("output").mkdir(exist_ok=True)
with open(OUTPUT, "w", encoding="utf-8") as f:
    json.dump(database, f, indent=2)
print(f"Saved {len(database)} people to {OUTPUT}")
