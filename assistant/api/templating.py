"""Shared Jinja2 environment, static-file location and cache-busting asset version.

Where this sits: HTTP layer helper used by `api.main` (mounts `/static`) and
`api/routes/pages.py` (renders templates). The files themselves live in `assistant/web/`.

Key ideas:
- Content-hashed asset URLs (cache busting): the version is derived from the static
  files' bytes, so any edit changes the `?v=` query string and browsers cannot keep
  serving a stale `widget.css`/`widget.js` after a deploy.

Read next: `api/routes/pages.py`.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi.templating import Jinja2Templates

ASSISTANT_DIR = Path(__file__).resolve().parent.parent  # assistant/
WEB_DIR = ASSISTANT_DIR / "web"
STATIC_DIR = WEB_DIR / "static"

templates = Jinja2Templates(directory=str(WEB_DIR / "templates"))


def compute_asset_version() -> str:
    """Short hash of every top-level static file's name and content."""
    digest = hashlib.sha256()
    for path in sorted(STATIC_DIR.glob("*")):
        if path.is_file():
            digest.update(path.name.encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


# Computed once at import: static files do not change while the process runs.
ASSET_VERSION = compute_asset_version()
