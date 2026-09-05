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
        "Chrome/131.0 Safari/537.36"
    ),
    "Accept": "application/json,text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
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
    return urlparse(url).netloc.lower() in {"www.hetc.ac.in", "hetc.ac.in"}


def is_faculty_profile(url: str) -> bool:
    path = urlparse(url).path
    return same_site(url) and re.match(r"^/faculty/[^/]+/?$", path) is not None


def get(session: requests.Session, url: str) -> requests.Response:
    response = session.get(url, headers=HEADERS, timeout=25)
    response.raise_for_status()
    return response


def discover_via_wp_api(session: requests.Session) -> list[tuple[str, str, str | None]]:
    """Return (name, profile_url, image_url) from the WordPress REST API when available."""
    api_root = urljoin(BASE, "wp-json/wp/v2/")

    try:
        response = get(session, urljoin(BASE, "wp-json/wp/v2/types"))
        types = response.json()
    except (requests.RequestException, ValueError):
        return []

    # Find a custom post type whose REST base is faculty, or whose name/base
    # clearly corresponds to the faculty archive.
    candidate = None
    for type_name, info in types.items():
        rest_base = str(info.get("rest_base", "")).strip("/").lower()
        label = str(info.get("name", type_name)).lower()
        if rest_base == "faculty" or type_name.lower() == "faculty" or "faculty" in label:
            candidate = rest_base or type_name
            break

    if not candidate:
        return []

    rows: list[tuple[str, str, str | None]] = []
    page = 1

    while page <= 20:
        endpoint = urljoin(api_root, f"{candidate}?per_page=100&page={page}&_embed=1&orderby=date&order=desc")
        try:
            response = get(session, endpoint)
            posts = response.json()
        except (requests.RequestException, ValueError):
            break

        if not isinstance(posts, list) or not posts:
            break

        for post in posts:
            title = post.get("title", {}).get("rendered", "")
            profile_url = post.get("link") or ""
            name = clean_name(BeautifulSoup(title, "html.parser").get_text(" ", strip=True))
            if not name or not profile_url:
                continue

            # Featured image supplied by _embed is the most reliable reference image.
            image_url = None
            embedded = post.get("_embedded", {})
            media = embedded.get("wp:featuredmedia", [])
            if media:
                image_url = media[0].get("source_url")

            rows.append((name, profile_url, image_url))

        if len(posts) < 100:
            break
        page += 1

    # Preserve order while removing duplicate profiles.
    unique: dict[str, tuple[str, str, str | None]] = {}
    for row in rows:
        unique[row[1]] = row
    return list(unique.values())


def discover_via_html(session: requests.Session, start_url: str, max_pages: int) -> list[tuple[str, str, str | None]]:
    """Fallback HTML discovery for sites where the REST API is unavailable."""
    queue = [start_url]
    seen_pages: set[str] = set()
    profiles: dict[str, tuple[str, str, str | None]] = {}

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
            href = urljoin(page, a["href"].split("#", 1)[0])
            if is_faculty_profile(href):
                text = clean_name(a.get_text(" ", strip=True))
                profiles.setdefault(href.rstrip("/") + "/", (text, href, None))
            elif same_site(href):
                path = urlparse(href).path.rstrip("/")
                if path == "/faculty" or re.match(r"^/faculty/page/\d+$", path):
                    if href not in seen_pages:
                        queue.append(href)

    return list(profiles.values())


def extract_profile_html(session: requests.Session, url: str) -> tuple[str | None, str | None]:
    html = get(session, url).text
    soup = BeautifulSoup(html, "html.parser")

    title = soup.find("h1")
    name = clean_name(title.get_text(" ", strip=True)) if title else None

    image_url = None
    for attrs in (
        {"property": "og:image"},
        {"name": "twitter:image"},
    ):
        meta = soup.find("meta", attrs=attrs)
        if meta and meta.get("content"):
            image_url = urljoin(url, meta["content"].strip())
            break

    if not image_url:
        main = soup.find("main") or soup.find("article") or soup
        candidates = []
        for img in main.find_all("img"):
            src = img.get("src") or img.get("data-src") or img.get("data-lazy-src")
            if not src:
                continue
            full = urljoin(url, src)
            if not same_site(full):
                continue
            ext = Path(urlparse(full).path).suffix.lower()
            if ext in IMAGE_EXTS:
                candidates.append(full)

        for candidate in candidates:
            low = candidate.lower()
            if "all-faculty-members" not in low and "logo" not in low:
                image_url = candidate
                break

    return name, image_url


def download_image(session: requests.Session, url: str, path: Path) -> bool:
    try:
        response = get(session, url)
        data = response.content
        content_type = (response.headers.get("Content-Type") or "").lower()
        if not data or (content_type and not content_type.startswith("image/")):
            return False
        path.write_bytes(data)
        return True
    except requests.RequestException as exc:
        print(f"    ! Image download failed: {exc}")
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Import HETC faculty names and official profile images.")
    parser.add_argument("--url", default=DEFAULT_START, help="HETC faculty archive URL")
    parser.add_argument("--max-pages", type=int, default=20, help="Maximum archive pages to scan in HTML fallback")
    parser.add_argument("--delay", type=float, default=0.4, help="Delay between requests")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    with requests.Session() as session:
        session.headers.update(HEADERS)

        print("Trying HETC WordPress REST API...")
        profiles = discover_via_wp_api(session)

        if profiles:
            print(f"REST API returned {len(profiles)} faculty profiles.")
        else:
            print("REST API unavailable or no faculty post type found; falling back to archive HTML.")
            profiles = discover_via_html(session, args.url, args.max_pages)

        print(f"\nFound {len(profiles)} faculty profile URLs.\n")

        rows: list[tuple[str, str]] = []
        used_slugs: set[str] = set()

        for index, (name, profile_url, image_url) in enumerate(profiles, start=1):
            print(f"[{index}/{len(profiles)}] {name}")

            if not image_url:
                try:
                    html_name, image_url = extract_profile_html(session, profile_url)
                    name = html_name or name
                except requests.RequestException as exc:
                    print(f"  ! Profile page fetch failed: {exc}")
                    image_url = None

            if not image_url:
                print("  ! No profile image found; skipping")
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
