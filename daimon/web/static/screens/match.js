// Match screen — pick an opponent, watch the cinematic, see the result.
//
// State machine:
//   "picker"     → list of NPCs grouped by tier; "FIGHT" button per row
//   "cinematic"  → live battle replay driven by the transcript that
//                  /api/match/start ships back. Walks rounds[].actions[]
//                  one event at a time, animating HP bars, damage
//                  numbers, status badges, and trigger cascades.
//   "result"     → win/loss panel + REMATCH / MENU
//
// The cinematic IS the screen — it owns the unit grid, action log, and
// round counter. SKIP fast-forwards to the result view; ABORT (back/escape)
// short-circuits and goes straight to the picker. All async pacing routes
// through abortableSleep() so cleanup is reliable.

import { backButton, el, fetchJSON, postJSON } from "/screens/_dom.js";

let state = {
  view: "picker",
  tiers: [],
  npcsById: {},        // npc_id -> {name, flavor, rank, tier}
  recommended: null,
  loadouts: [],
  selectedLoadout: null,
  selectedNpc: null,
  result: null,
  error: null,
  abort: null,         // AbortController for the running cinematic
};

// ---------------------------------------------------------------------------
// Picker view
// ---------------------------------------------------------------------------

const TIER_RARITY = {
  rookie: "common",
  novice: "uncommon",
  veteran: "rare",
  elite: "epic",
  champion: "legendary",
};

function pickerView(root) {
  const rec = state.recommended;
  const recMeta = rec ? state.npcsById[rec.npc_id] : null;

  return el("div", { class: "screen match-screen fade-in" },
    el("header", { class: "screen-header" },
      backButton(),
      el("h1", null, "MATCH"),
    ),
    el("div", { class: "match-body" },
      rec && recMeta ? heroBanner(recMeta, rec, root) : null,
      el("div", { class: "match-loadout-pick" },
        el("span", { class: "loadout-label" }, "LOADOUT"),
        loadoutPicker(root),
      ),
      el("div", { class: "match-npc-list" },
        ...state.tiers.map(t => tierSection(t, root)),
      ),
    ),
  );
}

