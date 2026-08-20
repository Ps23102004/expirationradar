"""One idempotent autonomous pass (plan §4): recall re-check + expiry-threshold
crossings. `cli.py`'s `watch` command is the only caller of `run_once`/
`install`/`uninstall`.

The single most important property here: this must never crash. A dead
network only degrades the recall re-check (logged into `WatcherRun.error`);
the expiry check is pure local sqlite date math and always completes, so a
scheduled launchd job never dies mid-pass.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from expirationradar import openfda, pantry, restock, safety, server
from expirationradar.models import Digest, RecallMatch, WatcherRun, to_json_dict

DEFAULT_DAYS = 5
WATCH_LOG = pantry.DATA_DIR / "watch.log"

LABEL = "com.parth.expirationradar"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"

_PLIST_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{label}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{exe}</string>
        <string>watch</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>8</integer>
        <key>Minute</key>
        <integer>30</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>{log}</string>
    <key>StandardErrorPath</key>
    <string>{log}</string>
</dict>
</plist>
"""


def _recall_recheck(conn, items: list) -> tuple[list[RecallMatch], list[str]]:
    """Every active item -> openFDA. Returns (newly-recorded hits, errors).

    "New" is exactly what `pantry.record_recall_hit` reports: the
    `UNIQUE(item_id, recall_number)` constraint means a recall alerts once,
    however many passes see it. Any per-item network failure is logged and
    skipped — it never stops the loop or the pass.
    """
    new_hits: list[RecallMatch] = []
    errors: list[str] = []
    for item in items:
        try:
            matches = openfda.search_by_upc(item.upc) if item.upc else []
            if not matches:
                matches = openfda.search_by_terms(item.product_name, item.brand)
        except Exception as exc:  # noqa: BLE001 - network/API failure, never crash the pass
            errors.append(f"{item.product_name} (id={item.id}): {exc}")
            continue
        for match in matches:
            if pantry.record_recall_hit(conn, item.id, match):
                new_hits.append(match)
    return new_hits, errors


def _write_digest(digest: Digest) -> None:
    server.DIGEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    server.DIGEST_PATH.write_text(json.dumps(to_json_dict(digest), indent=2))


def _append_log(run: WatcherRun) -> None:
    WATCH_LOG.parent.mkdir(parents=True, exist_ok=True)
    line = (
        f"{run.ran_at} items={run.items_checked} new_recalls={run.new_recalls} "
        f"expiring_soon={run.expiring_soon} expired={run.expired}"
    )
    if run.error:
        line += f" error={run.error}"
    with WATCH_LOG.open("a") as f:
        f.write(line + "\n")


def _notify(new_hits: list[RecallMatch], newly_crossed: list) -> None:
    """Best-effort macOS notification, only called when there's something new.
    Silently no-ops off macOS, sandboxed, or any other osascript failure."""
    parts = []
    if new_hits:
        parts.append(f"{len(new_hits)} new recall(s)")
    if newly_crossed:
        parts.append(f"{len(newly_crossed)} item(s) newly expiring/expired")
    message = " and ".join(parts)
    try:
        subprocess.run(
            ["osascript", "-e", f'display notification "{message}" with title "ExpirationRadar"'],
            check=False,
            capture_output=True,
            timeout=5,
        )
    except Exception:  # noqa: BLE001 - notification is best-effort, never fatal
        pass


def run_once(days: int = DEFAULT_DAYS) -> WatcherRun:
    """One idempotent pass. Always writes a valid digest + log line, even if
    the network step failed."""
    old_digest = server.read_digest(days)
    old_ids = {i["id"] for i in old_digest.get("expiring_soon", [])} | {
        i["id"] for i in old_digest.get("expired", [])
    }

    conn = pantry.connect()
    try:
        items = pantry.list_items(conn)
        new_hits, errors = _recall_recheck(conn, items)

        # Local-only sqlite date math — runs unconditionally, even if the
        # network step above failed entirely.
        expiring_soon = pantry.expiring_within(conn, days=days)
        expired = pantry.expired_items(conn)
        safety.annotate_expired(expired)  # every one of these IS expired

        digest = Digest(
            generated_at=datetime.now().isoformat(timespec="seconds"),
            days=days,
            expiring_soon=expiring_soon,
            expired=expired,
            new_recalls=new_hits,
            restock_forecasts=restock.restock_digest(),
        )
        _write_digest(digest)

        run = WatcherRun(
            items_checked=len(items),
            new_recalls=len(new_hits),
            expiring_soon=len(expiring_soon),
            expired=len(expired),
            error="; ".join(errors),
        )
        pantry.record_watcher_run(conn, run)
    finally:
        conn.close()

    _append_log(run)

    newly_crossed = [i for i in (expiring_soon + expired) if i.id not in old_ids]
    if new_hits or newly_crossed:
        _notify(new_hits, newly_crossed)

    return run


def install() -> Path:
    """Write + load the daily-08:30 launchd job. `sys.executable`-relative so
    it works from whatever venv the package is installed in."""
    # No .resolve(): a venv's `python` is commonly a symlink out to the system
    # interpreter, and resolving it would land outside the venv's bin/ dir —
    # where the `expirationradar` console script doesn't exist. sys.executable
    # itself already reports the venv-local bin path.
    exe = Path(sys.executable).parent / "expirationradar"
    plist = _PLIST_TEMPLATE.format(label=LABEL, exe=exe, log=WATCH_LOG)
    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    PLIST_PATH.write_text(plist)
    subprocess.run(["launchctl", "load", str(PLIST_PATH)], check=False, capture_output=True)
    return PLIST_PATH


def uninstall() -> bool:
    """Unload + remove the plist. Returns False if it wasn't installed."""
    if not PLIST_PATH.exists():
        return False
    subprocess.run(["launchctl", "unload", str(PLIST_PATH)], check=False, capture_output=True)
    PLIST_PATH.unlink()
    return True


if __name__ == "__main__":
    run = run_once()
    print(f"watch: {run.new_recalls} new recalls, {run.expiring_soon} expiring soon, {run.expired} expired")
