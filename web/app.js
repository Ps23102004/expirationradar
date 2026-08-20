function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function errorMessageFor(response) {
  try {
    const body = await response.json();
    if (body && typeof body.error === "string") {
      return body.error;
    }
  } catch {
    // The response did not contain JSON.
  }

  return `Request failed with status ${response.status}`;
}

function safeListingUrl(value) {
  try {
    const url = new URL(value);
    return url.protocol === "http:" || url.protocol === "https:" ? url.href : null;
  } catch {
    return null;
  }
}

// ---- App router ----

function switchView(name) {
  const requestedView = document.querySelector(`[data-view="${name}"]`);
  if (!requestedView) {
    return;
  }

  document.querySelectorAll("[data-view]").forEach((view) => {
    view.classList.toggle("is-active", view === requestedView);
  });

  document.querySelectorAll(".module-tab[data-target]").forEach((tab) => {
    const isActive = tab.dataset.target === name;
    tab.classList.toggle("is-active", isActive);
    tab.setAttribute("aria-selected", String(isActive));
  });
}

// ---- Scan view ----

// Fixture fallback kept the Scan view demoable before the backend existed.
// The backend has been live since Phase 2 — DEV_MODE off means a failed
// request surfaces as a real error instead of silently faking success.
const DEV_MODE = false;

const FIELD_DEFINITIONS = [
  { key: "product_name", label: "Product name", inputType: "text" },
  { key: "brand", label: "Brand", inputType: "text" },
  { key: "upc", label: "UPC", inputType: "text" },
  { key: "expiry_date", label: "Expiration date", inputType: "date" }
];

const scanState = {
  rawResult: null,
  candidates: [],
  warnings: [],
  editing: null,
  adding: new Set(),
  added: new Set(),
  messages: new Map()
};

const scanPhotoInput = document.querySelector("#scan-photo");
const scanButton = document.querySelector("#scan-button");
const scanStatus = document.querySelector("#scan-status");
const scanResults = document.querySelector("#scan-results");
const photoPreview = document.querySelector("#photo-preview");
const photoPreviewImage = document.querySelector("#photo-preview-image");
const removePhotoButton = document.querySelector("#remove-photo");
const toastRegion = document.querySelector("#toast-region");

let previewObjectUrl = null;

function cloneData(value) {
  if (typeof structuredClone === "function") {
    return structuredClone(value);
  }

  return JSON.parse(JSON.stringify(value));
}

function normalizeField(field, fallbackSource) {
  return {
    value: field?.value ?? null,
    source: field?.source ?? fallbackSource,
    confidence: Number.isFinite(Number(field?.confidence))
      ? Number(field.confidence)
      : 0
  };
}

function normalizeCandidate(candidate) {
  return {
    product_name: normalizeField(candidate.product_name, "VISION"),
    brand: normalizeField(candidate.brand, "VISION"),
    upc: normalizeField(candidate.upc, "BARCODE"),
    expiry_date: normalizeField(candidate.expiry_date, "OCR"),
    recalls: Array.isArray(candidate.recalls) ? cloneData(candidate.recalls) : []
  };
}

function readFileAsBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();

    reader.addEventListener("load", () => {
      const result = String(reader.result || "");
      const commaIndex = result.indexOf(",");

      if (commaIndex === -1) {
        reject(new Error("Could not read the selected image."));
        return;
      }

      resolve(result.slice(commaIndex + 1));
    });

    reader.addEventListener("error", () => {
      reject(reader.error || new Error("Could not read the selected image."));
    });

    reader.readAsDataURL(file);
  });
}

function sourceClass(source) {
  const normalized = String(source || "").toLowerCase();
  return ["barcode", "ocr", "vision", "user"].includes(normalized)
    ? `badge--${normalized}`
    : "badge--user";
}

function confidenceLabel(confidence) {
  const score = Number(confidence);
  const safeScore = Number.isFinite(score) ? Math.min(1, Math.max(0, score)) : 0;
  return `${Math.round(safeScore * 100)}% confidence`;
}

function formatReportDate(value) {
  const rawDate = String(value || "");
  if (!/^\d{8}$/.test(rawDate)) {
    return rawDate || "Date unavailable";
  }

  return `${rawDate.slice(0, 4)}-${rawDate.slice(4, 6)}-${rawDate.slice(6, 8)}`;
}

