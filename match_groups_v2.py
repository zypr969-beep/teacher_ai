from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

REFERENCE_FILE = Path("output/reference_bank.json")
FACE_FILE = Path("output/photo_faces.json")
OUTPUT_FILE = Path("output/results_v2.csv")
TOP_N = 5
MIN_SCORE = 0.45
MIN_MARGIN = 0.04


def normalize(vector: np.ndarray) -> np.ndarray:
    vector = np.asarray(vector, dtype=np.float32)
    norm = np.linalg.norm(vector)
    return vector if norm == 0 else vector / norm


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(normalize(a), normalize(b)))


def load() -> tuple[dict, list[dict]]:
    if not REFERENCE_FILE.exists():
        raise SystemExit("output/reference_bank.json not found. Run build_reference_bank.py first.")
    if not FACE_FILE.exists():
        raise SystemExit("output/photo_faces.json not found. Run index_photos.py first.")
    references = json.loads(REFERENCE_FILE.read_text(encoding="utf-8"))
    records = json.loads(FACE_FILE.read_text(encoding="utf-8"))
    return references, records


def main() -> None:
    references, records = load()
    teacher_vectors = {
        name: [normalize(np.asarray(ref["embedding"], dtype=np.float32)) for ref in data["references"]]
        for name, data in references.get("teachers", {}).items()
    }
    groups: dict[int, list[np.ndarray]] = {}
    for record in records:
        groups.setdefault(int(record["cluster"]), []).append(np.asarray(record["embedding"], dtype=np.float32))

    rows: list[dict] = []
    for cluster, embeddings in sorted(groups.items()):
        centroid = normalize(np.mean(np.vstack(embeddings), axis=0))
        scored: list[tuple[str, float, float]] = []
        for name, refs in teacher_vectors.items():
            scores = [cosine(centroid, ref) for ref in refs]
            best = max(scores)
            mean = float(np.mean(scores))
            # Best reference drives ranking; mean provides context when multiple official photos exist.
            combined = 0.7 * best + 0.3 * mean
            scored.append((name, combined, best))
        scored.sort(key=lambda item: item[1], reverse=True)

        row = {"group": f"person_{cluster:03d}", "face_count": len(embeddings)}
        if not scored:
            row.update({"status": "UNMATCHED", "best_score": "", "margin": ""})
        else:
            best_name, best_score, best_ref = scored[0]
            second_score = scored[1][1] if len(scored) > 1 else 0.0
            margin = best_score - second_score
            status = "REVIEW"
            if best_score >= MIN_SCORE and margin >= MIN_MARGIN:
                status = "CANDIDATE"
            row.update({
                "status": status,
                "best_candidate": best_name,
                "best_score": round(best_score, 4),
                "best_reference_score": round(best_ref, 4),
                "margin": round(margin, 4),
            })
            for index, (name, score, _) in enumerate(scored[:TOP_N], 1):
                row[f"candidate_{index}"] = name
                row[f"score_{index}"] = round(score, 4)
        rows.append(row)

    df = pd.DataFrame(rows)
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUTPUT_FILE, index=False)
    print(df.to_string(index=False))
    print(f"\nSaved to {OUTPUT_FILE}")
    print("CANDIDATE means the score and score margin cleared configurable thresholds; human confirmation is still required.")


if __name__ == "__main__":
    main()
