// Pull screen — cinematic gacha reveal.
//
// Phases: fetching → draw → tension → freeze → reveal → settled
//
// The reveal is a multi-layered spectacle driven by data-phase + data-rarity
// on the screen root. Every visual effect is CSS-only; JS manages phase
// timing and DOM construction.
//
// Effect layers (back to front):
//   - Ambient background glow (CSS gradient on :scope)
//   - Light rays (conic-gradient, epic+)
//   - Vignette overlay (radial darkening)
//   - Card with rarity glow halo
//   - Converging sparks (tension phase)
//   - Radial burst × 2
//   - Expanding rings × 2
//   - Scatter particles (40)
//   - Radial streaks (8)
//   - White flash overlay
//   - Shimmer sweep + ambient sparkles (settled, rare+)

import { backButton, el, postJSON } from "/screens/_dom.js";

const RARITY_TIMING = {
  common:    { draw: 500,  tension: 400,  freeze: 0,   reveal: 500  },
  uncommon:  { draw: 600,  tension: 600,  freeze: 80,  reveal: 600  },
  rare:      { draw: 700,  tension: 800,  freeze: 120, reveal: 650  },
  epic:      { draw: 800,  tension: 1100, freeze: 160, reveal: 750  },
  legendary: { draw: 900,  tension: 1500, freeze: 200, reveal: 850  },
};
const DEFAULT_TIMING = RARITY_TIMING.rare;

const PARTICLE_COUNT = 40;
const SPARK_COUNT    = 16;
const STREAK_COUNT   = 8;

// ---------------------------------------------------------------------------
// Tiny async helpers
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

// ---------------------------------------------------------------------------
// DOM construction (stable; runs ONCE per screen mount)
// ---------------------------------------------------------------------------

function buildScreen(refs) {
  const back = backButton();
  back.classList.add("pull-back-btn");

  const skip = el("button", { class: "pull-skip", type: "button" }, "SKIP");

  const header = el("header", { class: "screen-header" },
    back,
    el("h1", null, "PULL"),
    skip,
  );

  // Card
  const card = document.createElement("dm-card");
  card.setAttribute("size", "hero");
  card.setAttribute("face", "back");

  // Effect layers inside card-wrap
  const burst      = el("div", { class: "pull-burst" });
  const burstInner = el("div", { class: "pull-burst-inner" });
  const rays       = el("div", { class: "pull-rays" });
  const ring1      = el("div", { class: "pull-ring" });
  const ring2      = el("div", { class: "pull-ring pull-ring-2" });

  // Scatter particles — explode outward on reveal
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

  // Converging sparks — energy drawn toward card during tension
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

  // Radial streaks — light lines shooting outward on reveal
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

  // Overlays (sit above all stage content)
  const vignette = el("div", { class: "pull-vignette" });
  const flash    = el("div", { class: "pull-flash" });

  // Detail panel
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
  const cta = el("div", { class: "pull-cta" }, "PRESS SPACE TO CONTINUE");
  const errLine = el("div", { class: "error-line pull-error" }, "");

  const detail = el("div", { class: "pull-detail" },
    detailName, detailRarity, metaEdition, detailMeta, errLine, cta,
  );

  const body = el("div", { class: "pull-body" }, vignette, flash, stage, detail);
  const screen = el("div", { class: "screen pull-screen" }, header, body);
  screen.dataset.phase = "fetching";

  Object.assign(refs, {
    screen, card, burst, burstInner, hint, skip, back, rays, particles,
    sparks, streaks, ring1, ring2, vignette, flash,
    detailName, detailRarity, metaSerial, metaCost, metaBalance, metaEdition,
    errLine, cta,
  });
  return screen;
}

// ---------------------------------------------------------------------------
// Reveal-sequence orchestration
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

// ---------------------------------------------------------------------------
// Detail-panel population
// ---------------------------------------------------------------------------

function applyReceipt(refs, receipt) {
  const r = receipt || {};
  refs.card.setAttribute("card-id", r.card_id || "");
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
// Lifecycle
// ---------------------------------------------------------------------------

export async function render(root) {
  root.innerHTML = "";
  const refs = {};
  const screen = buildScreen(refs);
  root.appendChild(screen);

  const ctl = new AbortController();

  refs.skip.addEventListener("click", () => ctl.abort());

  function onKey(e) {
    if (e.key === " " || e.key === "Enter") {
      e.preventDefault();
      if (refs.screen.dataset.phase === "settled") {
        location.hash = "#menu";
      } else {
        ctl.abort();
      }
    } else if (e.key === "Escape") {
      location.hash = "#menu";
    }
  }
  document.addEventListener("keydown", onKey);

  let receipt;
  try {
    receipt = await postJSON("/api/pull");
  } catch (err) {
    applyError(refs, `pull failed: ${err}`);
    return cleanup;
  }
  if (receipt?.error) {
    applyError(refs, `${receipt.error}: ${receipt.message || receipt.hint || ""}`);
    return cleanup;
  }
  applyReceipt(refs, receipt);

  runReveal(refs, ctl.signal, receipt.rarity || "common").catch(err => {
    if (err.name !== "AbortError") {
      console.error("reveal sequence", err);
      applyError(refs, "animation error — see console");
    }
  });

  return cleanup;

  function cleanup() {
    document.removeEventListener("keydown", onKey);
    if (!ctl.signal.aborted) ctl.abort();
  }
}
