// Collection screen — paged grid of owned cards, full-card detail panel.
//
// Phase 2 of the redesign: built on <dm-card> rather than per-screen card
// layout. Tile = compact (art + name); detail = full card (art + stats +
// abilities + flavor). The screen owns no card visuals; visuals live in
// <dm-card>'s @scope CSS.

import { backButton, el, fetchJSON } from "/screens/_dom.js";
import { openCardModal } from "/components/dm-card.js";
import { liveStore } from "/store.js";

const PAGE_SIZE = 8;

// Module-local state — replaced fresh on every render() call so re-entry
// doesn't carry stale fields. Mutated by event handlers, never by other
// modules.
let state = { rows: [], page: 0, selected: null, catalogSize: 0, catalogByRarity: {} };

const RARITY_ORDER = ["legendary", "epic", "rare", "uncommon", "common"];

function buildRows(payload) {
  const by = new Map();
  for (const s of payload.serials || []) {
    const id = s.card_id;
    if (!by.has(id)) {
      by.set(id, { card_id: id, rarity: s.rarity, count: 0, serials: [] });
    }
    const entry = by.get(id);
    entry.count++;
    entry.serials.push(s);
  }
  return Array.from(by.values()).sort((a, b) => {
    const ra = RARITY_ORDER.indexOf(a.rarity);
    const rb = RARITY_ORDER.indexOf(b.rarity);
    if (ra !== rb) return ra - rb;
    return a.card_id.localeCompare(b.card_id);
  });
}

function pageCount() {
  return Math.max(1, Math.ceil(state.rows.length / PAGE_SIZE));
}

function tileNode(row, root) {
  const sel = state.selected?.card_id === row.card_id;
  const node = el("button", {
    class: `coll-tile${sel ? " selected" : ""}`,
    onClick: () => { state.selected = row; rerender(root); },
  });
  const card = document.createElement("dm-card");
  card.setAttribute("card-id", row.card_id);
  card.setAttribute("size", "tile");
  node.appendChild(card);
  if (row.count > 1) {
    node.appendChild(el("div", { class: "coll-tile-count" }, `×${row.count}`));
  }
  return node;
}

function detailNode(_root) {
  const r = state.selected;
  if (!r) {
    return el("div", { class: "coll-detail empty" }, "select a card");
  }
  const card = document.createElement("dm-card");
  card.setAttribute("card-id", r.card_id);
  card.setAttribute("size", "detail");

  const sorted = [...r.serials].sort((a, b) =>
    (a.minted_at || "").localeCompare(b.minted_at || ""));
  const firstSerial = sorted[0];

  // Set mint stamp on the detail card from the first serial.
  if (firstSerial?.mint_number != null)
    card.setAttribute("data-mint-number", firstSerial.mint_number);
  if (firstSerial?.edition)
    card.setAttribute("data-edition", firstSerial.edition);

  const meta = el("div", { class: "coll-detail-meta" },
    el("div", { class: "coll-detail-count" },
      `${r.count} serial${r.count > 1 ? "s" : ""} owned`),
  );

  // Serial list — each row shows mint #, W/L, edition. Clicking opens
  // the full imprint modal for that serial.
  const serialList = el("div", { class: "coll-serial-list" });
  _populateSerialList(serialList, r.card_id, sorted);

  return el("div", { class: "coll-detail" },
    el("div", { class: "coll-detail-card" }, card),
    meta,
    serialList,
  );
}

function _populateSerialList(container, card_id, sorted) {
  for (const s of sorted) {
    const mintLabel = s.mint_number != null
      ? `#${String(s.mint_number).padStart(3, "0")}`
      : "—";
    const edLabel = s.edition ? `${s.edition.toUpperCase()} Ed` : "";
    const row = el("button", {
      class: "coll-serial-row",
      onClick: () => openCardModal(card_id, s.serial),
    },
      el("span", { class: "coll-serial-mint" }, mintLabel),
      el("span", { class: "coll-serial-record" }, ""),
      edLabel ? el("span", { class: "coll-serial-edition" }, edLabel) : null,
    );
    container.appendChild(row);
  }

  // Async-fetch imprint data to fill in W/L records.
  fetchJSON(`/api/imprint/card/${encodeURIComponent(card_id)}`)
    .then(imprints => {
      if (!Array.isArray(imprints)) return;
      const bySerial = {};
      for (const imp of imprints) bySerial[imp.serial] = imp;
      const rows = container.querySelectorAll(".coll-serial-row");
      sorted.forEach((s, i) => {
        const imp = bySerial[s.serial];
        if (imp && rows[i]) {
          const stats = imp.stats || {};
          const w = stats.wins || 0;
          const l = stats.losses || 0;
          const recordEl = rows[i].querySelector(".coll-serial-record");
          if (recordEl) recordEl.textContent = `W${w} L${l}`;
        }
      });
    })
    .catch(() => {});
}

/** Per-rarity completion strip — "OWNED 24/200" with a thin per-rarity
 *  bar so the player can see at-a-glance which tiers are sparse. */
