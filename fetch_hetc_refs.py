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
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/140.0 Safari/537.36 TeacherFaceAI/1.2"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def clean_name(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def slugify(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return text or "faculty"


def canonical(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.netloc.lower() or "www.hetc.ac.in"
    path = parsed.path.rstrip("/") + "/"
    return f"https://{host}{path}"


def same_site(url: str) -> bool:
    return urlparse(url).netloc.lower() in {"www.hetc.ac.in", "hetc.ac.in"}


def is_faculty_profile(url: str) -> bool:
    if not same_site(url):
        return False
    path = urlparse(url).path.rstrip("/")
    if not path.startswith("/faculty/"):
        return False
    remainder = path[len("/faculty/"):].strip("/")
    if not remainder or "/" in remainder:
        return False
    if remainder.lower() in {"page", "faculty"}:
        return False
    return True


def get(session: requests.Session, url: str) -> requests.Response:
    response = session.get(url, headers=HEADERS, timeout=30, allow_redirects=True)
    response.raise_for_status()
    return response


def discover_from_archive(session: requests.Session, start_url: str, max_pages: int) -> list[str]:
    """Discover /faculty/<slug>/ profile URLs from the public HETC archive.

    HETC currently exposes numbered archive pages such as /faculty/page/4/.
    Fetching them explicitly is more robust than relying on the site's
    pagination markup or a custom WordPress post type being exposed via REST.
    """
    profiles: set[str] = set()

    start = canonical(start_url)
    page_urls = [start]
    for page_no in range(2, max_pages + 1):
        page_urls.append(f"https://www.hetc.ac.in/faculty/page/{page_no}/")

    for page_url in page_urls:
        print(f"Scanning archive: {page_url}")
        try:
            response = get(session, page_url)
        except requests.RequestException as exc:
            print(f"  ! Could not fetch: {exc}")
            continue

        # Normal anchors.
        soup = BeautifulSoup(response.text, "html.parser")
        found_this_page: set[str] = set()
        for a in soup.find_all("a", href=True):
            href = urljoin(page_url, a.get("href", "")).split("#", 1)[0]
            if is_faculty_profile(href):
                found_this_page.add(canonical(href))

        # Raw HTML fallback for links emitted inside theme data/JSON.
        raw = response.text
        patterns = [
            r"https?://(?:www\.)?hetc\.ac\.in/faculty/[a-z0-9-]+/?",
            r"(?:https?:)?//(?:www\.)?hetc\.ac\.in/faculty/[a-z0-9-]+/?",
            r"(?:(?<=['\"])/faculty/[a-z0-9-]+/?)(?=['\"])",
        ]
        for pattern in patterns:
            for hit in re.findall(pattern, raw, flags=re.IGNORECASE):
                found_this_page.add(canonical(urljoin(page_url, hit)))

        before = len(profiles)
        profiles.update(found_this_page)
        print(f"  Found {len(profiles) - before} new profile links.")

    return sorted(profiles)


def extract_profile(session: requests.Session, url: str) -> tuple[str | None, str | None]:
    html = get(session, url).text
    soup = BeautifulSoup(html, "html.parser")

    h1 = soup.find("h1")
    name = clean_name(h1.get_text(" ", strip=True)) if h1 else None

    if not name:
        title = soup.find("title")
        if title:
            name = clean_name(title.get_text(" ", strip=True))
            name = re.sub(r"\s+-\s+HOOGHLY ENGINEERING.*$", "", name, flags=re.I).strip() or None

    image_url = None

    # The profile pages are WordPress pages; use social metadata first.
    for attrs in ({"property": "og:image"}, {"name": "twitter:image"}):
        meta = soup.find("meta", attrs=attrs)
        if meta and meta.get("content"):
            candidate = urljoin(url, meta["content"].strip())
            if same_site(candidate):
                image_url = candidate
                break

    # Fall back to page images, including lazy-loaded WordPress images.
    if not image_url:
        scope = soup.find("article") or soup.find("main") or soup
        candidates: list[str] = []
        for img in scope.find_all("img"):
            for attr in ("src", "data-src", "data-lazy-src", "data-original"):
                src = img.get(attr)
                if not src:
                    continue
                full = urljoin(url, src)
                parsed = urlparse(full)
                if same_site(full) and Path(parsed.path).suffix.lower() in IMAGE_EXTS:
                    candidates.append(full)
                    break

        seen: set[str] = set()
        for candidate in candidates:
            if candidate in seen:
                continue
            seen.add(candidate)
            low = candidate.lower()
            if any(x in low for x in ("logo", "affiliation", "all-faculty-members")):
                continue
            image_url = candidate
            break

    return name, image_url


def download_image(session: requests.Session, url: str, path: Path) -> bool:
    try:
        response = get(session, url)
        data = response.content
        content_type = (response.headers.get("Content-Type") or "").lower()
        if not data:
            return False
        if content_type and not content_type.startswith("image/"):
            return False
        path.write_bytes(data)
        return True
    except requests.RequestException as exc:
        print(f"    ! Image download failed: {exc}")
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Import HETC faculty names and official profile images.")
    parser.add_argument("--url", default=DEFAULT_START, help="HETC faculty archive URL")
    parser.add_argument("--max-pages", type=int, default=7, help="Maximum numbered faculty pages to scan")
    parser.add_argument("--delay", type=float, default=0.6, help="Delay between profile/image requests")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    with requests.Session() as session:
        session.headers.update(HEADERS)
        profiles = discover_from_archive(session, args.url, args.max_pages)

        print(f"\nFound {len(profiles)} faculty profile URLs.\n")

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
                print("  ! No faculty name found; skipping")
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

    with OUT_CSV.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["name", "image"])
        writer.writerows(rows)

    print(f"\nImported {len(rows)} reference photos.")
    print(f"CSV: {OUT_CSV}")
    print(f"Images: {OUT_DIR}")
    print("Review refs.csv before running build_refs.py.")


if __name__ == "__main__":
    main()
