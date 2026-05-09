// Stats dashboard — collection completion, match performance, top cards,
// trophy showcase, element/rarity distribution.
//
// Single /api/stats call aggregates everything server-side. Layout is a
// grid of panels: overview strip, charts, leaderboard, trophies.

import { backButton, el, fetchJSON } from "/screens/_dom.js";
import { cardStore } from "/store.js";

const RARITY_ORDER = ["legendary", "epic", "rare", "uncommon", "common"];
const ELEMENT_ORDER = ["FIRE", "WATER", "NATURE", "VOID", "VOLT", "NORMAL"];
const ELEMENT_COLORS = {
  FIRE:   "#ff6b4a",
  WATER:  "#5ca8ff",
  NATURE: "#6cd96c",
  VOID:   "#c9a4ff",
  VOLT:   "#ffdc78",
  NORMAL: "#9b9b9b",
};

function prettyId(id) {
  if (!id) return "?";
  return id.split("_").map(w => w ? w[0].toUpperCase() + w.slice(1) : w).join(" ");
}

// ---------------------------------------------------------------------------
// Overview cards — big stat numbers across the top
// ---------------------------------------------------------------------------

function overviewStrip(data) {
  const coll = data.collection;
  const m = data.matches;
  const pct = coll.catalog_size > 0
    ? Math.round((coll.unique_owned / coll.catalog_size) * 100)
    : 0;
  const winRate = m.total > 0
    ? Math.round((m.wins / m.total) * 100)
    : 0;

  const streakLabel = m.current_streak > 0
    ? `${m.current_streak}${m.streak_type === "w" ? "W" : "L"}`
    : "—";

  return el("div", { class: "stats-overview" },
    overviewCard("WIN RATE", `${winRate}%`, winRate >= 50 ? "positive" : "negative"),
    overviewCard("MATCHES", String(m.total), "neutral"),
    overviewCard("W / L / D", `${m.wins} / ${m.losses} / ${m.draws}`, "neutral"),
    overviewCard("COLLECTION", `${coll.unique_owned}/${coll.catalog_size}`, "neutral", `${pct}%`),
    overviewCard("STREAK", streakLabel,
      m.streak_type === "w" ? "positive" : m.streak_type === "l" ? "negative" : "neutral"),
    overviewCard("TROPHIES", String(data.trophies.total), "neutral"),
  );
}

function overviewCard(label, value, mood, sub) {
  const card = el("div", { class: `stats-card stats-${mood}` },
    el("div", { class: "stats-card-label" }, label),
    el("div", { class: "stats-card-value" }, value),
  );
  if (sub) card.appendChild(el("div", { class: "stats-card-sub" }, sub));
  return card;
}

// ---------------------------------------------------------------------------
// Rarity completion bars
// ---------------------------------------------------------------------------

function rarityPanel(data) {
  const coll = data.collection;
  const bars = el("div", { class: "stats-bars" });
  for (const rarity of RARITY_ORDER) {
    const owned = coll.by_rarity[rarity] || 0;
    const total = coll.catalog_by_rarity[rarity] || 0;
    if (!total) continue;
    const pct = Math.round((owned / total) * 100);
    bars.appendChild(
      el("div", { class: "stats-bar-row", "data-rarity": rarity },
        el("span", { class: "stats-bar-label" }, rarity),
        el("div", { class: "stats-bar-track" },
          el("div", { class: "stats-bar-fill", style: `width:${pct}%` }),
        ),
        el("span", { class: "stats-bar-count" }, `${owned}/${total}`),
      ),
    );
  }
  return el("div", { class: "stats-panel" },
    el("h2", null, "RARITY COMPLETION"),
    bars,
  );
}

// ---------------------------------------------------------------------------
// Element distribution
// ---------------------------------------------------------------------------

function elementPanel(data) {
  const coll = data.collection;
  const maxVal = Math.max(1, ...ELEMENT_ORDER.map(e => coll.by_element[e] || 0));

  const bars = el("div", { class: "stats-element-bars" });
  for (const elem of ELEMENT_ORDER) {
    const count = coll.by_element[elem] || 0;
    const pct = Math.round((count / maxVal) * 100);
    const color = ELEMENT_COLORS[elem] || "#9b9b9b";
    bars.appendChild(
      el("div", { class: "stats-element-row" },
        el("span", { class: "stats-element-label", style: `color:${color}` },
          elem.toLowerCase()),
        el("div", { class: "stats-bar-track" },
          el("div", { class: "stats-bar-fill",
            style: `width:${pct}%;background:${color}` }),
        ),
        el("span", { class: "stats-bar-count" }, String(count)),
      ),
    );
  }
  return el("div", { class: "stats-panel" },
    el("h2", null, "ELEMENT SPREAD"),
    bars,
  );
}

// ---------------------------------------------------------------------------
// Top performers
// ---------------------------------------------------------------------------

