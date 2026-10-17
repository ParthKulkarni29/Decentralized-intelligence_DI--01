"""Download pinned vendor assets and fonts for fully offline frontend operation.

Phase 2:
1. Downloads Tailwind CSS (3.4.16) to frontend/assets/vendor/tailwindcss.js
2. Downloads React (18.3.1) to frontend/assets/vendor/react.production.min.js
3. Downloads ReactDOM (18.3.1) to frontend/assets/vendor/react-dom.production.min.js
4. Downloads Babel (7.26.4) to frontend/assets/vendor/babel.min.js
5. Downloads WOFF2 fonts for IBM Plex Sans, IBM Plex Mono, and Shippori Mincho to frontend/assets/fonts/
"""

from pathlib import Path
import sys
import httpx

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = PROJECT_ROOT / "frontend"
ASSETS_DIR = FRONTEND_DIR / "assets"
VENDOR_DIR = ASSETS_DIR / "vendor"
FONTS_DIR = ASSETS_DIR / "fonts"

VENDOR_DIR.mkdir(parents=True, exist_ok=True)
FONTS_DIR.mkdir(parents=True, exist_ok=True)

VENDOR_DOWNLOADS = [
    {
        "name": "Tailwind CSS 3.4.16",
        "url": "https://cdn.tailwindcss.com/3.4.16",
        "target": VENDOR_DIR / "tailwindcss.js",
    },
    {
        "name": "React 18.3.1",
        "url": "https://unpkg.com/react@18.3.1/umd/react.production.min.js",
        "target": VENDOR_DIR / "react.production.min.js",
    },
    {
        "name": "ReactDOM 18.3.1",
        "url": "https://unpkg.com/react-dom@18.3.1/umd/react-dom.production.min.js",
        "target": VENDOR_DIR / "react-dom.production.min.js",
    },
    {
        "name": "Babel Standalone 7.26.4",
        "url": "https://unpkg.com/@babel/standalone@7.26.4/babel.min.js",
        "target": VENDOR_DIR / "babel.min.js",
    },
]

FONT_DOWNLOADS = [
    # IBM Plex Sans
    {
        "name": "IBM Plex Sans 400 Normal",
        "url": "https://unpkg.com/@fontsource/ibm-plex-sans@5.1.0/files/ibm-plex-sans-latin-400-normal.woff2",
        "target": FONTS_DIR / "ibm-plex-sans-latin-400-normal.woff2",
    },
    {
        "name": "IBM Plex Sans 500 Normal",
        "url": "https://unpkg.com/@fontsource/ibm-plex-sans@5.1.0/files/ibm-plex-sans-latin-500-normal.woff2",
        "target": FONTS_DIR / "ibm-plex-sans-latin-500-normal.woff2",
    },
    {
        "name": "IBM Plex Sans 600 Normal",
        "url": "https://unpkg.com/@fontsource/ibm-plex-sans@5.1.0/files/ibm-plex-sans-latin-600-normal.woff2",
        "target": FONTS_DIR / "ibm-plex-sans-latin-600-normal.woff2",
    },
    {
        "name": "IBM Plex Sans 700 Normal",
        "url": "https://unpkg.com/@fontsource/ibm-plex-sans@5.1.0/files/ibm-plex-sans-latin-700-normal.woff2",
        "target": FONTS_DIR / "ibm-plex-sans-latin-700-normal.woff2",
    },
    {
        "name": "IBM Plex Sans 400 Italic",
        "url": "https://unpkg.com/@fontsource/ibm-plex-sans@5.1.0/files/ibm-plex-sans-latin-400-italic.woff2",
        "target": FONTS_DIR / "ibm-plex-sans-latin-400-italic.woff2",
    },
    # IBM Plex Mono
    {
        "name": "IBM Plex Mono 400 Normal",
        "url": "https://unpkg.com/@fontsource/ibm-plex-mono@5.1.0/files/ibm-plex-mono-latin-400-normal.woff2",
        "target": FONTS_DIR / "ibm-plex-mono-latin-400-normal.woff2",
    },
    {
        "name": "IBM Plex Mono 500 Normal",
        "url": "https://unpkg.com/@fontsource/ibm-plex-mono@5.1.0/files/ibm-plex-mono-latin-500-normal.woff2",
        "target": FONTS_DIR / "ibm-plex-mono-latin-500-normal.woff2",
    },
    {
        "name": "IBM Plex Mono 600 Normal",
        "url": "https://unpkg.com/@fontsource/ibm-plex-mono@5.1.0/files/ibm-plex-mono-latin-600-normal.woff2",
        "target": FONTS_DIR / "ibm-plex-mono-latin-600-normal.woff2",
    },
    {
        "name": "IBM Plex Mono 400 Italic",
        "url": "https://unpkg.com/@fontsource/ibm-plex-mono@5.1.0/files/ibm-plex-mono-latin-400-italic.woff2",
        "target": FONTS_DIR / "ibm-plex-mono-latin-400-italic.woff2",
    },
    # Shippori Mincho
    {
        "name": "Shippori Mincho 500 Latin",
        "url": "https://unpkg.com/@fontsource/shippori-mincho@5.1.0/files/shippori-mincho-latin-500-normal.woff2",
        "target": FONTS_DIR / "shippori-mincho-latin-500-normal.woff2",
    },
    {
        "name": "Shippori Mincho 500 Japanese",
        "url": "https://unpkg.com/@fontsource/shippori-mincho@5.1.0/files/shippori-mincho-japanese-500-normal.woff2",
        "target": FONTS_DIR / "shippori-mincho-japanese-500-normal.woff2",
    },
    {
        "name": "Shippori Mincho 700 Latin",
        "url": "https://unpkg.com/@fontsource/shippori-mincho@5.1.0/files/shippori-mincho-latin-700-normal.woff2",
        "target": FONTS_DIR / "shippori-mincho-latin-700-normal.woff2",
    },
    {
        "name": "Shippori Mincho 700 Japanese",
        "url": "https://unpkg.com/@fontsource/shippori-mincho@5.1.0/files/shippori-mincho-japanese-700-normal.woff2",
        "target": FONTS_DIR / "shippori-mincho-japanese-700-normal.woff2",
    },
]


def download_all() -> bool:
    client = httpx.Client(timeout=60.0, follow_redirects=True)
    all_items = VENDOR_DOWNLOADS + FONT_DOWNLOADS
    total = len(all_items)

    print(f"=== Starting Download of {total} Offline Assets ===")
    for idx, item in enumerate(all_items, 1):
        name = item["name"]
        url = item["url"]
        target = item["target"]

        if target.exists() and target.stat().st_size > 0:
            print(f"[{idx:02d}/{total:02d}] [EXISTS] {name:<35} -> {target.name} ({target.stat().st_size:,} bytes)")
            continue

        print(f"[{idx:02d}/{total:02d}] [FETCH ] {name:<35} from {url}...")
        try:
            resp = client.get(url)
            if resp.status_code != 200:
                print(f"[FAIL] HTTP {resp.status_code} for {url}", file=sys.stderr)
                return False
            target.write_bytes(resp.content)
            print(f"[{idx:02d}/{total:02d}] [SAVED ] {name:<35} -> {target.name} ({len(resp.content):,} bytes)")
        except Exception as exc:
            print(f"[FAIL] Download error for {name}: {exc}", file=sys.stderr)
            return False

    print("\n=== All Offline Assets Downloaded Successfully ===")
    return True


if __name__ == "__main__":
    success = download_all()
    sys.exit(0 if success else 1)
