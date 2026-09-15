/**
 * Filament Manager card.
 *
 * Written against plain DOM APIs on purpose: no import from the Home
 * Assistant frontend bundle, so a rename inside the frontend cannot turn the
 * input fields invisible. Everything it needs comes over the WebSocket API.
 */

const CARD_VERSION = "1.0.0";
const BASE_URL = new URL("./", import.meta.url).href;

const DEFAULT_CONFIG = {
  title: "Filament",
  show_stats: true,
  show_slots: true,
  show_archived: false,
  show_scanner: true,
  columns: 0,
};

/* ------------------------------------------------------------------ utils */

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "style" && typeof value === "object")
      Object.assign(node.style, value);
    else if (key === "dataset") Object.assign(node.dataset, value);
    else if (key.startsWith("on") && typeof value === "function")
      node.addEventListener(key.slice(2).toLowerCase(), value);
    else if (key === "text") node.textContent = String(value);
    else node.setAttribute(key, value === true ? "" : String(value));
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

function grams(value) {
  if (value === null || value === undefined) return "–";
  return `${Math.round(value)} g`;
}

function money(value, currency) {
  if (value === null || value === undefined) return "–";
  try {
    return new Intl.NumberFormat(undefined, {
      style: "currency",
      currency,
    }).format(value);
  } catch (err) {
    return `${value.toFixed(2)} ${currency}`;
  }
}

/** Pick black or white text so a colour swatch stays readable. */
function contrastColor(hex) {
  if (!hex) return "var(--primary-text-color)";
  const value = hex.replace("#", "");
  const [r, g, b] = [0, 2, 4].map((offset) =>
    parseInt(value.slice(offset, offset + 2), 16),
  );
  const luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
  return luminance > 0.6 ? "#111" : "#fff";
}

const SOURCE_LABELS = {
  rfid: "RFID",
  heuristic: "auto",
  nfc: "NFC",
  manual: "manual",
  print: "print",
};

/* ------------------------------------------------------------------ styles */

