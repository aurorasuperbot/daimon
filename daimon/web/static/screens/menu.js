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
import { liveStore, cardStore } from "/store.js";

const PULL_COST = 100;

const ACTIONS = [
  { id: "pull",       label: "PULL",       hash: "#pull"       },
  { id: "match",      label: "MATCH",      hash: "#match"      },
  { id: "pvp",        label: "ARENA",      hash: "#pvp"        },
  { id: "loadouts",   label: "LOADOUTS",   hash: "#loadouts"   },
  { id: "collection", label: "COLLECTION", hash: "#collection" },
  { id: "shop",       label: "SHOP",       hash: "#shop"       },
  { id: "stats",      label: "STATS",      hash: "#stats"      },
];

function shortPubkey(hex) {
  if (!hex || hex.length < 12) return hex || "—";
  return `${hex.slice(0, 6)}…${hex.slice(-4)}`;
}

/** Title-case a card_id slug like "sunscale_serpent" → "Sunscale Serpent". */
function prettyId(id) {
  if (!id) return "?";
  return id.split("_")
    .map(w => w ? w[0].toUpperCase() + w.slice(1) : w)
    .join(" ");
}

function renderHeader(payload) {
  const ident = payload.identity || {};
  const rank  = payload.rank || {};

  // Identity line: GitHub username when arena-bound, else handle, else placeholder.
  const ghUser = ident.github_username;
  const handleLabel = ghUser || ident.handle;
  const handleNode = handleLabel
    ? el("div", { class: "handle" },
        ghUser && ident.avatar_url
          ? el("img", { class: "avatar", src: ident.avatar_url, width: 20, height: 20 })
          : null,
        handleLabel,
      )
    : el("div", { class: "handle handle-empty" }, "unregistered");

  // Rank line: show real rank only when the player is on the leaderboard.
  // Otherwise surface the server-supplied note ("play one to enter…")
  // instead of the cosmetically broken "Rookie #? of 0".
  const onLadder = rank.rank != null && (rank.total_players ?? 0) > 0;
  const rankNode = onLadder
    ? el("div", { class: "rank-line" },
        `${rank.tier || "Rookie"} · #${rank.rank} of ${rank.total_players}`)
    : el("div", { class: "rank-line rank-empty" },
        rank.note || "play a match to enter the leaderboard");

  return el("header", { class: "menu-header" },
    el("h1", { class: "menu-title" }, "DAIMON"),
    el("div", { class: "menu-identity" },
      handleNode,
      rankNode,
      el("div", { class: "pubkey" }, shortPubkey(ident.pubkey_hex)),
    ),
  );
}

function renderCurrencyStrip(payload) {
  const arenaBalance = payload.arena_balance;
  const localBalance = payload.balance ?? 0;
  const isArena = arenaBalance != null;
  const displayBalance = isArena ? arenaBalance : localBalance;

  const pull      = payload.pull || {};
  const cost      = pull.cost ?? PULL_COST;

  // Arena players: pull availability is based on server balance.
  // Local players: use the pre-computed pull info from mining.
  const arenaPulls = isArena ? Math.floor(arenaBalance / cost) : 0;
  const arenaRemaining = isArena ? Math.max(0, cost - (arenaBalance % cost)) : cost;
  const remaining = isArena ? arenaRemaining : (pull.balance_to_next_pull ?? cost);
  const ready     = isArena ? arenaPulls > 0 : pull.pulls_available > 0;
  const readyCount = isArena ? arenaPulls : pull.pulls_available;
  const fraction  = 1 - Math.max(0, Math.min(remaining, cost)) / cost;

  const strip = el("div", { class: "currency-strip", id: "currency-strip" },
    el("div", { class: "currency-amount", id: "currency-amount" }, String(displayBalance)),
    isArena
      ? el("div", { class: "currency-source" }, "arena ¤")
      : null,
    el("div", { class: "currency-progress" },
      el("div", { class: "fill", id: "currency-fill",
                  style: `width:${(fraction * 100).toFixed(1)}%` }),
    ),
    el("div", { class: "currency-label", id: "currency-label" },
      ready
        ? `${readyCount}× ready to pull`
        : `next pull in ${remaining}¤`,
    ),
  );
  return strip;
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
      : el("ul", null, ...quests.map(q => {
          const showProgress =
            !q.complete && Number.isFinite(q.target) && q.target > 1;
          const cls =
            (q.complete ? "complete " : "") + (q.claimed ? "claimed" : "");
          return el("li", { class: cls.trim() },
            el("span", { class: "quest-title" }, q.title),
            showProgress
              ? el("span", { class: "quest-progress" },
                  `${q.progress ?? 0}/${q.target}`)
              : null,
            el("span", { class: "reward" },
              q.claimed ? "✓" : `+${q.reward}¤`),
          );
        })),
  );
}

