"""typer CLI: scan | pantry | recalls | digest | recipes | export | watch | serve."""

from __future__ import annotations

import typer

app = typer.Typer(help="Pantry photo → expiry dates + live FDA recall cross-reference.")
pantry_app = typer.Typer(help="Pantry inventory.")
app.add_typer(pantry_app, name="pantry")


@app.command()
def scan(image: str, save: bool = False) -> None:
    """Scan a photo; --save writes the confirmed candidates into the pantry."""
    raise NotImplementedError("Phase 2 — see plan §6")


@pantry_app.command("list")
def pantry_list(all: bool = False) -> None:
    """List pantry items, soonest expiry first."""
    raise NotImplementedError("Phase 1 — see plan §6")


@pantry_app.command("add")
def pantry_add(product_name: str, expiry: str = "", upc: str = "", brand: str = "") -> None:
    raise NotImplementedError("Phase 1 — see plan §6")


@pantry_app.command("rm")
def pantry_rm(item_id: int) -> None:
    raise NotImplementedError("Phase 1 — see plan §6")


@pantry_app.command("consume")
def pantry_consume(item_id: int) -> None:
    raise NotImplementedError("Phase 1 — see plan §6")


@app.command()
def recalls() -> None:
    """Re-check every active pantry item against openFDA right now."""
    raise NotImplementedError("Phase 3 — see plan §6")


@app.command()
def digest(days: int = 5) -> None:
    """Expiring-soon digest (reads ~/.expirationradar/last_digest.json)."""
    raise NotImplementedError("Phase 3 — see plan §6")


@app.command()
def recipes(days: int = 5) -> None:
    """Use-it-up suggestions for items expiring within `days`."""
    raise NotImplementedError("Phase 3 — see plan §6")


@app.command()
def export(days: int = 5) -> None:
    """Markdown shopping list of expiring/expired items. CLI-only."""
    raise NotImplementedError("Phase 3 — see plan §6")


@app.command()
def watch(install: bool = False, uninstall: bool = False) -> None:
    """Run one autonomous pass; --install/--uninstall manage the daily job."""
    raise NotImplementedError("Phase 3 — see plan §4")


@app.command()
def serve() -> None:
    """Start the local web UI on 127.0.0.1."""
    raise NotImplementedError("Phase 2 — see plan §6")


if __name__ == "__main__":
    app()
