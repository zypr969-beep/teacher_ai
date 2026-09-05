from __future__ import annotations

import argparse
import csv
import re
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree as ET

import requests
from bs4 import BeautifulSoup

BASE = "https://www.hetc.ac.in/"
DEFAULT_ARCHIVE = BASE + "faculty/"
OUT_DIR = Path("references/hetc")
OUT_CSV = Path("refs.csv")
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) TeacherFaceAI/2.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def slugify(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "_", text.lower()).strip("_") or "faculty"


def same_site(url: str) -> bool:
    return urlparse(url).netloc.lower() in {"hetc.ac.in", "www.hetc.ac.in"}


def is_profile(url: str) -> bool:
    if not same_site(url):
        return False
    path = urlparse(url).path.rstrip("/")
    return bool(re.fullmatch(r"/faculty/[^/]+", path, flags=re.I))


def get(session: requests.Session, url: str, *, accept: str | None = None) -> requests.Response:
    headers = dict(HEADERS)
    if accept:
        headers["Accept"] = accept
    r = session.get(url, headers=headers, timeout=30, allow_redirects=True)
    r.raise_for_status()
    return r


def discover_feed(session: requests.Session) -> set[str]:
    """Faculty category RSS/Atom feeds are usually the cleanest source of post URLs."""
    found: set[str] = set()
    for feed_url in (BASE + "faculty/feed/", BASE + "feed/?cat=faculty"):
        try:
            r = get(session, feed_url, accept="application/rss+xml, application/atom+xml, application/xml, text/xml")
            root = ET.fromstring(r.content)
        except Exception:
            continue

        for elem in root.iter():
            tag = elem.tag.rsplit("}", 1)[-1].lower()
            if tag != "link":
                continue
            href = (elem.attrib.get("href") or elem.text or "").strip()
            if not href:
                continue
            href = urljoin(feed_url, href)
            if is_profile(href):
                found.add(href.rstrip("/") + "/")
    return found


def discover_wp_category(session: requests.Session) -> set[str]:
    """Try WordPress categories first, because /faculty/ behaves like a post archive."""
    found: set[str] = set()
    try:
        r = get(session, BASE + "wp-json/wp/v2/categories?search=faculty&per_page=100", accept="application/json")
        cats = r.json()
    except Exception:
        return found

    category_ids = [c.get("id") for c in cats if isinstance(c, dict) and c.get("id")]
    for cat_id in category_ids:
        for page in range(1, 21):
            try:
                url = f"{BASE}wp-json/wp/v2/posts?categories={cat_id}&per_page=100&page={page}&_embed=1"
                r = get(session, url, accept="application/json")
                posts = r.json()
            except Exception:
                break
            if not isinstance(posts, list) or not posts:
                break
            for post in posts:
                link = post.get("link") or ""
                if is_profile(link):
                    found.add(link.rstrip("/") + "/")
            if len(posts) < 100:
                break
    return found


def discover_wp_search(session: requests.Session) -> set[str]:
    """Search standard WordPress posts for faculty-related content."""
    found: set[str] = set()
    queries = ["faculty", "professor", "assistant professor", "associate professor"]
    for q in queries:
        try:
            r = get(session, f"{BASE}wp-json/wp/v2/search?search={requests.utils.quote(q)}&per_page=100&type=post", accept="application/json")
            results = r.json()
        except Exception:
            continue
        if not isinstance(results, list):
            continue
        for item in results:
            url = item.get("url") or ""
            if is_profile(url):
                found.add(url.rstrip("/") + "/")
    return found


def discover_sitemaps(session: requests.Session) -> set[str]:
    found: set[str] = set()
    candidates = [BASE + "wp-sitemap.xml", BASE + "sitemap_index.xml"]
    for sitemap_url in candidates:
        try:
            r = get(session, sitemap_url, accept="application/xml, text/xml")
            root = ET.fromstring(r.content)
        except Exception:
            continue

        locs = [(x.text or "").strip() for x in root.iter() if x.tag.rsplit("}", 1)[-1].lower() == "loc"]
        child_maps = [u for u in locs if u.lower().endswith(".xml")]
        direct = [u for u in locs if is_profile(u)]
        found.update(u.rstrip("/") + "/" for u in direct)

        for child in child_maps[:30]:
            try:
                cr = get(session, child, accept="application/xml, text/xml")
                croot = ET.fromstring(cr.content)
            except Exception:
                continue
            for x in croot.iter():
                if x.tag.rsplit("}", 1)[-1].lower() != "loc":
                    continue
                u = (x.text or "").strip()
                if is_profile(u):
                    found.add(u.rstrip("/") + "/")
    return found


