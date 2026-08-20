"""One autonomous pass (§4): recall re-check + expiry-threshold crossings.

Idempotent: a recall_number already in `recall_hits` is not "new", so a fresh
recall alerts exactly once however many passes see it. Network down → log the
error, still run the local-only expiry check; the scheduled job never crashes.

Writes: sqlite (recall_hits, watcher_runs), ~/.expirationradar/last_digest.json,
append-only ~/.expirationradar/watch.log.
"""

from __future__ import annotations

from pathlib import Path

from expirationradar.models import Digest, WatcherRun
from expirationradar.pantry import DATA_DIR

DIGEST_PATH = DATA_DIR / "last_digest.json"
LOG_PATH = DATA_DIR / "watch.log"
PLIST_PATH = Path.home() / "Library/LaunchAgents/com.parth.expirationradar.plist"


def run_once(days: int = 5) -> WatcherRun:
    """One idempotent pass. Writes the digest file and returns the run record."""
    raise NotImplementedError("Phase 3 — see plan §4")


def load_digest() -> Digest:
    """Read last_digest.json — the single source for CLI `digest` and
    GET /api/digest. Missing file → an empty Digest, not an error."""
    raise NotImplementedError("Phase 3 — see plan §4")


def install() -> Path:
    """Write + `launchctl load` the daily-08:30 LaunchAgent plist."""
    raise NotImplementedError("Phase 3 — see plan §4")


def uninstall() -> bool:
    """Unload and remove the plist. False if it wasn't installed."""
    raise NotImplementedError("Phase 3 — see plan §4")
