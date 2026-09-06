"""
analyze_reference_quality.py

Quality-checks every reference image listed in refs.csv before it's trusted
as ground truth for face matching. Does NOT silently accept a bad photo.

Checks per image:
  - file opens without error / not corrupted
  - width, height
  - file type
  - face count (via InsightFace, same model as index_photos.py)
  - exactly one face vs zero vs multiple
  - face size relative to image (too-small face = low quality match source)
  - blur (variance of Laplacian, via OpenCV)
  - duplicate images (exact-content hash across the whole reference set)

Output:
  output/reference_quality.csv
    columns: name,image,profile_url,source_url,width,height,faces,status,reason

Status values:
  OK      - single clear face, decent size, not blurry, not a duplicate
  REVIEW  - usable but flagged (e.g. small face, borderline blur, multiple
            faces present) -- a human should look at it before trusting it
  BAD     - unusable (corrupt, zero faces, or clearly wrong)

Usage:
    python analyze_reference_quality.py
"""

import csv
import hashlib
from pathlib import Path

import cv2
from PIL import Image

REFS_CSV = Path("refs.csv")
OUTPUT_DIR = Path("output")
QUALITY_CSV = OUTPUT_DIR / "reference_quality.csv"

MIN_FACE_RATIO = 0.08   # face bounding box width / image width, below this -> REVIEW
BLUR_THRESHOLD = 80.0   # Laplacian variance below this -> REVIEW (tune per your photos)

_face_app = None


def get_face_app():
    """Lazy-load InsightFace so this script also works standalone for
    file-integrity checks even if the model isn't downloaded yet."""
    global _face_app
    if _face_app is None:
        from insightface.app import FaceAnalysis

        _face_app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
        _face_app.prepare(ctx_id=0, det_size=(640, 640))
    return _face_app


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def check_image(row: dict, seen_hashes: dict) -> dict:
    image_path = Path(row["image"])
    result = {
        "name": row.get("name", ""),
        "image": row.get("image", ""),
        "profile_url": row.get("profile_url", ""),
        "source_url": row.get("source_url", ""),
        "width": "",
        "height": "",
        "faces": "",
        "status": "BAD",
        "reason": "",
    }

    if not image_path.exists():
        result["reason"] = "file not found"
        return result

    # 1. Open / corruption check
    try:
        with Image.open(image_path) as im:
            im.verify()
        with Image.open(image_path) as im:
            width, height = im.size
    except Exception as e:
        result["reason"] = f"corrupt or unreadable image: {e}"
        return result

    result["width"] = width
    result["height"] = height

    if width < 80 or height < 80:
        result["status"] = "BAD"
        result["reason"] = f"image too small ({width}x{height})"
        return result

    # 2. Duplicate check (exact-content hash; catches re-used placeholder
    #    photos across different faculty entries)
    h = file_hash(image_path)
    if h in seen_hashes:
        result["status"] = "REVIEW"
        result["reason"] = f"duplicate of {seen_hashes[h]}"
        return result
    seen_hashes[h] = str(image_path)

    # 3. Face detection
    img_bgr = cv2.imread(str(image_path))
    if img_bgr is None:
        result["reason"] = "OpenCV could not read image (corrupt or unsupported format)"
        return result

    app = get_face_app()
    faces = app.get(img_bgr)
    result["faces"] = len(faces)

    if len(faces) == 0:
        result["status"] = "BAD"
        result["reason"] = "no face detected"
        return result

    if len(faces) > 1:
        result["status"] = "REVIEW"
        result["reason"] = f"{len(faces)} faces detected -- confirm which is the faculty member"
        return result

    # exactly one face: check size and blur
    face = faces[0]
    x1, y1, x2, y2 = face.bbox
    face_w = x2 - x1
    face_ratio = face_w / width

    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    blur_score = cv2.Laplacian(gray, cv2.CV_64F).var()

    reasons = []
    status = "OK"

    if face_ratio < MIN_FACE_RATIO:
        status = "REVIEW"
        reasons.append(f"face small relative to image ({face_ratio:.2%})")

    if blur_score < BLUR_THRESHOLD:
        status = "REVIEW"
        reasons.append(f"possibly blurry (score={blur_score:.1f})")

    result["status"] = status
    result["reason"] = "; ".join(reasons) if reasons else "ok"
    return result


def main():
    if not REFS_CSV.exists():
        print(f"ERROR: {REFS_CSV} not found. Run fetch_hetc_refs.py first.")
        return

    with open(REFS_CSV, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    print(f"Checking {len(rows)} reference images...")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    seen_hashes = {}
    results = []

    for i, row in enumerate(rows, 1):
        r = check_image(row, seen_hashes)
        results.append(r)
        print(f"  [{i}/{len(rows)}] {r['name']:30s} {r['status']:7s} {r['reason']}")

    with open(QUALITY_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "name",
                "image",
                "profile_url",
                "source_url",
                "width",
                "height",
                "faces",
                "status",
                "reason",
            ],
        )
        writer.writeheader()
        writer.writerows(results)

    ok = sum(1 for r in results if r["status"] == "OK")
    review = sum(1 for r in results if r["status"] == "REVIEW")
    bad = sum(1 for r in results if r["status"] == "BAD")

    print(f"\nDone. OK={ok}  REVIEW={review}  BAD={bad}")
    print(f"Wrote {QUALITY_CSV}")
    print("\nManually check REVIEW and BAD entries before building embeddings.")


if __name__ == "__main__":
    main()