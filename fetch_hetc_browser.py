from __future__ import annotations

import argparse
import csv
import re
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

BASE = "https://www.hetc.ac.in/"
PROFILE_RE = re.compile(r"^/faculty/([^/?#]+)/?$", re.I)
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}
OUT_DIR = Path("references/hetc")
OUT_CSV = Path("refs.csv")
DEBUG_DIR = Path("output/hetc_debug")

SEEDS = [
    "https://www.hetc.ac.in/faculty/",
    "https://www.hetc.ac.in/faculty-members/",
    "https://www.hetc.ac.in/?s=faculty",
    "https://www.hetc.ac.in/wp-sitemap.xml",
    "https://www.hetc.ac.in/sitemap_index.xml",
]


def same_site(url: str) -> bool:
    return urlparse(url).netloc.lower() in {"hetc.ac.in", "www.hetc.ac.in"}


def normalize_url(url: str, base_url: str) -> str:
    return urljoin(base_url, url).split("#", 1)[0].rstrip("/") + "/"


def is_profile(url: str) -> bool:
    return same_site(url) and bool(PROFILE_RE.fullmatch(urlparse(url).path.rstrip("/")))


def clean_name(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def slugify(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "_", text.lower()).strip("_") or "faculty"


def goto(page, url: str) -> None:
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
    except PlaywrightTimeoutError:
        print("  ! navigation timed out; using loaded DOM")
    page.wait_for_timeout(1200)


def discover_profiles(page, max_pages: int) -> set[str]:
    profiles: set[str] = set()
    queue = [normalize_url(x, BASE) for x in SEEDS]
    seen: set[str] = set()

    while queue and len(seen) < max_pages:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        print(f"Discovering: {url}")
        goto(page, url)

        current = normalize_url(page.url, url)
        if is_profile(current):
            profiles.add(current)

        try:
            links = page.locator("a[href]").evaluate_all("els => els.map(e => e.href)")
            html = page.content()
        except Exception:
            links, html = [], ""

        candidates = list(links)
        candidates += re.findall(r"https?://(?:www\.)?hetc\.ac\.in/[^\"'<>\s]+", html, flags=re.I)
        before = len(profiles)

        for raw in candidates:
            href = normalize_url(str(raw), url)
            if not same_site(href):
                continue
            if is_profile(href):
                profiles.add(href)
                continue
            path = urlparse(href).path.lower()
            if any(token in path for token in ("faculty", "sitemap", "staff", "academic")):
                if href not in seen and href not in queue:
                    queue.append(href)

        print(f"  + {len(profiles) - before} profiles; queue={len(queue)}")

    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    (DEBUG_DIR / "discovered_profiles.txt").write_text("\n".join(sorted(profiles)), encoding="utf-8")

    if not profiles:
        try:
            (DEBUG_DIR / "last_page.html").write_text(page.content(), encoding="utf-8")
        except Exception:
            pass
        print(f"  ! No profiles discovered; debug files are in {DEBUG_DIR}")

    return profiles


def extract_profile(page, profile_url: str) -> tuple[str | None, str | None]:
    goto(page, profile_url)

    name = None
    for selector in ("h1", ".entry-title", ".page-title", "article h2", "article h3"):
        try:
            loc = page.locator(selector)
            if loc.count():
                value = clean_name(loc.first.inner_text())
                if value:
                    name = value
                    break
        except Exception:
            pass

    if not name:
        title = clean_name(page.title())
        name = re.split(r"\s*[-|–—:]\s*", title)[0].strip() if title else None

    image_url = None
    for selector in ('meta[property="og:image"]', 'meta[property="og:image:url"]', 'meta[name="twitter:image"]'):
        try:
            loc = page.locator(selector)
            if loc.count():
                value = loc.first.get_attribute("content")
                if value:
                    candidate = urljoin(profile_url, value.strip())
                    if same_site(candidate):
                        image_url = candidate
                        break
        except Exception:
            pass

    if not image_url:
        try:
            images = page.locator("img").evaluate_all(
                "els => els.map(e => ({src:e.currentSrc || e.src || e.getAttribute('data-src') || '', alt:e.alt || '', w:e.naturalWidth || 0, h:e.naturalHeight || 0}))"
            )
        except Exception:
            images = []

        candidates: list[tuple[int, str]] = []
        for item in images:
            src = str(item.get("src") or "")
            if not src or not same_site(src):
                continue
            low = src.lower()
            if any(x in low for x in ("logo", "icon", "banner", "slider", "favicon", "header")):
                continue
            if Path(urlparse(src).path).suffix.lower() not in IMAGE_EXTS:
                continue
            area = int(item.get("w") or 0) * int(item.get("h") or 0)
            candidates.append((max(area, 1), src))
        if candidates:
            image_url = max(candidates)[1]

    return name, image_url


def download(page, image_url: str, output: Path) -> bool:
    try:
        response = page.request.get(image_url, timeout=60000)
        if not response.ok:
            return False
        data = response.body()
        ctype = (response.headers.get("content-type") or "").lower()
        if not data or (ctype and not ctype.startswith("image/")):
            return False
        output.write_bytes(data)
        return True
    except Exception as exc:
        print(f"  ! image download failed: {exc}")
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect public HETC faculty profile images.")
    parser.add_argument("--max-pages", type=int, default=80)
    parser.add_argument("--delay", type=float, default=0.4)
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1440, "height": 1200},
            locale="en-US",
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/131.0 Safari/537.36")
        )
        page = context.new_page()
        profiles = discover_profiles(page, args.max_pages)
        print(f"\nFound {len(profiles)} HETC faculty profile URLs.\n")

        rows: list[tuple[str, str]] = []
        used: set[str] = set()
        for i, profile_url in enumerate(sorted(profiles), 1):
            print(f"[{i}/{len(profiles)}] {profile_url}")
            name, image_url = extract_profile(page, profile_url)
            print(f"  name: {name or '(unknown)'}")
            print(f"  image: {image_url or '(not found)'}")
            if not name or not image_url:
                continue

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
                print("  ! could not download image")
            time.sleep(args.delay)

        context.close()
        browser.close()

    with OUT_CSV.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["name", "image"])
        writer.writerows(rows)

    print(f"\nImported {len(rows)} reference photos.")
    print(f"CSV: {OUT_CSV}")
    print(f"Images: {OUT_DIR}")


if __name__ == "__main__":
    main()
