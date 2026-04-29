// Match screen — pick an opponent, start a match, show the result.
//
// Phase 3 keeps it simple: REST round-trip then result panel. The full
// per-round play flow is post-Phase-3 polish.
//
// State machine:
//   "picker"  → list of NPCs grouped by tier; "FIGHT" button per row
//   "running" → spinner while POST /api/match/start resolves
//   "result"  → win/loss panel + "REMATCH" / "← BACK"

import { backButton, el, fetchJSON, postJSON } from "/screens/_dom.js";

let state = {
  view: "picker",
  tiers: [],
  recommended: null,
  loadouts: [],
  selectedLoadout: null,
  selectedNpc: null,
  result: null,
  error: null,
};

// ---------------------------------------------------------------------------
// Picker view
// ---------------------------------------------------------------------------

function pickerView(root) {
  return el("div", { class: "screen match-screen fade-in" },
    el("header", { class: "screen-header" },
      backButton(),
      el("h1", null, "MATCH"),
      state.recommended
        ? el("div", { class: "screen-balance" },
            `recommended: ${state.recommended.npc_id}`)
        : null,
    ),
    el("div", { class: "match-body" },
      el("div", { class: "match-loadout-pick" },
        el("h3", null, "YOUR LOADOUT"),
        loadoutPicker(root),
      ),
      el("div", { class: "match-npc-list" },
        ...state.tiers.map(t => tierSection(t, root)),
      ),
    ),
  );
}

function loadoutPicker(root) {
  if (state.loadouts.length === 0) {
    return el("div", { class: "empty" },
      "no saved loadouts — visit LOADOUTS first");
  }
  return el("select", {
    class: "loadout-select",
    onChange: (e) => { state.selectedLoadout = e.target.value || null; },
  },
    el("option", { value: "" }, "(active default)"),
    ...state.loadouts.map(lo =>
      el("option", { value: lo.name,
        selected: lo.active ? "selected" : false }, lo.name)),
  );
}

function tierSection(tier, root) {
  return el("section", { class: "tier-section" },
    el("h3", null, `${tier.label} — ${tier.rule || ""}`),
    el("div", { class: "npc-grid" },
      ...tier.npc_ids.map(id => npcCard(id, tier, root)),
    ),
  );
}

function npcCard(npcId, tier, root) {
  return el("div", { class: "npc-card" },
    el("div", { class: "npc-name" }, npcId),
    el("div", { class: "npc-tier" }, tier.label),
    el("button", {
      class: "primary-btn",
      onClick: () => startMatch(npcId, root),
    }, "FIGHT"),
  );
}

// ---------------------------------------------------------------------------
// Running + result
// ---------------------------------------------------------------------------

function runningView() {
  return el("div", { class: "screen match-running fade-in" },
    el("h2", null, `vs ${state.selectedNpc}…`),
    el("div", { class: "spinner" }, "resolving battle"),
  );
}

function resultView(root) {
  const r = state.result || {};
  const youWon = r.winner === 0;
  const draw = r.winner === null || r.winner === undefined;
  return el("div", { class: "screen match-result fade-in" },
    el("header", { class: "screen-header" },
      el("button", { class: "back-btn",
        onClick: () => { state.view = "picker"; state.result = null; rerender(root); } }, "← BACK"),
      el("h1", null,
        draw ? "DRAW" : youWon ? "VICTORY" : "DEFEAT"),
    ),
    el("div", { class: "match-result-body" },
      el("div", { class: "match-result-line" },
        `${r.round_count ?? 0} rounds — ${r.reason || ""}`),
      el("div", { class: "match-result-line" },
        `your team: ${r.side_a_final_hp ?? "?"} hp`),
      el("div", { class: "match-result-line" },
        `${state.selectedNpc}: ${r.side_b_final_hp ?? "?"} hp`),
      r.npc ? el("div", { class: "match-result-flavor" },
        r.npc.flavor || "") : null,
      state.error ? el("div", { class: "error-line" }, state.error) : null,
      el("div", { class: "match-result-actions" },
        el("button", { class: "primary-btn",
          onClick: () => startMatch(state.selectedNpc, root) }, "REMATCH"),
        el("button", { class: "btn-small",
          onClick: () => { location.hash = "#menu"; } }, "MENU"),
      ),
    ),
  );
}

// ---------------------------------------------------------------------------
// Actions
// ---------------------------------------------------------------------------

async function startMatch(npcId, root) {
  state.selectedNpc = npcId;
  state.view = "running";
  state.error = null;
  rerender(root);
  let out;
  try {
    out = await postJSON("/api/match/start", {
      npc_id: npcId,
      loadout: state.selectedLoadout || null,
    });
  } catch (err) {
    state.error = String(err);
    state.view = "result";
    state.result = {};
    rerender(root);
    return;
  }
  if (out.error) {
    state.error = `${out.error}: ${out.message || out.hint || ""}`;
    state.result = {};
  } else {
    state.result = out;
  }
  state.view = "result";
  rerender(root);
}

// ---------------------------------------------------------------------------
// Loaders + render
// ---------------------------------------------------------------------------

async function loadAll() {
  const [npcs, recommended, loadouts] = await Promise.all([
    fetchJSON("/api/npcs"),
    fetchJSON("/api/match/recommended").catch(() => ({})),
    fetchJSON("/api/loadouts").catch(() => ({ loadouts: [] })),
  ]);
  state.tiers = npcs.tiers || [];
  state.recommended = recommended.recommended_npc || null;
  state.loadouts = loadouts.loadouts || [];
  // Prime the picker with the active loadout if one exists.
  const active = state.loadouts.find(l => l.active);
  state.selectedLoadout = active ? active.name : null;
}

function rerender(root) {
  root.innerHTML = "";
  let view;
  if (state.view === "running") view = runningView();
  else if (state.view === "result") view = resultView(root);
  else view = pickerView(root);
  root.appendChild(view);
}

export async function render(root, params) {
  state = {
    view: "picker",
    tiers: [], recommended: null,
    loadouts: [], selectedLoadout: null,
    selectedNpc: null, result: null, error: null,
  };
  root.innerHTML = `<div class="loading">loading match…</div>`;
  try {
    await loadAll();
  } catch (err) {
    root.innerHTML = `<div class="error">match unreachable: ${err}</div>`;
    return;
  }
  // If a route arg was passed (e.g. #match/sparring_sam), auto-start.
  if (params && params.length > 0 && params[0]) {
    await startMatch(params[0], root);
    return;
  }
  rerender(root);
}