function displayFieldValue(field) {
  if (field.value === null || String(field.value).trim() === "") {
    return "N/A";
  }

  return String(field.value);
}

function renderField(candidateIndex, definition, field) {
  const isEditing =
    scanState.editing?.candidateIndex === candidateIndex &&
    scanState.editing?.fieldName === definition.key;

  const source = String(field.source || "USER").toUpperCase();
  const fieldId = `candidate-${candidateIndex}-${definition.key}`;

  if (isEditing) {
    return `
      <div class="candidate-field candidate-field--editing" data-field="${escapeHtml(definition.key)}">
        <div class="candidate-field__label-row">
          <label class="candidate-field__label" for="${fieldId}-input">${escapeHtml(definition.label)}</label>
          <span class="source-badge ${sourceClass(source)}">${escapeHtml(source)}</span>
        </div>
        <input
          class="candidate-field__input"
          id="${fieldId}-input"
          type="${definition.inputType}"
          value="${escapeHtml(field.value ?? "")}"
          data-edit-input
          data-candidate-index="${candidateIndex}"
          data-field-name="${escapeHtml(definition.key)}"
          autocomplete="off"
        >
        <span class="candidate-field__confidence">${escapeHtml(confidenceLabel(field.confidence))}</span>
      </div>
    `;
  }

  return `
    <div class="candidate-field" data-field="${escapeHtml(definition.key)}">
      <div class="candidate-field__label-row">
        <span class="candidate-field__label" id="${fieldId}-label">${escapeHtml(definition.label)}</span>
        <span class="source-badge ${sourceClass(source)}">${escapeHtml(source)}</span>
      </div>
      <button
        class="candidate-field__value"
        type="button"
        data-action="edit-field"
        data-candidate-index="${candidateIndex}"
        data-field-name="${escapeHtml(definition.key)}"
        aria-labelledby="${fieldId}-label"
        aria-label="Edit ${escapeHtml(definition.label)}: ${escapeHtml(displayFieldValue(field))}"
      >
        <span>${escapeHtml(displayFieldValue(field))}</span>
        <span class="edit-mark" aria-hidden="true">Edit</span>
      </button>
      <span class="candidate-field__confidence">${escapeHtml(confidenceLabel(field.confidence))}</span>
    </div>
  `;
}

function renderRecall(recall) {
  const isOngoing = recall.status === "Ongoing";
  const isPossibleMatch = recall.matched_on === "product_terms";
  const safeUrl = safeListingUrl(recall.url);

  const linkMarkup = safeUrl
    ? `<a class="recall-alert__link" href="${escapeHtml(safeUrl)}" target="_blank" rel="noreferrer">View FDA recall record <span aria-hidden="true">↗</span></a>`
    : `<span class="recall-alert__unavailable">Recall record link unavailable</span>`;

  return `
    <aside class="recall-alert ${isOngoing ? "recall-alert--ongoing" : ""}" aria-label="Product recall warning">
      <div class="recall-alert__heading">
        <span class="recall-alert__icon" aria-hidden="true">⚠</span>
        <div>
          <p class="recall-alert__eyebrow">Recall alert</p>
          <h3>${escapeHtml(recall.classification || "Classification unavailable")} recall found</h3>
        </div>
        <span class="recall-status ${isOngoing ? "recall-status--ongoing" : ""}">
          ${escapeHtml(recall.status || "Status unavailable")}
        </span>
      </div>

      ${isPossibleMatch ? `
        <p class="recall-alert__possible">
          Possible match, not confirmed — matched using product terms rather than an exact UPC.
        </p>
      ` : ""}

      <p class="recall-alert__reason">${escapeHtml(recall.reason || "No reason provided.")}</p>

      <dl class="recall-alert__details">
        <div>
          <dt>Recalling firm</dt>
          <dd>${escapeHtml(recall.recalling_firm || "Not provided")}</dd>
        </div>
        <div>
          <dt>Report date</dt>
          <dd>${escapeHtml(formatReportDate(recall.report_date))}</dd>
        </div>
        <div>
          <dt>Recall number</dt>
          <dd>${escapeHtml(recall.recall_number || "Not provided")}</dd>
        </div>
      </dl>

      ${recall.product_description ? `
        <p class="recall-alert__product">
          <strong>Affected product:</strong> ${escapeHtml(recall.product_description)}
        </p>
      ` : ""}

      ${linkMarkup}
    </aside>
  `;
}

