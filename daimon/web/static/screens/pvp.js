// PvP Arena screen — leaderboard, challenges, match management.
//
// Views: hub → matches | challenge | detail
//
// All PvP operations delegate to the arena module on the server side,
// which communicates via GitHub Issues (commit-reveal protocol).
// Requires `gh` CLI installed + authenticated.

import { backButton, el, fetchJSON, postJSON } from "/screens/_dom.js";

const TIER_RARITY = {
  Rookie:   "common",
  Novice:   "uncommon",
  Veteran:  "rare",
  Elite:    "epic",
  Champion: "legendary",
};

const PHASE_LABELS = {
  "pending-accept":  "WAITING",
  "revealing":       "REVEALING",
  "pending-arbiter": "JUDGING",
  "resolved":        "RESOLVED",
};

let state = {};

function freshState() {
  return {
    view: "hub",
    myRank: null,
    leaderboard: [],
    matches: [],
    loadouts: [],
    selectedLoadout: null,
    challengeForm: { opponent: "", memo: "" },
    detail: null,
    detailId: null,
    loading: false,
    error: null,
    ghError: null,
  };
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function shortKey(hex) {
  if (!hex || hex.length < 12) return hex || "---";
  return `${hex.slice(0, 8)}…${hex.slice(-4)}`;
}

function copyKey(hex) {
  navigator.clipboard.writeText(hex).catch(() => {});
}

function checkGhError(data) {
  if (data?.error === "gh_missing" || data?.error === "gh_auth") {
    state.ghError = data.error;
    return true;
  }
  return false;
}

// ---------------------------------------------------------------------------
// Data loading
// ---------------------------------------------------------------------------

async function loadHub() {
  const [rank, lb, loadouts] = await Promise.all([
    fetchJSON("/api/pvp/my-rank").catch(() => ({ tier: "Rookie", rank: null, wins: 0, losses: 0, draws: 0 })),
    fetchJSON("/api/pvp/leaderboard?limit=50").catch(() => ({ ranks: [] })),
    fetchJSON("/api/loadouts").catch(() => ({ loadouts: [] })),
  ]);

  if (checkGhError(rank) || checkGhError(lb)) return;

  state.myRank = rank;
  state.leaderboard = lb.ranks || [];
  state.loadouts = (loadouts.loadouts || []).filter(l => l.card_count === 6);
}

async function loadMatches() {
  const data = await fetchJSON("/api/pvp/matches?limit=30");
  if (checkGhError(data)) return;
  state.matches = data.matches || [];
}

async function loadDetail(id) {
  const data = await fetchJSON(`/api/pvp/status/${encodeURIComponent(id)}`);
  if (checkGhError(data)) return;
  if (data.error) {
    state.error = `${data.error}: ${data.message || ""}`;
    return;
  }
  state.detail = data;
}

// ---------------------------------------------------------------------------
// View: Hub
// ---------------------------------------------------------------------------

function hubView(root) {
  const rank = state.myRank || {};
  const tier = rank.tier || "Rookie";
  const rarity = TIER_RARITY[tier] || "common";
  const pubkey = rank.pubkey_hex || "";

  const playerCard = el("div", { class: "player-card" },
    el("div", { class: "tier-badge", "data-rarity": rarity }, tier.toUpperCase()),
    rank.rank != null
      ? el("div", { class: "tier-rank" }, `#${rank.rank} of ${rank.total_players || "?"}`)
      : el("div", { class: "tier-rank" }, "unranked"),
    el("div", { class: "player-record" },
      `${rank.wins || 0}W / ${rank.losses || 0}L / ${rank.draws || 0}D`),
    pubkey
      ? el("div", {
          class: "player-pubkey",
          title: "click to copy full pubkey",
          onClick: () => copyKey(pubkey),
        }, shortKey(pubkey))
      : null,
  );

  const actions = el("div", { class: "pvp-actions" },
    el("button", {
      class: "pvp-btn",
      onClick: () => { state.view = "challenge"; rerender(root); },
    }, "NEW CHALLENGE"),
    el("button", {
      class: "pvp-btn-outline",
      onClick: async () => {
        state.view = "matches";
        state.loading = true;
        rerender(root);
        await loadMatches();
        state.loading = false;
        rerender(root);
      },
    }, "MY MATCHES"),
  );

  const myPubkey = pubkey;
  const rows = state.leaderboard.map(r => {
    const isMe = r.pubkey_hex === myPubkey;
    const tr = el("tr", { class: isMe ? "is-me" : null },
      el("td", { class: "rank-num" }, `${r.rank}`),
      el("td", { class: "rank-player" }, shortKey(r.pubkey_hex)),
      el("td", null, `${r.wins}`),
      el("td", null, `${r.losses}`),
      el("td", null,
        el("span", { class: "rank-tier", "data-rarity": TIER_RARITY[r.tier] || "common" },
          r.tier.toUpperCase())),
    );
    return tr;
  });

  const leaderboard = el("div", { class: "leaderboard-panel" },
    el("h2", null, "LEADERBOARD"),
    el("div", { class: "leaderboard-scroll" },
      rows.length > 0
        ? el("table", { class: "leaderboard-table" },
            el("thead", null,
              el("tr", null,
                el("th", null, "#"),
                el("th", null, "PLAYER"),
                el("th", null, "W"),
                el("th", null, "L"),
                el("th", null, "TIER"),
              ),
            ),
            el("tbody", null, ...rows),
          )
        : el("div", { class: "empty-state" }, "no ranked players yet"),
    ),
  );

  return el("div", { class: "pvp-body" },
    el("div", { class: "pvp-left" }, playerCard, actions),
    el("div", { class: "pvp-right" }, leaderboard),
  );
}

// ---------------------------------------------------------------------------
// View: My Matches
// ---------------------------------------------------------------------------

function matchesView(root) {
  if (state.loading) {
    return el("div", { class: "pvp-single-body" },
      el("div", { class: "empty-state" }, "loading matches…"));
  }

  const rows = state.matches.map(m => {
    const phase = m.phase || "pending-accept";
    return el("div", {
      class: "match-row",
      onClick: async () => {
        state.detailId = m.challenge_id;
        state.view = "detail";
        state.loading = true;
        state.detail = null;
        state.error = null;
        rerender(root);
        await loadDetail(m.challenge_id);
        state.loading = false;
        rerender(root);
      },
    },
      el("div", { class: "match-id" }, `#${m.issue_number || "?"}`),
      el("div", { class: "phase-chip", "data-phase": phase },
        PHASE_LABELS[phase] || phase.toUpperCase()),
      el("div", { class: "match-opponent" }, shortKey(m.opponent_pubkey)),
      el("div", { class: "match-role" }, m.role || ""),
    );
  });

  return el("div", { class: "pvp-single-body" },
    rows.length > 0
      ? el("div", { class: "matches-list" }, ...rows)
      : el("div", { class: "empty-state" }, "no matches yet — start a challenge!"),
  );
}

// ---------------------------------------------------------------------------
// View: New Challenge
// ---------------------------------------------------------------------------

function challengeView(root) {
  const opponentInput = el("input", {
    class: "challenge-input",
    type: "text",
    placeholder: "64-character hex public key",
    value: state.challengeForm.opponent,
    onInput: (e) => { state.challengeForm.opponent = e.target.value; },
  });

  const memoInput = el("input", {
    class: "challenge-input",
    type: "text",
    placeholder: "e.g., best of 3, round 2",
    value: state.challengeForm.memo,
    onInput: (e) => { state.challengeForm.memo = e.target.value; },
  });

  const pills = state.loadouts.map(l =>
    el("button", {
      class: `loadout-pill${state.selectedLoadout === l.name ? " selected" : ""}`,
      onClick: () => { state.selectedLoadout = l.name; rerender(root); },
    }, l.name),
  );

  const canSubmit = state.challengeForm.opponent.trim().length === 64
                 && state.selectedLoadout
                 && !state.loading;

  const form = el("div", { class: "challenge-form" },
    el("label", null, "OPPONENT PUBKEY"),
    opponentInput,
    el("label", null, "LOADOUT"),
    pills.length > 0
      ? el("div", { class: "loadout-pills" }, ...pills)
      : el("div", { class: "empty-state" }, "no valid loadouts — save a 6-card loadout first"),
    el("label", null, "MEMO (OPTIONAL)"),
    memoInput,
    el("div", { class: "challenge-actions" },
      el("button", {
        class: "pvp-btn",
        disabled: !canSubmit,
        onClick: () => submitChallenge(root),
      }, state.loading ? "SENDING…" : "CHALLENGE"),
    ),
    state.error ? el("div", { class: "error-line" }, state.error) : null,
  );

  return el("div", { class: "pvp-single-body" }, form);
}

async function submitChallenge(root) {
  state.loading = true;
  state.error = null;
  rerender(root);

  try {
    const out = await postJSON("/api/pvp/challenge", {
      opponent_pubkey: state.challengeForm.opponent.trim(),
      loadout_name: state.selectedLoadout,
      memo: state.challengeForm.memo.trim() || null,
    });

    if (checkGhError(out)) { rerender(root); return; }
    if (out.error) {
      state.error = `${out.error}: ${out.message || out.hint || ""}`;
      state.loading = false;
      rerender(root);
      return;
    }

    state.detailId = out.challenge_id;
    state.view = "detail";
    state.loading = true;
    rerender(root);
    await loadDetail(out.challenge_id);
  } catch (err) {
    state.error = String(err);
  }
  state.loading = false;
  rerender(root);
}

// ---------------------------------------------------------------------------
// View: Match Detail
// ---------------------------------------------------------------------------

function detailView(root) {
  if (state.loading && !state.detail) {
    return el("div", { class: "pvp-single-body" },
      el("div", { class: "empty-state" }, "loading match…"));
  }

  const d = state.detail;
  if (!d) {
    return el("div", { class: "pvp-single-body" },
      el("div", { class: "empty-state" }, state.error || "match not found"));
  }

  const phase = d.phase || "pending-accept";

  // Timeline
  const phases = ["pending-accept", "revealing", "pending-arbiter", "resolved"];
  const phaseLabels = ["CHALLENGE", "ACCEPT", "REVEAL", "RESULT"];
  const phaseIdx = phases.indexOf(phase);
  const timelineItems = [];
  for (let i = 0; i < phaseLabels.length; i++) {
    if (i > 0) timelineItems.push(el("div", { class: "step-line" }));
    let cls = "step";
    if (i < phaseIdx) cls += " done";
    else if (i === phaseIdx) cls += " active";
    timelineItems.push(el("div", { class: cls }, phaseLabels[i]));
  }
  const timeline = el("div", { class: "detail-timeline" }, ...timelineItems);

  // Info
  const info = el("div", { class: "detail-info" },
    el("div", null,
      el("span", { class: "detail-label" }, "ISSUE"),
      `#${d.issue_number || "?"}`),
    el("div", null,
      el("span", { class: "detail-label" }, "PHASE"),
      el("span", { class: "phase-chip", "data-phase": phase },
        PHASE_LABELS[phase] || phase.toUpperCase())),
    el("div", null,
      el("span", { class: "detail-label" }, "COMMENTS"),
      `${d.comment_count ?? "?"}`),
    d.url
      ? el("div", null,
          el("span", { class: "detail-label" }, "GITHUB"),
          el("a", { href: d.url, target: "_blank" }, "view issue"))
      : null,
  );

  // Phase-specific actions
  let actionSection = null;
  const myPubkey = state.myRank?.pubkey_hex;

  if (phase === "pending-accept") {
    actionSection = el("div", { class: "detail-actions" },
      el("div", { class: "empty-state" }, "waiting for opponent to accept…"));
  }

  if (phase === "revealing" || phase === "pending-arbiter") {
    actionSection = el("div", { class: "detail-actions" },
      el("button", {
        class: "pvp-btn",
        disabled: state.loading,
        onClick: () => doReveal(root),
      }, state.loading ? "REVEALING…" : "REVEAL LOADOUT"),
      el("div", { style: "font-size:0.75rem; color:var(--text-dim)" },
        "reveals your loadout and nonce for arbitration"),
    );
  }

  if (phase === "resolved" && d.match) {
    const m = d.match;
    const winner = m.winner;
    let bannerClass = "result-banner draw";
    let bannerText = "DRAW";
    if (winner === 0) {
      const isMe = d.winner_pubkey === myPubkey;
      bannerClass = `result-banner ${isMe ? "win" : "loss"}`;
      bannerText = isMe ? "VICTORY" : "DEFEAT";
    } else if (winner === 1) {
      const isMe = d.winner_pubkey === myPubkey;
      bannerClass = `result-banner ${isMe ? "win" : "loss"}`;
      bannerText = isMe ? "VICTORY" : "DEFEAT";
    }

    actionSection = el("div", { class: "detail-actions" },
      el("div", { class: bannerClass }, bannerText),
      d.winner_pubkey
        ? el("div", { style: "text-align:center; color:var(--text-muted); font-size:0.82rem" },
            `winner: ${shortKey(d.winner_pubkey)}`)
        : null,
    );
  }

  const card = el("div", { class: "detail-card" },
    timeline,
    info,
    actionSection,
    state.error ? el("div", { class: "error-line" }, state.error) : null,
  );

  return el("div", { class: "pvp-single-body" }, card);
}

async function doReveal(root) {
  state.loading = true;
  state.error = null;
  rerender(root);

  try {
    const out = await postJSON("/api/pvp/reveal", {
      challenge_id: state.detailId,
    });

    if (checkGhError(out)) { rerender(root); return; }
    if (out.error) {
      state.error = `${out.error}: ${out.message || out.hint || ""}`;
      state.loading = false;
      rerender(root);
      return;
    }

    await loadDetail(state.detailId);
  } catch (err) {
    state.error = String(err);
  }
  state.loading = false;
  rerender(root);
}

// ---------------------------------------------------------------------------
// gh CLI error screen
// ---------------------------------------------------------------------------

function ghErrorView(root) {
  const isAuth = state.ghError === "gh_auth";
  return el("div", { class: "pvp-gh-error" },
    el("div", { class: "gh-error-icon" }, "!"),
    el("div", { class: "gh-error-title" },
      isAuth ? "GitHub Auth Required" : "GitHub CLI Required"),
    el("div", { class: "gh-error-hint" },
      isAuth
        ? "The GitHub CLI (gh) is installed but not authenticated. Run gh auth login in your terminal to sign in."
        : "PvP requires the GitHub CLI. Install it from cli.github.com, then run gh auth login."),
    el("button", {
      class: "pvp-btn",
      onClick: async () => {
        state.ghError = null;
        state.loading = true;
        rerender(root);
        await loadHub();
        state.loading = false;
        rerender(root);
      },
    }, "RETRY"),
  );
}

// ---------------------------------------------------------------------------
// Main render dispatcher
// ---------------------------------------------------------------------------

function rerender(root) {
  root.innerHTML = "";

  if (state.ghError) {
    const screen = el("div", { class: "screen pvp-screen fade-in" },
      el("header", { class: "screen-header" },
        backButton(),
        el("h1", null, "ARENA"),
      ),
      ghErrorView(root),
    );
    root.appendChild(screen);
    return;
  }

  let backTarget = "#menu";
  let backHandler = null;
  let title = "ARENA";
  let body;

  switch (state.view) {
    case "hub":
      body = hubView(root);
      break;
    case "matches":
      title = "MY MATCHES";
      backHandler = () => { state.view = "hub"; rerender(root); };
      body = matchesView(root);
      break;
    case "challenge":
      title = "NEW CHALLENGE";
      backHandler = () => { state.view = "hub"; rerender(root); };
      body = challengeView(root);
      break;
    case "detail":
      title = `MATCH ${state.detailId ? "#" + state.detailId : ""}`;
      backHandler = () => { state.view = "matches"; state.error = null; rerender(root); };
      body = detailView(root);
      break;
  }

  const back = backHandler
    ? el("button", { class: "back-btn", onClick: backHandler }, "← BACK")
    : backButton();

  const screen = el("div", { class: "screen pvp-screen fade-in" },
    el("header", { class: "screen-header" },
      back,
      el("h1", null, title),
    ),
    body,
  );
  root.appendChild(screen);
}

// ---------------------------------------------------------------------------
// Lifecycle
// ---------------------------------------------------------------------------

export async function render(root, params) {
  state = freshState();
  root.innerHTML = '<div class="loading">loading arena…</div>';

  try {
    await loadHub();
  } catch (err) {
    root.innerHTML = `<div class="error">arena unreachable: ${err}</div>`;
    return;
  }

  if (state.ghError) {
    rerender(root);
    return;
  }

  if (params && params[0]) {
    state.detailId = params[0];
    state.view = "detail";
    try { await loadDetail(params[0]); } catch { /* handled in view */ }
  }

  rerender(root);
}
