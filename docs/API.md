# ExpirationRadar — JSON API contract (FROZEN, Phase 0)

One source of truth for the Phase 1 frontend and backend teams. The frontend
builds against `tests/fixtures/*.json` with no live backend; the backend makes
`server.py` return exactly these shapes.

**Frozen means frozen.** If a field genuinely has to change, change
`expirationradar/models.py`, regenerate the fixtures, and update this file in
the same commit — never one without the others.

- Server: `http://127.0.0.1:8000` (loopback only; `EXPIRATIONRADAR_PORT` overrides).
- All request and response bodies are `application/json`.
- Every response body is a JSON **object** (never a bare array).
- Errors: `{"error": "<human-readable message>"}` with 400 (bad request),
  404 (unknown route / unknown item id), 503 (a local model is unavailable and
  the endpoint genuinely can't answer), 500 (internal — details never leaked).

---

## Shared object shapes

These are `dataclasses.asdict()` of `expirationradar/models.py`. Field names and
nesting are identical everywhere they appear.

### `Field` — one extracted value plus its provenance

```json
{ "value": "2026-11-14", "source": "OCR", "confidence": 0.82 }
```

| key | type | notes |
|---|---|---|
| `value` | `string \| null` | `null` = nothing extracted. The UI renders "N/A", not an error. |
| `source` | `"BARCODE" \| "OCR" \| "VISION" \| "USER" \| "ESTIMATED"` | Drives the provenance badge. |
| `confidence` | `number` | `0.0`–`1.0`. `0.0` whenever `value` is `null`. |

Precedence, enforced server-side: `USER` > `BARCODE` > `OCR` > `VISION`. The
vision tier only ever fills a hole — it never overwrites a deterministic read.

`ESTIMATED` is a fifth, separate source used only by `POST /api/scan-receipt`:
an expiry date computed from typical shelf-life-from-purchase (see
`expirationradar/shelf_life.py`), not read off anything printed. It is never
mixed into `/api/scan`'s precedence chain above — it always carries a low
confidence (`~0.3`–`0.45`) so the UI can render it as visibly a guess, not a
real date.

### `RecallMatch`

```json
{
  "recall_number": "F-0455-2026",
  "status": "Ongoing",
  "classification": "Class I",
  "reason": "Undeclared milk allergen...",
  "product_description": "Crunchy Oat Granola, 12 oz box, UPC 0038000138416",
  "recalling_firm": "Northvale Foods, Inc.",
  "report_date": "20260805",
  "matched_on": "upc",
  "url": "https://api.fda.gov/food/enforcement.json?search=..."
}
```

- `status`: `"Ongoing" | "Completed" | "Terminated"` — **`Ongoing` is the loud
  one**, surface it prominently.
- `classification`: `"Class I" | "Class II" | "Class III"` (I = most serious).
- `report_date`: openFDA's raw `YYYYMMDD` string, not ISO. Format client-side.
- `matched_on`: `"upc"` (exact, high trust) or `"product_terms"` (fuzzy — show
  it as a possible match, not a certainty).
- `recall_number` is the identity key: the watcher dedupes on it, so an item
  alerts exactly once per recall.

### `ScanCandidate`

```json
{
  "product_name": { "...Field..." },
  "brand":        { "...Field..." },
  "upc":          { "...Field..." },
  "expiry_date":  { "...Field..." },
  "recalls":      [ { "...RecallMatch..." } ]
}
```

`expiry_date.value` is ISO `YYYY-MM-DD` (or `null`). `recalls` is `[]` when
clean — an empty list means "checked, no recall", which is also what openFDA's
HTTP 404 means.

### `PantryItem`

```json
{
  "product_name": "Crunchy Oat Granola",
  "brand": "Northvale Foods",
  "upc": "0038000138416",
  "expiry_date": "2026-11-14",
  "quantity": 1,
  "source": "BARCODE",
  "added_at": "2026-08-20T09:14:40",
  "consumed_at": null,
  "notes": "",
  "percent_remaining": 100.0,
  "safety_note": null,
  "id": 1
}
```

- Strings default to `""` (never `null`) — only `consumed_at`, `safety_note`
  and `id` are nullable. `consumed_at: null` = still active.
- `expiry_date: ""` = undated item; such items sort **last** in every list.
- `id` is `null` only on a not-yet-inserted item (a POST request body).
- `percent_remaining`: manual self-report (0-100), `100.0` until the user logs
  usage via `POST /api/pantry/{id}/log-usage` (Feature 1 — restock
  forecasting is deliberately not vision-based quantity estimation).
- `safety_note`: `{risk_note, advice}` — set only when the item is actually
  **expired** (past `expiry_date`, server-computed); `null` otherwise
  (Feature 2). See `expirationradar/safety.py` for the category lookup.

---

## Endpoints

### `POST /api/scan`

Request:

```json
{ "image_base64": "<base64 of a JPEG/PNG, no data: prefix>" }
```

Response `200` — a `ScanResult`. Fixture: **`tests/fixtures/scan_result.json`**
(candidate 1 = the barcode-resolved path with an active recall; candidate 2 =
the OCR/vision fallback path with `null` brand and `null` upc).

```json
{
  "candidates": [ { "...ScanCandidate..." } ],
  "scanned_at": "2026-08-20T09:14:03",
  "vision_available": true,
  "warnings": ["no barcode found for 1 of 2 candidates; ..."]
}
```

- `candidates` may be `[]` (nothing recognized) — that is a `200`, not an error.
- `vision_available: false` means Ollama is off or the vision model isn't
  pulled. Everything else still works; VISION-sourced fields will be `null`.
- `warnings` is for degradations worth telling the user about. Render them as a
  soft notice, never as a failure.
- Scanning **does not** write to the pantry. The confirm-edit UI POSTs the
  accepted candidates to `/api/pantry` afterwards.
- `400` if `image_base64` is missing or not valid base64.

### `POST /api/scan-receipt`

Same request/response shape as `POST /api/scan` — a `ScanResult` — for a
photo of a grocery **receipt** instead of a single product. Receipts print a
purchase date and item names, never an expiry date, so every candidate's
`expiry_date` is computed (`expirationradar/shelf_life.py`: purchase date +
typical shelf-life-from-purchase for that item's category) with
`source: "ESTIMATED"` and a low `confidence` (`~0.3`–`0.45`) — never treat an
`ESTIMATED` date as a real printed one.

```json
{ "image_base64": "<base64 of a JPEG/PNG receipt photo, no data: prefix>" }
```

- `candidates` — one per parsed receipt line item. `upc` and `brand` are
  always `null` (a receipt has no per-item barcode); `recalls` is always `[]`
  — the recall cross-reference is skipped for receipts (no barcode to match,
  and line-item names are too noisy for a reliable product-description
  search), noted in `warnings` instead of attempted unreliably.
- `warnings` always includes an estimate-disclosure notice ("review before
  adding") and the recall-skip notice above, plus any OCR/vision degradation.
- Purchase date used for the estimate: the first date `dates.py` finds
  anywhere in the receipt's OCR text, or today if none is found.
- Same one-by-one and bulk-add flows as `/api/scan` apply client-side — this
  endpoint only returns candidates, it never writes to the pantry.
- `400` if `image_base64` is missing or not valid base64.

### `GET /api/pantry`

Response `200`. Fixture: **`tests/fixtures/pantry.json`**.

```json
{ "items": [ { "...PantryItem..." } ] }
```

Active (non-consumed) items only by default, soonest expiry first, undated last.
`?all=true` includes consumed items.

### `POST /api/pantry`

Request: a `PantryItem` **without `id`**. Only `product_name` is required;
every other field falls back to its default. `added_at` is set server-side if
omitted.

```json
{ "product_name": "Whole Milk, 1 gal", "expiry_date": "2026-08-24", "source": "VISION" }
```

Response `201` — the created `PantryItem`, `id` populated.

`400` if `product_name` is empty or `expiry_date` isn't ISO `YYYY-MM-DD`/`""`.

### `POST /api/pantry/{id}/consume`

No request body. Response `200` — the updated `PantryItem` with `consumed_at`
set. The row is kept (history), not deleted. `404` if `id` doesn't exist.
Idempotent: consuming an already-consumed item returns it unchanged, still 200.

### `POST /api/pantry/{id}/log-usage`

Manual self-report of how much of an item is left (Feature 1 — not
vision-based quantity estimation). Request:

```json
{ "percent_remaining": 40.0 }
```

Response `200` — the updated `PantryItem`. `400` if `percent_remaining` is
missing, not a number, or outside `0`-`100`. `404` if `id` doesn't exist.
Each call also appends a `consumption_log` row; `days_until_empty` needs
`>= 2` logged reports before a forecast exists.

### `DELETE /api/pantry/{id}`

Hard-delete (the row and its recall hits). Response `200`:

```json
{ "deleted": true, "id": 3 }
```

`404` if `id` doesn't exist.

### `GET /api/digest`

`?days=<int>` — the expiring-soon threshold, default `5`.

Response `200` — a `Digest`. Fixture: **`tests/fixtures/digest.json`**.

```json
{
  "generated_at": "2026-08-20T08:30:02",
  "days": 5,
  "expiring_soon": [ { "...PantryItem..." } ],
  "expired":       [ { "...PantryItem..." } ],
  "new_recalls":   [ { "...RecallMatch..." } ],
  "restock_forecasts": [
    { "item_id": 4, "item_name": "Olive Oil", "days_until_empty": 3.5,
      "message": "Olive Oil is running low — about 3 days left for your household, might be worth restocking soon." }
  ]
}
```

- `expiring_soon` = active, dated, expiring within `days`, not yet past.
  `expired` = active and already past. The two never overlap. Every item in
  `expired` carries a populated `safety_note`; `expiring_soon` items don't.
- `new_recalls` = hits first seen by the most recent watcher pass — i.e. alerts
  the user has not been shown before.
- `restock_forecasts` (Feature 1) = every active item whose consumption rate
  projects `days_until_empty <= 7`, from `expirationradar.restock`. `message`
  is chain-phrased (`restock_nudge` in `chains.yaml`) with a plain-template
  fallback when the local model is unavailable — never absent, worst case a
  template string. `[]` when nothing's low, and `[]` (missing key treated the
  same) on a digest file written before this field existed.
- This is the same payload the watcher writes to
  `~/.expirationradar/last_digest.json` and the same one `expirationradar
  digest` prints. One shape, three surfaces.
- Never `404`s: a machine that has never run the watcher gets a `Digest` with
  empty lists and `generated_at: ""`.

### `GET /api/recipes`

`?days=<int>` — which items to cook down, default `5` (same window as digest).

Response `200`:

```json
{
  "suggestions": [
    {
      "title": "Spinach and granola breakfast bowl",
      "uses": ["Baby Spinach, 5 oz", "Whole Milk, 1 gal"],
      "steps": "Wilt the spinach, ..."
    }
  ],
  "available": true
}
```

- `available: false` + `suggestions: []` when Ollama is off or the model isn't
  pulled. That is a **`200`**, not a 503 — the Pantry view shows a clean "N/A"
  panel. `uses` entries are `product_name` strings that match pantry items.

### `GET /api/settings` / `POST /api/settings`

Plain single-row-per-key store (Feature 1). Only `household_size` exists
today, used to phrase restock nudges ("household of N").

```json
{ "household_size": 2 }
```

`GET` always `200`s (`1` if never set). `POST` takes the same shape, `200`
with the new value. `400` if `household_size` isn't an integer `>= 1`.

---

## Degradation rules (all teams)

The app must stay fully usable with Ollama completely off and the network down:

| Broken | Still works | Degrades to |
|---|---|---|
| Ollama off / vision model not pulled | barcode, OCR, dates, pantry, recalls, watcher | `vision_available: false`, VISION fields `null`, `/api/recipes` → `available: false`. `/api/scan-receipt` still returns candidates — the `parse_receipt` text chain degrades to a raw OCR line as the name, shelf-life estimate still runs |
| Network down | pantry, expiry checks, watcher's local pass | `recalls: []` plus a `warnings` entry; the scheduled job never crashes |
| openFDA returns HTTP 404 | everything | that is **"no recall"**, not an error — `recalls: []` |

---

## Note for Phase 2 — vision.py (SHIPPED)

`llm_ladder.ollama_client.chat` takes `images: list[str] | None = None` (base64
strings, no `data:` prefix) and, since Phase 2, `timeout: float = 120` — a cold
vision-model load can outrun the old hard-coded 120s, and a truncated call is
indistinguishable from Ollama being off. Both kwargs are backwards compatible;
llm-ladder's suite is green (189 passed).

`vision.py` calls `chat(model, prompt, images=[b64], timeout=300)` and catches
`OllamaConnectionError` (which `OllamaModelNotFoundError` subclasses) rather
than talking to `/api/chat` directly.

**Entry point:** `expirationradar.scan.run_scan(image_bytes) -> ScanResult` is
the only function `server.py` and `cli.py` call for a scan.

**Vision sentinel:** `vision.extract()` returns `None` when the tier can't run
(Ollama off, model not pulled, no `vision_extract` chain) and `[]` when it ran
and saw nothing. `run_scan` turns `None` into `vision_available: false` plus a
`warnings` entry containing `vision.UNAVAILABLE_NOTE`
(`"N/A (vision unavailable)"`) and never raises.

**Vision model:** `qwen3-vl:8b`, pulled and verified live on 2026-08-20 (the
plan's `qwen2.5vl:7b` was superseded — qwen3-vl is in the registry, §5 says
prefer it). Tag lives in `chains.yaml`.