function renderCandidateCard(candidate, candidateIndex) {
  const recalls = Array.isArray(candidate.recalls) ? candidate.recalls : [];
  const isAdding = scanState.adding.has(candidateIndex);
  const isAdded = scanState.added.has(candidateIndex);
  const message = scanState.messages.get(candidateIndex);
  const title = displayFieldValue(candidate.product_name);

  return `
    <article class="candidate-result">
      ${recalls.map(renderRecall).join("")}

      <div class="candidate-card">
        <div class="candidate-card__header">
          <div>
            <p class="section-kicker">Recognized item ${candidateIndex + 1}</p>
            <h2>${escapeHtml(title)}</h2>
          </div>
          <span class="verify-note">Review before adding</span>
        </div>

        <div class="candidate-card__body">
          <div class="candidate-fields">
            ${FIELD_DEFINITIONS.map((definition) =>
              renderField(candidateIndex, definition, candidate[definition.key])
            ).join("")}
          </div>

          <div class="candidate-card__actions">
            <button
              class="primary-button confirm-button"
              type="button"
              data-action="confirm-candidate"
              data-candidate-index="${candidateIndex}"
              ${isAdding || isAdded ? "disabled" : ""}
            >
              ${isAdding ? "Adding…" : isAdded ? "✓ Added" : "Confirm & Add to Pantry"}
            </button>

            ${message ? `
              <p class="candidate-message ${isAdded ? "candidate-message--success" : "candidate-message--error"}" role="status">
                ${escapeHtml(message)}
              </p>
            ` : ""}
          </div>
        </div>
      </div>
    </article>
  `;
}

function renderWarnings(warnings) {
  if (!warnings.length) {
    return "";
  }

  return `
    <aside class="notice-bar" aria-label="Scan notes">
      <span class="notice-bar__icon" aria-hidden="true">i</span>
      <div>
        <strong>Scan notes</strong>
        <ul>
          ${warnings.map((warning) => `<li>${escapeHtml(warning)}</li>`).join("")}
        </ul>
      </div>
    </aside>
  `;
}

function renderScanResults() {
  const warningsMarkup = renderWarnings(scanState.warnings);

  if (!scanState.rawResult) {
    scanResults.innerHTML = "";
    return;
  }

  if (scanState.candidates.length === 0) {
    scanResults.innerHTML = `
      ${warningsMarkup}
      <div class="empty-state">
        <span class="empty-state__icon" aria-hidden="true">⌕</span>
        <h2>No items recognized in that photo</h2>
        <p>Try another photo with the package label and expiration date in clear view.</p>
      </div>
    `;
    return;
  }

  scanResults.innerHTML = `
    ${warningsMarkup}
    <div class="results-heading">
      <div>
        <p class="section-kicker">Scan complete</p>
        <h2>Verify recognized items</h2>
      </div>
      <span>${scanState.candidates.length} ${scanState.candidates.length === 1 ? "item" : "items"} found</span>
    </div>
    <div class="candidate-list">
      ${scanState.candidates.map(renderCandidateCard).join("")}
    </div>
  `;

  focusActiveEditor();
}

function focusActiveEditor() {
  if (!scanState.editing) {
    return;
  }

  requestAnimationFrame(() => {
    const input = scanResults.querySelector("[data-edit-input]");
    if (input) {
      input.focus();
      if (input.type !== "date") {
        input.select();
      }
    }
  });
}

function acceptScanResult(result) {
  const candidates = Array.isArray(result?.candidates) ? result.candidates : [];

  scanState.rawResult = cloneData(result || {});
  scanState.candidates = candidates.map(normalizeCandidate);
  scanState.warnings = Array.isArray(result?.warnings) ? cloneData(result.warnings) : [];
  scanState.editing = null;
  scanState.adding.clear();
  scanState.added.clear();
  scanState.messages.clear();

  renderScanResults();
}

