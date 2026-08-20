"""typer CLI: scan | pantry | recalls | digest | recipes | export | watch | serve."""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from expirationradar import openfda, pantry, receipt as receipt_module, recipes as recipes_module, safety, scan as scan_module
from expirationradar.models import PantryItem, to_json_dict
from expirationradar.server import read_digest

app = typer.Typer(help="Pantry photo → expiry dates + live FDA recall cross-reference.")
pantry_app = typer.Typer(help="Pantry inventory.")
app.add_typer(pantry_app, name="pantry")

console = Console()
error_console = Console(stderr=True)


def _print_field(label: str, field) -> None:
    value = field.value if field.value is not None else "N/A"
    console.print(f"  {label}: [bold]{value}[/bold] [dim]({field.source}, {field.confidence:.2f})[/dim]")


def _print_scan_result(result) -> None:
    if not result.candidates:
        console.print("No items recognized in that photo.")
    for i, candidate in enumerate(result.candidates, 1):
        console.print(f"[bold]Item {i}[/bold]")
        _print_field("Product", candidate.product_name)
        _print_field("Brand", candidate.brand)
        _print_field("UPC", candidate.upc)
        _print_field("Expires", candidate.expiry_date)
        for recall in candidate.recalls:
            style = "bold red" if recall.status == "Ongoing" else "yellow"
            console.print(f"  [{style}]RECALL ({recall.status}, {recall.classification}): {recall.reason}[/{style}]")
    for warning in result.warnings:
        console.print(f"[dim]Note: {warning}[/dim]")


@app.command()
def scan(
    image: str = typer.Argument(..., help="Path to a photo of the pantry item or receipt."),
    receipt: bool = typer.Option(
        False, "--receipt", help="Scan a grocery receipt instead of a single item (estimated expiry dates)."
    ),
    json_out: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation and add every candidate to the pantry."),
) -> None:
    """Scan a photo for product/expiry info and live FDA recalls, or a receipt with --receipt."""
    image_bytes = Path(image).read_bytes()
    result = receipt_module.parse_receipt(image_bytes) if receipt else scan_module.run_scan(image_bytes)

    if json_out:
        print(json.dumps(to_json_dict(result), indent=2))
    else:
        _print_scan_result(result)

    if not result.candidates:
        return
    if not yes:
        if json_out:
            # --json is for scripts; a scripted caller has no stdin to answer
            # a confirm prompt with, so require --yes explicitly instead.
            return
        if not typer.confirm("Add these items to your pantry?", default=False):
            return

    conn = pantry.connect()
    try:
        for candidate in result.candidates:
            item = pantry.add_item(
                conn,
                PantryItem(
                    product_name=candidate.product_name.value or "Unknown item",
                    brand=candidate.brand.value or "",
                    upc=candidate.upc.value or "",
                    expiry_date=candidate.expiry_date.value or "",
                    source=candidate.product_name.source,
                ),
            )
            console.print(f"Added [bold]{item.product_name}[/bold] to pantry (id={item.id}).")
    finally:
        conn.close()


@pantry_app.command("list")
def pantry_list(all: bool = False, json_out: bool = typer.Option(False, "--json")) -> None:
    """List pantry items, soonest expiry first."""
    conn = pantry.connect()
    try:
        items = pantry.list_items(conn, include_consumed=all)
    finally:
        conn.close()
    safety.annotate_expired(items)

    if json_out:
        print(json.dumps(to_json_dict(items), indent=2))
        return

    table = Table(title="Pantry")
    for column in ("ID", "Product", "Brand", "Expires", "Qty", "Source"):
        table.add_column(column)
    for item in items:
        table.add_row(str(item.id), item.product_name, item.brand, item.expiry_date or "N/A", str(item.quantity), item.source)
    console.print(table)
    for item in items:
        if item.safety_note:
            console.print(f"  [red]! {item.product_name}:[/red] {item.safety_note['risk_note']} {item.safety_note['advice']}")


@pantry_app.command("add")
def pantry_add(product_name: str, expiry: str = "", upc: str = "", brand: str = "") -> None:
    """Add an item directly (no scan)."""
    conn = pantry.connect()
    try:
        item = pantry.add_item(conn, PantryItem(product_name=product_name, expiry_date=expiry, upc=upc, brand=brand))
    finally:
        conn.close()
    console.print(f"Added [bold]{item.product_name}[/bold] (id={item.id}).")


@pantry_app.command("rm")
def pantry_rm(item_id: int) -> None:
    """Delete a pantry item."""
    conn = pantry.connect()
    try:
        deleted = pantry.delete_item(conn, item_id)
    finally:
        conn.close()
    if not deleted:
        error_console.print(f"No pantry item with id {item_id}.")
        raise typer.Exit(1)
    console.print(f"Deleted item {item_id}.")


