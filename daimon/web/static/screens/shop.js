// Shop screen — 6-tile grid + hero detail panel + BUY button.
// Backend: GET /api/shop, POST /api/shop/buy/{slot}.

import { backButton, el, fetchJSON, postJSON } from "/screens/_dom.js";

let state = { shop: null, balance: 0, selectedSlot: 0, error: null, busy: false };

async function load() {
  const [shop, home] = await Promise.all([
    fetchJSON("/api/shop"),
    fetchJSON("/api/home"),
  ]);
  state.shop = shop;
  state.balance = home.balance ?? 0;
  state.selectedSlot = (shop.slots || []).findIndex(s => !s.sold);
  if (state.selectedSlot < 0) state.selectedSlot = 0;
}

function tileNode(slot, root) {
  const sel = state.selectedSlot === slot.index;
  const node = el("button", {
    class: `shop-tile${sel ? " selected" : ""}${slot.sold ? " sold" : ""}`,
    onClick: () => { state.selectedSlot = slot.index; rerender(root); },
  });
  const art = document.createElement("card-art");
  art.setAttribute("card-id", slot.card_id);
  node.appendChild(art);
  node.appendChild(el("div", { class: "shop-tile-overlay" },
    el("div", { class: "shop-tile-name" }, slot.skin_name),
    slot.sold
      ? el("div", { class: "shop-tile-cost owned" }, "OWNED")
      : el("div", { class: "shop-tile-cost" }, `${slot.cost}¤`),
  ));
  return node;
}

function detailNode(root) {
  const slots = state.shop.slots || [];
  const slot = slots[state.selectedSlot];
  if (!slot) {
    return el("div", { class: "shop-detail empty" }, "no slot selected");
  }
  const cost = slot.cost;
  const canAfford = state.balance >= cost && !slot.sold;
  const btn = el("button", {
    class: "shop-buy-btn",
    disabled: state.busy || slot.sold || !canAfford ? true : false,
    onClick: () => buy(slot.index, root),
  }, slot.sold ? "OWNED" : `BUY  ${cost}¤`);
  const chip = document.createElement("rarity-chip");
  chip.setAttribute("rarity", slot.rarity);
  return el("div", { class: "shop-detail" },
    el("div", { class: "shop-detail-art" }, (() => {
      const a = document.createElement("card-art");
      a.setAttribute("card-id", slot.card_id);
      return a;
    })()),
    el("div", { class: "shop-detail-meta" },
      el("div", { class: "shop-detail-name" }, slot.skin_name),
      el("div", { class: "shop-detail-axis" }, slot.skin_axis),
      el("div", { class: "shop-detail-card" }, slot.card_id),
      chip,
    ),
    state.error ? el("div", { class: "error-line" }, state.error) : null,
    btn,
  );
}

async function buy(slotIdx, root) {
  state.busy = true;
  state.error = null;
  rerender(root);
  try {
    const out = await postJSON(`/api/shop/buy/${slotIdx}`);
    if (out.error) {
      state.error = out.message || out.error;
    } else {
      // Reload — the slot now reads as sold + balance dropped.
      await load();
    }
  } catch (err) {
    state.error = String(err);
  } finally {
    state.busy = false;
    rerender(root);
  }
}

function rerender(root) {
  if (!state.shop) {
    root.innerHTML = `<div class="loading">loading shop…</div>`;
    return;
  }
  root.innerHTML = "";
  const grid = el("div", { class: "shop-grid" },
    ...((state.shop.slots || []).map(s => tileNode(s, root))),
  );
  root.appendChild(el("div", { class: "screen shop-screen fade-in" },
    el("header", { class: "screen-header" },
      backButton(),
      el("h1", null, "SHOP"),
      el("div", { class: "screen-balance" }, `${state.balance}¤`),
    ),
    el("div", { class: "shop-body" },
      grid,
      detailNode(root),
    ),
  ));
}

export async function render(root) {
  state = { shop: null, balance: 0, selectedSlot: 0, error: null, busy: false };
  rerender(root);
  try {
    await load();
  } catch (err) {
    root.innerHTML = `<div class="error">shop unreachable: ${err}</div>`;
    return;
  }
  rerender(root);

  // Live balance updates from purchases in other windows.
  document.addEventListener("daimon:balance", (e) => {
    if (typeof e.detail?.balance === "number") {
      state.balance = e.detail.balance;
      rerender(root);
    }
  }, { once: false });
}
