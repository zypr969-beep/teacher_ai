from __future__ import annotations

import csv
import re
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree as ET

import requests
from bs4 import BeautifulSoup

BASE = "https://www.hetc.ac.in/"
OUT_DIR = Path("references/hetc")
OUT_CSV = Path("refs.csv")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) TeacherFaceAI/1.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def slugify(text: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "_", text.lower()).strip("_")
    return s or "faculty"


def same_site(url: str) -> bool:
    return urlparse(url).netloc.lower() in {"hetc.ac.in", "www.hetc.ac.in"}


def is_faculty_profile(url: str) -> bool:
    if not same_site(url):
        return False
    path = urlparse(url).path.rstrip("/")
    return bool(re.fullmatch(r"/faculty/[^/]+", path, flags=re.I))


def get(session: requests.Session, url: str) -> requests.Response:
    r = session.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r


def discover_from_feed(session: requests.Session) -> list[str]:
    urls: list[str] = []
    for feed_url in (BASE + "faculty/feed/", BASE + "feed/"):
        try:
            r = get(session, feed_url)
            root = ET.fromstring(r.content)
        except Exception:
            continue
        for loc in root.findall('.//{*}link'):
            href = (loc.text or '').strip()
            if is_faculty_profile(href):
                urls.append(href.rstrip('/') + '/')
        for item in root.findall('.//{*}item'):
            link = item.findtext('{*}link') or ''
            if is_faculty_profile(link):
                urls.append(link.rstrip('/') + '/')
    return sorted(set(urls))


def discover_from_wp_posts(session: requests.Session) -> list[str]:
    urls: list[str] = []
    try:
        for page in range(1, 21):
            api = f"{BASE}wp-json/wp/v2/posts?per_page=100&page={page}&search=faculty"
            r = get(session, api)
            posts = r.json()
            if not isinstance(posts, list) or not posts:
                break
            for post in posts:
                link = post.get('link') or ''
                if is_faculty_profile(link):
                    urls.append(link.rstrip('/') + '/')
            if len(posts) < 100:
                break
    except Exception:
        pass
    return sorted(set(urls))


def discover_from_sitemap(session: requests.Session) -> list[str]:
    urls: set[str] = set()
    sitemap_candidates = [BASE + "wp-sitemap.xml", BASE + "sitemap_index.xml"]
    for sm in sitemap_candidates:
        try:
            r = get(session, sm)
            root = ET.fromstring(r.content)
        except Exception:
            continue
        locs = [x.text.strip() for x in root.findall('.//{*}loc') if x.text]
        child_sitemaps = [u for u in locs if 'sitemap' in u.lower() and u.lower().endswith('.xml')]
        direct_urls = [u for u in locs if is_faculty_profile(u)]
        urls.update(u.rstrip('/') + '/' for u in direct_urls)
        for child in child_sitemaps:
            if len(child_sitemaps) > 30:
                break
            try:
                cr = get(session, child)
                cr_root = ET.fromstring(cr.content)
                for u in cr_root.findall('.//{*}loc'):
                    value = (u.text or '').strip()
                    if is_faculty_profile(value):
                        urls.add(value.rstrip('/') + '/')
            except Exception:
                continue
    return sorted(urls)


def extract_profile(session: requests.Session, url: str) -> tuple[str | None, str | None]:
    r = get(session, url)
    soup = BeautifulSoup(r.text, 'html.parser')

    name = None
    for selector in ('h1', '.entry-title', '.page-title', 'article h2', 'article h3'):
        el = soup.select_one(selector)
        if el:
            value = clean(el.get_text(' ', strip=True))
            if value:
                name = value
                break
    if not name and soup.title:
        name = clean(re.split(r'\s*[-|:]\s*', soup.title.get_text(' ', strip=True))[0])

    image = None
    for selector in (
        'meta[property="og:image"]',
        'meta[property="og:image:url"]',
        'meta[name="twitter:image"]',
    ):
        el = soup.select_one(selector)
        if el and el.get('content'):
            candidate = urljoin(url, el['content'].strip())
            if same_site(candidate):
                image = candidate
                break

    if not image:
        candidates: list[tuple[int, str]] = []
        for img in soup.select('img'):
            src = img.get('src') or img.get('data-src') or img.get('data-lazy-src')
            if not src:
                continue
            candidate = urljoin(url, src)
            if not same_site(candidate):
                continue
            low = candidate.lower()
            if any(x in low for x in ('logo', 'icon', 'favicon', 'banner', 'slider', 'header')):
                continue
            area = 0
            try:
                width = int(img.get('width') or 0)
                height = int(img.get('height') or 0)
                area = width * height
            except ValueError:
                pass
            candidates.append((area, candidate))
        if candidates:
            image = max(candidates)[1]

    return name, image


def download(session: requests.Session, url: str, path: Path) -> bool:
    try:
        r = get(session, url)
        ctype = (r.headers.get('content-type') or '').lower()
        if ctype and not ctype.startswith('image/'):
            return False
        if not r.content:
            return False
        path.write_bytes(r.content)
        return True
    except Exception:
        return False


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with requests.Session() as session:
        discovered: set[str] = set()
        print('1/3 Feed discovery...')
        discovered.update(discover_from_feed(session))
        print(f'   {len(discovered)} profile URLs')

        print('2/3 WordPress posts discovery...')
        before = len(discovered)
        discovered.update(discover_from_wp_posts(session))
        print(f'   +{len(discovered)-before}')

        print('3/3 Sitemap discovery...')
        before = len(discovered)
        discovered.update(discover_from_sitemap(session))
        print(f'   +{len(discovered)-before}')

        profiles = sorted(discovered)
        print(f'\nFound {len(profiles)} HETC faculty profile URLs.\n')

        rows: list[tuple[str, str]] = []
        used: set[str] = set()
        for i, url in enumerate(profiles, 1):
            print(f'[{i}/{len(profiles)}] {url}')
            try:
                name, image_url = extract_profile(session, url)
            except requests.RequestException as exc:
                print(f'  ! profile request failed: {exc}')
                continue
            if not name:
                print('  ! no faculty name found')
                continue
            if not image_url:
                print('  ! no image found')
                continue

            slug = slugify(name)
            base = slug
            n = 2
            while slug in used:
                slug = f'{base}_{n}'
                n += 1
            used.add(slug)

            ext = Path(urlparse(image_url).path).suffix.lower()
            if ext not in {'.jpg', '.jpeg', '.png', '.webp', '.bmp'}:
                ext = '.jpg'
            path = OUT_DIR / f'{slug}{ext}'

            if download(session, image_url, path):
                rows.append((name, path.as_posix()))
                print(f'  name: {name}')
                print(f'  saved: {path}')
            else:
                print('  ! image download failed')
            time.sleep(0.35)

    with OUT_CSV.open('w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        writer.writerow(['name', 'image'])
        writer.writerows(rows)

    print(f'\nImported {len(rows)} reference photos.')
    print(f'CSV: {OUT_CSV}')
    print(f'Images: {OUT_DIR}')


if __name__ == '__main__':
    main()
