"""
diagnose_hetc.py

Purpose: figure out HOW hetc.ac.in/faculty/ actually loads its content,
instead of guessing selectors again. Run this once and inspect the
output/ folder it creates.

Usage (PowerShell, from D:\TeacherFaceAI\teacher_ai with venv active):
    python diagnose_hetc.py

Output (written to ./hetc_diagnosis/):
    rendered_page.html      - full DOM after JS has run + network idle
    screenshot_full.png     - full-page screenshot (visually confirm content loaded)
    network_log.json        - every request/response the page made, esp. JSON/XHR
    console_log.txt         - any JS console errors (blocked API calls, CORS, etc.)
    candidate_json.json     - any response bodies that look like faculty data
"""

import json
import re
from pathlib import Path
from playwright.sync_api import sync_playwright

URL = "https://www.hetc.ac.in/faculty/"
OUT_DIR = Path("hetc_diagnosis")
OUT_DIR.mkdir(exist_ok=True)

FACULTY_HINT_RE = re.compile(r"faculty|professor|dr\.|designation|department", re.I)


def looks_like_faculty_json(text: str) -> bool:
    if not text:
        return False
    sample = text[:5000]
    return bool(FACULTY_HINT_RE.search(sample)) and (
        sample.strip().startswith("{") or sample.strip().startswith("[")
    )


def main():
    network_log = []
    candidate_bodies = []
    console_lines = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            )
        )
        page = context.new_page()

        page.on("console", lambda msg: console_lines.append(f"[{msg.type}] {msg.text}"))

        def handle_response(response):
            try:
                ctype = response.headers.get("content-type", "")
                entry = {
                    "url": response.url,
                    "status": response.status,
                    "content_type": ctype,
                }
                network_log.append(entry)

                if "json" in ctype or "javascript" in ctype:
                    try:
                        body = response.text()
                    except Exception:
                        body = None
                    if body and looks_like_faculty_json(body):
                        candidate_bodies.append({"url": response.url, "body": body[:20000]})
            except Exception as e:
                network_log.append({"error": str(e)})

        page.on("response", handle_response)

        print(f"Loading {URL} ...")
        page.goto(URL, wait_until="networkidle", timeout=60000)

        # Give any lazy/deferred JS (infinite scroll, delayed fetch) a chance to fire.
        page.wait_for_timeout(4000)

        # Try scrolling to bottom in case content is lazy-loaded on scroll.
        page.mouse.wheel(0, 20000)
        page.wait_for_timeout(3000)

        html = page.content()
        (OUT_DIR / "rendered_page.html").write_text(html, encoding="utf-8")

        page.screenshot(path=str(OUT_DIR / "screenshot_full.png"), full_page=True)

        browser.close()

    (OUT_DIR / "network_log.json").write_text(
        json.dumps(network_log, indent=2), encoding="utf-8"
    )
    (OUT_DIR / "console_log.txt").write_text(
        "\n".join(console_lines), encoding="utf-8"
    )
    (OUT_DIR / "candidate_json.json").write_text(
        json.dumps(candidate_bodies, indent=2), encoding="utf-8"
    )

    print(f"\nDone. Wrote diagnostics to {OUT_DIR.resolve()}/")
    print(f"  - network requests logged: {len(network_log)}")
    print(f"  - candidate faculty-data responses: {len(candidate_bodies)}")
    print("\nNext: open rendered_page.html and screenshot_full.png to confirm")
    print("whether faculty content actually rendered, and check candidate_json.json")
    print("for any API response that looks like the faculty list.")


if __name__ == "__main__":
    main()