async function fetchScanResult(imageBase64) {
  try {
    const response = await fetch("/api/scan", {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({ image_base64: imageBase64 })
    });

    if (!response.ok) {
      throw new Error(await errorMessageFor(response));
    }

    return await response.json();
  } catch (error) {
    if (!DEV_MODE) {
      throw error;
    }

    console.info("Scan API unavailable; loading the local fixture.", error);
    // NB: server.py's WEB_DIR only serves web/, so this path only resolves
    // when the dev server root is the repo root (e.g. `python3 -m http.server`
    // run from ~/Developer/expirationradar) — that's the Phase 1 demo setup.
    // Phase 2 flips DEV_MODE off once /api/scan is live, so this path never
    // has to work against the real server.
    const fixtureResponse = await fetch("../tests/fixtures/scan_result.json");

    if (!fixtureResponse.ok) {
      throw new Error(await errorMessageFor(fixtureResponse));
    }

    return await fixtureResponse.json();
  }
}

function setScanningState(isScanning) {
  scanButton.disabled = isScanning || !scanPhotoInput.files?.length;
  scanButton.textContent = isScanning ? "Scanning…" : "Scan photo";
  scanButton.classList.toggle("is-loading", isScanning);
  scanPhotoInput.disabled = isScanning;
}

function clearSelectedPhoto() {
  scanPhotoInput.value = "";

  if (previewObjectUrl) {
    URL.revokeObjectURL(previewObjectUrl);
    previewObjectUrl = null;
  }

  photoPreviewImage.removeAttribute("src");
  photoPreview.hidden = true;
  scanButton.disabled = true;
  scanStatus.textContent = "";
}

function updatePhotoPreview() {
  const file = scanPhotoInput.files?.[0];

  if (!file) {
    clearSelectedPhoto();
    return;
  }

  if (previewObjectUrl) {
    URL.revokeObjectURL(previewObjectUrl);
  }

  previewObjectUrl = URL.createObjectURL(file);
  photoPreviewImage.src = previewObjectUrl;
  photoPreview.hidden = false;
  scanButton.disabled = false;
  scanStatus.textContent = "Photo ready to scan.";
}

async function scanSelectedPhoto() {
  const file = scanPhotoInput.files?.[0];

  if (!file) {
    scanStatus.textContent = "Choose a photo before scanning.";
    return;
  }

  setScanningState(true);
  scanStatus.textContent = "Scanning the photo for products, dates, and recalls…";

  try {
    const imageBase64 = await readFileAsBase64(file);
    const result = await fetchScanResult(imageBase64);
    acceptScanResult(result);
    scanStatus.textContent = "Scan complete. Review each item before adding it.";
  } catch (error) {
    console.error(error);
    scanStatus.textContent = error instanceof Error
      ? error.message
      : "The photo could not be scanned. Please try again.";
  } finally {
    setScanningState(false);
  }
}

function beginFieldEdit(candidateIndex, fieldName) {
  if (!scanState.candidates[candidateIndex]?.[fieldName]) {
    return;
  }

  scanState.editing = { candidateIndex, fieldName };
  renderScanResults();
}

function finishFieldEdit(input) {
  const candidateIndex = Number(input.dataset.candidateIndex);
  const fieldName = input.dataset.fieldName;

  // Guards a real reentrancy bug: replacing scanResults.innerHTML removes the
  // old (focused) input synchronously, which fires a focusout on it — even
  // when the re-render itself was the Escape-cancel handler clearing
  // scanState.editing first. Without this check, that stale focusout would
  // re-commit the abandoned edit as a USER value right through the cancel.
  // Only commit when this input is still the one flagged as being edited.
  if (
    scanState.editing?.candidateIndex !== candidateIndex ||
    scanState.editing?.fieldName !== fieldName
  ) {
    return;
  }

  const candidate = scanState.candidates[candidateIndex];
  const field = candidate?.[fieldName];

  if (!field) {
    return;
  }

  const nextValue = input.value.trim();
  field.value = nextValue === "" ? null : nextValue;
  field.source = "USER";
  field.confidence = 1;

  scanState.editing = null;
  scanState.messages.delete(candidateIndex);
  renderScanResults();
}