/** Recent activity panel — interleaves matches + pulls newest-first.
 *  Pulls show the card display name (resolved async via cardStore) with
 *  a rarity-colored dot. Matches show W/L badge + opponent. */
function renderActivityPanel(payload) {
  const events = [];
  for (const m of (payload.recent_matches || [])) {
    events.push({ kind: "match", ts: m.ts, opponent: m.opponent, outcome: m.outcome });
  }
  for (const p of (payload.recent_pulls || [])) {
    events.push({ kind: "pull", ts: p.ts, card_id: p.card_id, rarity: p.rarity });
  }
  events.sort((a, b) => (b.ts || "").localeCompare(a.ts || ""));
  const items = events.slice(0, 6);

  if (!items.length) {
    return el("section", { class: "panel" },
      el("h2", null, "RECENT ACTIVITY"),
      el("div", { class: "empty" }, "no activity yet — try a pull"),
    );
  }

  const ul = el("ul", null);
  for (const ev of items) {
    if (ev.kind === "pull") {
      const txt = el("span", { class: "activity-text" },
        `pulled ${prettyId(ev.card_id)}`);
      const li = el("li", { class: "activity-pull", "data-rarity": ev.rarity || "" },
        el("span", { class: "activity-dot" }),
        txt,
        el("span", { class: "activity-tag" }, ev.rarity || ""),
      );
      // Resolve display name once cardStore resolves; soft-fail to slug.
      cardStore.get(ev.card_id)
        .then(p => { if (p?.name) txt.textContent = `pulled ${p.name}`; })
        .catch(() => {});
      ul.append(li);
    } else {
      const win = (ev.outcome || "").toLowerCase().startsWith("w");
      const draw = (ev.outcome || "").toLowerCase().startsWith("d");
      const badge = win ? "W" : draw ? "—" : "L";
      const cls = win ? "win" : draw ? "draw" : "loss";
      const li = el("li", { class: `activity-match ${cls}` },
        el("span", { class: "activity-badge" }, badge),
        el("span", { class: "activity-text" }, `vs ${ev.opponent || "?"}`),
        el("span", { class: "activity-tag" }, ev.outcome || ""),
      );
      ul.append(li);
    }
  }
  return el("section", { class: "panel" },
    el("h2", null, "RECENT ACTIVITY"),
    ul,
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
  const ver   = payload.identity?.version || "";
  const stats = payload.stats || {};
  // Right-aligned stats give the bottom strip a real reason to exist:
  // collection size + total mined + verified-ledger badge.
  const right = el("span", { class: "footer-stats" });
  const arenaCollCount = payload.arena_collection_count;
  if (arenaCollCount != null) {
    right.append(el("span", { class: "footer-stat" },
      `${arenaCollCount} cards (arena)`));
  } else if (stats.pull_count != null) {
    right.append(el("span", { class: "footer-stat" },
      `${stats.pull_count} pulls`));
  }
  if (stats.total_mined != null) {
    right.append(el("span", { class: "footer-stat" },
      `${stats.total_mined.toLocaleString()}¤ mined`));
  }
  if (stats.verified === true) {
    right.append(el("span", { class: "footer-stat verified" }, "ledger ok"));
  } else if (stats.verified === false) {
    right.append(el("span", { class: "footer-stat unverified" }, "ledger ?"));
  }
  return el("footer", { class: "menu-footer" },
    el("span", null, "DAIMON" + (ver ? ` v${ver}` : "")),
    right,
  );
}

/** Subscribe the currency strip to liveStore. The strip is a fixed DOM
 *  tree built once; the subscription patches the three live nodes
 *  (amount, fill width, label). Returns an unsubscribe function so
 *  app.js can detach when the user navigates away. */
function listenForBalance(payload) {
  const cost = payload.pull?.cost ?? PULL_COST;
  const isArena = payload.arena_balance != null;
  return liveStore.subscribe(state => {
    // Arena balance is server-authoritative — don't patch from mining ticks.
    // A full /api/home refetch (on pull/purchase frames) will update it.
    if (isArena || typeof state.balance !== "number") return;

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
    const errDiv = document.createElement("div");
    errDiv.className = "error";
    errDiv.textContent = `backend unreachable: ${err}`;
    root.innerHTML = "";
    root.appendChild(errDiv);
    return;
  }
  if (payload.error) {
    const errDiv2 = document.createElement("div");
    errDiv2.className = "error";
    errDiv2.textContent = `backend error: ${payload.error}`;
    root.innerHTML = "";
    root.appendChild(errDiv2);
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
