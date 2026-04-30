// Hash router + screen lifecycle. Each screen module exports an
// async render(rootEl, params) that may return a cleanup function.
// app.js calls the previous screen's cleanup before swapping, and
// wraps the swap in document.startViewTransition() when available
// so route changes get a free cross-fade and any matching
// view-transition-name elements morph (e.g. menu hero → pull card).
//
// Routes:
//   #menu (default)  → screens/menu.js
//   #shop            → screens/shop.js
//   #collection      → screens/collection.js
//   #loadouts        → screens/loadouts.js
//   #pull            → screens/pull.js
//   #match/<npc_id>  → screens/match.js (npc id passed via URL)
//   #pvp[/<id>]      → screens/pvp.js  (optional challenge id for deep-link)

import { startLiveSocket } from "/store.js";

const root = document.getElementById("root");

const routes = {
  "menu":       () => import("/screens/menu.js"),
  "shop":       () => import("/screens/shop.js"),
  "collection": () => import("/screens/collection.js"),
  "loadouts":   () => import("/screens/loadouts.js"),
  "pull":       () => import("/screens/pull.js"),
  "match":      () => import("/screens/match.js"),
  "pvp":        () => import("/screens/pvp.js"),
};

let currentCleanup = null;

function showLoading() {
  root.innerHTML = `<div class="loading">loading…</div>`;
}

function showError(msg) {
  root.innerHTML = `<div class="error">${msg}</div>`;
}

function parseHash() {
  const raw = (location.hash || "#menu").slice(1);
  const [name, ...rest] = raw.split("/");
  return { name: name || "menu", params: rest };
}

async function navigate() {
  const { name, params } = parseHash();
  const loader = routes[name];
  if (!loader) {
    runCleanup();
    showError(`unknown route: ${name}`);
    return;
  }

  let mod;
  try {
    mod = await loader();
  } catch (err) {
    runCleanup();
    showError(`failed to load ${name}: ${err}`);
    return;
  }
  if (typeof mod.render !== "function") {
    runCleanup();
    showError(`screen ${name} did not export render()`);
    return;
  }

  // The actual swap — runs inside startViewTransition when supported so
  // the browser captures before/after states and animates the diff. Any
  // matching view-transition-name elements morph between screens
  // (the menu hero card and the pull-front card share the name
  // "dm-card-hero", giving a shared-element transition for free).
  const swap = async () => {
    runCleanup();
    showLoading();
    try {
      const cleanup = await mod.render(root, params);
      currentCleanup = (typeof cleanup === "function") ? cleanup : null;
    } catch (err) {
      console.error("render error", err);
      showError(`failed to render ${name}: ${err}`);
    }
  };

  if (typeof document.startViewTransition === "function") {
    // The transition wraps both the synchronous DOM teardown AND the
    // async render. Browsers without same-document VT support run
    // the swap directly.
    try {
      const t = document.startViewTransition(swap);
      // Ignore .ready / .finished rejections — they just mean the
      // transition was skipped (e.g. duplicate view-transition-name).
      t.finished.catch(() => {});
    } catch (err) {
      console.warn("view transition failed; falling back", err);
      await swap();
    }
  } else {
    await swap();
  }
}

function runCleanup() {
  if (!currentCleanup) return;
  try { currentCleanup(); }
  catch (err) { console.error("screen cleanup", err); }
  currentCleanup = null;
}

export function go(hash) {
  if (location.hash === hash) {
    navigate();
  } else {
    location.hash = hash;
  }
}

window.addEventListener("hashchange", navigate);
window.addEventListener("DOMContentLoaded", () => {
  startLiveSocket();
  navigate();
});
