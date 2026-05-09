// Pull screen — station landing + single/multi gacha reveal.
//
// Flow: station (choose x1 or x10) → cinematic → settled
//
// Single-pull: full multi-layered spectacle (phases + effects).
// Multi-pull: cascade — cards revealed one at a time with abbreviated
// animations, then displayed in a 5×2 results grid.

import { backButton, el, fetchJSON, postJSON } from "/screens/_dom.js";
import { go } from "/app.js";
import { openCardModal } from "/components/dm-card.js";
import { liveStore } from "/store.js";

const RARITY_TIMING = {
  common:    { draw: 500,  tension: 400,  freeze: 0,   reveal: 500  },
  uncommon:  { draw: 600,  tension: 600,  freeze: 80,  reveal: 600  },
  rare:      { draw: 700,  tension: 800,  freeze: 120, reveal: 650  },
  epic:      { draw: 800,  tension: 1100, freeze: 160, reveal: 750  },
  legendary: { draw: 900,  tension: 1500, freeze: 200, reveal: 850  },
};
const DEFAULT_TIMING = RARITY_TIMING.rare;

const MULTI_TIMING = {
  common:    { tension: 200, reveal: 300 },
  uncommon:  { tension: 300, reveal: 350 },
  rare:      { tension: 400, reveal: 450 },
  epic:      { tension: 600, reveal: 550 },
  legendary: { tension: 800, reveal: 650 },
};

const PARTICLE_COUNT = 40;
const SPARK_COUNT    = 16;
const STREAK_COUNT   = 8;

const RARITY_ORDER = ["common", "uncommon", "rare", "epic", "legendary"];

// ---------------------------------------------------------------------------
// Async helpers
// ---------------------------------------------------------------------------

