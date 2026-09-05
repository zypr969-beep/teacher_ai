from pathlib import Path
import json
import shutil
import cv2
import numpy as np
from tqdm import tqdm
from insightface.app import FaceAnalysis
from sklearn.cluster import DBSCAN

PHOTO_DIR = Path("photos")
GROUP_DIR = Path("output/groups")
DATA_FILE = Path("output/photo_faces.json")
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
DBSCAN_EPS = 0.28


def normalize(v):
    v = np.asarray(v, dtype=np.float32)
    n = np.linalg.norm(v)
    return v if n == 0 else v / n


print("Loading face model...")
app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
app.prepare(ctx_id=0, det_size=(640, 640))
images = [p for p in PHOTO_DIR.rglob("*") if p.suffix.lower() in IMAGE_EXTENSIONS]
print(f"Found {len(images)} images")
records = []

for path in tqdm(images, desc="Detecting faces"):
    img = cv2.imread(str(path))
    if img is None:
        continue
    for face_index, face in enumerate(app.get(img)):
        records.append({
            "image": str(path),
            "face_index": face_index,
            "embedding": normalize(face.embedding).tolist(),
            "bbox": face.bbox.tolist()
        })

print(f"Detected {len(records)} faces")
if not records:
    raise SystemExit("No faces found.")

X = np.array([r["embedding"] for r in records], dtype=np.float32)
labels = DBSCAN(eps=DBSCAN_EPS, min_samples=1, metric="cosine").fit_predict(X)
for record, label in zip(records, labels):
    record["cluster"] = int(label)

DATA_FILE.parent.mkdir(exist_ok=True)
with open(DATA_FILE, "w", encoding="utf-8") as f:
    json.dump(records, f, indent=2)

GROUP_DIR.mkdir(parents=True, exist_ok=True)
groups = {}
for record in records:
    groups.setdefault(record["cluster"], []).append(record)

for cluster, items in groups.items():
    group_dir = GROUP_DIR / f"person_{cluster:03d}"
    group_dir.mkdir(exist_ok=True)
    copied = set()
    for item in items:
        source = Path(item["image"])
        if source in copied:
            continue
        copied.add(source)
        destination = group_dir / source.name
        if not destination.exists():
            shutil.copy2(source, destination)

print(f"Created {len(groups)} face groups")
print(f"Results saved to {DATA_FILE}")
