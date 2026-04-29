// Hash-routed entry point. Lazy-loads each screen module on first visit
// so the menu paint isn't blocked by code the user might never reach.
//
// Routes:
//   #menu (default)  → screens/menu.js
//   #shop            → screens/shop.js
//   #collection      → screens/collection.js
//   #loadouts        → screens/loadouts.js
//   #pull            → screens/pull.js
//   #match/<npc_id>  → screens/match.js (npc id passed via URL)
//
// Live updates land via the WebSocket helper in screens/_ws.js — it
// dispatches "daimon:balance" CustomEvents that any active screen can
// listen for.

import { startLiveSocket } from "/screens/_ws.js";

const root = document.getElementById("root");

const routes = {
  "menu":       () => import("/screens/menu.js"),
  "shop":       () => import("/screens/shop.js"),
  "collection": () => import("/screens/collection.js"),
  "loadouts":   () => import("/screens/loadouts.js"),
  "pull":       () => import("/screens/pull.js"),
  "match":      () => import("/screens/match.js"),
};

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
    showError(`unknown route: ${name}`);
    return;
  }
  showLoading();
  try {
    const mod = await loader();
    if (typeof mod.render !== "function") {
      throw new Error(`screen ${name} did not export render()`);
    }
    // Await so async errors thrown inside render() surface here instead
    // of becoming silent unhandled promise rejections that leave the
    // page blank.
    await mod.render(root, params);
  } catch (err) {
    console.error("nav error", err);
    showError(`failed to render ${name}: ${err}`);
  }
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
