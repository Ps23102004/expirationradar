"""sqlite3 store at ~/.expirationradar/pantry.db (stdlib, no ORM).

Phase 0 owns the schema (below) because scan.py, watcher.py and server.py all
build against it. The CRUD functions are Phase 1 — see plan §6.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from expirationradar.models import PantryItem, RecallMatch, WatcherRun

DATA_DIR = Path.home() / ".expirationradar"
DB_PATH = DATA_DIR / "pantry.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS pantry_items (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    product_name TEXT    NOT NULL,
    brand        TEXT    NOT NULL DEFAULT '',
    upc          TEXT    NOT NULL DEFAULT '',
    expiry_date  TEXT    NOT NULL DEFAULT '',   -- ISO 'YYYY-MM-DD'; '' = undated
    quantity     INTEGER NOT NULL DEFAULT 1,
    source       TEXT    NOT NULL DEFAULT 'USER',
    added_at     TEXT    NOT NULL,
    consumed_at  TEXT,                          -- NULL = still active
    notes        TEXT    NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_pantry_active_expiry
    ON pantry_items (expiry_date) WHERE consumed_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_pantry_upc ON pantry_items (upc);

-- One row per (item, recall_number). The UNIQUE constraint IS the watcher's
-- idempotency: a recall alerts exactly once, however many passes see it (§4).
CREATE TABLE IF NOT EXISTS recall_hits (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id             INTEGER NOT NULL REFERENCES pantry_items(id) ON DELETE CASCADE,
    recall_number       TEXT    NOT NULL,
    status              TEXT    NOT NULL,
    classification      TEXT    NOT NULL DEFAULT '',
    reason              TEXT    NOT NULL DEFAULT '',
    product_description TEXT    NOT NULL DEFAULT '',
    recalling_firm      TEXT    NOT NULL DEFAULT '',
    report_date         TEXT    NOT NULL DEFAULT '',
    matched_on          TEXT    NOT NULL DEFAULT '',
    url                 TEXT    NOT NULL DEFAULT '',
    first_seen_at       TEXT    NOT NULL,
    UNIQUE (item_id, recall_number)
);

CREATE TABLE IF NOT EXISTS watcher_runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ran_at        TEXT    NOT NULL,
    items_checked INTEGER NOT NULL DEFAULT 0,
    new_recalls   INTEGER NOT NULL DEFAULT 0,
    expiring_soon INTEGER NOT NULL DEFAULT 0,
    expired       INTEGER NOT NULL DEFAULT 0,
    error         TEXT    NOT NULL DEFAULT ''
);
"""


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Open (creating if needed) the pantry DB with the schema applied.

    Pass an explicit path in tests; production uses ~/.expirationradar/pantry.db.
    """
    path = Path(db_path) if db_path is not None else DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def add_item(conn: sqlite3.Connection, item: PantryItem) -> PantryItem:
    """Insert an item; returns it with `id` populated."""
    raise NotImplementedError("Phase 1 — see plan §6")


def list_items(conn: sqlite3.Connection, include_consumed: bool = False) -> list[PantryItem]:
    """All items, soonest expiry first. Undated items sort last."""
    raise NotImplementedError("Phase 1 — see plan §6")


def get_item(conn: sqlite3.Connection, item_id: int) -> PantryItem | None:
    raise NotImplementedError("Phase 1 — see plan §6")


def consume_item(conn: sqlite3.Connection, item_id: int) -> PantryItem | None:
    """Mark consumed (sets consumed_at). Keeps the row for history."""
    raise NotImplementedError("Phase 1 — see plan §6")


def delete_item(conn: sqlite3.Connection, item_id: int) -> bool:
    """Hard-delete a row (and its recall_hits). Returns False if absent."""
    raise NotImplementedError("Phase 1 — see plan §6")


def expiring_within(conn: sqlite3.Connection, days: int = 5) -> list[PantryItem]:
    """Active items expiring in the next `days` days (not yet expired)."""
    raise NotImplementedError("Phase 1 — see plan §6")


def expired_items(conn: sqlite3.Connection) -> list[PantryItem]:
    """Active items already past their expiry date."""
    raise NotImplementedError("Phase 1 — see plan §6")


def record_recall_hit(conn: sqlite3.Connection, item_id: int, match: RecallMatch) -> bool:
    """Insert a hit; returns True only if it's NEW (UNIQUE conflict = seen)."""
    raise NotImplementedError("Phase 1 — see plan §6")


def record_watcher_run(conn: sqlite3.Connection, run: WatcherRun) -> WatcherRun:
    raise NotImplementedError("Phase 1 — see plan §6")


def _demo() -> None:
    """Schema self-check — the one runnable thing Phase 0 actually implements."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        conn = connect(Path(tmp) / "t.db")
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"pantry_items", "recall_hits", "watcher_runs"} <= tables, tables
        conn.execute(
            "INSERT INTO pantry_items (product_name, added_at) VALUES ('Milk', '2026-08-20')"
        )
        item_id = conn.execute("SELECT id FROM pantry_items").fetchone()["id"]
        for _ in range(2):
            conn.execute(
                "INSERT OR IGNORE INTO recall_hits (item_id, recall_number, status, first_seen_at)"
                " VALUES (?, 'F-0123-2026', 'Ongoing', '2026-08-20')",
                (item_id,),
            )
        assert conn.execute("SELECT count(*) c FROM recall_hits").fetchone()["c"] == 1
        connect(Path(tmp) / "t.db")  # idempotent re-open
        print("pantry schema ok")


if __name__ == "__main__":
    _demo()
