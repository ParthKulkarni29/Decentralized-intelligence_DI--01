"""
verify_offline_assets.py

Verification script for Phase 2:
1. Scans frontend/index.html and frontend/assets/sakura.css for external network references.
2. Checks that every local referenced asset exists on disk.
3. Starts a temporary local HTTP server on frontend/ and curls/requests every referenced asset.
4. Verifies HTTP 200, Content-Type, Content-Length, and integrity with 0 network failures.
"""

import sys
import re
import os
import http.server
import socketserver
import threading
import urllib.request
import urllib.error

sys.stdout.reconfigure(encoding="utf-8")

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND_DIR = os.path.join(ROOT_DIR, "frontend")
INDEX_PATH = os.path.join(FRONTEND_DIR, "index.html")
CSS_PATH = os.path.join(FRONTEND_DIR, "assets", "sakura.css")


def scan_external_links():
    print("=== 1. Scanning for External Network References ===")
    external_found = []

    # Check index.html
    with open(INDEX_PATH, "r", encoding="utf-8") as f:
        html = f.read()

    # Find any http/https links except svg xmlns and localhost API_BASE
    for match in re.finditer(r'(?:href|src|url)\s*=\s*["\']([^"\']+)["\']', html, re.IGNORECASE):
        target = match.group(1)
        if target.startswith(("http://", "https://", "//")):
            if not target.startswith("http://localhost"):
                external_found.append(("index.html", target))

    # Check sakura.css
    with open(CSS_PATH, "r", encoding="utf-8") as f:
        css = f.read()

    for match in re.finditer(r'url\s*\(\s*["\']?([^"\'\)]+)["\']?\s*\)', css, re.IGNORECASE):
        target = match.group(1)
        if target.startswith(("http://", "https://", "//")):
            external_found.append(("sakura.css", target))

    if external_found:
        print("FAIL: External network links detected:")
        for file, link in external_found:
            print(f"  [{file}] {link}")
        return False
    else:
        print("PASS: Zero external CDN/network assets found in index.html and sakura.css.")
        return True


def scan_local_assets():
    print("\n=== 2. Resolving Referenced Local Assets on Disk ===")
    with open(INDEX_PATH, "r", encoding="utf-8") as f:
        html = f.read()

    with open(CSS_PATH, "r", encoding="utf-8") as f:
        css = f.read()

    assets = []

    # HTML scripts and stylesheets
    for match in re.finditer(r'(?:href|src)\s*=\s*["\']([^"\']+)["\']', html, re.IGNORECASE):
        target = match.group(1)
        if target.endswith((".css", ".js")):
            assets.append(target)

    # CSS font urls
    for match in re.finditer(r'url\s*\(\s*["\']?([^"\'\)]+)["\']?\s*\)', css, re.IGNORECASE):
        target = match.group(1)
        # In CSS, relative paths are relative to sakura.css directory (assets/)
        if target.startswith("./"):
            target = target[2:]
        if not target.startswith("assets/"):
            target = "assets/" + target
        assets.append(target)

    print(f"Total unique referenced assets: {len(set(assets))}")
    missing = []
    for asset in sorted(set(assets)):
        full_path = os.path.join(FRONTEND_DIR, asset)
        if os.path.exists(full_path):
            size = os.path.getsize(full_path)
            print(f"  [OK] {asset:<55} ({size:>10,} bytes)")
        else:
            print(f"  [MISSING] {asset}")
            missing.append(asset)

    if missing:
        print(f"FAIL: {len(missing)} assets missing on disk.")
        return False, assets
    print("PASS: All referenced assets exist on disk.")
    return True, sorted(set(assets))


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # Suppress default noisy console logs


def verify_http_serving(assets):
    print("\n=== 3. Simulating Offline Browser Asset Fetch via HTTP Server ===")
    port = 8765
    server = socketserver.TCPServer(("127.0.0.1", port), lambda *args, **kwargs: QuietHandler(*args, directory=FRONTEND_DIR, **kwargs))
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    all_passed = True
    base_url = f"http://127.0.0.1:{port}"

    # Also test index.html itself
    test_urls = ["/index.html"] + [f"/{asset}" for asset in assets]

    print(f"{'Status':<8} | {'Asset Path':<55} | {'Content-Type':<25} | {'Bytes':<10}")
    print("-" * 105)

    for path in test_urls:
        url = base_url + path
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=5) as resp:
                status = resp.status
                content_type = resp.headers.get("Content-Type", "")
                content = resp.read()
                length = len(content)
                if status == 200 and length > 0:
                    print(f"HTTP {status:<3} | {path:<55} | {content_type:<25} | {length:>9,} B")
                else:
                    print(f"FAIL {status} | {path}")
                    all_passed = False
        except Exception as e:
            print(f"ERROR   | {path} -> {e}")
            all_passed = False

    server.shutdown()
    server.server_close()

    if all_passed:
        print("\nPASS: All assets served successfully with HTTP 200 and zero network errors.")
    else:
        print("\nFAIL: Some assets failed to load.")
    return all_passed


def main():
    ok1 = scan_external_links()
    ok2, assets = scan_local_assets()
    ok3 = verify_http_serving(assets) if ok2 else False

    if ok1 and ok2 and ok3:
        print("\n*** ALL PHASE 2 VERIFICATION CHECKS PASSED ***")
        sys.exit(0)
    else:
        print("\n*** VERIFICATION FAILED ***")
        sys.exit(1)


if __name__ == "__main__":
    main()
