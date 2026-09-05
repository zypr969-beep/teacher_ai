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
SKIP = {"newer posts", "older posts", "1", "2", "3", "4", "5", "6", "7"}


def clean_name(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def slugify(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "_", text.lower()).strip("_") or "faculty"


def same_site(url: str) -> bool:
    return urlparse(url).netloc.lower() in {"hetc.ac.in", "www.hetc.ac.in"}


def is_profile(url: str) -> bool:
    if not same_site(url):
        return False
    return bool(re.fullmatch(r"/faculty/[^/]+", urlparse(url).path.rstrip("/"), flags=re.I))


def discover_profiles(page, max_pages: int) -> list[str]:
    profiles: set[str] = set()
    for page_no in range(1, max_pages + 1):
        url = DEFAULT_ARCHIVE if page_no == 1 else f"{DEFAULT_ARCHIVE}page/{page_no}/"
        print(f"Scanning archive: {url}")
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
        except PlaywrightTimeoutError:
            print("  ! Load timed out; continuing")
        page.wait_for_timeout(1500)

        # HETC's current archive exposes faculty entries as H3 title links.
        items = page.locator("h3 a[href]").evaluate_all(
            "els => els.map(e => ({href:e.href, text:(e.innerText || e.textContent || '').trim()}))"
        )
        before = len(profiles)
        for item in items:
            href = str(item.get("href") or "").split("#", 1)[0]
            text = clean_name(str(item.get("text") or ""))
            if text.lower() in SKIP or not is_profile(href):
                continue
            profiles.add(href.rstrip("/") + "/")

        # Broad fallback: any /faculty/<slug>/ link with non-empty link text.
        if len(profiles) == before:
            items = page.locator("a[href]").evaluate_all(
                "els => els.map(e => ({href:e.href, text:(e.innerText || e.textContent || '').trim()}))"
            )
            for item in items:
                href = str(item.get("href") or "").split("#", 1)[0]
                text = clean_name(str(item.get("text") or ""))
                if len(text) < 3 or text.lower() in SKIP or not is_profile(href):
                    continue
                profiles.add(href.rstrip("/") + "/")

        print(f"  + {len(profiles) - before} profile links found")
    return sorted(profiles)


def first_good_image(page, profile_url: str) -> str | None:
    # Prefer page metadata when it points to a same-site image.
    for selector in ('meta[property="og:image"]', 'meta[name="twitter:image"]'):
        loc = page.locator(selector)
        if loc.count():
            value = loc.first.get_attribute("content")
            if value:
                candidate = urljoin(profile_url, value.strip())
                if same_site(candidate):
                    return candidate

    data = page.locator("img").evaluate_all(
        "els => els.map(e => ({src:e.currentSrc || e.src, alt:e.alt || '', w:e.naturalWidth || 0, h:e.naturalHeight || 0}))"
    )
    candidates: list[tuple[int, str]] = []
    for item in data:
        src = str(item.get("src") or "")
        if not src or not same_site(src):
            continue
        low = src.lower()
        if any(x in low for x in ("logo", "icon", "banner", "slider", "all-faculty-members")):
            continue
        if Path(urlparse(src).path).suffix.lower() not in IMAGE_EXTS:
            continue
        area = int(item.get("w") or 0) * int(item.get("h") or 0)
        candidates.append((area, src))
    return max(candidates)[1] if candidates else None


def extract_profile(page, url: str) -> tuple[str | None, str | None]:
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
    except PlaywrightTimeoutError:
        print("  ! Profile load timed out; continuing")
    page.wait_for_timeout(900)

    name = None
    for selector in ("h1", "article h2", "article h3"):
        loc = page.locator(selector)
        if loc.count():
            value = clean_name(loc.first.inner_text())
            if value:
                name = value
                break
    if not name:
        title = clean_name(page.title())
        if title:
            name = re.split(r"\s*[-|]\s*", title)[0].strip()
    return name, first_good_image(page, url)


def download(page, url: str, output: Path) -> bool:
    try:
        response = page.request.get(url, timeout=60000)
        if not response.ok:
            return False
        body = response.body()
        ctype = (response.headers.get("content-type") or "").lower()
        if not body or (ctype and not ctype.startswith("image/")):
            return False
        output.write_bytes(body)
        return True
    except Exception as exc:
        print(f"    ! Download failed: {exc}")
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect HETC public faculty names and official profile photos.")
    parser.add_argument("--max-pages", type=int, default=7)
    parser.add_argument("--delay", type=float, default=0.4)
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1440, "height": 1100},
            locale="en-US",
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/131.0 Safari/537.36")
        )
        page = context.new_page()
        profiles = discover_profiles(page, args.max_pages)
        print(f"\nFound {len(profiles)} faculty profile URLs.\n")

        rows: list[tuple[str, str]] = []
        used: set[str] = set()
        for i, profile_url in enumerate(profiles, 1):
            print(f"[{i}/{len(profiles)}] {profile_url}")
            name, image_url = extract_profile(page, profile_url)
            if not name:
                print("  ! No faculty name found; skipping")
                continue
            print(f"  name: {name}")
            if not image_url:
                print("  ! No suitable image found; skipping")
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
            if download(page, image_url, output):
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
