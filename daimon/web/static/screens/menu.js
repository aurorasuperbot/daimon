// Menu screen — dashboard. Built on the redesign primitives:
//   - <dm-card size="hero"> for the recommended-NPC hero (shared
//     view-transition-name with the pull-card front, so the
//     menu→pull route swap morphs the same element).
//   - liveStore.subscribe for balance ticking (replaces the
//     legacy `daimon:balance` custom-event indirection).
// The screen mounts ONE stable DOM tree and patches text-node
// content as live balance arrives. No re-renders.

import { go } from "/app.js";
import { el, fetchJSON } from "/screens/_dom.js";
import { liveStore } from "/store.js";

const PULL_COST = 100;

const ACTIONS = [
  { id: "pull",       label: "PULL",       hash: "#pull"       },
  { id: "match",      label: "MATCH",      hash: "#match"      },
  { id: "loadouts",   label: "LOADOUTS",   hash: "#loadouts"   },
  { id: "collection", label: "COLLECTION", hash: "#collection" },
  { id: "shop",       label: "SHOP",       hash: "#shop"       },
];

function shortPubkey(hex) {
  if (!hex || hex.length < 12) return hex || "—";
  return `${hex.slice(0, 6)}…${hex.slice(-4)}`;
}

function renderHeader(payload) {
  const ident = payload.identity || {};
  const rank  = payload.rank || {};
  return el("header", { class: "menu-header" },
    el("h1", { class: "menu-title" }, "DAIMON"),
    el("div", { class: "menu-identity" },
      el("div", { class: "handle" }, ident.handle || "<unregistered>"),
      el("div", null,
        `${rank.tier || "Rookie"} #${rank.rank ?? "?"} of ${rank.total_players ?? "?"}`),
      el("div", { class: "pubkey" }, shortPubkey(ident.pubkey_hex)),
    ),
  );
}

function renderCurrencyStrip(payload) {
  const balance   = payload.balance ?? 0;
  const pull      = payload.pull || {};
  const cost      = pull.cost ?? PULL_COST;
  const remaining = pull.balance_to_next_pull ?? cost;
  const fraction  = 1 - Math.max(0, Math.min(remaining, cost)) / cost;
  const ready     = pull.pulls_available > 0;
  return el("div", { class: "currency-strip", id: "currency-strip" },
    el("div", { class: "currency-amount", id: "currency-amount" }, String(balance)),
    el("div", { class: "currency-progress" },
      el("div", { class: "fill", id: "currency-fill",
                  style: `width:${(fraction * 100).toFixed(1)}%` }),
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
  const pulls   = (payload.recent_pulls   || []).slice(0, 4);
  const items = [];
  for (const m of matches) items.push(`vs ${m.opponent || "?"} — ${m.outcome || "?"}`);
  for (const p of pulls)   items.push(`pulled ${p.card_id || "?"} (${p.rarity || "?"})`);
  return el("section", { class: "panel" },
    el("h2", null, "RECENT ACTIVITY"),
    items.length === 0
      ? el("div", { class: "empty" }, "no activity yet — try a pull")
      : el("ul", null, ...items.map(text =>
          el("li", null, el("span", null, text)),
        )),
  );
}

/** The hero card uses <dm-card size="hero">; that size CSS includes
 *  view-transition-name=dm-card-hero so the menu→pull swap morphs
 *  this exact card into the pull-front when the user pulls. */
function renderHero(payload) {
  const cardId = payload.recommended_npc?.cover_card_id;
  if (!cardId) {
    return el("div", { class: "hero-card placeholder" }, "DAIMON");
  }
  const card = document.createElement("dm-card");
  card.setAttribute("card-id", cardId);
  card.setAttribute("size", "hero");
  return card;
}

function renderFooter(payload) {
  const ver = payload.identity?.version || "";
  return el("footer", { class: "menu-footer" },
    el("span", null, "DAIMON" + (ver ? ` v${ver}` : "")),
    el("span", null, "press button to play"),
  );
}

/** Subscribe the currency strip to liveStore. The strip is a fixed DOM
 *  tree built once; the subscription patches the three live nodes
 *  (amount, fill width, label). Returns an unsubscribe function so
 *  app.js can detach when the user navigates away. */
function listenForBalance(payload) {
  const cost = payload.pull?.cost ?? PULL_COST;
  return liveStore.subscribe(state => {
    if (typeof state.balance !== "number") return;
    const balance   = state.balance;
    const remaining = Math.max(0, cost - (balance % cost));
    const fraction  = 1 - remaining / cost;

    const amt = document.getElementById("currency-amount");
    if (amt) amt.textContent = String(balance);

    const fill = document.getElementById("currency-fill");
    if (fill) fill.style.width = `${(fraction * 100).toFixed(1)}%`;

    const label = document.getElementById("currency-label");
    if (label) {
      const ready = balance >= cost;
      label.textContent = ready
        ? `${Math.floor(balance / cost)}× ready to pull`
        : `next pull in ${remaining}¤`;
    }
  });
}

function paint(root, payload) {
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
}

export async function render(root) {
  let payload;
  try {
    payload = await fetchJSON("/api/home");
  } catch (err) {
    root.innerHTML = `<div class="error">backend unreachable: ${err}</div>`;
    return;
  }
  if (payload.error) {
    root.innerHTML = `<div class="error">backend error: ${payload.error}</div>`;
    return;
  }
  paint(root, payload);

  // Balance pill stays patched in-place by liveStore (cheap). Larger
  // panels (quests progress, recent activity, hero card) need a full
  // /api/home refetch — pull/match/purchase events change all three.
  // We coalesce reloads with a 200ms debounce so a flurry of frames
  // doesn't trigger a flurry of fetches.
  let reloadTimer = null;
  const unsubscribe = liveStore.subscribe((_s, frame) => {
    // Always patch the balance pill (handled inside listenForBalance).
    if (!frame) return;
    if (["pull", "purchase", "match", "loadout"].includes(frame.kind)) {
      if (reloadTimer) clearTimeout(reloadTimer);
      reloadTimer = setTimeout(async () => {
        try {
          const fresh = await fetchJSON("/api/home");
          if (!fresh.error) paint(root, fresh);
        } catch {}
      }, 200);
    }
  });
  const unBalance = listenForBalance(payload);
  return () => {
    if (reloadTimer) clearTimeout(reloadTimer);
    unsubscribe();
    unBalance();
  };
}
