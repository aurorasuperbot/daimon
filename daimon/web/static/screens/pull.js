// Pull screen — fires POST /api/pull then animates the 4-phase reveal:
//
//   DRAW    (0–900ms)    : face-down silhouette, gold shimmer, shuffle
//   TENSION (900–1500ms) : rarity hint pulses in
//   REVEAL  (1500–2200ms): card flips via CSS 3D transform, particle burst
//   SETTLED              : full hero panel + "PRESS SPACE" CTA
//
// SPACE / ENTER skip pre-reveal phases straight to SETTLED.

import { backButton, el, postJSON } from "/screens/_dom.js";

const DRAW_END    = 900;
const TENSION_END = 1500;
const REVEAL_END  = 2200;

let state = { receipt: null, error: null, phase: "fetching", startTs: 0, settled: false };

function shouldFlipBy(elapsed) {
  if (state.settled) return "settled";
  if (elapsed >= REVEAL_END) return "settled";
  if (elapsed >= TENSION_END) return "reveal";
  if (elapsed >= DRAW_END) return "tension";
  return "draw";
}

function settleNow() {
  state.settled = true;
  state.phase = "settled";
  paint();
}

function rarityClass(r) {
  return `rarity-${(r || "common").toLowerCase()}`;
}

function paint() {
  const root = document.getElementById("root");
  if (!root) return;
  if (state.error) {
    root.innerHTML = `<div class="error">${state.error}</div>`;
    return;
  }
  if (!state.receipt) {
    root.innerHTML = `<div class="loading">drawing…</div>`;
    return;
  }
  const r = state.receipt;
  const phaseClass = `phase-${state.phase}`;
  const rarity = rarityClass(r.rarity);

  root.innerHTML = "";
  const back = backButton();
  back.style.opacity = state.phase === "settled" ? "1" : "0";

  const skip = el("button", {
    class: "pull-skip",
    onClick: settleNow,
    style: state.phase === "settled" ? "display:none" : "",
  }, "SKIP");

  const cardArt = document.createElement("card-art");
  cardArt.setAttribute("card-id", r.card_id);

  const flipper = el("div", { class: `pull-flipper ${phaseClass}` },
    el("div", { class: "pull-back" }, "DAIMON"),
    el("div", { class: `pull-front ${rarity}` }, cardArt),
  );

  const burst = el("div", { class: `pull-burst ${rarity}` });

  const hint = el("div", { class: "pull-rarity-hint" },
    (r.rarity || "").toUpperCase(),
  );

  const card = el("div", { class: `pull-card ${rarity}` },
    flipper,
    burst,
    hint,
  );

  const detail = el("div", { class: "pull-detail" },
    el("h2", null, (r.payload?.name || r.card_id || "").toUpperCase()),
    (() => {
      const chip = document.createElement("rarity-chip");
      chip.setAttribute("rarity", r.rarity);
      return chip;
    })(),
    el("div", { class: "pull-detail-meta" },
      el("div", null, `serial ${r.serial || "?"}`),
      el("div", null, `cost ${r.cost ?? "?"}¤`),
      el("div", null, `balance ${r.balance_after ?? "?"}¤`),
    ),
    state.phase === "settled"
      ? el("div", { class: "pull-cta" }, "PRESS SPACE TO CONTINUE")
      : null,
  );

  root.appendChild(el("div", { class: `screen pull-screen fade-in ${phaseClass}` },
    el("header", { class: "screen-header" }, back, el("h1", null, "PULL"), skip),
    el("div", { class: "pull-body" }, card, detail),
  ));
}

function tick() {
  if (state.settled || state.phase === "settled") return;
  const elapsed = performance.now() - state.startTs;
  const next = shouldFlipBy(elapsed);
  if (next !== state.phase) {
    state.phase = next;
    paint();
  }
  if (next !== "settled") {
    requestAnimationFrame(tick);
  } else {
    state.settled = true;
  }
}

function keyHandler(e) {
  if (e.key === " " || e.key === "Enter") {
    e.preventDefault();
    if (state.phase === "settled") {
      location.hash = "#menu";
    } else {
      settleNow();
    }
  } else if (e.key === "Escape") {
    location.hash = "#menu";
  }
}

export async function render(root) {
  state = { receipt: null, error: null, phase: "fetching", startTs: 0, settled: false };
  paint();
  document.addEventListener("keydown", keyHandler);

  let receipt;
  try {
    receipt = await postJSON("/api/pull");
  } catch (err) {
    state.error = `pull failed: ${err}`;
    paint();
    return;
  }
  if (receipt.error) {
    state.error = `${receipt.error}: ${receipt.message || ""}`;
    paint();
    return;
  }
  state.receipt = receipt;
  state.phase = "draw";
  state.startTs = performance.now();
  paint();
  requestAnimationFrame(tick);

  // Cleanup keydown when the user navigates away.
  window.addEventListener("hashchange", () => {
    document.removeEventListener("keydown", keyHandler);
  }, { once: true });
}
