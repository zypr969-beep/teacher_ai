from __future__ import annotations

import csv
from pathlib import Path

import cv2
from insightface.app import FaceAnalysis

REF_CSV = Path("refs.csv")
REPORT = Path("output/reference_quality.csv")


def main() -> None:
    if not REF_CSV.exists():
        raise SystemExit("refs.csv not found. Run fetch_hetc_browser.py first.")

    app = FaceAnalysis(
        name="buffalo_l",
        providers=["CPUExecutionProvider"],
    )
    app.prepare(ctx_id=0, det_size=(640, 640))

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    rows = []

    with REF_CSV.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = (row.get("name") or "").strip()
            image_path = Path((row.get("image") or "").strip())
            img = cv2.imread(str(image_path))

            status = "OK"
            reason = ""
            width = height = 0
            faces = 0

            if img is None:
                status = "BAD"
                reason = "image could not be read"
            else:
                height, width = img.shape[:2]
                detected = app.get(img)
                faces = len(detected)

                if faces == 0:
                    status = "BAD"
                    reason = "no face detected"
                elif faces > 1:
                    status = "REVIEW"
                    reason = "multiple faces detected"
                elif min(width, height) < 200:
                    status = "REVIEW"
                    reason = "image is small"

            rows.append({
                "name": name,
                "image": image_path.as_posix(),
                "width": width,
                "height": height,
                "faces": faces,
                "status": status,
                "reason": reason,
            })

    with REPORT.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["name", "image", "width", "height", "faces", "status", "reason"],
        )
        writer.writeheader()
        writer.writerows(rows)

    ok = sum(r["status"] == "OK" for r in rows)
    review = sum(r["status"] == "REVIEW" for r in rows)
    bad = sum(r["status"] == "BAD" for r in rows)

    print(f"Checked {len(rows)} reference images")
    print(f"OK: {ok} | REVIEW: {review} | BAD: {bad}")
    print(f"Report: {REPORT}")


if __name__ == "__main__":
    main()
