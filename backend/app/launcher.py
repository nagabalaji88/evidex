"""Helpers for evidex.bat (Windows start-up script).

    python -m app.launcher frontend-hash   print a fingerprint of the front-end sources; the
                                           script rebuilds frontend\\dist when it changes
    python -m app.launcher open URL        wait until the app answers at URL, then open it
                                           in the default browser
"""

from __future__ import annotations

import hashlib
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

from .config import REPO_ROOT

FRONTEND = REPO_ROOT / "frontend"
# Everything the build reads. node_modules and dist are outputs, not sources.
SOURCES = ("src", "public", "index.html", "package.json", "package-lock.json", "vite.config.ts", "tsconfig.json")


def frontend_hash(root: Path = FRONTEND) -> str:
    h = hashlib.sha256()
    for name in SOURCES:
        path = root / name
        files = sorted(p for p in path.rglob("*") if p.is_file()) if path.is_dir() else [path] if path.is_file() else []
        for f in files:
            h.update(f.relative_to(root).as_posix().encode())
            h.update(b"\0")
            h.update(f.read_bytes())
            h.update(b"\0")
    return h.hexdigest()[:32]


def wait_and_open(url: str, timeout: float = 180.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            # /api/settings answers from this computer alone (/api/health also asks Google).
            urllib.request.urlopen(url.rstrip("/") + "/api/settings", timeout=5)
        except urllib.error.HTTPError:
            pass  # it answered (any status means the server is up)
        except OSError:
            time.sleep(1)
            continue
        webbrowser.open(url)
        return True
    return False


def main(argv: list[str]) -> int:
    if argv[:1] == ["frontend-hash"]:
        print(frontend_hash())
        return 0
    if len(argv) == 2 and argv[0] == "open":
        return 0 if wait_and_open(argv[1]) else 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
