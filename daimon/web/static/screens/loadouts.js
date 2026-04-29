// Loadouts screen — list view + 6-card editor. Phase 5 redesign:
// every card-shaped surface (catalog grid, slot strip) renders via
// <dm-card>. Layout chrome stays in this screen; card visuals are
// the primitive's responsibility.

import { backButton, el, fetchJSON, postJSON } from "/screens/_dom.js";

const LOADOUT_SIZE = 6;
const CATALOG_PAGE_SIZE = 12;

let state = {
  view: "list",        // "list" | "editor"
  loadouts: [],
  activeName: null,
  catalog: [],
  page: 0,
  editingName: "",
  slots: [],           // card_ids in the editor; length ≤ LOADOUT_SIZE
  saving: false,
  error: null,
};

// ---------------------------------------------------------------------------
// list view
// ---------------------------------------------------------------------------

function listView(root) {
  return el("div", { class: "screen loadouts-screen fade-in" },
    el("header", { class: "screen-header" },
      backButton(),
      el("h1", null, "LOADOUTS"),
      el("button", {
        class: "screen-action",
        onClick: () => beginNewLoadout(root),
      }, "+ NEW"),
    ),
    el("div", { class: "loadouts-body" },
      state.loadouts.length === 0
        ? el("div", { class: "empty" }, "no saved loadouts — click + NEW")
        : el("ul", { class: "loadouts-list" },
            ...state.loadouts.map(lo => loadoutRow(lo, root))),
    ),
  );
}

function loadoutRow(lo, root) {
  const isActive = lo.active;
  return el("li", { class: `loadout-row${isActive ? " active" : ""}` },
    el("div", { class: "loadout-name" },
      lo.name,
      isActive ? el("span", { class: "active-pill" }, "ACTIVE") : null,
    ),
    el("div", { class: "loadout-meta" },
      lo.corrupt
        ? el("span", { class: "error-line" }, `corrupt: ${lo.message}`)
        : `${lo.card_count} cards`,
    ),
    el("div", { class: "loadout-actions" },
      el("button", { class: "btn-small",
        onClick: () => beginEditLoadout(lo.name, root) }, "EDIT"),
      el("button", { class: "btn-small",
        onClick: () => deleteLoadout(lo.name, root) }, "DELETE"),
    ),
  );
}

async function deleteLoadout(name, root) {
  try {
    await fetch(`/api/loadout/${encodeURIComponent(name)}`, { method: "DELETE" });
  } catch (err) {
    state.error = String(err);
  }
  await loadList();
  rerender(root);
}

// ---------------------------------------------------------------------------
// editor view
// ---------------------------------------------------------------------------

function beginNewLoadout(root) {
  const name = (window.prompt("Name this loadout:") || "").trim();
  if (!name) return;
  state.editingName = name;
  state.slots = [];
  state.error = null;
  state.view = "editor";
  rerender(root);
}

async function beginEditLoadout(name, root) {
  state.error = null;
  let payload;
  try {
    payload = await fetchJSON(`/api/loadout/${encodeURIComponent(name)}`);
  } catch (err) {
    state.error = `failed to load: ${err}`;
    rerender(root);
    return;
  }
  state.editingName = name;
  state.slots = (payload.cards || []).map(c => c.card_id).filter(Boolean);
  state.view = "editor";
  rerender(root);
}