function abortableSleep(ms, signal) {
  if (ms <= 0) return Promise.resolve();
  return new Promise((resolve, reject) => {
    if (signal.aborted) return reject(new DOMException("aborted", "AbortError"));
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

function waitForAdvance(signal) {
  return new Promise((resolve, reject) => {
    if (signal.aborted) return reject(new DOMException("aborted", "AbortError"));
    const modalOpen = () => !!document.querySelector(".dm-card-modal-overlay:not([hidden])");
    function onKey(e) {
      if (modalOpen()) return;
      if (e.key === " " || e.key === "Enter") {
        e.preventDefault();
        cleanup();
        resolve();
      }
    }
    function onClick(e) {
      if (modalOpen()) return;
      if (e.target.closest(".pull-skip")) return;
      if (e.target.closest("dm-card")) return;
      cleanup();
      resolve();
    }
    function onAbort() { cleanup(); reject(new DOMException("aborted", "AbortError")); }
    function cleanup() {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("click", onClick);
      signal.removeEventListener("abort", onAbort);
    }
    document.addEventListener("keydown", onKey);
    document.addEventListener("click", onClick);
    signal.addEventListener("abort", onAbort, { once: true });
  });
}

// ---------------------------------------------------------------------------
// Station view — landing with balance, pity meter, pull buttons
// ---------------------------------------------------------------------------

function buildStation(refs) {
  const back = backButton();
  const header = el("header", { class: "screen-header" },
    back,
    el("h1", null, "PULL STATION"),
    el("div"),
  );

  const balanceVal = el("span", { class: "station-balance-val" }, "—");
  const balanceRow = el("div", { class: "station-balance" },
    el("span", { class: "station-balance-label" }, "BALANCE"),
    balanceVal,
  );

  const pityBar = el("div", { class: "station-pity-bar" });
  const pityFill = el("div", { class: "station-pity-fill" });
  pityBar.appendChild(pityFill);
  const pityLabel = el("div", { class: "station-pity-label" }, "");
  const pityStatus = el("div", { class: "station-pity-status" }, "");
  const pitySection = el("div", { class: "station-pity" },
    el("div", { class: "station-pity-header" }, "PITY COUNTER"),
    pityBar,
    pityLabel,
    pityStatus,
  );

  const pullBtn = el("button", { class: "station-pull-btn", "data-mode": "single" },
    el("span", { class: "station-pull-label" }, "PULL ×1"),
    el("span", { class: "station-pull-cost" }, "100¤"),
  );
  const multiBtn = el("button", { class: "station-pull-btn station-pull-multi", "data-mode": "multi" },
    el("span", { class: "station-pull-label" }, "PULL ×10"),
    el("span", { class: "station-pull-cost" }, "1000¤"),
  );

  const btnRow = el("div", { class: "station-btn-row" }, pullBtn, multiBtn);

  const errLine = el("div", { class: "error-line station-error" }, "");

  const body = el("div", { class: "station-body" },
    balanceRow, pitySection, btnRow, errLine,
  );

  const screen = el("div", { class: "screen pull-screen", "data-view": "station" }, header, body);

  Object.assign(refs, {
    screen, balanceVal, pityFill, pityLabel, pityStatus,
    pullBtn, multiBtn, errLine,
  });
  return screen;
}

async function loadStationData(refs) {
  try {
    const data = await fetchJSON("/api/pull/pity");
    refs.balanceVal.textContent = `${data.balance ?? 0}¤`;

    const pct = Math.min(100, (data.pulls_since_rare_plus / data.hard_pity_at) * 100);
    refs.pityFill.style.width = `${pct}%`;

    if (data.pulls_since_rare_plus >= data.soft_pity_start) {
      refs.pityFill.dataset.active = "soft";
    }
    if (data.next_is_guaranteed) {
      refs.pityFill.dataset.active = "hard";
    }

    refs.pityLabel.textContent =
      `${data.pulls_since_rare_plus} / ${data.hard_pity_at} pulls since rare+`;

    if (data.next_is_guaranteed) {
      refs.pityStatus.textContent = "GUARANTEED RARE+ ON NEXT PULL";
      refs.pityStatus.dataset.state = "guaranteed";
    } else if (data.soft_pity_active) {
      refs.pityStatus.textContent = `SOFT PITY ACTIVE — +${Math.round(data.pity_bonus * 100)}% RARE+ BONUS`;
      refs.pityStatus.dataset.state = "soft";
    } else {
      const remaining = data.soft_pity_start - data.pulls_since_rare_plus;
      refs.pityStatus.textContent = `${remaining} pulls until soft pity`;
      refs.pityStatus.dataset.state = "normal";
    }

    refs.pullBtn.disabled = !data.can_pull;
    refs.multiBtn.disabled = !data.can_multi;

    if (!data.can_pull) {
      refs.pullBtn.title = "Not enough balance";
    }
    if (!data.can_multi) {
      refs.multiBtn.title = data.can_pull ? "Need 1000¤ for x10" : "Not enough balance";
    }
  } catch (err) {
    refs.errLine.textContent = `failed to load: ${err}`;
    refs.errLine.style.display = "block";
  }
}

// ---------------------------------------------------------------------------
// Single-pull DOM construction
// ---------------------------------------------------------------------------

function buildSinglePull(refs) {
  const skip = el("button", { class: "pull-skip", type: "button" }, "SKIP");

  const header = el("header", { class: "screen-header" },
    el("div"),
    el("h1", null, "PULL"),
    skip,
  );

  const card = document.createElement("dm-card");
  card.setAttribute("size", "hero");
  card.setAttribute("face", "back");

  const burst      = el("div", { class: "pull-burst" });
  const burstInner = el("div", { class: "pull-burst-inner" });
  const rays       = el("div", { class: "pull-rays" });
  const ring1      = el("div", { class: "pull-ring" });
  const ring2      = el("div", { class: "pull-ring pull-ring-2" });

  const particles = el("div", { class: "pull-particles" });
  for (let i = 0; i < PARTICLE_COUNT; i++) {
    const angle = (i / PARTICLE_COUNT) * Math.PI * 2;
    const dist = 120 + Math.random() * 250;
    const p = el("div", { class: "pull-particle" });
    p.style.setProperty("--dx", `${Math.cos(angle) * dist}px`);
    p.style.setProperty("--dy", `${Math.sin(angle) * dist}px`);
    p.style.setProperty("--delay", `${Math.random() * 250}ms`);
    p.style.setProperty("--size", `${2 + Math.random() * 6}px`);
    particles.appendChild(p);
  }

  const sparks = el("div", { class: "pull-sparks" });
  for (let i = 0; i < SPARK_COUNT; i++) {
    const angle = (i / SPARK_COUNT) * Math.PI * 2 + (Math.random() - 0.5) * 0.4;
    const dist = 180 + Math.random() * 160;
    const s = el("div", { class: "pull-spark" });
    s.style.setProperty("--sx", `${Math.cos(angle) * dist}px`);
    s.style.setProperty("--sy", `${Math.sin(angle) * dist}px`);
    s.style.setProperty("--delay", `${Math.random() * 400}ms`);
    s.style.setProperty("--size", `${2 + Math.random() * 3}px`);
    sparks.appendChild(s);
  }

  const streaks = el("div", { class: "pull-streaks" });
  for (let i = 0; i < STREAK_COUNT; i++) {
    const angle = (i / STREAK_COUNT) * 360 + Math.random() * 15;
    const dist = 180 + Math.random() * 170;
    const s = el("div", { class: "pull-streak" });
    s.style.setProperty("--angle", `${angle}deg`);
    s.style.setProperty("--dist", `${dist}px`);
    s.style.setProperty("--delay", `${Math.random() * 120}ms`);
    streaks.appendChild(s);
  }

  const cardWrap = el("div", { class: "pull-card-wrap" },
    card, rays, burst, burstInner, ring1, ring2, particles, sparks, streaks);

  const hint  = el("div", { class: "pull-rarity-hint" });
  const stage = el("div", { class: "pull-stage" }, cardWrap, hint);

  const vignette = el("div", { class: "pull-vignette" });
  const flash    = el("div", { class: "pull-flash" });

  const detailName   = el("h2", { class: "pull-detail-name" }, "—");
  const detailRarity = el("div", { class: "pull-detail-rarity" }, "");
  const detailMeta   = el("div", { class: "pull-detail-meta" });
  const metaSerial   = el("span", null, "serial —");
  const metaCost     = el("span", null, "cost —");
  const metaBalance  = el("span", null, "balance —");
  const metaEdition  = el("span", { class: "pull-edition-badge" }, "");
  detailMeta.appendChild(metaSerial);
  detailMeta.appendChild(el("span", { class: "pull-meta-sep" }, "·"));
  detailMeta.appendChild(metaCost);
  detailMeta.appendChild(el("span", { class: "pull-meta-sep" }, "·"));
  detailMeta.appendChild(metaBalance);
  const cta = el("div", { class: "pull-cta" }, "CLICK CARD TO INSPECT · SPACE TO CONTINUE");
  const errLine = el("div", { class: "error-line pull-error" }, "");

  const detail = el("div", { class: "pull-detail" },
    detailName, detailRarity, metaEdition, detailMeta, errLine, cta,
  );

  const body = el("div", { class: "pull-body" }, vignette, flash, stage, detail);
  const screen = el("div", { class: "screen pull-screen" }, header, body);
  screen.dataset.phase = "fetching";

  Object.assign(refs, {
    screen, card, burst, burstInner, hint, skip, rays, particles,
    sparks, streaks, ring1, ring2, vignette, flash,
    detailName, detailRarity, metaSerial, metaCost, metaBalance, metaEdition,
    errLine, cta,
  });
  return screen;
}

// ---------------------------------------------------------------------------
// Single-pull orchestration
// ---------------------------------------------------------------------------

async function runReveal(refs, signal, rarity) {
  const { screen, card } = refs;
  const timing = RARITY_TIMING[rarity] || DEFAULT_TIMING;

  screen.style.setProperty("--tension-ms", `${timing.tension}ms`);

  screen.dataset.phase = "draw";
  try {
    await abortableSleep(timing.draw, signal);
    screen.dataset.phase = "tension";
    await abortableSleep(timing.tension, signal);
    if (timing.freeze > 0) {
      screen.dataset.phase = "freeze";
      await abortableSleep(timing.freeze, signal);
    }
    screen.dataset.phase = "reveal";
    card.setAttribute("face", "front");
    await abortableSleep(timing.reveal, signal);
    screen.dataset.phase = "settled";
  } catch (err) {
    if (err.name === "AbortError") {
      if (screen.isConnected) {
        screen.dataset.phase = "settled";
        card.setAttribute("face", "front");
      }
      return;
    }
    throw err;
  }
}

function applyReceipt(refs, receipt) {
  const r = receipt || {};
  refs.card.setAttribute("card-id", r.card_id || "");
  refs.card.setAttribute("data-serial", r.serial || "");
  refs.detailName.textContent   = (r.payload?.name || r.card_id || "").toUpperCase();
  refs.detailRarity.textContent = (r.rarity || "").toUpperCase();
  refs.detailRarity.dataset.rarity = (r.rarity || "common");
  refs.metaSerial.textContent   = `serial ${r.serial || "?"}`;
  refs.metaCost.textContent     = `cost ${r.cost ?? "?"}¤`;
  refs.metaBalance.textContent  = `balance ${r.balance_after ?? "?"}¤`;
  refs.screen.dataset.rarity = (r.rarity || "common");

  if (r.edition) {
    refs.metaEdition.textContent = `${r.edition.toUpperCase()} EDITION`;
    refs.card.setAttribute("data-edition", r.edition);
  } else {
    refs.metaEdition.textContent = "";
    refs.card.removeAttribute("data-edition");
  }
}

function applyError(refs, msg) {
  refs.errLine.textContent = msg || "pull failed";
  refs.errLine.style.display = "block";
  refs.screen.dataset.phase = "settled";
}

// ---------------------------------------------------------------------------
// Multi-pull — cascade reveal
// ---------------------------------------------------------------------------

function buildMultiPull(refs) {
  const skip = el("button", { class: "pull-skip", type: "button" }, "SKIP ALL");

  const header = el("header", { class: "screen-header" },
    el("div"),
    el("h1", null, "PULL ×10"),
    skip,
  );

  const cascade = el("div", { class: "multi-cascade" });
  const counter = el("div", { class: "multi-counter" }, "0 / 10");
  const advanceHint = el("div", { class: "multi-advance-hint", hidden: "" }, "TAP / SPACE");
  const stage = el("div", { class: "multi-stage" }, cascade, counter, advanceHint);

  const grid = el("div", { class: "multi-grid" });

  const summary = el("div", { class: "multi-summary" });
  const cta = el("div", { class: "pull-cta" }, "CLICK CARDS TO INSPECT · SPACE TO CONTINUE");

  const body = el("div", { class: "pull-body multi-body" }, stage, grid, summary, cta);
  const screen = el("div", { class: "screen pull-screen", "data-view": "multi" }, header, body);
  screen.dataset.phase = "fetching";

  Object.assign(refs, { screen, skip, cascade, counter, advanceHint, grid, summary, cta });
  return screen;
}

async function runMultiReveal(refs, receipts, signal) {
  const { screen, cascade, counter, advanceHint, grid, summary } = refs;

  screen.dataset.phase = "cascade";

  const rarityCount = {};
  let bestRarity = "common";

  for (let i = 0; i < receipts.length; i++) {
    if (signal.aborted) break;

    const r = receipts[i];
    const rarity = r.rarity || "common";
    rarityCount[rarity] = (rarityCount[rarity] || 0) + 1;

    const ri = RARITY_ORDER.indexOf(rarity);
    if (ri > RARITY_ORDER.indexOf(bestRarity)) bestRarity = rarity;

    counter.textContent = `${i + 1} / ${receipts.length}`;

    const card = document.createElement("dm-card");
    card.setAttribute("size", "detail");
    card.setAttribute("card-id", r.card_id || "");
    card.setAttribute("data-serial", r.serial || "");
    card.setAttribute("face", "back");
    card.dataset.rarity = rarity;

    const wrap = el("div", { class: "multi-cascade-card" }, card);
    wrap.dataset.rarity = rarity;
    cascade.innerHTML = "";
    cascade.appendChild(wrap);

    screen.dataset.rarity = rarity;

    const timing = MULTI_TIMING[rarity] || MULTI_TIMING.rare;

    try {
      wrap.dataset.phase = "tension";
      await abortableSleep(timing.tension, signal);
      wrap.dataset.phase = "reveal";
      card.setAttribute("face", "front");
      advanceHint.removeAttribute("hidden");
      await waitForAdvance(signal);
      advanceHint.setAttribute("hidden", "");
    } catch (err) {
      if (err.name === "AbortError") break;
      throw err;
    }
  }

  advanceHint.setAttribute("hidden", "");

  // Transition to grid view
  screen.dataset.phase = "grid";
  cascade.innerHTML = "";
  counter.textContent = "";

  for (let i = 0; i < receipts.length; i++) {
    const r = receipts[i];
    const card = document.createElement("dm-card");
    card.setAttribute("size", "tile");
    card.setAttribute("card-id", r.card_id || "");
    card.setAttribute("face", "front");

    const tile = el("div", { class: "multi-grid-tile" }, card);
    tile.dataset.rarity = r.rarity || "common";
    tile.style.setProperty("--i", i);
    tile.addEventListener("click", () => openCardModal(r.card_id, r.serial));
    grid.appendChild(tile);
  }

  // Summary strip
  const parts = [];
  for (const r of RARITY_ORDER) {
    if (rarityCount[r]) {
      parts.push(el("span", { class: "multi-summary-chip", "data-rarity": r },
        `${rarityCount[r]}× ${r.toUpperCase()}`));
    }
  }
  const lastR = receipts[receipts.length - 1];
  parts.push(el("span", { class: "multi-summary-balance" },
    `Balance: ${lastR?.balance_after ?? "?"}¤`));
  for (const p of parts) summary.appendChild(p);

  screen.dataset.rarity = bestRarity;
  screen.dataset.phase = "settled";
}

// ---------------------------------------------------------------------------
// Lifecycle
// ---------------------------------------------------------------------------

export async function render(root) {
  root.innerHTML = "";

  const stationRefs = {};
  const stationScreen = buildStation(stationRefs);
  root.appendChild(stationScreen);
  await loadStationData(stationRefs);

  let cleanup = () => {};
  let activeSub = null;

  activeSub = liveStore.subscribe((_state, frame) => {
    if (frame?.kind === "pull" || frame?.kind === "multi_pull") {
      loadStationData(stationRefs);
    }
  });

  function stationCleanup() {
    if (activeSub) { activeSub(); activeSub = null; }
  }

  async function startSinglePull() {
    stationCleanup();
    root.innerHTML = "";
    const refs = {};
    const screen = buildSinglePull(refs);
    root.appendChild(screen);

    const ctl = new AbortController();

    refs.skip.addEventListener("click", () => ctl.abort());

    function onKey(e) {
      if (e.key === " " || e.key === "Enter") {
        e.preventDefault();
        if (refs.screen.dataset.phase === "settled") {
          go("#pull");
        } else {
          ctl.abort();
        }
      } else if (e.key === "Escape") {
        go("#pull");
      }
    }
    document.addEventListener("keydown", onKey);

    cleanup = () => {
      document.removeEventListener("keydown", onKey);
      if (!ctl.signal.aborted) ctl.abort();
    };

    let receipt;
    try {
      receipt = await postJSON("/api/pull");
    } catch (err) {
      applyError(refs, `pull failed: ${err}`);
      return;
    }
    if (receipt?.error) {
      applyError(refs, `${receipt.error}: ${receipt.message || receipt.hint || ""}`);
      return;
    }
    applyReceipt(refs, receipt);

    runReveal(refs, ctl.signal, receipt.rarity || "common").catch(err => {
      if (err.name !== "AbortError") {
        console.error("reveal sequence", err);
        applyError(refs, "animation error — see console");
      }
    });
  }

  async function startMultiPull() {
    stationCleanup();
    root.innerHTML = "";
    const refs = {};
    const screen = buildMultiPull(refs);
    root.appendChild(screen);

    const ctl = new AbortController();

    refs.skip.addEventListener("click", () => ctl.abort());

    function onKey(e) {
      if (e.key === " " || e.key === "Enter") {
        e.preventDefault();
        if (refs.screen.dataset.phase === "settled") {
          go("#pull");
        }
      } else if (e.key === "Escape") {
        go("#pull");
      }
    }
    document.addEventListener("keydown", onKey);

    cleanup = () => {
      document.removeEventListener("keydown", onKey);
      if (!ctl.signal.aborted) ctl.abort();
    };

    let result;
    try {
      result = await postJSON("/api/pull/multi", { count: 10 });
    } catch (err) {
      refs.screen.dataset.phase = "settled";
      refs.summary.textContent = `pull failed: ${err}`;
      return;
    }
    if (result?.error) {
      refs.screen.dataset.phase = "settled";
      refs.summary.textContent = `${result.error}: ${result.message || ""}`;
      return;
    }

    const receipts = result.receipts || [];
    if (receipts.length === 0) {
      refs.screen.dataset.phase = "settled";
      refs.summary.textContent = "No pulls — insufficient balance";
      return;
    }

    refs.counter.textContent = `0 / ${receipts.length}`;

    runMultiReveal(refs, receipts, ctl.signal).catch(err => {
      if (err.name !== "AbortError") {
        console.error("multi-reveal", err);
        refs.summary.textContent = "animation error — see console";
      }
    });
  }

  stationRefs.pullBtn.addEventListener("click", startSinglePull);
  stationRefs.multiBtn.addEventListener("click", startMultiPull);

  return () => {
    stationCleanup();
    cleanup();
  };
}
