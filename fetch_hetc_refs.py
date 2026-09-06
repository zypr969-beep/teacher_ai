"""
fetch_hetc_refs.py  (v3)

v2 fixes (after the first real run):
  - Some `image` values are relative paths (e.g.
    "/assets/media/image/user/<uuid>/profile/profile_....png") instead of
    full URLs. These get resolved against a couple of candidate base hosts.
  - The site's API/CDN rate-limits (HTTP 429) under back-to-back requests.
    Added retry-with-backoff (honoring Retry-After when present) and a
    slower per-request pace.
  - Resumable: if a reference image already exists on disk, it's not
    re-downloaded, so re-running after a partial failure is cheap and fast.

v3 change:
  - Photos are now saved into per-department subfolders:
      references/hetc/<department-slug>/<person-slug>.<ext>
    instead of one flat references/hetc/ folder. Any files already
    downloaded by v2 into the flat folder are moved (not re-downloaded)
    into their department folder automatically on first run.

Usage (PowerShell, from D:\\TeacherFaceAI\\teacher_ai with venv active):
    python fetch_hetc_refs.py
"""

import csv
import re
import time
from pathlib import Path
from urllib.parse import urlparse, urljoin

import requests

BASE = "https://login.hetc.ac.in"
INDEX_URL = f"{BASE}/api/public/faculty-preview-order"
DEPT_URL_TMPL = f"{BASE}/api/public/faculty-preview-order/{{uuid}}?status=active"

# Some `image` fields come back as paths relative to a host, not full URLs.
# Try these bases in order until one actually serves the file.
RELATIVE_IMAGE_BASE_CANDIDATES = [
    "https://login.hetc.ac.in",
    "https://www.hetc.ac.in",
    "https://hetc.ac.in",
]

PROFILE_URL_TMPL = "https://www.hetc.ac.in/faculty/{slug}/"

REFS_DIR = Path("references/hetc")
REFS_CSV = Path("refs.csv")

REQUEST_TIMEOUT = 20
DOWNLOAD_DELAY_SEC = 1.2     # pace between successful downloads
API_DELAY_SEC = 1.0          # pace between department API calls
MAX_RETRIES = 5
BACKOFF_BASE_SEC = 3.0       # doubles each retry: 3, 6, 12, 24, 48

SESSION = requests.Session()
SESSION.headers.update(
    {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) TeacherFaceAI-RefCollector/1.1",
        "Accept": "application/json",
    }
)


