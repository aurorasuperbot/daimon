// Pull screen — stable-DOM redesign.
//
// Mount sequence:
//   1. render() builds the screen DOM ONCE: header + flipper +
//      detail panel. Card starts face="back" (face-down) and
//      empty (no card-id yet).
//   2. POST /api/pull → receipt with card_id, rarity, payload, etc.
//      Set card-id on the dm-card while it's still face="back" so
//      the front quietly populates behind the scenes; the user
//      doesn't see anything change.
//   3. Run the orchestration sequence:
//        draw     (0–900ms)   — face-down card subtly idles
//        tension  (900–1500ms)— rarity hint pulses in
//        reveal   (1500–2200ms)— face flips to "front"; rarity burst
//        settled  (2200ms+)   — final, CTA visible
//   4. SKIP aborts the sequence and jumps straight to settled.
//
// Animation philosophy:
//   - Phase progression is data-phase on the screen root. CSS reads it
//     and runs declarative keyframes/transitions. No DOM rebuilds, no
//     paint() loop, no requestAnimationFrame in JS land.
//   - Phase timing is sleep() awaits with an AbortController so SKIP /
//     navigate-away can cleanly cancel mid-sequence.
//   - The face flip itself is the dm-card's built-in rotateY transition;
//     setting face="front" is the only thing this screen does to flip it.

import { backButton, el, postJSON } from "/screens/_dom.js";

const DRAW_MS    = 900;
const TENSION_MS = 600;   // 900 → 1500
const REVEAL_MS  = 700;   // 1500 → 2200

// ---------------------------------------------------------------------------
// Tiny async helpers
// ---------------------------------------------------------------------------

/** Sleep that rejects on abort signal so callers can cancel cleanly. */
function abortableSleep(ms, signal) {
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
  const back = backButton();   // "← BACK"
  back.classList.add("pull-back-btn");

  const skip = el("button", { class: "pull-skip", type: "button" }, "SKIP");

  const header = el("header", { class: "screen-header" },
    back,
    el("h1", null, "PULL"),
    skip,
  );

  // The card itself — face=back at mount; switched to face=front during
  // the reveal phase. Same element through every phase. Same view-
  // transition-name as the menu hero, so the route swap morphs.
  const card = document.createElement("dm-card");
  card.setAttribute("size", "hero");
  card.setAttribute("face", "back");

  // Burst flash + rarity hint live alongside the card; both are pure
  // CSS-driven on data-phase so they cost no JS at runtime.
  const burst = el("div", { class: "pull-burst" });
  const hint  = el("div", { class: "pull-rarity-hint" });

  const cardWrap = el("div", { class: "pull-card-wrap" }, card, burst, hint);

  // Detail panel — text-only nodes that get patched in updateDetail().
  // Built once, mutated leaf-only thereafter.
  const detailName    = el("h2", { class: "pull-detail-name" }, "—");
  const detailRarity  = el("div", { class: "pull-detail-rarity" }, "");
  const detailMeta    = el("div", { class: "pull-detail-meta" });
  const metaSerial    = el("div", null, "serial —");
  const metaCost      = el("div", null, "cost —");
  const metaBalance   = el("div", null, "balance —");
  detailMeta.appendChild(metaSerial);
  detailMeta.appendChild(metaCost);
  detailMeta.appendChild(metaBalance);
  const cta = el("div", { class: "pull-cta" }, "PRESS SPACE TO CONTINUE");
  const errLine = el("div", { class: "error-line pull-error" }, "");

  const detail = el("div", { class: "pull-detail" },
    detailName, detailRarity, detailMeta, errLine, cta,
  );

  const body = el("div", { class: "pull-body" }, cardWrap, detail);
  const screen = el("div", { class: "screen pull-screen" }, header, body);

  // Initial phase — the CSS reveals/hides progressively as data-phase advances.
  screen.dataset.phase = "fetching";

  Object.assign(refs, {
    screen, card, burst, hint, skip, back,
    detailName, detailRarity, metaSerial, metaCost, metaBalance, errLine, cta,
  });
  return screen;
}

// ---------------------------------------------------------------------------
// Reveal-sequence orchestration
// ---------------------------------------------------------------------------

async function runReveal(refs, signal) {
  const { screen, card } = refs;
  screen.dataset.phase = "draw";
  try {
    await abortableSleep(DRAW_MS, signal);
    screen.dataset.phase = "tension";
    await abortableSleep(TENSION_MS, signal);
    screen.dataset.phase = "reveal";
    card.setAttribute("face", "front");
    await abortableSleep(REVEAL_MS, signal);
    screen.dataset.phase = "settled";
  } catch (err) {
    if (err.name === "AbortError") {
      // Skip pressed (or screen unmounted). Jump straight to settled
      // unless we were torn down (in which case the screen is gone).
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
  // Pipe rarity to the screen root so .pull-burst etc. inherit
  // --rarity-* tokens.
  refs.screen.dataset.rarity = (r.rarity || "common");
}

function applyError(refs, msg) {
  refs.errLine.textContent = msg || "pull failed";
  refs.errLine.style.display = "block";
  // Skip past the animation — show the error in settled state.
  refs.screen.dataset.phase = "settled";
}

// ---------------------------------------------------------------------------
// Lifecycle
// ---------------------------------------------------------------------------

export async function render(root) {
  // The router primes root with `<div class="loading">` before awaiting
  // us — every other screen replaces that, so do the same. Without
  // this, the loading glyph stacks under the pull-screen because we
  // append rather than reset.
  root.innerHTML = "";
  const refs = {};
  const screen = buildScreen(refs);
  root.appendChild(screen);

  // Cancellation harness — covers both SKIP and screen unmount.
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

  // Kick off the network call. The card mounts face=back so the user
  // doesn't see the data populate before the dramatic reveal.
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

  // Run the reveal. Errors other than AbortError surface in the
  // detail strip; AbortError is the SKIP path and is handled inline.
  runReveal(refs, ctl.signal).catch(err => {
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
