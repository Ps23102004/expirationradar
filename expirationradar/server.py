"""stdlib ThreadingHTTPServer, 127.0.0.1-only — fairdeal/server.py skeleton.

Serves web/ plus the frozen JSON API in docs/API.md:
    POST   /api/scan               {image_base64} -> ScanResult
    GET    /api/pantry                            -> {"items": [PantryItem]}
    POST   /api/pantry             PantryItem     -> PantryItem
    POST   /api/pantry/{id}/consume               -> PantryItem
    DELETE /api/pantry/{id}                       -> {"deleted": true}
    GET    /api/digest             ?days=5        -> Digest
    GET    /api/recipes            ?days=5        -> {"suggestions": [...]}
"""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_PORT = 8000
PORT = int(os.environ.get("EXPIRATIONRADAR_PORT", DEFAULT_PORT))
WEB_DIR = (Path(__file__).resolve().parent.parent / "web").resolve()


def main() -> None:
    """Bind 127.0.0.1:PORT and serve forever."""
    raise NotImplementedError("Phase 2 — see plan §6 and docs/API.md")


if __name__ == "__main__":
    main()