function pantryPayloadFor(candidate) {
  const anyFieldEdited = FIELD_DEFINITIONS.some(
    ({ key }) => candidate[key].source === "USER"
  );

  return {
    product_name: String(candidate.product_name.value ?? ""),
    brand: String(candidate.brand.value ?? ""),
    upc: String(candidate.upc.value ?? ""),
    expiry_date: String(candidate.expiry_date.value ?? ""),
    source: anyFieldEdited ? "USER" : String(candidate.product_name.source || "VISION")
  };
}

async function savePantryItem(payload) {
  try {
    const response = await fetch("/api/pantry", {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify(payload)
    });

    if (!response.ok) {
      throw new Error(await errorMessageFor(response));
    }

    return { devMode: false };
  } catch (error) {
    if (!DEV_MODE) {
      throw error;
    }

    console.info("Pantry API unavailable; simulating a successful add.", payload, error);
    return { devMode: true };
  }
}

function showToast(message) {
  toastRegion.textContent = message;
  toastRegion.classList.add("is-visible");

  window.setTimeout(() => {
    toastRegion.classList.remove("is-visible");
  }, 3200);
}

async function confirmCandidate(candidateIndex) {
  const candidate = scanState.candidates[candidateIndex];

  if (!candidate || scanState.adding.has(candidateIndex) || scanState.added.has(candidateIndex)) {
    return;
  }

  scanState.adding.add(candidateIndex);
  scanState.messages.delete(candidateIndex);
  renderScanResults();

  try {
    const result = await savePantryItem(pantryPayloadFor(candidate));
    const message = result.devMode
      ? "Added to pantry (dev mode — no live backend yet)"
      : "Added to pantry";

    scanState.added.add(candidateIndex);
    scanState.messages.set(candidateIndex, message);
    showToast(message);
  } catch (error) {
    console.error(error);
    scanState.messages.set(
      candidateIndex,
      error instanceof Error ? error.message : "Could not add this item to the pantry."
    );
  } finally {
    scanState.adding.delete(candidateIndex);
    renderScanResults();
  }
}

function handleScanResultsClick(event) {
  const actionButton = event.target.closest("[data-action]");
  if (!actionButton) {
    return;
  }

  const candidateIndex = Number(actionButton.dataset.candidateIndex);

  if (actionButton.dataset.action === "edit-field") {
    beginFieldEdit(candidateIndex, actionButton.dataset.fieldName);
  }

  if (actionButton.dataset.action === "confirm-candidate") {
    confirmCandidate(candidateIndex);
  }
}

function handleScanResultsFocusOut(event) {
  const input = event.target.closest("[data-edit-input]");
  if (input) {
    finishFieldEdit(input);
  }
}

function handleScanResultsKeydown(event) {
  const input = event.target.closest("[data-edit-input]");
  if (!input) {
    return;
  }

  if (event.key === "Enter") {
    event.preventDefault();
    input.blur();
  }

  if (event.key === "Escape") {
    event.preventDefault();
    scanState.editing = null;
    renderScanResults();
  }
}

// ---- Shared event wiring ----

document.querySelectorAll(".module-tab[data-target]").forEach((tab) => {
  tab.addEventListener("click", () => switchView(tab.dataset.target));
});

scanPhotoInput.addEventListener("change", updatePhotoPreview);
removePhotoButton.addEventListener("click", clearSelectedPhoto);
scanButton.addEventListener("click", scanSelectedPhoto);
scanResults.addEventListener("click", handleScanResultsClick);
scanResults.addEventListener("focusout", handleScanResultsFocusOut);
scanResults.addEventListener("keydown", handleScanResultsKeydown);

// ---- Pantry + Digest views ----

// Pantry + Digest views

let pantryItems = [];
let cachedRecipes = null;
let pantryLoaded = false;
let digestLoaded = false;

async function fetchPantryItems() {
  try {
    const response = await fetch("/api/pantry");
    if (!response.ok) throw new Error(await errorMessageFor(response));
    return await response.json();
  } catch (error) {
    if (!DEV_MODE) throw error;
    console.info("Pantry API unavailable; loading the local fixture.", error);
    const fixtureResponse = await fetch("../tests/fixtures/pantry.json");
    if (!fixtureResponse.ok) throw new Error(await errorMessageFor(fixtureResponse));
    return await fixtureResponse.json();
  }
}

