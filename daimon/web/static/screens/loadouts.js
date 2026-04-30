// Loadouts screen — visual list + 2-panel deck editor.
//
// List view: each loadout is a card panel showing its 6 card art
// thumbnails, name, and actions. The active loadout gets accent glow.
//
// Editor view: two panels — catalog browse (tiles with add/remove
// toggle) and the 6-slot deck strip. Clicking a tile opens the
// full-detail card modal so players can read abilities before deciding.

import { backButton, el, fetchJSON, postJSON, promptText } from "/screens/_dom.js";
import { openCardModal } from "/components/dm-card.js";
import { liveStore } from "/store.js";

const LOADOUT_SIZE = 6;
const CATALOG_PAGE_SIZE = 12;

let state = {
  view: "list",        // "list" | "editor"
  loadouts: [],
  activeName: null,
  catalog: [],
  page: 0,
  editingName: "",
  slots: [],           // card_ids in the editor; length <= LOADOUT_SIZE
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
        ? emptyState(root)
        : el("div", { class: "loadouts-grid" },
            ...state.loadouts.map(lo => loadoutPanel(lo, root))),
    ),
  );
}

function emptyState(root) {
  return el("div", { class: "loadouts-empty" },
    el("div", { class: "empty-icon" }, "⚔"),
    el("div", { class: "empty-title" }, "NO LOADOUTS YET"),
    el("div", { class: "empty-hint" },
      "Build a team of 6 cards to bring into battle."),
    el("button", {
      class: "primary-btn",
      onClick: () => beginNewLoadout(root),
    }, "CREATE FIRST LOADOUT"),
  );
}

function loadoutPanel(lo, root) {
  const isActive = lo.active;
  const cardIds = lo.card_ids || [];

  const panel = el("div", {
    class: `loadout-panel${isActive ? " active" : ""}${lo.corrupt ? " corrupt" : ""}`,
  },
    el("div", { class: "loadout-header" },
      el("div", { class: "loadout-name" }, lo.name),
      isActive
        ? el("span", { class: "active-pill" }, "ACTIVE")
        : null,
    ),
    lo.corrupt
      ? el("div", { class: "error-line" }, lo.message || "corrupt file")
      : el("div", { class: "loadout-art-strip" },
          ...cardIds.map(cid =>
            el("img", {
              class: "loadout-art-thumb",
              src: `/art/${encodeURIComponent(cid)}`,
              alt: cid,
              draggable: "false",
              loading: "lazy",
            })
          ),
          ...Array.from({ length: Math.max(0, LOADOUT_SIZE - cardIds.length) }, () =>
            el("div", { class: "loadout-art-empty" })
          ),
        ),
    el("div", { class: "loadout-footer" },
      el("span", { class: "loadout-count" },
        lo.corrupt ? "" : `${lo.card_count} cards`),
      el("div", { class: "loadout-actions" },
        !isActive && !lo.corrupt
          ? el("button", {
              class: "btn-small btn-activate",
              onClick: (e) => { e.stopPropagation(); activateLoadout(lo.name, root); },
            }, "SET ACTIVE")
          : null,
        !lo.corrupt
          ? el("button", {
              class: "btn-small",
              onClick: (e) => { e.stopPropagation(); beginEditLoadout(lo.name, root); },
            }, "EDIT")
          : null,
        el("button", {
          class: "btn-small btn-danger",
          onClick: (e) => { e.stopPropagation(); deleteLoadout(lo.name, root); },
        }, "DELETE"),
      ),
    ),
  );

  if (!lo.corrupt) {
    panel.addEventListener("click", () => beginEditLoadout(lo.name, root));
    panel.style.cursor = "pointer";
  }
  return panel;
}

async function activateLoadout(name, root) {
  state.error = null;
  try {
    const out = await postJSON(`/api/loadout/${encodeURIComponent(name)}/activate`);
    if (out.error) state.error = out.message || out.error;
  } catch (err) {
    state.error = String(err);
  }
  await loadList();
  rerender(root);
}