const STYLES = `
:host { display: block; }
.card {
  background: var(--ha-card-background, var(--card-background-color, #fff));
  border-radius: var(--ha-card-border-radius, 12px);
  box-shadow: var(--ha-card-box-shadow, none);
  border: var(--ha-card-border-width, 1px) solid
    var(--ha-card-border-color, var(--divider-color, #e0e0e0));
  color: var(--primary-text-color);
  overflow: hidden;
}
header {
  display: flex; align-items: center; justify-content: space-between;
  gap: 8px; padding: 12px 16px 4px;
}
header h1 { font-size: 1.2rem; font-weight: 500; margin: 0; }
.stats { display: flex; flex-wrap: wrap; gap: 8px; padding: 4px 16px 12px; }
.stat {
  flex: 1 1 110px; background: var(--secondary-background-color, #f5f5f5);
  border-radius: 10px; padding: 8px 10px;
}
.stat .value { font-size: 1.15rem; font-weight: 600; }
.stat .label { font-size: 0.75rem; color: var(--secondary-text-color); }
.slots { display: flex; flex-wrap: wrap; gap: 8px; padding: 0 16px 12px; }
.slot {
  flex: 1 1 130px; min-width: 120px; border-radius: 10px; padding: 8px 10px;
  border: 1px solid var(--divider-color, #e0e0e0); cursor: pointer;
  background: var(--secondary-background-color, #f5f5f5);
}
.slot:hover { border-color: var(--primary-color); }
.slot .name { font-size: 0.85rem; font-weight: 500; }
.slot .meta { font-size: 0.72rem; color: var(--secondary-text-color); }
.slot .head { display: flex; align-items: center; gap: 6px; margin-bottom: 4px; }
.toolbar { display: flex; gap: 8px; padding: 0 16px 12px; flex-wrap: wrap; }
.toolbar input[type="search"] {
  flex: 1 1 140px; min-width: 0; padding: 7px 10px; border-radius: 8px;
  border: 1px solid var(--divider-color, #e0e0e0);
  background: var(--card-background-color, #fff); color: inherit; font: inherit;
}
button {
  font: inherit; cursor: pointer; border-radius: 8px; padding: 7px 12px;
  border: 1px solid var(--divider-color, #e0e0e0);
  background: var(--secondary-background-color, #f5f5f5); color: inherit;
}
button:hover { border-color: var(--primary-color); }
button.primary {
  background: var(--primary-color); color: var(--text-primary-color, #fff);
  border-color: var(--primary-color);
}
button.danger { color: var(--error-color, #db4437); }
button:disabled { opacity: 0.5; cursor: default; }
ul.spools { list-style: none; margin: 0; padding: 0 8px 8px; display: grid; gap: 8px; }
li.spool {
  display: grid; grid-template-columns: 34px 1fr auto; gap: 10px;
  align-items: center; padding: 8px; border-radius: 10px;
  border: 1px solid var(--divider-color, #e0e0e0);
}
li.spool.low { border-color: var(--warning-color, #ffa600); }
li.spool.archived { opacity: 0.55; }
.swatch {
  width: 34px; height: 34px; border-radius: 8px; display: grid;
  place-items: center; font-size: 0.6rem; font-weight: 700;
  border: 1px solid rgba(128, 128, 128, 0.35);
}
.spool .name { font-weight: 500; font-size: 0.92rem; }
.spool .meta { font-size: 0.75rem; color: var(--secondary-text-color); }
.bar {
  margin-top: 4px; height: 5px; border-radius: 3px; overflow: hidden;
  background: var(--divider-color, #e0e0e0);
}
.bar > div { height: 100%; background: var(--primary-color); }
.bar.low > div { background: var(--warning-color, #ffa600); }
.right { text-align: right; display: grid; gap: 2px; justify-items: end; }
.right .weight { font-weight: 600; }
.badge {
  display: inline-block; font-size: 0.65rem; padding: 1px 6px; border-radius: 6px;
  background: var(--divider-color, #e0e0e0); color: var(--secondary-text-color);
}
.badge.slot-badge { background: var(--primary-color); color: var(--text-primary-color, #fff); }
.empty { padding: 24px 16px; text-align: center; color: var(--secondary-text-color); }
.overlay {
  position: fixed; inset: 0; background: rgba(0, 0, 0, 0.55); z-index: 9;
  display: grid; place-items: center; padding: 16px;
}
.dialog {
  background: var(--card-background-color, #fff); color: var(--primary-text-color);
  border-radius: 14px; padding: 16px; width: min(460px, 100%);
  max-height: 86vh; overflow: auto; box-shadow: 0 8px 30px rgba(0, 0, 0, 0.3);
}
.dialog h2 { margin: 0 0 10px; font-size: 1.05rem; }
.field { display: grid; gap: 4px; margin-bottom: 10px; }
.field label { font-size: 0.78rem; color: var(--secondary-text-color); }
.field input, .field select, .field textarea {
  font: inherit; padding: 7px 9px; border-radius: 8px; color: inherit;
  border: 1px solid var(--divider-color, #e0e0e0);
  background: var(--card-background-color, #fff);
}
.row { display: flex; gap: 8px; }
.row > * { flex: 1; min-width: 0; }
.actions { display: flex; gap: 8px; justify-content: flex-end; margin-top: 12px; flex-wrap: wrap; }
.error { color: var(--error-color, #db4437); font-size: 0.8rem; margin: 6px 0; }
.hint { color: var(--secondary-text-color); font-size: 0.78rem; margin: 6px 0; }
video { width: 100%; border-radius: 10px; background: #000; aspect-ratio: 4 / 3; object-fit: cover; }
.scan-result { font-family: var(--code-font-family, monospace); font-size: 0.9rem; }
`;

/* -------------------------------------------------------------- main card */

class FilamentManagerCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = { ...DEFAULT_CONFIG };
    this._data = null;
    this._hass = null;
    this._unsubscribe = null;
    this._filter = "";
    this._dialog = null;
    this._scanner = null;
  }

  static getStubConfig() {
    return { ...DEFAULT_CONFIG };
  }

  static getConfigElement() {
    return document.createElement("filament-manager-card-editor");
  }

  setConfig(config) {
    this._config = { ...DEFAULT_CONFIG, ...(config || {}) };
    this._render();
  }

  getCardSize() {
    return 4 + Math.min(8, (this._data?.spools?.length ?? 3));
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) this._subscribe();
  }

  connectedCallback() {
    if (this._hass && !this._unsubscribe) this._subscribe();
    this._render();
  }

  disconnectedCallback() {
    this._closeScanner();
    if (this._unsubscribe) {
      Promise.resolve(this._unsubscribe).then((fn) => fn && fn());
      this._unsubscribe = null;
    }
  }

  async _subscribe() {
    if (!this._hass || this._unsubscribe) return;
    try {
      this._unsubscribe = await this._hass.connection.subscribeMessage(
        (data) => {
          this._data = data;
          this._render();
        },
        { type: "filament_manager/subscribe" },
      );
      this._data = await this._call("filament_manager/list");
      this._render();
    } catch (err) {
      this._error = err?.message || String(err);
      this._render();
    }
  }

  _call(type, payload = {}) {
    return this._hass.connection.sendMessagePromise({ type, ...payload });
  }

  /* ---------------------------------------------------------- rendering */

  _render() {
    if (!this.shadowRoot) return;
    const root = this.shadowRoot;
    root.textContent = "";
    root.append(el("style", { text: STYLES }));

    const card = el("div", { class: "card" });
    root.append(card);

    if (!this._data) {
      card.append(
        el("div", { class: "empty" }, this._error || "Loading filament inventory…"),
      );
      return;
    }

    card.append(this._renderHeader());
    if (this._config.show_stats) card.append(this._renderStats());
    if (this._config.show_slots) card.append(this._renderSlots());
    card.append(this._renderToolbar());
    card.append(this._renderSpools());
    if (this._dialog) root.append(this._dialog);
  }

  _renderHeader() {
    return el(
      "header",
      {},
      el("h1", { text: this._config.title }),
      el(
        "div",
        { class: "row", style: { gap: "6px" } },
        this._config.show_scanner
          ? el("button", {
              text: "Scan",
              title: "Scan a barcode",
              onclick: () => this._openScanner(),
            })
          : null,
        el("button", {
          class: "primary",
          text: "Add",
          title: "Add a spool",
          onclick: () => this._openAddSpool(),
        }),
      ),
    );
  }

  _renderStats() {
    const stats = this._data.stats || {};
    const tile = (value, label) =>
      el(
        "div",
        { class: "stat" },
        el("div", { class: "value", text: value }),
        el("div", { class: "label", text: label }),
      );
    return el(
      "div",
      { class: "stats" },
      tile(String(stats.spool_count ?? 0), "spools"),
      tile(grams(stats.total_remaining), "in stock"),
      tile(money(stats.total_value, stats.currency || "EUR"), "value"),
      tile(String(stats.low_stock_count ?? 0), "running out"),
    );
  }

  _renderSlots() {
    const slots = this._data.slots || [];
    if (!slots.length) return el("div", {});
    return el(
      "div",
      { class: "slots" },
      slots.map((slot) => {
        const spool = slot.spool;
        const hex = spool?.color_hex ? `#${spool.color_hex}` : null;
        return el(
          "div",
          {
            class: "slot",
            title: "Assign a spool to this slot",
            onclick: () => this._openAssignSlot(slot.slot),
          },
          el(
            "div",
            { class: "head" },
            el("div", {
              class: "swatch",
              style: {
                width: "18px",
                height: "18px",
                background: hex || "transparent",
                color: contrastColor(hex),
              },
            }),
            el("div", { class: "name", text: `Slot ${slot.slot}` }),
            slot.source
              ? el("span", {
                  class: "badge",
                  text: SOURCE_LABELS[slot.source] || slot.source,
                })
              : null,
          ),
          el("div", { class: "name", text: spool ? spool.name : "empty" }),
          el("div", {
            class: "meta",
            text: spool
              ? `${grams(spool.remaining_weight)} · ${spool.remaining_percent}%`
              : slot.reported_material
                ? `reported: ${slot.reported_material}`
                : "—",
          }),
        );
      }),
    );
  }

  _renderToolbar() {
    return el(
      "div",
      { class: "toolbar" },
      el("input", {
        type: "search",
        placeholder: "Filter by name, material, colour, location…",
        value: this._filter,
        oninput: (event) => {
          this._filter = event.target.value.toLowerCase();
          this._renderSpoolsOnly();
        },
      }),
    );
  }

  _matchesFilter(spool) {
    if (!this._filter) return true;
    return [
      spool.name,
      spool.material,
      spool.color_name,
      spool.location,
      spool.vendor,
    ]
      .filter(Boolean)
      .join(" ")
      .toLowerCase()
      .includes(this._filter);
  }

  _renderSpoolsOnly() {
    const list = this.shadowRoot.querySelector("ul.spools");
    if (list) list.replaceWith(this._renderSpools());
  }

  _renderSpools() {
    const spools = (this._data.spools || []).filter(
      (spool) =>
        (this._config.show_archived || !spool.archived) && this._matchesFilter(spool),
    );
    const list = el("ul", { class: "spools" });
    if (this._config.columns > 1) {
      list.style.gridTemplateColumns = `repeat(${this._config.columns}, minmax(0, 1fr))`;
    }
    if (!spools.length) {
      list.append(
        el("li", { class: "empty" }, "No spools yet. Add one to get started."),
      );
      return list;
    }
    for (const spool of spools) list.append(this._renderSpool(spool));
    return list;
  }

  _renderSpool(spool) {
    const hex = spool.color_hex ? `#${spool.color_hex}` : null;
    const percent = Math.max(0, Math.min(100, spool.remaining_percent || 0));
    const meta = [
      spool.material,
      spool.color_name,
      spool.location,
      spool.remaining_length ? `${spool.remaining_length} m` : null,
    ]
      .filter(Boolean)
      .join(" · ");
    return el(
      "li",
      {
        class: `spool${spool.low_stock ? " low" : ""}${spool.archived ? " archived" : ""}`,
      },
      el("div", {
        class: "swatch",
        style: { background: hex || "transparent", color: contrastColor(hex) },
        text: hex ? "" : "?",
        title: spool.color_name || "",
      }),
      el(
        "div",
        {},
        el("div", { class: "name", text: spool.name }),
        el("div", { class: "meta", text: meta || "—" }),
        el(
          "div",
          { class: `bar${spool.low_stock ? " low" : ""}` },
          el("div", { style: { width: `${percent}%` } }),
        ),
      ),
      el(
        "div",
        { class: "right" },
        el("div", { class: "weight", text: grams(spool.remaining_weight) }),
        spool.slot
          ? el("span", { class: "badge slot-badge", text: `Slot ${spool.slot}` })
          : null,
        el("button", {
          text: "…",
          title: "Actions",
          onclick: () => this._openSpoolActions(spool),
        }),
      ),
    );
  }

  /* ----------------------------------------------------------- dialogs */

  _showDialog(title, body, actions, onClose) {
    const overlay = el("div", {
      class: "overlay",
      onclick: (event) => {
        if (event.target === overlay) this._closeDialog();
      },
    });
    const dialog = el("div", { class: "dialog" }, el("h2", { text: title }), body);
    dialog.append(el("div", { class: "actions" }, actions));
    overlay.append(dialog);
    this._dialogClose = onClose;
    this._dialog = overlay;
    this._render();
    const first = dialog.querySelector("input, select, textarea");
    if (first) first.focus();
  }

  _closeDialog() {
    const close = this._dialogClose;
    this._dialog = null;
    this._dialogClose = null;
    if (close) close();
    this._render();
  }

  _field(label, input, hint) {
    return el(
      "div",
      { class: "field" },
      el("label", { text: label }),
      input,
      hint ? el("div", { class: "hint", text: hint }) : null,
    );
  }

  _openAddSpool(preselectedType = null) {
    const types = this._data.types || [];
    if (!types.length) {
      this._showDialog(
        "No filament types yet",
        el(
          "div",
          { class: "hint" },
          "Add a filament type first, under Settings → Devices & services → " +
            "Filament Manager → Configure.",
        ),
        [el("button", { text: "Close", onclick: () => this._closeDialog() })],
      );
      return;
    }
    const typeSelect = el(
      "select",
      {},
      types.map((type) =>
        el("option", {
          value: type.id,
          text: type.label,
          selected: preselectedType === type.id,
        }),
      ),
    );
    const count = el("input", { type: "number", min: "1", max: "50", value: "1" });
    const price = el("input", { type: "number", min: "0", step: "0.01" });
    const location = el("input", { type: "text" });
    const purchased = el("input", { type: "date" });
    const error = el("div", { class: "error" });
    error.hidden = true;

    const body = el(
      "div",
      {},
      this._field("Filament type", typeSelect, "Sorted by most recently used."),
      el(
        "div",
        { class: "row" },
        this._field("Count", count),
        this._field("Price", price),
      ),
      el(
        "div",
        { class: "row" },
        this._field("Purchase date", purchased),
        this._field("Location", location),
      ),
      error,
    );

    const submit = el("button", {
      class: "primary",
      text: "Add",
      onclick: async () => {
        submit.disabled = true;
        try {
          await this._call("filament_manager/add_spool", {
            type_id: typeSelect.value,
            count: Number(count.value) || 1,
            price: price.value ? Number(price.value) : null,
            location: location.value || null,
            purchase_date: purchased.value || null,
          });
          this._closeDialog();
        } catch (err) {
          error.hidden = false;
          error.textContent = err?.message || String(err);
          submit.disabled = false;
        }
      },
    });

    this._showDialog("Add spool", body, [
      el("button", { text: "Cancel", onclick: () => this._closeDialog() }),
      submit,
    ]);
  }

  _openSpoolActions(spool) {
    const gross = el("input", {
      type: "number",
      min: "0",
      step: "1",
      value: String(
        Math.round(spool.remaining_weight + (spool.spool_weight || 0)),
      ),
    });
    const consumed = el("input", { type: "number", min: "0", step: "1" });
    const slotSelect = el(
      "select",
      {},
      el("option", { value: "", text: "— not loaded —" }),
      (this._data.slots || []).map((slot) =>
        el("option", {
          value: String(slot.slot),
          text: `Slot ${slot.slot}`,
          selected: spool.slot === slot.slot,
        }),
      ),
    );
    const error = el("div", { class: "error" });
    error.hidden = true;

    const run = async (fn) => {
      try {
        await fn();
        this._closeDialog();
      } catch (err) {
        error.hidden = false;
        error.textContent = err?.message || String(err);
      }
    };

    const body = el(
      "div",
      {},
      el("div", { class: "hint", text: `${grams(spool.remaining_weight)} left · ${spool.remaining_percent}% · ${grams(spool.total_consumed)} printed` }),
      this._field(
        "Measured gross weight",
        gross,
        spool.spool_weight
          ? `Empty spool: ${grams(spool.spool_weight)} — it is subtracted for you.`
          : "This type has no empty spool weight, so the value is used as-is.",
      ),
      this._field("Subtract filament", consumed, "Grams to book as consumed."),
      this._field("Slot", slotSelect),
      error,
    );

    this._showDialog(spool.name, body, [
      el("button", {
        class: "danger",
        text: spool.archived ? "Restore" : "Archive",
        onclick: () =>
          run(() =>
            this._call("filament_manager/archive", {
              spool_id: spool.id,
              archived: !spool.archived,
            }),
          ),
      }),
      el("button", { text: "Cancel", onclick: () => this._closeDialog() }),
      el("button", {
        class: "primary",
        text: "Save",
        onclick: () =>
          run(async () => {
            if (consumed.value && Number(consumed.value) > 0) {
              await this._call("filament_manager/consume", {
                spool_id: spool.id,
                amount: Number(consumed.value),
              });
            } else if (gross.value !== "") {
              await this._call("filament_manager/correct_weight", {
                spool_id: spool.id,
                gross_weight: Number(gross.value),
              });
            }
            const slot = slotSelect.value ? Number(slotSelect.value) : null;
            if (slot !== (spool.slot ?? null)) {
              if (slot) {
                await this._call("filament_manager/assign_slot", {
                  slot,
                  spool_id: spool.id,
                });
              } else if (spool.slot) {
                await this._call("filament_manager/assign_slot", {
                  slot: spool.slot,
                  spool_id: null,
                });
              }
            }
          }),
      }),
    ]);
  }

  _openAssignSlot(slot) {
    const spools = (this._data.spools || []).filter((spool) => !spool.archived);
    const current = (this._data.slots || []).find((item) => item.slot === slot);
    const select = el(
      "select",
      {},
      el("option", { value: "", text: "— empty —" }),
      spools.map((spool) =>
        el("option", {
          value: spool.id,
          text: `${spool.name} — ${grams(spool.remaining_weight)}`,
          selected: current?.spool_id === spool.id,
        }),
      ),
    );
    const error = el("div", { class: "error" });
    error.hidden = true;

    this._showDialog(
      `Slot ${slot}`,
      el(
        "div",
        {},
        current?.reported_material
          ? el("div", {
              class: "hint",
              text: `The printer reports ${current.reported_material}${
                current.reported_color ? ` in ${current.reported_color}` : ""
              }${current.reported_uid ? ` (tag ${current.reported_uid})` : ""}.`,
            })
          : null,
        this._field("Spool", select),
        error,
      ),
      [
        el("button", { text: "Cancel", onclick: () => this._closeDialog() }),
        el("button", {
          class: "primary",
          text: "Save",
          onclick: async () => {
            try {
              await this._call("filament_manager/assign_slot", {
                slot,
                spool_id: select.value || null,
              });
              this._closeDialog();
            } catch (err) {
              error.hidden = false;
              error.textContent = err?.message || String(err);
            }
          },
        }),
      ],
    );
  }

  /* ---------------------------------------------------------- scanning */

  async _openScanner() {
    const video = el("video", { autoplay: true, muted: true, playsinline: true });
    const status = el("div", { class: "hint", text: "Starting the camera…" });
    const manual = el("input", {
      type: "text",
      inputmode: "numeric",
      placeholder: "…or type the barcode",
    });
    const body = el(
      "div",
      {},
      video,
      status,
      this._field("Barcode", manual),
    );

    this._showDialog(
      "Scan barcode",
      body,
      [
        el("button", { text: "Cancel", onclick: () => this._closeDialog() }),
        el("button", {
          class: "primary",
          text: "Look up",
          onclick: () => manual.value && this._handleCode(manual.value.trim()),
        }),
      ],
      () => this._closeScanner(),
    );

    if (!window.isSecureContext) {
      status.textContent =
        "The camera needs HTTPS. Use your external URL, or type the code.";
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia) {
      status.textContent = "This browser exposes no camera. Type the code instead.";
      return;
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: { ideal: "environment" } },
        audio: false,
      });
      this._scanner = { stream, stop: false };
      video.srcObject = stream;
      await video.play().catch(() => {});
      status.textContent = "Hold the barcode in front of the camera.";
      this._scanLoop(video, status);
    } catch (err) {
      status.textContent = `Camera unavailable: ${err?.message || err}`;
    }
  }

  /**
   * Read frames until a barcode shows up.
   *
   * `BarcodeDetector` is the fast path where it exists — Chrome, Edge and the
   * Android companion app. It does not exist on iOS at all, which is why the
   * bundled ZXing WASM build is the fallback rather than the exception.
   */
  async _scanLoop(video, status) {
    const detector = await this._getDetector(status);
    if (!detector) return;
    const canvas = document.createElement("canvas");
    const context = canvas.getContext("2d", { willReadFrequently: true });

    const tick = async () => {
      if (!this._scanner || this._scanner.stop) return;
      if (video.readyState >= 2 && video.videoWidth) {
        try {
          const code = await detector(video, canvas, context);
          if (code) {
            this._closeScanner();
            this._handleCode(code);
            return;
          }
        } catch (err) {
          status.textContent = `Scan error: ${err?.message || err}`;
        }
      }
      this._scanner.timer = setTimeout(tick, 220);
    };
    tick();
  }

  async _getDetector(status) {
    if ("BarcodeDetector" in window) {
      try {
        const formats = await window.BarcodeDetector.getSupportedFormats();
        const wanted = ["ean_13", "ean_8", "upc_a", "upc_e", "code_128", "qr_code"].filter(
          (format) => formats.includes(format),
        );
        if (wanted.length) {
          const native = new window.BarcodeDetector({ formats: wanted });
          return async (video) => {
            const results = await native.detect(video);
            return results[0]?.rawValue || null;
          };
        }
      } catch (err) {
        /* fall through to the WASM reader */
      }
    }
    try {
      const zxing = await loadZXing();
      status.textContent = "Hold the barcode in front of the camera.";
      return async (video, canvas, context) => {
        canvas.width = video.videoWidth;
        canvas.height = video.videoHeight;
        context.drawImage(video, 0, 0, canvas.width, canvas.height);
        const image = context.getImageData(0, 0, canvas.width, canvas.height);
        const results = await zxing.readBarcodes(image, {
          tryHarder: true,
          formats: ["EAN-13", "EAN-8", "UPC-A", "UPC-E", "Code128", "QRCode"],
        });
        return results.find((result) => result.isValid !== false)?.text || null;
      };
    } catch (err) {
      status.textContent = `Could not load the barcode reader: ${err?.message || err}`;
      return null;
    }
  }

  _closeScanner() {
    if (!this._scanner) return;
    this._scanner.stop = true;
    clearTimeout(this._scanner.timer);
    this._scanner.stream?.getTracks().forEach((track) => track.stop());
    this._scanner = null;
  }

  /**
   * A barcode identifies the product, never the individual roll: every black
   * PLA from the same vendor carries the same code. A hit therefore adds
   * another instance of a known type, and a miss opens the type form.
   */
  async _handleCode(code) {
    this._closeDialog();
    let result;
    try {
      result = await this._call("filament_manager/scan", { code });
    } catch (err) {
      this._showDialog(
        "Scan failed",
        el("div", { class: "error", text: err?.message || String(err) }),
        [el("button", { text: "Close", onclick: () => this._closeDialog() })],
      );
      return;
    }
    if (result.known) {
      this._openKnownCode(result);
    } else {
      this._openNewType(code);
    }
  }

  _openKnownCode(result) {
    const type = result.types[0];
    const body = el(
      "div",
      {},
      el("div", { class: "scan-result", text: result.code }),
      el("div", { class: "hint", text: `Known product: ${type.label}` }),
      result.spools.length
        ? el("div", {
            class: "hint",
            text: `You already have ${result.spools.length} roll(s) of this type.`,
          })
        : null,
    );
    this._showDialog("Barcode recognised", body, [
      el("button", { text: "Cancel", onclick: () => this._closeDialog() }),
      el("button", {
        class: "primary",
        text: "Add a roll",
        onclick: () => {
          this._closeDialog();
          this._openAddSpool(type.id);
        },
      }),
    ]);
  }

  _openNewType(code) {
    const vendors = this._data.vendors || [];
    const vendorSelect = el(
      "select",
      {},
      vendors.map((vendor) => el("option", { value: vendor.id, text: vendor.name })),
    );
    const name = el("input", { type: "text", placeholder: "PLA Basic" });
    const material = el("input", { type: "text", value: "PLA" });
    const colorName = el("input", { type: "text" });
    const color = el("input", { type: "color", value: "#1f6feb" });
    const netWeight = el("input", { type: "number", min: "0", value: "1000" });
    const spoolWeight = el("input", { type: "number", min: "0", value: "0" });
    const error = el("div", { class: "error" });
    error.hidden = true;

    const body = el(
      "div",
      {},
      el("div", { class: "scan-result", text: code }),
      el("div", {
        class: "hint",
        text: "Unknown barcode. Describe the product once and it is remembered.",
      }),
      this._field("Manufacturer", vendorSelect),
      this._field("Product name", name),
      el(
        "div",
        { class: "row" },
        this._field("Material", material),
        this._field("Colour name", colorName),
      ),
      el(
        "div",
        { class: "row" },
        this._field("Colour", color),
        this._field("Nominal weight", netWeight),
      ),
      this._field(
        "Empty spool weight",
        spoolWeight,
        "Weigh an empty spool once — it makes re-weighing exact.",
      ),
      error,
    );

    const submit = el("button", {
      class: "primary",
      text: "Save and add a roll",
      onclick: async () => {
        if (!name.value.trim()) {
          error.hidden = false;
          error.textContent = "The product needs a name.";
          return;
        }
        submit.disabled = true;
        try {
          await this._call("filament_manager/add_type", {
            filament_type: {
              vendor_id: vendorSelect.value,
              name: name.value.trim(),
              material: material.value.trim().toUpperCase() || "PLA",
              color_name: colorName.value || null,
              color_hex: color.value.replace("#", "").toUpperCase(),
              net_weight: Number(netWeight.value) || 1000,
              spool_weight: Number(spoolWeight.value) || 0,
              gtin: [code],
            },
            add_spool: true,
          });
          this._closeDialog();
        } catch (err) {
          error.hidden = false;
          error.textContent = err?.message || String(err);
          submit.disabled = false;
        }
      },
    });

    this._showDialog("New filament type", body, [
      el("button", { text: "Cancel", onclick: () => this._closeDialog() }),
      submit,
    ]);
  }
}

