from __future__ import annotations

import argparse
import csv
import re
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

BASE = "https://www.hetc.ac.in/"
DEFAULT_START = "https://www.hetc.ac.in/faculty/"
OUT_DIR = Path("references/hetc")
OUT_CSV = Path("refs.csv")

HEADERS = {
    "User-Agent": "TeacherFaceAI/1.0 (local faculty-reference importer)"
}

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def clean_name(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text


def slugify(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return text or "faculty"


def same_site(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return host in {"www.hetc.ac.in", "hetc.ac.in"}


def is_faculty_profile(url: str) -> bool:
    p = urlparse(url)
    return same_site(url) and re.match(r"^/faculty/[^/]+/?$", p.path) is not None


def get(session: requests.Session, url: str) -> requests.Response:
    response = session.get(url, headers=HEADERS, timeout=25)
    response.raise_for_status()
    return response


def discover_profiles(session: requests.Session, start_url: str, max_pages: int) -> list[str]:
    queue = [start_url]
    seen_pages: set[str] = set()
    profiles: set[str] = set()

    while queue and len(seen_pages) < max_pages:
        page = queue.pop(0)
        if page in seen_pages:
            continue
        seen_pages.add(page)
        print(f"Scanning archive: {page}")

        try:
            html = get(session, page).text
        except requests.RequestException as exc:
            print(f"  ! Could not fetch: {exc}")
            continue

        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", href=True):
            href = urljoin(page, a["href"])
            href = href.split("#", 1)[0]
            if is_faculty_profile(href):
                profiles.add(href.rstrip("/") + "/")
            elif same_site(href):
                path = urlparse(href).path.rstrip("/")
                if path.startswith("/faculty") and ("page" in path or path == "/faculty"):
                    if href not in seen_pages:
                        queue.append(href)

    return sorted(profiles)


def extract_profile(session: requests.Session, url: str) -> tuple[str | None, str | None]:
    html = get(session, url).text
    soup = BeautifulSoup(html, "html.parser")

    title = soup.find("h1")
    name = clean_name(title.get_text(" ", strip=True)) if title else None

    image_url = None

    # Prefer OpenGraph image when available.
    og = soup.find("meta", attrs={"property": "og:image"})
    if og and og.get("content"):
        image_url = urljoin(url, og["content"])

    # Fall back to images near the main profile/article content.
    if not image_url:
        main = soup.find("main") or soup.find("article") or soup
        candidates = []
        for img in main.find_all("img", src=True):
            src = urljoin(url, img["src"])
            parsed = urlparse(src)
            if not same_site(src):
                continue
            ext = Path(parsed.path).suffix.lower()
            if ext in IMAGE_EXTS:
                candidates.append(src)
        # Skip the common all-faculty banner when possible.
        for candidate in candidates:
            if "All-Faculty-Members" not in candidate and "logo" not in candidate.lower():
                image_url = candidate
                break

    return name, image_url


def download_image(session: requests.Session, url: str, path: Path) -> bool:
    try:
        response = get(session, url)
        data = response.content
        if not data:
            return False
        path.write_bytes(data)
        return True
    except requests.RequestException as exc:
        print(f"    ! Image download failed: {exc}")
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Import HETC faculty names and official profile images.")
    parser.add_argument("--url", default=DEFAULT_START, help="HETC faculty archive URL")
    parser.add_argument("--max-pages", type=int, default=20, help="Maximum archive pages to scan")
    parser.add_argument("--delay", type=float, default=0.5, help="Delay between profile requests")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    profiles = discover_profiles(session, args.url, args.max_pages)

    print(f"\nFound {len(profiles)} faculty profile URLs.")

    rows: list[tuple[str, str]] = []
    used_slugs: set[str] = set()

    for index, profile_url in enumerate(profiles, start=1):
        print(f"[{index}/{len(profiles)}] {profile_url}")
        try:
            name, image_url = extract_profile(session, profile_url)
        except requests.RequestException as exc:
            print(f"  ! Profile fetch failed: {exc}")
            continue

        if not name:
            print("  ! No H1 name found; skipping")
            continue
        if not image_url:
            print(f"  ! No profile image found for {name}; skipping")
            continue

        slug = slugify(name)
        base_slug = slug
        suffix = 2
        while slug in used_slugs:
            slug = f"{base_slug}_{suffix}"
            suffix += 1
        used_slugs.add(slug)

        ext = Path(urlparse(image_url).path).suffix.lower()
        if ext not in IMAGE_EXTS:
            ext = ".jpg"
        image_path = OUT_DIR / f"{slug}{ext}"

        print(f"  {name}")
        print(f"  image: {image_url}")
        if download_image(session, image_url, image_path):
            rows.append((name, image_path.as_posix()))
            print(f"  saved: {image_path}")
        else:
            print("  ! Skipping reference")

        time.sleep(args.delay)

    with OUT_CSV.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["name", "image"])
        writer.writerows(rows)

    print(f"\nImported {len(rows)} reference photos.")
    print(f"CSV: {OUT_CSV}")
    print(f"Images: {OUT_DIR}")
    print("Review refs.csv before running build_refs.py.")


if __name__ == "__main__":
    main()