async function deleteLoadout(name, root) {
  const confirm = await promptText({
    title: "DELETE LOADOUT",
    label: `Type the loadout name to confirm: ${name}`,
    placeholder: name,
    confirmLabel: "DELETE",
    validate: (v) => (v === name ? null : "name doesn't match"),
  });
  if (confirm !== name) return;
  try {
    await fetch(`/api/loadout/${encodeURIComponent(name)}`, { method: "DELETE" });
  } catch (err) {
    state.error = String(err);
  }
  await loadList();
  rerender(root);
}

// ---------------------------------------------------------------------------
// editor view — 2-panel: catalog | slots
// ---------------------------------------------------------------------------

async function beginNewLoadout(root) {
  const existing = new Set((state.loadouts || []).map(lo => lo.name));
  const name = await promptText({
    title: "NEW LOADOUT",
    label: "Name this loadout:",
    placeholder: "e.g. inferno_swarm",
    confirmLabel: "CREATE",
    validate: (v) => {
      if (!v) return "name required";
      if (!/^[A-Za-z0-9_\- ]+$/.test(v))
        return "letters, digits, spaces, _ or - only";
      if (existing.has(v)) return `loadout '${v}' already exists`;
      return null;
    },
  });
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
      el("h1", null, state.editingName),
      el("div", { class: `validity-chip ${ready ? "ready" : "incomplete"}` },
        ready ? "READY" : `NEED ${remaining}`),
    ),
    el("div", { class: "editor-body" },
      el("div", { class: "editor-catalog" },
        el("div", { class: "catalog-header" },
          el("span", { class: "catalog-title" }, "CATALOG"),
          el("span", { class: "catalog-hint" }, "click to inspect · ➕ to add"),
          el("span", { class: "catalog-page-num" },
            `${state.page + 1} / ${totalPages}`),
        ),
        el("div", { class: "catalog-grid" },
          ...visible.map(c => catalogTile(c, root))),
        el("div", { class: "page-controls" },
          el("button", {
            class: "page-arrow",
            disabled: state.page === 0 ? true : false,
            onClick: () => { state.page = Math.max(0, state.page - 1); rerender(root); },
          }, "◀"),
          el("div", { class: "page-dots" },
            ...Array.from({ length: totalPages }, (_, i) =>
              el("button", {
                class: `page-dot${i === state.page ? " active" : ""}`,
                onClick: () => { state.page = i; rerender(root); },
              }))),
          el("button", {
            class: "page-arrow",
            disabled: state.page >= totalPages - 1 ? true : false,
            onClick: () => { state.page = Math.min(totalPages - 1, state.page + 1); rerender(root); },
          }, "▶"),
        ),
      ),
      el("div", { class: "editor-slots" },
        el("h3", null, `DECK (${filled}/${LOADOUT_SIZE})`),
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
  const dimmed = full && !inDeck;
  const node = el("button", {
    class: `catalog-tile${inDeck ? " in-deck" : ""}${dimmed ? " dimmed" : ""}`,
    onClick: () => openCardModal(card.card_id),
  });
  const dm = document.createElement("dm-card");
  dm.setAttribute("card-id", card.card_id);
  dm.setAttribute("size", "tile");
  node.appendChild(dm);

  const toggleBtn = el("button", {
    class: `tile-toggle ${inDeck ? "remove" : "add"}`,
    title: inDeck ? "Remove from deck" : "Add to deck",
    onClick: (e) => {
      e.stopPropagation();
      if (inDeck) {
        state.slots = state.slots.filter(id => id !== card.card_id);
      } else if (!full) {
        state.slots = [...state.slots, card.card_id];
      }
      rerender(root);
    },
  }, inDeck ? "−" : "+");
  node.appendChild(toggleBtn);

  if (inDeck) {
    node.appendChild(el("div", { class: "tile-in-deck-badge" }, "✓"));
  }
  return node;
}

function slotTile(idx, root) {
  const cardId = state.slots[idx];
  if (!cardId) {
    return el("div", { class: "slot-tile empty" },
      el("span", { class: "slot-num" }, `${idx + 1}`),
    );
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

  const unsubscribe = liveStore.subscribe((_s, frame) => {
    if (frame?.kind !== "loadout") return;
    loadList().then(() => {
      if (state.view === "list") rerender(root);
    }).catch(() => {});
  });
  return unsubscribe;
}