async function fetchDigest() {
  try {
    const response = await fetch("/api/digest");
    if (!response.ok) throw new Error(await errorMessageFor(response));
    return await response.json();
  } catch (error) {
    if (!DEV_MODE) throw error;
    console.info("Digest API unavailable; loading the local fixture.", error);
    const fixtureResponse = await fetch("../tests/fixtures/digest.json");
    if (!fixtureResponse.ok) throw new Error(await errorMessageFor(fixtureResponse));
    return await fixtureResponse.json();
  }
}

async function fetchRecipes() {
  try {
    const response = await fetch("/api/recipes");
    if (!response.ok) throw new Error(await errorMessageFor(response));
    return await response.json();
  } catch (error) {
    if (!DEV_MODE) throw error;
    console.info("Recipes API unavailable; loading the local fixture.", error);
    const fixtureResponse = await fetch("../tests/fixtures/recipes.json");
    if (!fixtureResponse.ok) throw new Error(await errorMessageFor(fixtureResponse));
    return await fixtureResponse.json();
  }
}

async function consumePantryItem(id) {
  try {
    const response = await fetch(`/api/pantry/${encodeURIComponent(id)}/consume`, {
      method: "POST"
    });
    if (!response.ok) throw new Error(await errorMessageFor(response));
    return { devMode: false };
  } catch (error) {
    if (!DEV_MODE) throw error;
    console.info("Pantry API unavailable; simulating a successful consume.", id, error);
    return { devMode: true };
  }
}

async function deletePantryItem(id) {
  try {
    const response = await fetch(`/api/pantry/${encodeURIComponent(id)}`, {
      method: "DELETE"
    });
    if (!response.ok) throw new Error(await errorMessageFor(response));
    return { devMode: false };
  } catch (error) {
    if (!DEV_MODE) throw error;
    console.info("Pantry API unavailable; simulating a successful delete.", id, error);
    return { devMode: true };
  }
}

function parseExpiryDate(value) {
  if (!value) return null;
  const [year, month, day] = value.split("-").map(Number);
  if (!year || !month || !day) return null;
  return new Date(year, month - 1, day);
}

function startOfToday() {
  const today = new Date();
  return new Date(today.getFullYear(), today.getMonth(), today.getDate());
}

function expiryInfo(expiryDate) {
  const date = parseExpiryDate(expiryDate);
  if (!date) return { kind: "undated", daysUntil: null };

  const daysUntil = Math.round((date - startOfToday()) / 86400000);
  if (daysUntil < 0) return { kind: "expired", daysUntil };
  if (daysUntil <= 5) return { kind: "soon", daysUntil };
  return { kind: "normal", daysUntil };
}

function formatExpiryDate(expiryDate) {
  const date = parseExpiryDate(expiryDate);
  if (!date) return "No expiry date";
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric"
  }).format(date);
}

function formatTimestamp(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short"
  }).format(date);
}

function sortPantryItems(items) {
  return [...items].sort((a, b) => {
    if (!a.expiry_date && !b.expiry_date) return 0;
    if (!a.expiry_date) return 1;
    if (!b.expiry_date) return -1;
    return parseExpiryDate(a.expiry_date) - parseExpiryDate(b.expiry_date);
  });
}

function renderUrgencyBadge(item) {
  const urgency = expiryInfo(item.expiry_date);

  if (urgency.kind === "expired") {
    return '<span class="recall-status recall-status--ongoing item-urgency item-urgency--expired">EXPIRED</span>';
  }

  if (urgency.kind === "soon") {
    const label = urgency.daysUntil === 0
      ? "Expires today"
      : urgency.daysUntil === 1
        ? "Expires tomorrow"
        : `Expires in ${urgency.daysUntil} days`;

    return `<span class="item-urgency item-urgency--soon">${label}</span>`;
  }

  return "";
}