function heroBanner(meta, rec, root) {
  const coverId = rec.cover_card_id || meta.cover_card_id || meta.loadout?.[0];
  const tierLabel = meta.tier
    ? meta.tier.charAt(0).toUpperCase() + meta.tier.slice(1)
    : "";
  return el("div", {
    class: "hero-banner",
    onClick: () => startMatch(meta.npc_id, root),
  },
    coverId
      ? el("img", {
          class: "hero-bg",
          src: `/art/${encodeURIComponent(coverId)}`,
          alt: "",
          draggable: "false",
        })
      : null,
    el("div", { class: "hero-overlay" }),
    el("div", { class: "hero-content" },
      el("div", { class: "hero-pill" }, "RECOMMENDED"),
      el("div", { class: "hero-name" }, meta.name),
      el("div", { class: "hero-detail" },
        `${tierLabel}${meta.rank ? ` · #${meta.rank}` : ""}`),
      meta.flavor
        ? el("div", { class: "hero-flavor" }, meta.flavor)
        : null,
      el("button", {
        class: "hero-fight-btn",
        type: "button",
        onClick: (e) => { e.stopPropagation(); startMatch(meta.npc_id, root); },
      }, "FIGHT"),
    ),
    coverId ? npcTeamStrip(meta) : null,
  );
}

function npcTeamStrip(meta) {
  const ids = meta.loadout || [];
  if (ids.length === 0) return null;
  return el("div", { class: "hero-team" },
    ...ids.map(cid =>
      el("img", {
        class: "hero-team-thumb",
        src: `/art/${encodeURIComponent(cid)}`,
        alt: "",
        draggable: "false",
        loading: "lazy",
      })
    ),
  );
}

function loadoutPicker(root) {
  if (state.loadouts.length === 0) {
    return el("div", { class: "empty" },
      "no saved loadouts — visit LOADOUTS first");
  }
  const container = el("div", { class: "loadout-pills" });
  function activate(btn) {
    container.querySelectorAll(".loadout-pill")
      .forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
  }
  const defaultPill = el("button", {
    class: `loadout-pill${!state.selectedLoadout ? " active" : ""}`,
    type: "button",
    onClick: (e) => { state.selectedLoadout = null; activate(e.currentTarget); },
  }, "DEFAULT");
  container.appendChild(defaultPill);
  for (const lo of state.loadouts) {
    const pill = el("button", {
      class: `loadout-pill${state.selectedLoadout === lo.name ? " active" : ""}`,
      type: "button",
      onClick: (e) => { state.selectedLoadout = lo.name; activate(e.currentTarget); },
    }, lo.name);
    container.appendChild(pill);
  }
  return container;
}

function tierSection(tier, root) {
  const rarity = TIER_RARITY[tier.tier_id] || "common";
  return el("section", { class: "tier-section", "data-rarity": rarity },
    el("div", { class: "tier-header" },
      el("div", { class: "tier-accent" }),
      el("div", { class: "tier-header-text" },
        el("h3", null, tier.label),
        tier.rule
          ? el("div", { class: "tier-rule" }, tier.rule)
          : null,
      ),
    ),
    el("div", { class: "npc-grid" },
      ...tier.npc_ids.map(id => npcCard(id, tier, root)),
    ),
  );
}

function npcCard(npcId, tier, root) {
  const meta = state.npcsById[npcId] || {};
  const isRecommended = state.recommended?.npc_id === npcId;
  const coverId = meta.cover_card_id || meta.loadout?.[0];

  return el("div", {
    class: `npc-card${isRecommended ? " recommended" : ""}`,
    onClick: () => startMatch(npcId, root),
  },
    coverId
      ? el("img", {
          class: "npc-art",
          src: `/art/${encodeURIComponent(coverId)}`,
          alt: meta.name || npcId,
          draggable: "false",
          loading: "lazy",
        })
      : el("div", { class: "npc-art-placeholder" }),
    el("div", { class: "npc-gradient" }),
    el("div", { class: "npc-info" },
      el("div", { class: "npc-name-row" },
        el("span", { class: "npc-name" }, meta.name || npcId),
        el("span", { class: "npc-rank" }, `#${meta.rank || "?"}`),
      ),
      meta.flavor
        ? el("div", { class: "npc-flavor" }, meta.flavor)
        : null,
    ),
    isRecommended
      ? el("div", { class: "npc-rec-badge" }, "REC")
      : null,
  );
}

// ---------------------------------------------------------------------------
// Running + result
// ---------------------------------------------------------------------------

function runningView() {
  const opponentName = state.npcsById[state.selectedNpc]?.name || state.selectedNpc;
  return el("div", { class: "screen match-running fade-in" },
    el("h2", null, `vs ${opponentName}…`),
    el("div", { class: "spinner" }, "resolving battle"),
  );
}

// ---------------------------------------------------------------------------
// Cinematic: walk the transcript event-by-event with HP/damage animation
// ---------------------------------------------------------------------------

/** Speed control: shared ref the cinematic reads on each sleep so the
 *  player can change pacing mid-fight without having to restart the
 *  walker. `multiplier` is "wall-clock per event" — 0.5 = half-speed
 *  (slow-mo), 2 = 2x faster, 0 = paused (sleep waits on a Promise the
 *  control resolves on resume).
 *
 *  The control object is rebuilt per-cinematic so abort cleans it up. */
function makeSpeedCtl(initialMultiplier = 1) {
  const ctl = {
    multiplier: initialMultiplier,
    paused: false,
    _resumeWaiters: [],
    setSpeed(mult) {
      this.multiplier = mult;
      this.paused = false;
      const waiters = this._resumeWaiters.splice(0);
      for (const w of waiters) w();
    },
    togglePause() {
      this.paused = !this.paused;
      if (!this.paused) {
        const waiters = this._resumeWaiters.splice(0);
        for (const w of waiters) w();
      }
    },
    waitWhilePaused() {
      if (!this.paused) return Promise.resolve();
      return new Promise(resolve => this._resumeWaiters.push(resolve));
    },
  };
  return ctl;
}

/** Sleep that respects the speed multiplier + abort signal + pause state.
 *  When paused, the wait extends until resumed (or aborted). */
async function abortableSleep(ms, signal, speed) {
  if (signal.aborted) throw new DOMException("aborted", "AbortError");
  if (speed) {
    while (speed.paused) {
      await Promise.race([
        speed.waitWhilePaused(),
        new Promise((_, rej) =>
          signal.addEventListener("abort",
            () => rej(new DOMException("aborted", "AbortError")),
            { once: true })),
      ]);
      if (signal.aborted) throw new DOMException("aborted", "AbortError");
    }
    if (speed.multiplier > 0) ms = ms / speed.multiplier;
  }
  return new Promise((resolve, reject) => {
    const t = setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    function onAbort() {
      clearTimeout(t);
      signal.removeEventListener("abort", onAbort);
      reject(new DOMException("aborted", "AbortError"));
    }
    signal.addEventListener("abort", onAbort, { once: true });
  });
}

const KIND_PACING_MS = {
  damage: 380,
  heal: 320,
  buff: 240,
  debuff: 240,
  status: 260,
  shield: 220,
  passive: 200,
  death: 520,
};

function buildCinematicDom(root, transcript, speed) {
  const opponentName = state.npcsById[state.selectedNpc]?.name || state.selectedNpc;
  const player = transcript.participants.player;
  const opponent = transcript.participants.opponent;

  const refs = { cells: { player: {}, opponent: {} }, log: null, roundChip: null };

  function makeUnitCell(side, card) {
    const cardId = card.species;
    const dm = document.createElement("dm-card");
    dm.setAttribute("card-id", cardId);
    dm.setAttribute("size", "battle");

    // HP bar + numeric strip live below the card so they stay readable
    // while the art/text is busy. Per-event flash overlays the whole
    // cell so a damage hit reads at-a-glance.
    const hpFill = el("div", { class: "unit-hp-fill", style: "width:100%" });
    const hpBar = el("div", { class: "unit-hp-bar" }, hpFill);
    const hpText = el("div", { class: "unit-hp-text" }, `${card.hp}/${card.hp_max}`);
    const flash = el("div", { class: "unit-flash" });

    const cell = el("div", {
      class: `unit-cell side-${side}`,
      "data-element": card.element || "",
    },
      el("div", { class: "unit-card-frame" }, dm),
      hpBar, hpText, flash,
    );
    refs.cells[side][card.position] = {
      cell, hpFill, hpText, flash,
      hp: card.hp, hp_max: card.hp_max,
      dead: false,
    };
    return cell;
  }

  const sortedPlayer   = player.loadout.slice().sort((a, b) => a.position - b.position);
  const sortedOpponent = opponent.loadout.slice().sort((a, b) => a.position - b.position);

  const opponentRow = el("div", { class: "unit-row opponent-row" },
    ...sortedOpponent.map(c => makeUnitCell("opponent", c)));
  const playerRow = el("div", { class: "unit-row player-row" },
    ...sortedPlayer.map(c => makeUnitCell("player", c)));

  refs.log = el("div", { class: "cinematic-log" });
  refs.roundChip = el("div", { class: "round-chip" }, "ROUND 1");

  // Speed controls: pause + three speed levels. The active button is
  // marked so the player has a clear "this is what's happening now"
  // signal. Buttons mutate the shared speed ref; the walker reads it
  // every sleep, so changes take effect on the next event boundary.
  const SPEED_LEVELS = [
    { label: "0.5×", mult: 0.5 },
    { label: "1×",   mult: 1 },
    { label: "2×",   mult: 2 },
  ];
  const speedBtns = SPEED_LEVELS.map(s => {
    const btn = el("button", {
      class: `speed-btn${s.mult === speed.multiplier ? " active" : ""}`,
      type: "button",
    }, s.label);
    btn.addEventListener("click", () => {
      speed.setSpeed(s.mult);
      pauseBtn.classList.remove("active");
      pauseBtn.textContent = "❚❚";
      controlBar.querySelectorAll(".speed-btn")
        .forEach(b => b.classList.toggle("active", b === btn));
    });
    return btn;
  });
  const pauseBtn = el("button", { class: "speed-btn pause-btn", type: "button" }, "❚❚");
  pauseBtn.addEventListener("click", () => {
    speed.togglePause();
    pauseBtn.classList.toggle("active", speed.paused);
    pauseBtn.textContent = speed.paused ? "▶" : "❚❚";
  });
  const skip = el("button", { class: "pull-skip", type: "button" }, "SKIP");
  skip.addEventListener("click", () => state.abort?.abort());

  const controlBar = el("div", { class: "speed-controls" },
    pauseBtn, ...speedBtns,
  );

  const back = backButton();
  back.classList.add("pull-back-btn");

  const screen = el("div", { class: "screen match-cinematic fade-in" },
    el("header", { class: "screen-header" },
      back,
      el("h1", null, `vs ${opponentName}`),
      el("div", { class: "cinematic-header-right" },
        refs.roundChip, controlBar, skip),
    ),
    el("div", { class: "cinematic-body" },
      el("div", { class: "cinematic-field" },
        el("div", { class: "team-label opponent" },
          opponent.name || opponentName),
        opponentRow,
        el("div", { class: "team-label player" }, "YOUR TEAM"),
        playerRow,
      ),
      el("div", { class: "cinematic-sidebar" },
        el("div", { class: "log-header" }, "BATTLE LOG"),
        refs.log,
      ),
    ),
  );
  return { screen, refs };
}

/** Apply a single Action: animate actor, target, hp, status, then recurse
 *  into nested triggers. Returns when this branch of the cascade settles
 *  (so the outer walker stays sequential). */
async function playAction(action, refs, signal, speed, opts = {}) {
  if (signal.aborted) return;
  const pacing = opts.pacing ?? KIND_PACING_MS[action.kind] ?? 250;

  // Actor flash — passes can be untargeted (passive); always pulse the actor.
  const actorRef = refs.cells[action.actor.side]?.[action.actor.position];
  if (actorRef && !actorRef.dead) {
    actorRef.cell.classList.add("acting");
    setTimeout(() => actorRef.cell.classList.remove("acting"), pacing);
    if (action.kind === "passive") {
      actorRef.cell.classList.add("fx-passive");
      setTimeout(() => actorRef.cell.classList.remove("fx-passive"), 600);
    }
  }

  // Target flash + amount float + effect animation.
  if (action.target) {
    const tRef = refs.cells[action.target.side]?.[action.target.position];
    if (tRef) {
      tRef.flash.classList.remove("damage", "heal", "buff", "debuff", "status", "shield");
      tRef.flash.classList.add("active", action.kind);
      setTimeout(() => tRef.flash.classList.remove("active"), pacing);

      const fxClass = `fx-${action.kind}`;
      tRef.cell.classList.remove(fxClass);
      void tRef.cell.offsetWidth;
      tRef.cell.classList.add(fxClass);
      const fxMs = { damage: 400, heal: 500, buff: 450, debuff: 450,
                     shield: 500, status: 900 }[action.kind] || 500;
      setTimeout(() => tRef.cell.classList.remove(fxClass), fxMs);

      if (action.amount != null && ["damage", "heal", "buff", "debuff", "shield"].includes(action.kind)) {
        const sign = action.kind === "heal" || action.kind === "buff" || action.kind === "shield" ? "+" : "-";
        const f = document.createElement("div");
        f.className = `float-num ${action.kind}`;
        f.textContent = `${sign}${action.amount}`;
        tRef.cell.appendChild(f);
        setTimeout(() => f.remove(), 900);
      }
      if (action.status_applied) {
        const f = document.createElement("div");
        f.className = `float-num status`;
        f.textContent = action.status_applied;
        tRef.cell.appendChild(f);
        setTimeout(() => f.remove(), 900);
      }
    }
  }

  // HP after — hp_after is keyed "side/pos": int.
  if (action.hp_after) {
    for (const [key, hp] of Object.entries(action.hp_after)) {
      const [side, posStr] = key.split("/");
      const r = refs.cells[side]?.[Number(posStr)];
      if (!r) continue;
      const pct = Math.max(0, Math.min(100, (hp / r.hp_max) * 100));
      r.hpFill.style.width = `${pct.toFixed(1)}%`;
      r.hpText.textContent = `${Math.max(0, hp)}/${r.hp_max}`;
      r.hpFill.classList.toggle("low", pct > 0 && pct < 30);
      r.hpFill.classList.toggle("crit", pct > 0 && pct < 12);
      r.hp = hp;
    }
  }

  // Death: animated collapse, then static .dead class after the animation.
  if (action.kind === "death") {
    const aRef = refs.cells[action.actor.side]?.[action.actor.position];
    if (aRef) {
      aRef.cell.classList.add("fx-death");
      aRef.dead = true;
      setTimeout(() => {
        aRef.cell.classList.remove("fx-death");
        aRef.cell.classList.add("dead");
      }, 600);
    }
  }

  // Append to log — every action gets a line. Triggers are rendered
  // indented so the cascade reads as nested under its parent.
  appendLog(refs.log, action, opts.depth || 0);

  await abortableSleep(pacing, signal, speed);

  // Triggers — half-pacing, recurse. Ordering matches engine emission.
  for (const child of (action.triggers || [])) {
    await playAction(child, refs, signal, speed,
      { pacing: Math.max(120, pacing / 2), depth: (opts.depth || 0) + 1 });
  }
}

/** Build a human-readable line for the live log. The engine's log_line
 *  is terse and lowercase ("ironseed gains TAUNT(2)"); we re-format it
 *  using the structured action data so the cinematic reads more like
 *  a play-by-play commentary. */
function formatActionLine(action) {
  const actor = action.actor?.card || "?";
  const target = action.target?.card;
  const amt = action.amount;
  const status = action.status_applied;
  const reason = action.reason;
  const reasonHint = reason && reason !== null
    ? ` (${reason.toLowerCase().replace(/^on_/, "")})`
    : "";

  switch (action.kind) {
    case "damage":
      if (target && target !== actor) {
        return `${actor} hits ${target} for ${amt ?? "?"}${reasonHint}`;
      }
      return `${actor} takes ${amt ?? "?"} damage${reasonHint}`;
    case "heal":
      if (target && target !== actor) {
        return `${actor} heals ${target} for ${amt ?? "?"}${reasonHint}`;
      }
      return `${actor} heals for ${amt ?? "?"}${reasonHint}`;
    case "buff":
      return `${actor} buffs ${target || "self"} +${amt ?? "?"}${reasonHint}`;
    case "debuff":
      return `${actor} debuffs ${target || "?"} -${amt ?? "?"}${reasonHint}`;
    case "shield":
      return `${actor} shields ${target || "self"} +${amt ?? "?"}${reasonHint}`;
    case "status":
      if (status) {
        if (target && target !== actor) {
          return `${actor} inflicts ${status}${amt != null ? ` ${amt}` : ""} on ${target}${reasonHint}`;
        }
        return `${actor} gains ${status}${amt != null ? ` ${amt}` : ""}${reasonHint}`;
      }
      return action.log_line || `${actor} status${reasonHint}`;
    case "death":
      return `${actor} falls`;
    case "passive":
      return `${actor} — ${action.log_line || "passive"}`;
    default:
      return action.log_line || `${actor} → ${action.kind}`;
  }
}

function appendLog(logEl, action, depth = 0) {
  const line = document.createElement("div");
  line.className = `log-line log-${action.kind}${depth > 0 ? " trigger" : ""}`;
  if (depth > 0) line.style.paddingLeft = `${depth * 1.4}rem`;
  line.textContent = formatActionLine(action);
  logEl.appendChild(line);
  // Keep a longer history for the breakdown — easy to scroll back during
  // a pause to re-read what happened.
  const lines = logEl.querySelectorAll(".log-line");
  if (lines.length > 60) lines[0].remove();
  logEl.scrollTop = logEl.scrollHeight;
}

/** Trace ring buffer the cinematic walker writes to. Inspect via:
 *    fetch /api/_dev/eval { js: "JSON.stringify(window.__cinTrace)" }
 *  Cleared on each new playCinematic() call. */
function cinTrace(msg) {
  if (!window.__cinTrace) window.__cinTrace = [];
  window.__cinTrace.push(`${(performance.now() | 0)}ms ${msg}`);
  // Also surface via console for browser devtools.
  console.log(`[cinematic] ${msg}`);
}

async function playEntranceStagger(refs, signal, speed) {
  const allCells = [
    ...Object.values(refs.cells.opponent),
    ...Object.values(refs.cells.player),
  ];
  for (const r of allCells) {
    r.cell.classList.add("unit-entrance");
  }
  for (let i = 0; i < allCells.length; i++) {
    if (signal.aborted) return;
    allCells[i].cell.classList.add("unit-entered");
    await abortableSleep(80, signal, speed);
  }
  await abortableSleep(200, signal, speed);
  for (const r of allCells) {
    r.cell.classList.remove("unit-entrance", "unit-entered");
  }
}

function showVsSplash(container) {
  const splash = el("div", { class: "vs-splash" },
    el("div", { class: "vs-text" }, "VS"),
  );
  container.appendChild(splash);
  return splash;
}

function showRoundBanner(container, roundNum) {
  const existing = container.querySelector(".round-banner");
  if (existing) existing.remove();
  const banner = el("div", { class: "round-banner" },
    el("span", { class: "round-banner-text" }, `ROUND ${roundNum}`),
  );
  container.appendChild(banner);
  setTimeout(() => banner.remove(), 1200);
}

async function playCinematic(transcript, refs, signal, speed) {
  window.__cinTrace = [];
  const rounds = transcript.rounds || [];
  cinTrace(`start: ${rounds.length} rounds`);

  const field = refs.log.closest(".match-cinematic")
    ?.querySelector(".cinematic-field");

  const splash = field ? showVsSplash(field) : null;
  await abortableSleep(900, signal, speed);
  if (splash) {
    splash.classList.add("vs-exit");
    setTimeout(() => splash.remove(), 500);
  }

  await playEntranceStagger(refs, signal, speed);

  let totalActions = 0;
  for (const round of rounds) {
    if (signal.aborted) {
      cinTrace(`abort before round ${round.round}`);
      return;
    }
    cinTrace(`round ${round.round}: ${round.actions?.length || 0} actions`);
    refs.roundChip.textContent = `ROUND ${round.round}`;
    refs.roundChip.classList.add("pulse");
    setTimeout(() => refs.roundChip.classList.remove("pulse"), 650);

    if (field && round.round > 1) {
      showRoundBanner(field, round.round);
    }
    await abortableSleep(450, signal, speed);

    for (const action of (round.actions || [])) {
      if (signal.aborted) {
        cinTrace(`abort at r${round.round} a${totalActions}`);
        return;
      }
      try {
        await playAction(action, refs, signal, speed);
      } catch (err) {
        if (err.name === "AbortError") throw err;
        cinTrace(`THROW at action ${totalActions}: ${err.message || err}`);
        console.error(`[cinematic] playAction threw at action ${totalActions}:`, err, action);
        throw err;
      }
      totalActions++;
    }
    // Brief breather between rounds.
    await abortableSleep(380, signal, speed);
  }
  cinTrace(`complete: ${totalActions} actions / ${rounds.length} rounds`);
}

async function startCinematic(root, transcript) {
  // Snapshot result + selected NPC into closures so a stray render()
  // re-init (which resets module-level `state`) can't blank them out
  // by the time the cinematic finishes 30s later.
  const capturedResult = state.result;
  const capturedNpcId = state.selectedNpc;
  const capturedNpc = state.npcsById?.[capturedNpcId];

  state.abort = new AbortController();
  const speed = makeSpeedCtl(1);
  const { screen, refs } = buildCinematicDom(root, transcript, speed);
  root.innerHTML = "";
  root.appendChild(screen);

  let cinematicError = null;
  try {
    await playCinematic(transcript, refs, state.abort.signal, speed);
  } catch (err) {
    if (err.name !== "AbortError") {
      cinematicError = err;
      console.error("cinematic walker crashed:", err);
    }
  }
  if (!screen.isConnected) return;
  state.view = "result";
  state.result = state.result ?? capturedResult;
  state.selectedNpc = state.selectedNpc ?? capturedNpcId;
  if (capturedNpc && !state.npcsById?.[capturedNpcId]) {
    state.npcsById = state.npcsById || {};
    state.npcsById[capturedNpcId] = capturedNpc;
  }
  state.abort = null;
  if (cinematicError) state.error = `cinematic: ${cinematicError.message || cinematicError}`;
  rerender(root);
}

function resultView(root) {
  const r = state.result || {};
  const youWon = r.winner === 0;
  const draw = r.winner === null || r.winner === undefined;
  const opponentName = state.npcsById[state.selectedNpc]?.name || state.selectedNpc;
  const outcomeWord = draw ? "DRAW" : youWon ? "VICTORY" : "DEFEAT";
  return el("div", { class: `screen match-result fade-in${
    draw ? " draw" : youWon ? " win" : " loss"}` },
    el("header", { class: "screen-header" },
      el("button", { class: "back-btn",
        onClick: () => { state.view = "picker"; state.result = null; rerender(root); } }, "← BACK"),
      el("h1", null, outcomeWord),
    ),
    el("div", { class: "match-result-body" },
      el("div", { class: "result-banner" },
        el("div", { class: "result-banner-text" }, outcomeWord),
      ),
      el("div", { class: "result-stats-strip" },
        el("div", { class: "result-stat" },
          el("span", { class: "result-stat-label" }, "ROUNDS"),
          el("span", { class: "result-stat-value" }, String(r.round_count ?? 0)),
        ),
        el("div", { class: "result-stat" },
          el("span", { class: "result-stat-label" }, "YOUR HP"),
          el("span", { class: "result-stat-value" }, String(r.side_a_final_hp ?? "?")),
        ),
        el("div", { class: "result-stat" },
          el("span", { class: "result-stat-label" }, opponentName.toUpperCase()),
          el("span", { class: "result-stat-value" }, String(r.side_b_final_hp ?? "?")),
        ),
      ),
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
    state.view = "result";
    rerender(root);
    return;
  }
  state.result = out;
  // If we got a structured transcript, hand off to the cinematic;
  // otherwise (older API or transcript_error) fall back to the
  // legacy result-only view so the player still sees the outcome.
  if (out.transcript && out.transcript.rounds) {
    state.view = "cinematic";
    startCinematic(root, out.transcript);
    return;
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
  // Index the flat npc list by id so npcCard() can resolve names + flavor
  // without N tier-walks per render.
  state.npcsById = {};
  for (const npc of (npcs.npcs || [])) {
    state.npcsById[npc.npc_id] = npc;
  }
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
  if (state.abort) {
    state.abort.abort();
    state.abort = null;
  }
  const renderId = (window.__matchRenderId = (window.__matchRenderId || 0) + 1);
  console.log(`[match] render() #${renderId} params=`, params);
  state = {
    view: "picker",
    tiers: [], npcsById: {}, recommended: null,
    loadouts: [], selectedLoadout: null,
    selectedNpc: null, result: null, error: null,
    abort: null,
  };
  root.innerHTML = `<div class="loading">loading match…</div>`;
  const cleanup = () => { state.abort?.abort(); };
  try {
    await loadAll();
  } catch (err) {
    root.innerHTML = `<div class="error">match unreachable: ${err}</div>`;
    return cleanup;
  }
  if (params && params.length > 0 && params[0]) {
    await startMatch(params[0], root);
    return cleanup;
  }
  rerender(root);
  return cleanup;
}