def safe_slug(text: str) -> str:
    text = (text or "").strip().lower()
    text = re.sub(r"[^a-z0-9\-]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text or "unknown"


def request_with_backoff(method: str, url: str, **kwargs) -> requests.Response:
    """GET/HEAD with retry on 429/5xx, honoring Retry-After when present."""
    last_exc = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = SESSION.request(method, url, timeout=REQUEST_TIMEOUT, **kwargs)
            if resp.status_code == 429 or resp.status_code >= 500:
                retry_after = resp.headers.get("Retry-After")
                if retry_after is not None:
                    try:
                        wait = float(retry_after)
                    except ValueError:
                        wait = BACKOFF_BASE_SEC * (2 ** attempt)
                else:
                    wait = BACKOFF_BASE_SEC * (2 ** attempt)
                print(f"    [{resp.status_code}] rate-limited/server error, "
                      f"waiting {wait:.0f}s (attempt {attempt + 1}/{MAX_RETRIES})...")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp
        except requests.RequestException as e:
            last_exc = e
            wait = BACKOFF_BASE_SEC * (2 ** attempt)
            print(f"    request error: {e} -- retrying in {wait:.0f}s "
                  f"(attempt {attempt + 1}/{MAX_RETRIES})")
            time.sleep(wait)
    raise RuntimeError(f"Giving up on {url} after {MAX_RETRIES} attempts") from last_exc


def get_json(url: str) -> dict:
    resp = request_with_backoff("GET", url)
    return resp.json()


def fetch_departments() -> list[dict]:
    data = get_json(INDEX_URL)
    rows = data.get("data") or []
    depts = []
    for row in rows:
        dept = row.get("department") or {}
        order = row.get("order") or {}
        if not dept.get("uuid"):
            continue
        if str(order.get("active", 1)) != "1":
            continue
        count = order.get("faculty_count") or 0
        if count <= 0:
            continue
        depts.append(dept)
    return depts


def fetch_department_faculty(dept: dict) -> list[dict]:
    url = DEPT_URL_TMPL.format(uuid=dept["uuid"])
    data = get_json(url)
    return data.get("assigned") or []


def resolve_image_url(image_url: str) -> str:
    """Full URLs pass through untouched. Relative paths get tried against
    each candidate base until one responds successfully (HEAD request)."""
    if not image_url:
        return ""
    parsed = urlparse(image_url)
    if parsed.scheme in ("http", "https"):
        return image_url

    for base in RELATIVE_IMAGE_BASE_CANDIDATES:
        candidate = urljoin(base, image_url)
        try:
            resp = SESSION.head(candidate, timeout=REQUEST_TIMEOUT, allow_redirects=True)
            if resp.status_code == 200:
                return candidate
        except requests.RequestException:
            continue
    # Nothing confirmed -- return the first guess anyway so the caller gets
    # a clear download error rather than silently skipping.
    return urljoin(RELATIVE_IMAGE_BASE_CANDIDATES[0], image_url)


def download_image(url: str, dest_path: Path) -> tuple[bool, str]:
    try:
        resp = request_with_backoff("GET", url, stream=True)
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        with open(dest_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        return True, ""
    except Exception as e:
        return False, str(e)


def guess_extension(url: str) -> str:
    path = urlparse(url).path
    ext = Path(path).suffix.lower()
    if ext in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        return ext
    return ".jpg"


def main():
    print("Fetching department index...")
    departments = fetch_departments()
    print(f"  {len(departments)} active departments found.")

    all_rows = []
    seen_ids = set()
    ok_count = 0
    fail_count = 0
    skip_count = 0

    for dept in departments:
        print(f"\nDepartment: {dept['title']} ({dept['slug']})")
        try:
            faculty = fetch_department_faculty(dept)
        except Exception as e:
            print(f"  ERROR fetching department faculty list: {e}")
            continue
        time.sleep(API_DELAY_SEC)

        print(f"  {len(faculty)} faculty entries.")

        for person in faculty:
            fid = person.get("id")
            if fid in seen_ids:
                continue
            seen_ids.add(fid)

            name = (person.get("name") or "").strip()
            slug = safe_slug(person.get("slug") or name)
            raw_image_url = person.get("image") or ""
            role = person.get("role") or ""
            email = person.get("email") or ""

            if not raw_image_url:
                print(f"  SKIP (no image): {name}")
                fail_count += 1
                continue

            ext = guess_extension(raw_image_url)
            local_path = REFS_DIR / dept["slug"] / f"{slug}{ext}"

            # Migrate a file downloaded by the older flat-folder version
            # instead of re-downloading it.
            legacy_flat_path = REFS_DIR / f"{slug}{ext}"
            if legacy_flat_path.exists() and not local_path.exists():
                local_path.parent.mkdir(parents=True, exist_ok=True)
                legacy_flat_path.rename(local_path)
                print(f"  MOVED (department folder) {name}")

            if local_path.exists():
                print(f"  SKIP (already have it): {name}")
                skip_count += 1
                all_rows.append({
                    "name": name,
                    "image": str(local_path).replace("\\", "/"),
                    "profile_url": PROFILE_URL_TMPL.format(slug=slug),
                    "source_url": DEPT_URL_TMPL.format(uuid=dept["uuid"]),
                    "department": dept["title"],
                    "role": role,
                    "email": email,
                })
                continue

            image_url = resolve_image_url(raw_image_url)

            success, err = download_image(image_url, local_path)
            time.sleep(DOWNLOAD_DELAY_SEC)

            if not success:
                print(f"  FAIL download for {name}: {err}")
                fail_count += 1
                continue

            ok_count += 1
            print(f"  OK  {name}  -> {local_path}")

            all_rows.append({
                "name": name,
                "image": str(local_path).replace("\\", "/"),
                "profile_url": PROFILE_URL_TMPL.format(slug=slug),
                "source_url": DEPT_URL_TMPL.format(uuid=dept["uuid"]),
                "department": dept["title"],
                "role": role,
                "email": email,
            })

    REFS_CSV.parent.mkdir(parents=True, exist_ok=True)
    with open(REFS_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["name", "image", "profile_url", "source_url",
                        "department", "role", "email"],
        )
        writer.writeheader()
        writer.writerows(all_rows)

    print(f"\nDone. {ok_count} downloaded, {skip_count} already had, {fail_count} failed.")
    print(f"Wrote {REFS_CSV} with {len(all_rows)} rows.")
    print(f"Images saved under {REFS_DIR}/")
    if fail_count:
        print(f"\n{fail_count} still failed -- just run this script again; it will")
        print("skip everything already downloaded and only retry the failures.")


if __name__ == "__main__":
    main()