def discover_archive_html(session: requests.Session, max_pages: int) -> set[str]:
    """Fallback for the public archive itself."""
    found: set[str] = set()
    for page_num in range(1, max_pages + 1):
        url = DEFAULT_ARCHIVE if page_num == 1 else f"{DEFAULT_ARCHIVE}page/{page_num}/"
        print(f"Scanning archive fallback: {url}")
        try:
            html = get(session, url).text
        except requests.RequestException as exc:
            print(f"  ! {exc}")
            continue

        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", href=True):
            href = urljoin(url, a.get("href", "")).split("#", 1)[0]
            if is_profile(href):
                found.add(href.rstrip("/") + "/")

        # Also inspect serialized HTML for profile paths.
        for match in re.findall(r"(?:https?:)?//(?:www\.)?hetc\.ac\.in/faculty/[a-z0-9-]+/?", html, flags=re.I):
            if is_profile(match):
                found.add(match.rstrip("/") + "/")
    return found


def extract_profile(session: requests.Session, url: str) -> tuple[str | None, str | None]:
    html = get(session, url).text
    soup = BeautifulSoup(html, "html.parser")

    name = None
    for selector in ("h1", ".entry-title", ".page-title", "article h2", "article h3"):
        element = soup.select_one(selector)
        if element:
            value = clean(element.get_text(" ", strip=True))
            if value:
                name = value
                break

    if not name and soup.title:
        name = clean(soup.title.get_text(" ", strip=True))
        name = re.sub(r"\s*[-|:]\s*HOOGHLY ENGINEERING.*$", "", name, flags=re.I).strip() or None

    image = None
    for selector in ('meta[property="og:image"]', 'meta[property="og:image:url"]', 'meta[name="twitter:image"]'):
        element = soup.select_one(selector)
        if element and element.get("content"):
            candidate = urljoin(url, element["content"].strip())
            if same_site(candidate):
                image = candidate
                break

    if not image:
        scope = soup.select_one("article") or soup.select_one("main") or soup
        candidates: list[tuple[int, str]] = []
        for img in scope.find_all("img"):
            src = img.get("src") or img.get("data-src") or img.get("data-lazy-src") or img.get("data-original")
            if not src:
                continue
            full = urljoin(url, src)
            if not same_site(full):
                continue
            low = full.lower()
            if any(x in low for x in ("logo", "icon", "favicon", "banner", "slider", "header", "affiliation")):
                continue
            ext = Path(urlparse(full).path).suffix.lower()
            if ext not in IMAGE_EXTS:
                continue
            try:
                area = int(img.get("width") or 0) * int(img.get("height") or 0)
            except (TypeError, ValueError):
                area = 0
            candidates.append((area, full))
        if candidates:
            image = max(candidates)[1]

    return name, image


def download(session: requests.Session, url: str, path: Path) -> bool:
    try:
        r = get(session, url)
        content_type = (r.headers.get("content-type") or "").lower()
        if content_type and not content_type.startswith("image/"):
            return False
        if not r.content:
            return False
        path.write_bytes(r.content)
        return True
    except requests.RequestException:
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Build HETC's local reference photo dataset.")
    parser.add_argument("--max-pages", type=int, default=7)
    parser.add_argument("--delay", type=float, default=0.35)
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    with requests.Session() as session:
        session.headers.update(HEADERS)

        discovered: set[str] = set()

        print("1/4 RSS/Atom feed discovery...")
        discovered.update(discover_feed(session))
        print(f"   {len(discovered)} profiles")

        print("2/4 WordPress category/search discovery...")
        before = len(discovered)
        discovered.update(discover_wp_category(session))
        discovered.update(discover_wp_search(session))
        print(f"   +{len(discovered) - before}")

        print("3/4 Sitemap discovery...")
        before = len(discovered)
        discovered.update(discover_sitemaps(session))
        print(f"   +{len(discovered) - before}")

        print("4/4 Archive HTML fallback...")
        before = len(discovered)
        discovered.update(discover_archive_html(session, args.max_pages))
        print(f"   +{len(discovered) - before}")

        profiles = sorted(discovered)
        print(f"\nFound {len(profiles)} HETC faculty profile URLs.\n")

        rows: list[tuple[str, str]] = []
        used: set[str] = set()

        for i, url in enumerate(profiles, 1):
            print(f"[{i}/{len(profiles)}] {url}")
            try:
                name, image_url = extract_profile(session, url)
            except requests.RequestException as exc:
                print(f"  ! Profile fetch failed: {exc}")
                continue

            if not name:
                print("  ! No name")
                continue
            if not image_url:
                print(f"  ! No image found for {name}")
                continue

            slug = slugify(name)
            base = slug
            suffix = 2
            while slug in used:
                slug = f"{base}_{suffix}"
                suffix += 1
            used.add(slug)

            ext = Path(urlparse(image_url).path).suffix.lower()
            if ext not in IMAGE_EXTS:
                ext = ".jpg"
            output = OUT_DIR / f"{slug}{ext}"

            if download(session, image_url, output):
                rows.append((name, output.as_posix()))
                print(f"  name: {name}")
                print(f"  saved: {output}")
            else:
                print("  ! image download failed")
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
