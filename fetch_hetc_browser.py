from __future__ import annotations

import argparse
import csv
import re
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

BASE = "https://www.hetc.ac.in/"
DEFAULT_ARCHIVE = "https://www.hetc.ac.in/faculty/"
OUT_DIR = Path("references/hetc")
OUT_CSV = Path("refs.csv")

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def clean_name(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text


def slugify(text: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "_", text.lower()).strip("_")
    return text or "faculty"


def same_site(url: str) -> bool:
    return urlparse(url).netloc.lower() in {"hetc.ac.in", "www.hetc.ac.in"}


def is_profile(url: str) -> bool:
    if not same_site(url):
        return False
    path = urlparse(url).path.rstrip("/")
    return bool(re.fullmatch(r"/faculty/[^/]+", path))


def discover_profiles(page, max_pages: int) -> list[str]:
    profiles: set[str] = set()

    for page_no in range(1, max_pages + 1):
        url = DEFAULT_ARCHIVE if page_no == 1 else f"{DEFAULT_ARCHIVE}page/{page_no}/"
        print(f"Scanning archive: {url}")
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(1800)
        except PlaywrightTimeoutError:
            print("  ! Page load timed out; continuing with loaded DOM")

        hrefs = page.locator("a[href]").evaluate_all(
            "els => els.map(e => e.href)"
        )
        before = len(profiles)
        for href in hrefs:
            href = href.split("#", 1)[0].rstrip("/") + "/"
            if is_profile(href):
                profiles.add(href)
        print(f"  + {len(profiles) - before} profile links found")

    return sorted(profiles)


def first_good_image(page, profile_url: str) -> str | None:
    # Prefer OpenGraph/Twitter images.
    for selector in [
        'meta[property="og:image"]',
        'meta[name="twitter:image"]',
    ]:
        loc = page.locator(selector)
        if loc.count():
            value = loc.first.get_attribute("content")
            if value:
                return urljoin(profile_url, value)

    # Then inspect actual loaded images in the main content.
    data = page.locator("img").evaluate_all(
        "els => els.map(e => ({src:e.currentSrc || e.src, alt:e.alt || '', w:e.naturalWidth, h:e.naturalHeight}))"
    )
    candidates = []
    for item in data:
        src = item.get("src") or ""
        if not src or not same_site(src):
            continue
        ext = Path(urlparse(src).path).suffix.lower()
        if ext not in IMAGE_EXTS:
            continue
        low = src.lower()
        if "logo" in low or "icon" in low or "all-faculty-members" in low:
            continue
        w = int(item.get("w") or 0)
        h = int(item.get("h") or 0)
        candidates.append((w * h, src))

    if candidates:
        candidates.sort(reverse=True)
        return candidates[0][1]
    return None


def extract_profile(page, profile_url: str) -> tuple[str | None, str | None]:
    try:
        page.goto(profile_url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(1200)
    except PlaywrightTimeoutError:
        print("  ! Profile load timed out; using loaded DOM")

    # Profile title is the authoritative display name on the official page.
    headings = page.locator("h1")
    name = None
    if headings.count():
        name = clean_name(headings.first.inner_text())

    if not name:
        title = page.title()
        name = clean_name(re.sub(r"\s*[-|].*HETC.*$", "", title, flags=re.I))

    image = first_good_image(page, profile_url)
    return name or None, image


def download_via_browser(page, image_url: str, output: Path) -> bool:
    try:
        response = page.request.get(image_url, timeout=60000)
        if not response.ok:
            return False
        body = response.body()
        content_type = (response.headers.get("content-type") or "").lower()
        if not body or (content_type and not content_type.startswith("image/")):
            return False
        output.write_bytes(body)
        return True
    except Exception as exc:
        print(f"    ! Image download failed: {exc}")
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect HETC's publicly listed faculty names and official profile photos.")
    parser.add_argument("--max-pages", type=int, default=7)
    parser.add_argument("--delay", type=float, default=0.5)
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})

        profiles = discover_profiles(page, args.max_pages)
        print(f"\nFound {len(profiles)} faculty profile URLs.\n")

        rows: list[tuple[str, str]] = []
        used: set[str] = set()

        for i, profile_url in enumerate(profiles, 1):
            print(f"[{i}/{len(profiles)}] {profile_url}")
            name, image_url = extract_profile(page, profile_url)

            if not name:
                print("  ! Could not determine name; skipping")
                continue
            print(f"  name: {name}")

            if not image_url:
                print("  ! Could not find a suitable profile image; skipping")
                continue
            print(f"  image: {image_url}")

            slug = slugify(name)
            base = slug
            n = 2
            while slug in used:
                slug = f"{base}_{n}"
                n += 1
            used.add(slug)

            ext = Path(urlparse(image_url).path).suffix.lower()
            if ext not in IMAGE_EXTS:
                ext = ".jpg"
            output = OUT_DIR / f"{slug}{ext}"

            if download_via_browser(page, image_url, output):
                rows.append((name, output.as_posix()))
                print(f"  saved: {output}")
            else:
                print("  ! Could not download image")

            time.sleep(args.delay)

        browser.close()

    with OUT_CSV.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["name", "image"])
        writer.writerows(rows)

    print(f"\nImported {len(rows)} reference photos.")
    print(f"CSV: {OUT_CSV}")
    print(f"Images: {OUT_DIR}")
    print("Review refs.csv before using the references locally.")


if __name__ == "__main__":
    main()