function editorView(root) {
  const totalPages = Math.max(1, Math.ceil(state.catalog.length / CATALOG_PAGE_SIZE));
  const start = state.page * CATALOG_PAGE_SIZE;
  const visible = state.catalog.slice(start, start + CATALOG_PAGE_SIZE);
  const filled = state.slots.length;
  const remaining = LOADOUT_SIZE - filled;
  const ready = remaining === 0;

  return el("div", { class: "screen loadouts-editor fade-in" },
    el("header", { class: "screen-header" },
      el("button", { class: "back-btn",
        onClick: () => { state.view = "list"; rerender(root); } }, "← BACK"),
      el("h1", null, `EDIT — ${state.editingName}`),
      el("div", { class: `validity-chip ${ready ? "ready" : "incomplete"}` },
        ready ? "READY" : `NEED ${remaining}`),
    ),
    el("div", { class: "editor-body" },
      el("div", { class: "editor-catalog" },
        el("div", { class: "catalog-grid" },
          ...visible.map(c => catalogTile(c, root))),
        el("div", { class: "page-dots" },
          ...Array.from({ length: totalPages }, (_, i) =>
            el("button", {
              class: `page-dot${i === state.page ? " active" : ""}`,
              onClick: () => { state.page = i; rerender(root); },
            }))),
      ),
      el("div", { class: "editor-slots" },
        el("h3", null, `LOADOUT (${filled}/${LOADOUT_SIZE})`),
        el("div", { class: "slot-strip" },
          ...Array.from({ length: LOADOUT_SIZE }, (_, i) => slotTile(i, root))),
        state.error ? el("div", { class: "error-line" }, state.error) : null,
        el("div", { class: "editor-actions" },
          el("button", {
            class: "primary-btn",
            disabled: !ready || state.saving ? true : false,
            onClick: () => saveCurrent(root),
          }, state.saving ? "saving…" : "SAVE"),
          el("button", {
            class: "btn-small",
            onClick: () => { state.view = "list"; rerender(root); },
          }, "QUIT"),
        ),
      ),
    ),
  );
}

function catalogTile(card, root) {
  const inDeck = state.slots.includes(card.card_id);
  const full   = state.slots.length >= LOADOUT_SIZE;
  const node = el("button", {
    class: `catalog-tile${inDeck ? " in-deck" : ""}`,
    disabled: (full && !inDeck) ? true : false,
    onClick: () => {
      if (state.slots.length >= LOADOUT_SIZE) return;
      state.slots = [...state.slots, card.card_id];
      rerender(root);
    },
  });
  const dm = document.createElement("dm-card");
  dm.setAttribute("card-id", card.card_id);
  dm.setAttribute("size", "tile");
  node.appendChild(dm);
  return node;
}

function slotTile(idx, root) {
  const cardId = state.slots[idx];
  if (!cardId) {
    return el("div", { class: "slot-tile empty" }, `slot ${idx + 1}`);
  }
  const node = el("button", {
    class: "slot-tile filled",
    title: "click to remove",
    onClick: () => {
      state.slots = state.slots.filter((_, i) => i !== idx);
      rerender(root);
    },
  });
  const dm = document.createElement("dm-card");
  dm.setAttribute("card-id", cardId);
  dm.setAttribute("size", "tile");
  node.appendChild(dm);
  return node;
}

async function saveCurrent(root) {
  state.saving = true;
  state.error = null;
  rerender(root);
  try {
    const out = await postJSON(
      `/api/loadout/${encodeURIComponent(state.editingName)}`,
      { card_ids: state.slots },
    );
    if (out.error) {
      state.error = out.message || out.error;
    } else {
      await loadList();
      state.view = "list";
    }
  } catch (err) {
    state.error = String(err);
  } finally {
    state.saving = false;
    rerender(root);
  }
}

// ---------------------------------------------------------------------------
// data loaders
// ---------------------------------------------------------------------------

async function loadList() {
  const payload = await fetchJSON("/api/loadouts");
  state.loadouts = payload.loadouts || [];
  state.activeName = payload.active_loadout || null;
}

async function loadCatalog() {
  const payload = await fetchJSON("/api/catalog");
  state.catalog = (payload.cards || []).slice().sort((a, b) => {
    const ORDER = ["legendary", "epic", "rare", "uncommon", "common"];
    const ra = ORDER.indexOf(a.rarity);
    const rb = ORDER.indexOf(b.rarity);
    if (ra !== rb) return ra - rb;
    return (a.card_id || "").localeCompare(b.card_id || "");
  });
}

function rerender(root) {
  root.innerHTML = "";
  root.appendChild(state.view === "editor" ? editorView(root) : listView(root));
}

export async function render(root) {
  state = {
    view: "list",
    loadouts: [], activeName: null,
    catalog: [], page: 0,
    editingName: "", slots: [],
    saving: false, error: null,
  };
  root.innerHTML = `<div class="loading">loading loadouts…</div>`;
  try {
    await Promise.all([loadList(), loadCatalog()]);
  } catch (err) {
    root.innerHTML = `<div class="error">loadouts unreachable: ${err}</div>`;
    return;
  }
  rerender(root);
}