function renderPantryItem(item, options = {}) {
  const interactive = options.interactive === true;
  const urgency = expiryInfo(item.expiry_date);
  const cardClass = urgency.kind === "expired"
    ? " pantry-item-card--expired"
    : urgency.kind === "soon"
      ? " pantry-item-card--soon"
      : "";

  const brand = item.brand
    ? `<p class="pantry-item-card__brand">${escapeHtml(item.brand)}</p>`
    : "";

  const quantity = Number(item.quantity) > 1
    ? `<span>Qty ${escapeHtml(item.quantity)}</span>`
    : "";

  const source = item.source
    ? `<span class="source-badge ${sourceClass(item.source)}">${escapeHtml(item.source)}</span>`
    : "";

  const notes = item.notes
    ? `<p class="pantry-item-card__notes">${escapeHtml(item.notes)}</p>`
    : "";

  const actions = interactive
    ? `
      <div class="pantry-item-card__actions">
        <button class="secondary-button" type="button" data-pantry-action="consume" data-item-id="${escapeHtml(item.id)}">
          Consume
        </button>
        <button class="secondary-button secondary-button--danger" type="button" data-pantry-action="delete" data-item-id="${escapeHtml(item.id)}">
          Delete
        </button>
      </div>
    `
    : "";

  return `
    <article class="candidate-card pantry-item-card${cardClass}">
      <div class="candidate-card__header pantry-item-card__header">
        <div>
          <h3>${escapeHtml(item.product_name || "Unnamed item")}</h3>
          ${brand}
        </div>
        ${renderUrgencyBadge(item)}
      </div>
      <div class="pantry-item-card__meta">
        <span class="pantry-item-card__expiry">${formatExpiryDate(item.expiry_date)}</span>
        ${quantity}
        ${source}
      </div>
      ${notes}
      ${actions}
    </article>
  `;
}

function renderRecipePanel(recipes) {
  if (!recipes.available) {
    return `
      <section class="recipe-panel">
        <p class="section-kicker">Recipes</p>
        <h2>Use it up before it’s gone</h2>
        <p class="recipe-panel__empty">Recipe suggestions aren’t available right now.</p>
      </section>
    `;
  }

  const suggestions = (recipes.suggestions || []).slice(0, 3);

  return `
    <section class="recipe-panel">
      <p class="section-kicker">Recipes</p>
      <h2>Use it up before it’s gone</h2>
      <div class="recipe-list">
        ${suggestions.map((recipe) => `
          <article class="recipe-card">
            <h3>${escapeHtml(recipe.title)}</h3>
            <div class="recipe-card__uses">
              ${(recipe.uses || []).map((item) =>
                `<span class="recipe-use-tag">${escapeHtml(item)}</span>`
              ).join("")}
            </div>
            <p>${escapeHtml(recipe.steps)}</p>
          </article>
        `).join("") || '<p class="recipe-panel__empty">No recipe suggestions right now.</p>'}
      </div>
    </section>
  `;
}

function renderPantry() {
  const content = document.querySelector("#pantry-content");
  if (!content) return;

  const sortedItems = sortPantryItems(pantryItems);

  content.innerHTML = `
    <div class="view-intro">
      <p class="section-kicker">Your pantry</p>
      <h1>Keep the good stuff in view</h1>
      <p>Items nearest their expiry date appear first.</p>
    </div>

    <section class="pantry-list-section">
      <div class="digest-section__heading">
        <h2>Pantry items</h2>
        <span>${sortedItems.length} item${sortedItems.length === 1 ? "" : "s"}</span>
      </div>
      <div class="pantry-item-list">
        ${sortedItems.length
          ? sortedItems.map((item) => renderPantryItem(item, { interactive: true })).join("")
          : '<div class="empty-state"><p>Your pantry is clear for now.</p></div>'}
      </div>
    </section>

    <div id="recipe-content"></div>
  `;

  renderRecipes(cachedRecipes);
}

function renderRecipes(recipes) {
  const container = document.querySelector("#recipe-content");
  if (!container) return;

  if (!recipes) {
    container.innerHTML = `
      <section class="recipe-panel">
        <p class="section-kicker">Recipes</p>
        <h2>Use it up before it’s gone</h2>
        <p class="recipe-panel__empty">Loading recipe suggestions…</p>
      </section>
    `;
    return;
  }

  container.innerHTML = renderRecipePanel(recipes);
}

