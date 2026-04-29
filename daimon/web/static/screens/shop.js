// Shop screen — 6-tile grid + full-card detail panel + BUY button.
// Backend: GET /api/shop, POST /api/shop/buy/{slot}.
//
// Phase 5 of the redesign: tiles and detail both use <dm-card>; balance
// pill is driven by liveStore.subscribe (no more daimon:balance custom
// events). Returns a cleanup function so app.js can detach the
// subscription on navigate-away.

import { backButton, el, fetchJSON, postJSON } from "/screens/_dom.js";
import { liveStore } from "/store.js";

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
  const card = document.createElement("dm-card");
  card.setAttribute("card-id", slot.card_id);
  card.setAttribute("size", "tile");
  node.appendChild(card);
  // Cost / OWNED chip — top-right corner. The card name is rendered
  // by <dm-card> itself; we don't double it up here.
  node.appendChild(slot.sold
    ? el("div", { class: "shop-tile-chip owned" }, "OWNED")
    : el("div", { class: "shop-tile-chip" }, `${slot.cost}¤`));
  return node;
}

function detailNode(root) {
  const slots = state.shop.slots || [];
  const slot = slots[state.selectedSlot];
  if (!slot) {
    return el("div", { class: "shop-detail empty" }, "no slot selected");
  }
  const cost = slot.cost;
  const weeklyCapped = (state.shop.weekly_remaining ?? 99) <= 0;
  const canAfford = state.balance >= cost;
  const disabled =
    state.busy || slot.sold || !canAfford || weeklyCapped;

  // Reason hint surfaces WHY the button is disabled — replaces the
  // "dead button" UX where players couldn't tell what was wrong.
  let reason = null;
  if (!slot.sold) {
    if (!canAfford) {
      reason = `need ${cost - state.balance}¤ more`;
    } else if (weeklyCapped) {
      reason = "weekly purchase cap reached";
    }
  }

  const btn = el("button", {
    class: "shop-buy-btn",
    disabled: disabled ? true : false,
    onClick: () => buy(slot.index, root),
  }, slot.sold ? "OWNED" : `BUY  ${cost}¤`);

  const card = document.createElement("dm-card");
  card.setAttribute("card-id", slot.card_id);
  card.setAttribute("size", "detail");

  return el("div", { class: "shop-detail" },
    el("div", { class: "shop-detail-card" }, card),
    el("div", { class: "shop-detail-meta" },
      slot.skin_name
        ? el("div", { class: "shop-detail-skin" },
            el("span", { class: "shop-detail-skin-label" }, "skin"),
            el("span", { class: "shop-detail-name" }, slot.skin_name))
        : el("div", { class: "shop-detail-name" }, slot.card_id),
      slot.skin_axis
        ? el("div", { class: "shop-detail-axis" }, slot.skin_axis)
        : null,
    ),
    state.error ? el("div", { class: "error-line" }, state.error) : null,
    reason ? el("div", { class: "shop-buy-reason" }, reason) : null,
    btn,
  );
}

/** Format seconds → "2d 14h" / "14h 22m" / "22m". For longer ranges
 *  show days+hours; under an hour show minutes only. */
function formatCountdown(secs) {
  if (!Number.isFinite(secs) || secs <= 0) return null;
  const d = Math.floor(secs / 86400);
  const h = Math.floor((secs % 86400) / 3600);
  const m = Math.floor((secs % 3600) / 60);
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
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
  const weeklyRem = state.shop.weekly_remaining;
  const weeklyCap = state.shop.weekly_cap;
  const rotationTxt = formatCountdown(state.shop.seconds_until_rotation);
  const meta = el("div", { class: "shop-meta" });
  if (Number.isFinite(weeklyRem) && Number.isFinite(weeklyCap)) {
    const cls = weeklyRem === 0 ? "shop-meta-stat capped" : "shop-meta-stat";
    meta.append(el("div", { class: cls },
      el("span", { class: "shop-meta-val" }, `${weeklyRem}/${weeklyCap}`),
      el("span", { class: "shop-meta-lbl" }, "buys this week"),
    ));
  }
  if (rotationTxt) {
    meta.append(el("div", { class: "shop-meta-stat" },
      el("span", { class: "shop-meta-val" }, rotationTxt),
      el("span", { class: "shop-meta-lbl" }, "until rotation"),
    ));
  }

  root.appendChild(el("div", { class: "screen shop-screen fade-in" },
    el("header", { class: "screen-header" },
      backButton(),
      el("h1", null, "SHOP"),
      el("div", { class: "screen-balance", id: "shop-balance" }, `${state.balance}¤`),
    ),
    meta.children.length ? meta : null,
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

  // Live balance from purchases — patches the header pill in place.
  // ALSO listen for `kind: "purchase"` frames so an agent-driven buy
  // (or a buy from another tab) refetches the shop and flips the
  // bought slot to "sold" without a manual refresh.
  const unsubscribe = liveStore.subscribe((s, frame) => {
    if (typeof s.balance === "number") {
      state.balance = s.balance;
      const pill = document.getElementById("shop-balance");
      if (pill) pill.textContent = `${s.balance}¤`;
      const detail = root.querySelector(".shop-detail");
      if (detail) {
        const fresh = detailNode(root);
        detail.replaceWith(fresh);
      }
    }
    if (frame?.kind === "purchase") {
      // Refetch — the slot rotation is unchanged but slot[i].sold flips.
      load().then(() => rerender(root)).catch(() => {});
    }
  });

  return unsubscribe;
}
