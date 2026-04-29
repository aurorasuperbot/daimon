// Collection screen — paged grid of owned cards, full-card detail panel.
//
// Phase 2 of the redesign: built on <dm-card> rather than per-screen card
// layout. Tile = compact (art + name); detail = full card (art + stats +
// abilities + flavor). The screen owns no card visuals; visuals live in
// <dm-card>'s @scope CSS.

import { backButton, el, fetchJSON } from "/screens/_dom.js";

const PAGE_SIZE = 8;

// Module-local state — replaced fresh on every render() call so re-entry
// doesn't carry stale fields. Mutated by event handlers, never by other
// modules.
let state = { rows: [], page: 0, selected: null };

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
  const RARITY = ["legendary", "epic", "rare", "uncommon", "common"];
  return Array.from(by.values()).sort((a, b) => {
    const ra = RARITY.indexOf(a.rarity);
    const rb = RARITY.indexOf(b.rarity);
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
  return el("div", { class: "coll-detail" },
    el("div", { class: "coll-detail-card" }, card),
    el("div", { class: "coll-detail-meta" },
      el("div", { class: "coll-detail-count" },
        `${r.count} serial${r.count > 1 ? "s" : ""} owned`),
    ),
  );
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
  root.appendChild(el("div", { class: "screen coll-screen fade-in" },
    el("header", { class: "screen-header" },
      backButton(),
      el("h1", null, "COLLECTION"),
      el("div", { class: "screen-balance" },
        `${state.rows.length} unique`),
    ),
    el("div", { class: "coll-body" },
      el("div", { class: "coll-left" },
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

export async function render(root) {
  state = { rows: [], page: 0, selected: null };
  root.innerHTML = `<div class="loading">loading collection…</div>`;
  try {
    const payload = await fetchJSON("/api/collection");
    state.rows = buildRows(payload);
    if (state.rows.length > 0) state.selected = state.rows[0];
  } catch (err) {
    root.innerHTML = `<div class="error">collection unreachable: ${err}</div>`;
    return;
  }
  rerender(root);
}