async function loadPantryView() {
  if (pantryLoaded) return;
  pantryLoaded = true;

  const content = document.querySelector("#pantry-content");
  if (content) {
    content.innerHTML = '<div class="empty-state"><p>Loading your pantry…</p></div>';
  }

  try {
    const [pantry, recipes] = await Promise.all([fetchPantryItems(), fetchRecipes()]);
    pantryItems = pantry.items || [];
    cachedRecipes = recipes;
    renderPantry();
  } catch (error) {
    pantryLoaded = false;
    if (content) {
      content.innerHTML = `
        <div class="empty-state">
          <p>We couldn’t load your pantry.</p>
          <p>${escapeHtml(error.message)}</p>
        </div>
      `;
    }
  }
}

function renderDigestSection(title, items, emptyMessage) {
  return `
    <section class="digest-section">
      <div class="digest-section__heading">
        <h2>${escapeHtml(title)}</h2>
        <span>${items.length}</span>
      </div>
      ${items.length
        ? `<div class="pantry-item-list">${items.map((item) => renderPantryItem(item)).join("")}</div>`
        : `<p class="digest-section__empty">${escapeHtml(emptyMessage)}</p>`}
    </section>
  `;
}

function renderDigest(digest) {
  const content = document.querySelector("#digest-content");
  if (!content) return;

  if (!digest.generated_at) {
    content.innerHTML = `
      <div class="empty-state">
        <p class="section-kicker">Digest</p>
        <h1>Your watcher hasn’t run yet</h1>
        <p>Check back after ExpirationRadar has had a chance to review your pantry.</p>
      </div>
    `;
    return;
  }

  const recalls = digest.new_recalls || [];
  const expired = digest.expired || [];
  const expiringSoon = digest.expiring_soon || [];

  content.innerHTML = `
    <div class="view-intro digest-intro">
      <p class="section-kicker">Digest</p>
      <h1>What happened since you last checked</h1>
      <p>Last updated ${escapeHtml(formatTimestamp(digest.generated_at))}</p>
    </div>

    <section class="digest-section digest-section--recalls">
      <div class="digest-section__heading">
        <h2>New recalls</h2>
        <span>${recalls.length}</span>
      </div>
      ${recalls.length
        ? `<div class="digest-recall-list">${recalls.map(renderRecall).join("")}</div>`
        : '<p class="digest-section__empty">No new recalls were found.</p>'}
    </section>

    ${renderDigestSection("Expired items", expired, "Nothing has newly expired.")}
    ${renderDigestSection("Expiring soon", expiringSoon, "Nothing is nearing its expiry date.")}
  `;
}

async function loadDigestView() {
  if (digestLoaded) return;
  digestLoaded = true;

  const content = document.querySelector("#digest-content");
  if (content) {
    content.innerHTML = '<div class="empty-state"><p>Checking for updates…</p></div>';
  }

  try {
    renderDigest(await fetchDigest());
  } catch (error) {
    digestLoaded = false;
    if (content) {
      content.innerHTML = `
        <div class="empty-state">
          <p>We couldn’t load your digest.</p>
          <p>${escapeHtml(error.message)}</p>
        </div>
      `;
    }
  }
}

document.querySelector('.module-tab[data-target="pantry"]')?.addEventListener("click", loadPantryView);
document.querySelector('.module-tab[data-target="digest"]')?.addEventListener("click", loadDigestView);

document.querySelector("#pantry-content")?.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-pantry-action]");
  if (!button) return;

  const id = button.dataset.itemId;
  const action = button.dataset.pantryAction;
  const item = pantryItems.find((pantryItem) => String(pantryItem.id) === id);
  if (!item) return;

  button.disabled = true;

  try {
    const result = action === "consume"
      ? await consumePantryItem(id)
      : await deletePantryItem(id);

    pantryItems = pantryItems.filter((pantryItem) => String(pantryItem.id) !== id);
    renderPantry();

    const actionLabel = action === "consume" ? "Consumed" : "Deleted";
    showToast(
      result.devMode
        ? `${actionLabel} ${item.product_name} (dev mode — no live backend yet)`
        : `${actionLabel} ${item.product_name}`
    );
  } catch (error) {
    button.disabled = false;
    showToast(`Couldn’t update ${item.product_name}: ${error.message}`);
  }
});