@pantry_app.command("consume")
def pantry_consume(item_id: int) -> None:
    """Mark a pantry item consumed."""
    conn = pantry.connect()
    try:
        item = pantry.consume_item(conn, item_id)
    finally:
        conn.close()
    if item is None:
        error_console.print(f"No pantry item with id {item_id}.")
        raise typer.Exit(1)
    console.print(f"Consumed [bold]{item.product_name}[/bold].")


@app.command()
def recalls() -> None:
    """Re-check every active pantry item against openFDA right now."""
    conn = pantry.connect()
    try:
        items = pantry.list_items(conn)
        found = 0
        for item in items:
            matches = openfda.search_by_upc(item.upc) if item.upc else []
            if not matches:
                matches = openfda.search_by_terms(item.product_name, item.brand)
            for match in matches:
                is_new = pantry.record_recall_hit(conn, item.id, match)
                found += 1
                flag = "[bold red]NEW[/bold red]" if is_new else "[dim]seen[/dim]"
                console.print(f"{flag} {item.product_name}: {match.status} {match.classification} — {match.reason}")
    finally:
        conn.close()
    if found == 0:
        console.print("No recalls found for anything in your pantry.")


@app.command()
def digest(days: int = 5) -> None:
    """Expiring-soon digest (reads ~/.expirationradar/last_digest.json)."""
    payload = read_digest(days)
    if not payload["generated_at"]:
        console.print("The watcher hasn't run yet. Run `expirationradar watch` to generate a digest.")
        return
    console.print(f"Digest as of {payload['generated_at']}")
    console.print(f"  Expired: {len(payload['expired'])}")
    console.print(f"  Expiring soon: {len(payload['expiring_soon'])}")
    console.print(f"  New recalls: {len(payload['new_recalls'])}")
    console.print(f"  Restock soon: {len(payload.get('restock_forecasts', []))}")
    for item in payload["expired"]:
        console.print(f"  [red]EXPIRED[/red] {item['product_name']} ({item['expiry_date']})")
        note = item.get("safety_note")
        if note:
            console.print(f"    [dim]{note['risk_note']} {note['advice']}[/dim]")
    for item in payload["expiring_soon"]:
        console.print(f"  [yellow]soon[/yellow] {item['product_name']} ({item['expiry_date']})")
    for recall in payload["new_recalls"]:
        console.print(f"  [bold red]RECALL[/bold red] {recall['product_description']}")
    for forecast in payload.get("restock_forecasts", []):
        console.print(f"  [cyan]restock[/cyan] {forecast['message']}")


@app.command()
def recipes(days: int = 5) -> None:
    """Use-it-up suggestions for items expiring within `days`."""
    conn = pantry.connect()
    try:
        items = pantry.expiring_within(conn, days=days)
    finally:
        conn.close()
    try:
        suggestions = recipes_module.suggest(items)
    except recipes_module.RecipesUnavailable:
        console.print("Recipe suggestions aren't available right now.")
        return
    if not suggestions:
        console.print("Nothing to cook down right now.")
        return
    for suggestion in suggestions:
        console.print(f"[bold]{suggestion.title}[/bold]")
        console.print(f"  Uses: {', '.join(suggestion.uses)}")
        console.print(f"  {suggestion.steps}")


@app.command()
def export(days: int = 5) -> None:
    """Markdown shopping list of expiring/expired items. CLI-only."""
    conn = pantry.connect()
    try:
        expiring = pantry.expiring_within(conn, days=days)
        expired = pantry.expired_items(conn)
    finally:
        conn.close()

    lines = ["# Shopping list", ""]
    for item in expired:
        lines.append(f"- [ ] {item.product_name} (expired {item.expiry_date})")
        note = safety.safety_note(item.product_name)
        lines.append(f"  - {note['risk_note']} {note['advice']}")
    for item in expiring:
        lines.append(f"- [ ] {item.product_name} (expires {item.expiry_date})")
    if not (expired or expiring):
        lines.append("Nothing expiring soon — pantry's in good shape.")
    print("\n".join(lines))


@app.command()
def watch(install: bool = False, uninstall: bool = False) -> None:
    """Run one autonomous pass; --install/--uninstall manage the daily job."""
    from expirationradar import watcher

    if install:
        path = watcher.install()
        console.print(f"Installed launchd job at {path}")
        return
    if uninstall:
        removed = watcher.uninstall()
        console.print("Uninstalled." if removed else "Was not installed.")
        return
    run = watcher.run_once()
    console.print(
        f"Watch pass complete: {run.new_recalls} new recalls, "
        f"{run.expiring_soon} expiring soon, {run.expired} expired."
    )


@app.command()
def serve(
    port: int = typer.Option(None, "--port", min=1, max=65535, help="Local HTTP port (default: $EXPIRATIONRADAR_PORT or 8000)."),
) -> None:
    """Start the local web UI on 127.0.0.1."""
    from expirationradar import server

    server.main(port)


if __name__ == "__main__":
    app()