/* ------------------------------------------------------------ ZXing glue */

let zxingPromise = null;

/**
 * Load the bundled ZXing reader on demand.
 *
 * It is roughly a megabyte of WebAssembly, so it is only fetched the first
 * time somebody actually scans something.
 */
function loadZXing() {
  if (zxingPromise) return zxingPromise;
  zxingPromise = new Promise((resolve, reject) => {
    if (window.ZXingWASM?.readBarcodes) {
      resolve(window.ZXingWASM);
      return;
    }
    const script = document.createElement("script");
    script.src = `${BASE_URL}zxing-reader.js?v=${CARD_VERSION}`;
    script.onload = () => {
      const zxing = window.ZXingWASM;
      if (!zxing?.readBarcodes) {
        reject(new Error("ZXing loaded but exposes no reader"));
        return;
      }
      zxing.prepareZXingModule({
        overrides: {
          locateFile: (path, prefix) =>
            path.endsWith(".wasm") ? `${BASE_URL}${path}` : `${prefix}${path}`,
        },
      });
      resolve(zxing);
    };
    script.onerror = () => reject(new Error("Could not load zxing-reader.js"));
    document.head.append(script);
  }).catch((err) => {
    zxingPromise = null;
    throw err;
  });
  return zxingPromise;
}

/* --------------------------------------------------------------- editor */

