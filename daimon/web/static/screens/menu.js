// Menu screen — dashboard. Phase 2: action buttons navigate to real
// screens. Currency strip listens for daimon:balance events.

import { go } from "/app.js";

const PULL_COST = 100;

const ACTIONS = [
  { id: "pull",       label: "PULL",       hash: "#pull"       },
  { id: "match",      label: "MATCH",      hash: "#match"      },
  { id: "loadouts",   label: "LOADOUTS",   hash: "#loadouts"   },
  { id: "collection", label: "COLLECTION", hash: "#collection" },
  { id: "shop",       label: "SHOP",       hash: "#shop"       },
];

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k.startsWith("on") && typeof v === "function") {
      node.addEventListener(k.slice(2).toLowerCase(), v);
    } else if (v !== false && v !== null && v !== undefined) {
      node.setAttribute(k, v);
    }
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.appendChild(typeof child === "string"
      ? document.createTextNode(child)
      : child);
  }
  return node;
}

function shortPubkey(hex) {
  if (!hex || hex.length < 12) return hex || "—";
  return `${hex.slice(0, 6)}…${hex.slice(-4)}`;
}

function renderHeader(payload) {
  const ident = payload.identity || {};
  const rank = payload.rank || {};
  return el("header", { class: "menu-header" },
    el("h1", { class: "menu-title" }, "DAIMON"),
    el("div", { class: "menu-identity" },
      el("div", { class: "handle" }, ident.handle || "<unregistered>"),
      el("div", null, `${rank.tier || "Rookie"} #${rank.rank ?? "?"} of ${rank.total_players ?? "?"}`),
      el("div", { class: "pubkey" }, shortPubkey(ident.pubkey_hex)),
    ),
  );
}

function renderCurrencyStrip(payload) {
  const balance = payload.balance ?? 0;
  const pull = payload.pull || {};
  const cost = pull.cost ?? PULL_COST;
  const remaining = pull.balance_to_next_pull ?? cost;
  const fraction = 1 - Math.max(0, Math.min(remaining, cost)) / cost;
  const ready = pull.pulls_available > 0;
  return el("div", { class: "currency-strip", id: "currency-strip" },
    el("div", { class: "currency-amount", id: "currency-amount" }, String(balance)),
    el("div", { class: "currency-progress" },
      el("div", { class: "fill", id: "currency-fill", style: `width:${(fraction * 100).toFixed(1)}%` }),
    ),
    el("div", { class: "currency-label", id: "currency-label" },
      ready
        ? `${pull.pulls_available}× ready to pull`
        : `next pull in ${remaining}¤`,
    ),
  );
}

function renderActionRow() {
  return el("div", { class: "action-row" },
    ...ACTIONS.map(a =>
      el("button", { class: "action-btn", onClick: () => go(a.hash) }, a.label),
    ),
  );
}

function renderQuestsPanel(payload) {
  const quests = payload.daily_quests || [];
  return el("section", { class: "panel" },
    el("h2", null, "DAILY QUESTS"),
    quests.length === 0
      ? el("div", { class: "empty" }, "no quests rolled yet")
      : el("ul", null, ...quests.map(q =>
          el("li", { class: q.complete ? "complete" : "" },
            el("span", null, q.title),
            el("span", { class: "reward" },
              q.claimed ? "✓" : `+${q.reward}¤`),
          ),
        )),
  );
}

function renderActivityPanel(payload) {
  const matches = (payload.recent_matches || []).slice(0, 4);
  const pulls = (payload.recent_pulls || []).slice(0, 4);
  const items = [];
  for (const m of matches) {
    items.push(`vs ${m.opponent || "?"} — ${m.outcome || "?"}`);
  }
  for (const p of pulls) {
    items.push(`pulled ${p.card_id || "?"} (${p.rarity || "?"})`);
  }
  return el("section", { class: "panel" },
    el("h2", null, "RECENT ACTIVITY"),
    items.length === 0
      ? el("div", { class: "empty" }, "no activity yet — try a pull")
      : el("ul", null, ...items.map(text =>
          el("li", null, el("span", null, text)),
        )),
  );
}

function renderHero(payload) {
  const npc = payload.recommended_npc;
  const cardId = npc?.npc_id;
  const node = el("div", { class: "hero-card" });
  if (cardId) {
    const art = document.createElement("card-art");
    art.setAttribute("card-id", cardId);
    node.appendChild(art);
  } else {
    node.appendChild(el("div", { class: "placeholder" }, "DAIMON"));
  }
  return node;
}

function renderFooter(payload) {
  const ver = payload.identity?.version || "";
  return el("footer", { class: "menu-footer" },
    el("span", null, "DAIMON" + (ver ? ` v${ver}` : "")),
    el("span", null, "press button to play"),
  );
}

function listenForBalance(payload) {
  const handler = (e) => {
    const balance = e.detail?.balance;
    if (typeof balance !== "number") return;
    const amt = document.getElementById("currency-amount");
    if (amt) amt.textContent = String(balance);
    // Recompute progress relative to PULL_COST.
    const cost = payload.pull?.cost ?? PULL_COST;
    const remaining = Math.max(0, cost - (balance % cost));
    const fraction = 1 - remaining / cost;
    const fill = document.getElementById("currency-fill");
    if (fill) fill.style.width = `${(fraction * 100).toFixed(1)}%`;
    const label = document.getElementById("currency-label");
    if (label) {
      const ready = balance >= cost;
      label.textContent = ready
        ? `${Math.floor(balance / cost)}× ready to pull`
        : `next pull in ${remaining}¤`;
    }
  };
  document.addEventListener("daimon:balance", handler);
}

export async function render(root) {
  let payload;
  try {
    const r = await fetch("/api/home");
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    payload = await r.json();
  } catch (err) {
    root.innerHTML = `<div class="error">backend unreachable: ${err}</div>`;
    return;
  }
  if (payload.error) {
    root.innerHTML = `<div class="error">backend error: ${payload.error}</div>`;
    return;
  }

  root.innerHTML = "";
  const app = el("div", { class: "menu-app fade-in" },
    renderHeader(payload),
    el("div", { class: "menu-body" },
      el("div", { class: "menu-left" },
        renderCurrencyStrip(payload),
        renderActionRow(),
        el("div", { class: "panels" },
          renderQuestsPanel(payload),
          renderActivityPanel(payload),
        ),
      ),
      el("div", { class: "menu-right" },
        renderHero(payload),
      ),
    ),
    renderFooter(payload),
  );
  root.appendChild(app);
  listenForBalance(payload);
}