function performersPanel(data) {
  const list = el("div", { class: "stats-performers" });
  if (data.top_performers.length === 0) {
    list.appendChild(el("div", { class: "empty" }, "no battle data yet"));
  }
  for (const p of data.top_performers) {
    const row = el("div", { class: "stats-performer", "data-rarity": p.rarity },
      el("div", { class: "stats-performer-art-wrap" }),
      el("div", { class: "stats-performer-info" },
        el("div", { class: "stats-performer-name" }, ""),
        el("div", { class: "stats-performer-stats" },
          el("span", { class: "perf-w" }, `${p.total_wins}W`),
          el("span", { class: "perf-l" }, `${p.total_losses}L`),
          el("span", { class: "perf-k" }, `${p.total_kills}K`),
        ),
      ),
    );
    const artWrap = row.querySelector(".stats-performer-art-wrap");
    const nameEl = row.querySelector(".stats-performer-name");
    const img = el("img", {
      class: "stats-performer-art",
      src: `/art/${encodeURIComponent(p.card_id)}`,
      draggable: "false",
      loading: "lazy",
    });
    artWrap.appendChild(img);
    cardStore.get(p.card_id)
      .then(c => { if (c?.name) nameEl.textContent = c.name; })
      .catch(() => { nameEl.textContent = prettyId(p.card_id); });
    nameEl.textContent = prettyId(p.card_id);
    list.appendChild(row);
  }
  return el("div", { class: "stats-panel stats-panel-wide" },
    el("h2", null, "TOP PERFORMERS"),
    list,
  );
}

// ---------------------------------------------------------------------------
// Trophy showcase
// ---------------------------------------------------------------------------

const TROPHY_LABELS = {
  first_edition: "1ST ED",
  early_bird: "EARLY BIRD",
  genesis: "GENESIS",
  centurion: "CENTURION",
  veteran: "VETERAN",
  slayer: "SLAYER",
  undefeated_5: "5x STREAK",
  undefeated_10: "10x STREAK",
  undefeated_25: "25x STREAK",
};
const TROPHY_FAMILIES = {
  first_edition: "provenance", early_bird: "provenance", genesis: "provenance",
  centurion: "combat", veteran: "combat", slayer: "combat",
  undefeated_5: "streak", undefeated_10: "streak", undefeated_25: "streak",
};

function trophyPanel(data) {
  const trophies = data.trophies;
  const grid = el("div", { class: "stats-trophy-grid" });

  if (trophies.total === 0) {
    grid.appendChild(el("div", { class: "empty" }, "no trophies earned yet"));
  } else {
    const order = [
      "centurion", "veteran", "slayer",
      "undefeated_25", "undefeated_10", "undefeated_5",
      "first_edition", "early_bird", "genesis",
    ];
    for (const t of order) {
      const count = trophies.by_type[t];
      if (!count) continue;
      const family = TROPHY_FAMILIES[t] || "combat";
      grid.appendChild(
        el("div", { class: "stats-trophy-chip", "data-family": family },
          el("span", { class: "stats-trophy-label" }, TROPHY_LABELS[t] || t.toUpperCase()),
          el("span", { class: "stats-trophy-count" }, `x${count}`),
        ),
      );
    }
  }
  return el("div", { class: "stats-panel" },
    el("h2", null, "TROPHY SHOWCASE"),
    grid,
  );
}

// ---------------------------------------------------------------------------
// Match timeline — visual streak of recent results
// ---------------------------------------------------------------------------

function timelinePanel(data) {
  const recent = data.matches.recent;
  const strip = el("div", { class: "stats-timeline" });
  if (recent.length === 0) {
    strip.appendChild(el("div", { class: "empty" }, "no matches yet"));
  } else {
    for (const r of recent) {
      const dot = el("div", {
        class: `stats-timeline-dot ${r.outcome}`,
        title: `${r.outcome.toUpperCase()} vs ${r.opponent}`,
      });
      strip.appendChild(dot);
    }
  }
  return el("div", { class: "stats-panel stats-panel-wide" },
    el("h2", null, "RECENT MATCHES"),
    strip,
  );
}

// ---------------------------------------------------------------------------
// Render
// ---------------------------------------------------------------------------

export async function render(root) {
  root.innerHTML = `<div class="loading">loading stats…</div>`;
  let data;
  try {
    data = await fetchJSON("/api/stats");
  } catch (err) {
    root.innerHTML = `<div class="error">stats unreachable: ${err}</div>`;
    return;
  }

  root.innerHTML = "";
  root.appendChild(
    el("div", { class: "screen stats-screen fade-in" },
      el("header", { class: "screen-header" },
        backButton(),
        el("h1", null, "STATS"),
      ),
      el("div", { class: "stats-body" },
        overviewStrip(data),
        el("div", { class: "stats-row" },
          rarityPanel(data),
          elementPanel(data),
        ),
        timelinePanel(data),
        el("div", { class: "stats-row" },
          performersPanel(data),
          trophyPanel(data),
        ),
      ),
    ),
  );
}