class FilamentManagerCardEditor extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = { ...DEFAULT_CONFIG };
  }

  setConfig(config) {
    this._config = { ...DEFAULT_CONFIG, ...(config || {}) };
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
  }

  _emit() {
    this.dispatchEvent(
      new CustomEvent("config-changed", {
        detail: { config: this._config },
        bubbles: true,
        composed: true,
      }),
    );
  }

  _render() {
    const root = this.shadowRoot;
    root.textContent = "";
    root.append(el("style", { text: STYLES }));

    const toggle = (key, label) => {
      const input = el("input", {
        type: "checkbox",
        checked: Boolean(this._config[key]),
        onchange: (event) => {
          this._config = { ...this._config, [key]: event.target.checked };
          this._emit();
        },
      });
      return el(
        "label",
        { style: { display: "flex", gap: "8px", alignItems: "center", margin: "6px 0" } },
        input,
        label,
      );
    };

    root.append(
      el(
        "div",
        { class: "dialog", style: { boxShadow: "none", padding: "8px" } },
        el(
          "div",
          { class: "field" },
          el("label", { text: "Title" }),
          el("input", {
            type: "text",
            value: this._config.title,
            oninput: (event) => {
              this._config = { ...this._config, title: event.target.value };
              this._emit();
            },
          }),
        ),
        el(
          "div",
          { class: "field" },
          el("label", { text: "Columns (0 = automatic)" }),
          el("input", {
            type: "number",
            min: "0",
            max: "4",
            value: String(this._config.columns),
            oninput: (event) => {
              this._config = {
                ...this._config,
                columns: Number(event.target.value) || 0,
              };
              this._emit();
            },
          }),
        ),
        toggle("show_stats", "Show the summary"),
        toggle("show_slots", "Show the AMS slots"),
        toggle("show_archived", "Show archived spools"),
        toggle("show_scanner", "Show the barcode scanner"),
      ),
    );
  }
}

customElements.define("filament-manager-card", FilamentManagerCard);
customElements.define("filament-manager-card-editor", FilamentManagerCardEditor);

window.customCards = window.customCards || [];
if (!window.customCards.some((card) => card.type === "filament-manager-card")) {
  window.customCards.push({
    type: "filament-manager-card",
    name: "Filament Manager",
    description: "Filament inventory, AMS slots and barcode scanning.",
    preview: true,
    documentationURL: "https://github.com/niclasreutter/FilamentMaster",
  });
}

console.info(
  `%c FILAMENT-MANAGER-CARD %c ${CARD_VERSION} `,
  "color: white; background: #1f6feb; font-weight: 700;",
  "color: #1f6feb; background: white; font-weight: 700;",
);
