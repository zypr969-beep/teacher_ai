from __future__ import annotations

import csv
import json
import re
from pathlib import Path

import cv2
import numpy as np
from insightface.app import FaceAnalysis

REF_CSV = Path("refs.csv")
OUT_FILE = Path("output/reference_bank.json")
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def normalize(vector: np.ndarray) -> np.ndarray:
    vector = np.asarray(vector, dtype=np.float32)
    norm = np.linalg.norm(vector)
    return vector if norm == 0 else vector / norm


def slugify(text: str) -> str:
    text = (text or "").strip().lower()
    text = re.sub(r"[^a-z0-9\-]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text or "unassigned"


def load_rows() -> list[dict[str, str]]:
    if not REF_CSV.exists():
        raise SystemExit("refs.csv not found. Build the official reference set first.")
    with REF_CSV.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def main() -> None:
    rows = load_rows()
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    print("Loading InsightFace...")
    app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
    app.prepare(ctx_id=0, det_size=(640, 640))

    teachers: dict[str, dict] = {}
    rejected: list[dict] = []

    for row in rows:
        name = (row.get("name") or "").strip()
        image_value = (row.get("image") or "").strip()
        source_url = (row.get("source_url") or "").strip()
        department = (row.get("department") or "").strip()
        department_slug = slugify(department)

        if not name or not image_value:
            rejected.append({**row, "reason": "missing name or image"})
            continue

        image_path = Path(image_value)
        if image_path.suffix.lower() not in IMAGE_EXTENSIONS:
            rejected.append({**row, "reason": "unsupported image extension"})
            continue

        image = cv2.imread(str(image_path))
        if image is None:
            rejected.append({**row, "reason": "image could not be read"})
            continue

        faces = app.get(image)
        if len(faces) != 1:
            rejected.append({
                **row,
                "reason": f"expected exactly 1 face, found {len(faces)}",
            })
            continue

        face = faces[0]
        embedding = normalize(face.embedding)
        record = {
            "image": image_path.as_posix(),
            "source_url": source_url,
            "department": department,
            "department_slug": department_slug,
            "embedding": embedding.tolist(),
            "bbox": [float(v) for v in face.bbox.tolist()],
            "face_det_score": float(face.det_score),
        }
        teacher = teachers.setdefault(
            name, {"references": [], "department": department, "department_slug": department_slug}
        )
        teacher["references"].append(record)
        # If a teacher somehow has references tagged with different
        # departments, keep the first one seen but don't silently hide it.
        if teacher["department"] and department and teacher["department"] != department:
            teacher.setdefault("department_conflict", []).append(department)

    for teacher in teachers.values():
        teacher["count"] = len(teacher["references"])

    payload = {
        "model": "insightface/buffalo_l",
        "embedding_metric": "cosine",
        "teachers": teachers,
        "rejected": rejected,
    }
    OUT_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    accepted_refs = sum(t["count"] for t in teachers.values())
    print(f"Teachers: {len(teachers)}")
    print(f"Accepted reference images: {accepted_refs}")
    print(f"Rejected reference images: {len(rejected)}")
    print(f"Saved: {OUT_FILE}")


if __name__ == "__main__":
    main()