function progressNode() {
  const totalCatalog = state.catalogSize || 0;
  const totalOwned = state.rows.length;
  if (!totalCatalog) return null;

  const strip = el("div", { class: "coll-progress" });
  const summary = el("div", { class: "coll-progress-total" },
    el("span", { class: "coll-progress-num" }, `${totalOwned}`),
    el("span", { class: "coll-progress-sep" }, "/"),
    el("span", { class: "coll-progress-cat" }, `${totalCatalog}`),
    el("span", { class: "coll-progress-lbl" }, "unique cards"),
  );
  strip.append(summary);

  // Owned counts by rarity — derived from rows we already have.
  const ownedByRarity = {};
  for (const row of state.rows) {
    ownedByRarity[row.rarity] = (ownedByRarity[row.rarity] || 0) + 1;
  }

  const bars = el("div", { class: "coll-rarity-bars" });
  for (const rarity of RARITY_ORDER) {
    const owned = ownedByRarity[rarity] || 0;
    const total = state.catalogByRarity[rarity] || 0;
    if (!total) continue;
    const pct = Math.round((owned / total) * 100);
    const bar = el("div", { class: "coll-rarity-bar", "data-rarity": rarity },
      el("span", { class: "coll-rarity-label" }, rarity),
      el("div", { class: "coll-rarity-track" },
        el("div", { class: "coll-rarity-fill", style: `width:${pct}%` })),
      el("span", { class: "coll-rarity-count" }, `${owned}/${total}`),
    );
    bars.append(bar);
  }
  strip.append(bars);
  return strip;
}

function pagerNode(root) {
  const pages = pageCount();
  return el("div", { class: "page-dots" },
    ...Array.from({ length: pages }, (_, i) =>
      el("button", {
        class: `page-dot${i === state.page ? " active" : ""}`,
        onClick: () => { state.page = i; rerender(root); },
      }),
    ),
  );
}

function rerender(root) {
  root.innerHTML = "";
  const start = state.page * PAGE_SIZE;
  const visible = state.rows.slice(start, start + PAGE_SIZE);
  const headerLabel = state.catalogSize
    ? `${state.rows.length} of ${state.catalogSize}`
    : `${state.rows.length} unique`;
  root.appendChild(el("div", { class: "screen coll-screen fade-in" },
    el("header", { class: "screen-header" },
      backButton(),
      el("h1", null, "COLLECTION"),
      el("div", { class: "screen-balance" }, headerLabel),
    ),
    el("div", { class: "coll-body" },
      el("div", { class: "coll-left" },
        progressNode(),
        el("div", { class: "coll-grid" },
          state.rows.length === 0
            ? el("div", { class: "empty" }, "(no cards yet — try a pull)")
            : visible.map(r => tileNode(r, root)),
        ),
        pagerNode(root),
      ),
      detailNode(root),
    ),
  ));
}

async function loadCollection() {
  const payload = await fetchJSON("/api/collection");
  state.rows = buildRows(payload);
  // Preserve the selection across refetches if it's still owned;
  // otherwise fall back to the first row so the detail pane isn't blank.
  if (state.selected) {
    const stillOwned = state.rows.find(r => r.card_id === state.selected.card_id);
    state.selected = stillOwned || state.rows[0] || null;
  } else if (state.rows.length > 0) {
    state.selected = state.rows[0];
  }
}

/** Catalog size + per-rarity totals. Loaded once per render — fixed
 *  data, doesn't change while the screen is open. Soft-fails to a 0
 *  total which collapses the progress strip. */
async function loadCatalog() {
  try {
    const cat = await fetchJSON("/api/catalog");
    const cards = cat.cards || [];
    state.catalogSize = cards.length;
    state.catalogByRarity = {};
    for (const c of cards) {
      state.catalogByRarity[c.rarity] = (state.catalogByRarity[c.rarity] || 0) + 1;
    }
  } catch {
    state.catalogSize = 0;
    state.catalogByRarity = {};
  }
}

export async function render(root) {
  state = { rows: [], page: 0, selected: null, catalogSize: 0, catalogByRarity: {} };
  root.innerHTML = `<div class="loading">loading collection…</div>`;
  try {
    await Promise.all([loadCollection(), loadCatalog()]);
  } catch (err) {
    root.innerHTML = `<div class="error">collection unreachable: ${err}</div>`;
    return;
  }
  rerender(root);

  // Live: any pull or skin equip elsewhere refetches and repaints.
  // Pulls add a new card_id (or bump the count of an existing one);
  // skin equips swap the art served by /art/{id}, so the card store
  // doesn't need to refetch payloads — but we still rerender to force
  // <dm-card> elements to re-request the art URL. Cheap.
  const unsubscribe = liveStore.subscribe((_s, frame) => {
    if (!frame) return;
    if (frame.kind === "pull" || frame.kind === "skin") {
      loadCollection().then(() => rerender(root)).catch(() => {});
    }
  });
  return unsubscribe;
}
