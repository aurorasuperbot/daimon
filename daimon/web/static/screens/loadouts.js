// Loadouts screen — visual list + 2-panel deck editor.
//
// List view: each loadout is a card panel showing its 6 card art
// thumbnails, name, and actions. The active loadout gets accent glow.
//
// Editor view: two panels — catalog browse (tiles with add/remove
// toggle, element badges) and the 6-slot deck strip with drag-to-reorder,
// synergy indicators, and power rating preview.

import { backButton, el, fetchJSON, postJSON, promptText } from "/screens/_dom.js";
import { openCardModal } from "/components/dm-card.js";
import { liveStore } from "/store.js";

const LOADOUT_SIZE = 6;
const CATALOG_PAGE_SIZE = 12;

const ELEMENT_COLORS = {
  FIRE:   "#ff6b4a",
  WATER:  "#5ca8ff",
  NATURE: "#6cd96c",
  VOID:   "#c9a4ff",
  VOLT:   "#ffdc78",
  NORMAL: "#9b9b9b",
};

let state = {
  view: "list",        // "list" | "editor"
  loadouts: [],
  activeName: null,
  catalog: [],
  catalogMap: {},      // card_id → card data for quick lookup
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
// editor view — 2-panel: catalog | slots + synergy + power
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

function computeDeckStats() {
  const elements = {};
  let totalAtk = 0, totalDef = 0, totalHp = 0, totalSpd = 0;
  let cardCount = 0;

  for (const cid of state.slots) {
    const card = state.catalogMap[cid];
    if (!card) continue;
    cardCount++;
    const elem = card.element || "NORMAL";
    elements[elem] = (elements[elem] || 0) + 1;
    totalAtk += card.atk || 0;
    totalDef += card.def || 0;
    totalHp  += card.hp  || 0;
    totalSpd += card.spd || 0;
  }

  const synergies = [];
  for (const [elem, count] of Object.entries(elements)) {
    if (count >= 2) {
      synergies.push({ element: elem, count, bonus: count >= 3 ? "strong" : "weak" });
    }
  }

  return {
    elements,
    synergies,
    power: totalAtk + totalDef + totalHp + totalSpd,
    stats: { atk: totalAtk, def: totalDef, hp: totalHp, spd: totalSpd },
    cardCount,
  };
}

function editorView(root) {
  const totalPages = Math.max(1, Math.ceil(state.catalog.length / CATALOG_PAGE_SIZE));
  const start = state.page * CATALOG_PAGE_SIZE;
  const visible = state.catalog.slice(start, start + CATALOG_PAGE_SIZE);
  const filled = state.slots.length;
  const remaining = LOADOUT_SIZE - filled;
  const ready = remaining === 0;

  const deck = computeDeckStats();

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
        el("div", { class: "slot-strip", "data-slot-strip": "" },
          ...Array.from({ length: LOADOUT_SIZE }, (_, i) => slotTile(i, root))),

        // Synergy indicators
        deck.cardCount > 0
          ? el("div", { class: "synergy-row" },
              ...Object.entries(deck.elements).map(([elem, count]) =>
                el("span", {
                  class: `synergy-chip${count >= 3 ? " strong" : count >= 2 ? " active" : ""}`,
                  style: `--elem-color: ${ELEMENT_COLORS[elem] || ELEMENT_COLORS.NORMAL}`,
                },
                  el("span", { class: "synergy-dot" }),
                  `${elem} ×${count}`,
                )
              ),
              ...deck.synergies.map(s =>
                el("span", { class: "synergy-bonus" },
                  s.bonus === "strong" ? `${s.element} TRIO!` : `${s.element} DUO`),
              ),
            )
          : null,

        // Power rating preview
        deck.cardCount > 0
          ? el("div", { class: "power-preview" },
              el("div", { class: "power-total" },
                el("span", { class: "power-label" }, "POWER"),
                el("span", { class: "power-value" }, String(deck.power)),
              ),
              el("div", { class: "power-stats" },
                statBar("ATK", deck.stats.atk, "#ff6b4a"),
                statBar("DEF", deck.stats.def, "#5ca8ff"),
                statBar("HP",  deck.stats.hp,  "#6cd96c"),
                statBar("SPD", deck.stats.spd, "#ffdc78"),
              ),
            )
          : null,

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

function statBar(label, value, color) {
  const maxStat = 60;
  const pct = Math.min(100, (value / maxStat) * 100);
  return el("div", { class: "stat-bar-row" },
    el("span", { class: "stat-bar-label" }, label),
    el("div", { class: "stat-bar-track" },
      el("div", { class: "stat-bar-fill", style: `width: ${pct}%; background: ${color}` }),
    ),
    el("span", { class: "stat-bar-val" }, String(value)),
  );
}

function catalogTile(card, root) {
  const inDeck = state.slots.includes(card.card_id);
  const full   = state.slots.length >= LOADOUT_SIZE;
  const dimmed = full && !inDeck;
  const elem = card.element || "NORMAL";

  const node = el("button", {
    class: `catalog-tile${inDeck ? " in-deck" : ""}${dimmed ? " dimmed" : ""}`,
    onClick: () => openCardModal(card.card_id),
  });
  const dm = document.createElement("dm-card");
  dm.setAttribute("card-id", card.card_id);
  dm.setAttribute("size", "tile");
  node.appendChild(dm);

  // Element badge
  const elemBadge = el("span", {
    class: "tile-element-badge",
    style: `--elem-color: ${ELEMENT_COLORS[elem] || ELEMENT_COLORS.NORMAL}`,
  }, elem.slice(0, 3));
  node.appendChild(elemBadge);

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
    const empty = el("div", { class: "slot-tile empty" },
      el("span", { class: "slot-num" }, `${idx + 1}`),
    );
    empty.addEventListener("dragover", (e) => {
      e.preventDefault();
      empty.classList.add("drag-over");
    });
    empty.addEventListener("dragleave", () => empty.classList.remove("drag-over"));
    empty.addEventListener("drop", (e) => {
      e.preventDefault();
      empty.classList.remove("drag-over");
    });
    return empty;
  }

  const card = state.catalogMap[cardId];
  const elem = card?.element || "NORMAL";

  const node = el("button", {
    class: "slot-tile filled",
    draggable: "true",
    "data-slot-idx": idx,
    title: "drag to reorder · click to remove",
  });

  // Element indicator strip on left edge
  const elemStrip = el("div", {
    class: "slot-element-strip",
    style: `background: ${ELEMENT_COLORS[elem] || ELEMENT_COLORS.NORMAL}`,
  });
  node.appendChild(elemStrip);

  const dm = document.createElement("dm-card");
  dm.setAttribute("card-id", cardId);
  dm.setAttribute("size", "tile");
  node.appendChild(dm);

  // Click to remove
  node.addEventListener("click", () => {
    state.slots = state.slots.filter((_, i) => i !== idx);
    rerender(root);
  });

  // Drag handlers
  node.addEventListener("dragstart", (e) => {
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", String(idx));
    node.classList.add("dragging");
  });
  node.addEventListener("dragend", () => {
    node.classList.remove("dragging");
    document.querySelectorAll(".drag-over").forEach(el => el.classList.remove("drag-over"));
  });
  node.addEventListener("dragover", (e) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    node.classList.add("drag-over");
  });
  node.addEventListener("dragleave", () => node.classList.remove("drag-over"));
  node.addEventListener("drop", (e) => {
    e.preventDefault();
    node.classList.remove("drag-over");
    const fromIdx = parseInt(e.dataTransfer.getData("text/plain"), 10);
    const toIdx = idx;
    if (fromIdx === toIdx || isNaN(fromIdx)) return;
    const newSlots = [...state.slots];
    const [moved] = newSlots.splice(fromIdx, 1);
    newSlots.splice(toIdx, 0, moved);
    state.slots = newSlots;
    rerender(root);
  });

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
  const cards = (payload.cards || []).slice().sort((a, b) => {
    const ORDER = ["legendary", "epic", "rare", "uncommon", "common"];
    const ra = ORDER.indexOf(a.rarity);
    const rb = ORDER.indexOf(b.rarity);
    if (ra !== rb) return ra - rb;
    return (a.card_id || "").localeCompare(b.card_id || "");
  });
  state.catalog = cards;
  state.catalogMap = {};
  for (const c of cards) {
    state.catalogMap[c.card_id] = c;
  }
}

function rerender(root) {
  root.innerHTML = "";
  root.appendChild(state.view === "editor" ? editorView(root) : listView(root));
}

export async function render(root) {
  state = {
    view: "list",
    loadouts: [], activeName: null,
    catalog: [], catalogMap: {}, page: 0,
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
