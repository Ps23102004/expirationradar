"""stdlib ThreadingHTTPServer, 127.0.0.1-only — fairdeal/server.py skeleton.

Serves web/ plus the frozen JSON API in docs/API.md:
    POST   /api/scan               {image_base64} -> ScanResult
    GET    /api/pantry             ?all=true       -> {"items": [PantryItem]}
    POST   /api/pantry             PantryItem     -> PantryItem
    POST   /api/pantry/{id}/consume               -> PantryItem
    DELETE /api/pantry/{id}                       -> {"deleted": true}
    GET    /api/digest             ?days=5        -> Digest
    GET    /api/recipes            ?days=5        -> {"suggestions": [...], "available": bool}
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
import sys
import traceback
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from expirationradar import pantry, recipes, scan
from expirationradar.models import PantryItem, to_json_dict

DEFAULT_PORT = 8000
PORT = int(os.environ.get("EXPIRATIONRADAR_PORT", DEFAULT_PORT))
WEB_DIR = (Path(__file__).resolve().parent.parent / "web").resolve()

_ALLOWED_HOSTS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}
_ALLOWED_ORIGINS = {f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"}

_STATIC_ROUTES = {"/": "index.html", "/index.html": "index.html"}

# base64-encoded JPEG/PNG photo upload cap — fairdeal uses the same ceiling
# for its PDF uploads; this is a loopback-only server, so it guards against a
# misbehaving local client, not a remote attacker.
_MAX_BODY_BYTES = 15_000_000

DIGEST_PATH = pantry.DATA_DIR / "last_digest.json"

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_PANTRY_ITEM_RE = re.compile(r"^/api/pantry/(\d+)$")
_PANTRY_CONSUME_RE = re.compile(r"^/api/pantry/(\d+)/consume$")


class _BadRequestError(Exception):
    """Raised for client-side request problems that should map to HTTP 400."""


def _empty_digest(days: int) -> dict:
    return {"generated_at": "", "days": days, "expiring_soon": [], "expired": [], "new_recalls": []}


def read_digest(days: int = 5) -> dict:
    """Read ~/.expirationradar/last_digest.json (written by the Phase 3 watcher).

    A missing or corrupt file is an empty Digest, never an error — a machine
    that has never run the watcher still gets a 200 (docs/API.md). Shared by
    GET /api/digest and `expirationradar digest`.
    """
    try:
        raw = DIGEST_PATH.read_text()
    except FileNotFoundError:
        return _empty_digest(days)
    try:
        return json.loads(raw)
    except ValueError:
        return _empty_digest(days)


def _validate_pantry_item(body: dict) -> PantryItem:
    """A PantryItem without `id` — only product_name is required (docs/API.md)."""
    product_name = body.get("product_name")
    if not isinstance(product_name, str) or not product_name.strip():
        raise _BadRequestError("'product_name' is required")

    expiry_date = body.get("expiry_date", "")
    if not isinstance(expiry_date, str) or (expiry_date and not _ISO_DATE_RE.match(expiry_date)):
        raise _BadRequestError("'expiry_date' must be ISO YYYY-MM-DD or empty")

    try:
        quantity = int(body.get("quantity", 1))
    except (TypeError, ValueError):
        raise _BadRequestError("'quantity' must be an integer")

    return PantryItem(
        product_name=product_name.strip(),
        brand=str(body.get("brand") or ""),
        upc=str(body.get("upc") or ""),
        expiry_date=expiry_date,
        quantity=quantity,
        source=str(body.get("source") or "USER"),
        added_at=str(body.get("added_at") or ""),
        notes=str(body.get("notes") or ""),
    )


def _recipes_payload(days: int) -> dict:
    conn = pantry.connect()
    try:
        items = pantry.expiring_within(conn, days=days)
    finally:
        conn.close()
    try:
        suggestions = recipes.suggest(items)
    except NotImplementedError:
        # recipes.py is a Phase 3 stub — degrade exactly like "Ollama off"
        # does per docs/API.md: 200, available: false, never a 503.
        return {"suggestions": [], "available": False}
    return {"suggestions": to_json_dict(suggestions), "available": True}


def _scan_payload(body: dict) -> dict:
    image_b64 = body.get("image_base64")
    if not isinstance(image_b64, str) or not image_b64.strip():
        raise _BadRequestError("'image_base64' is required")
    try:
        image_bytes = base64.b64decode(image_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise _BadRequestError(f"'image_base64' is not valid base64: {exc}")
    return to_json_dict(scan.run_scan(image_bytes))


def _int_query_param(query: dict, name: str, default: int) -> int:
    values = query.get(name)
    if not values:
        return default
    try:
        return int(values[0])
    except ValueError:
        raise _BadRequestError(f"'{name}' must be an integer")


class Handler(BaseHTTPRequestHandler):
    server_version = "expirationradar/0.1"

    # -- trust-boundary check -------------------------------------------------

    def _origin_allowed(self) -> bool:
        host = self.headers.get("Host", "")
        if host not in _ALLOWED_HOSTS:
            return False
        origin = self.headers.get("Origin")
        if origin is not None and origin not in _ALLOWED_ORIGINS:
            return False
        return True

    # -- helpers ----------------------------------------------------------

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self) -> dict:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise _BadRequestError("missing Content-Length header")
        try:
            length = int(raw_length)
        except (TypeError, ValueError):
            raise _BadRequestError("malformed Content-Length header")
        if length < 0:
            raise _BadRequestError("negative Content-Length header")
        if length > _MAX_BODY_BYTES:
            raise _BadRequestError(f"request body exceeds {_MAX_BODY_BYTES} byte limit")
        raw = self.rfile.read(length) if length else b""
        body = json.loads(raw or b"{}")
        if not isinstance(body, dict):
            raise _BadRequestError(f"request body must be a JSON object, got {type(body).__name__}")
        return body

    def _serve_static(self, url_path: str) -> None:
        rel = _STATIC_ROUTES.get(url_path)
        if rel is None:
            rel = unquote(url_path).lstrip("/")
        target = (WEB_DIR / rel).resolve()
        if WEB_DIR not in target.parents and target != WEB_DIR:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        if not target.is_file():
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        content_type = {
            ".html": "text/html",
            ".css": "text/css",
            ".js": "application/javascript",
        }.get(target.suffix, "application/octet-stream")
        body = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # -- dispatch -----------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler naming
        self._handle(self._route_get)

    def do_POST(self) -> None:  # noqa: N802
        self._handle(self._route_post)

    def do_DELETE(self) -> None:  # noqa: N802
        self._handle(self._route_delete)

    def _handle(self, route_fn) -> None:
        if not self._origin_allowed():
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "forbidden host/origin"})
            return
        try:
            route_fn()
        except _BadRequestError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except json.JSONDecodeError:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "malformed JSON body"})
        except Exception:  # noqa: BLE001 - never leak internals to the client
            traceback.print_exc(file=sys.stderr)
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "internal server error"})

    def _route_get(self) -> None:
        parsed = urlsplit(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path == "/api/pantry":
            include_consumed = (query.get("all", [""])[0]).lower() == "true"
            conn = pantry.connect()
            try:
                items = pantry.list_items(conn, include_consumed=include_consumed)
            finally:
                conn.close()
            self._send_json(HTTPStatus.OK, {"items": to_json_dict(items)})
            return

        if path == "/api/digest":
            days = _int_query_param(query, "days", 5)
            self._send_json(HTTPStatus.OK, read_digest(days))
            return

        if path == "/api/recipes":
            days = _int_query_param(query, "days", 5)
            self._send_json(HTTPStatus.OK, _recipes_payload(days))
            return

        if path.startswith("/api/"):
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return

        self._serve_static(path)

    def _route_post(self) -> None:
        path = urlsplit(self.path).path

        if path == "/api/scan":
            body = self._read_json_body()
            self._send_json(HTTPStatus.OK, _scan_payload(body))
            return

        if path == "/api/pantry":
            item = _validate_pantry_item(self._read_json_body())
            conn = pantry.connect()
            try:
                created = pantry.add_item(conn, item)
            finally:
                conn.close()
            self._send_json(HTTPStatus.CREATED, to_json_dict(created))
            return

        match = _PANTRY_CONSUME_RE.match(path)
        if match:
            conn = pantry.connect()
            try:
                updated = pantry.consume_item(conn, int(match.group(1)))
            finally:
                conn.close()
            if updated is None:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "no such pantry item"})
                return
            self._send_json(HTTPStatus.OK, to_json_dict(updated))
            return

        self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def _route_delete(self) -> None:
        path = urlsplit(self.path).path
        match = _PANTRY_ITEM_RE.match(path)
        if not match:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        item_id = int(match.group(1))
        conn = pantry.connect()
        try:
            deleted = pantry.delete_item(conn, item_id)
        finally:
            conn.close()
        if not deleted:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "no such pantry item"})
            return
        self._send_json(HTTPStatus.OK, {"deleted": True, "id": item_id})

    def log_message(self, fmt: str, *args) -> None:  # quieter default logging
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))


def main(port: int | None = None) -> None:
    """Bind 127.0.0.1:port and serve forever.

    `port=None` uses the module-level PORT ($EXPIRATIONRADAR_PORT or 8000,
    resolved at import time). An explicit `port` (the CLI's `--port`) is
    applied here instead, since `cli.py` may import this module — freezing
    PORT/_ALLOWED_HOSTS from the environment at that moment — before the
    `--port` option is even parsed.
    """
    global PORT, _ALLOWED_HOSTS, _ALLOWED_ORIGINS
    if port is not None:
        PORT = port
        _ALLOWED_HOSTS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}
        _ALLOWED_ORIGINS = {f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"}

    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Serving on http://127.0.0.1:{PORT}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
