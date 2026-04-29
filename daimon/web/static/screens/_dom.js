// Tiny createElement helper used by every screen. Keeps each screen
// JSX-free without pulling in a framework.

export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "class") node.className = v;
    else if (k.startsWith("on") && typeof v === "function") {
      node.addEventListener(k.slice(2).toLowerCase(), v);
    } else if (v !== false && v !== null && v !== undefined) {
      node.setAttribute(k, v);
    }
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.appendChild(typeof child === "string" || typeof child === "number"
      ? document.createTextNode(String(child))
      : child);
  }
  return node;
}

export function backButton(toHash = "#menu") {
  return el("button", {
    class: "back-btn",
    onClick: () => { location.hash = toHash; },
  }, "← BACK");
}

export async function fetchJSON(url, opts = {}) {
  const r = await fetch(url, opts);
  if (!r.ok) {
    let detail = "";
    try { detail = JSON.stringify(await r.json()); } catch {}
    throw new Error(`${r.status} ${r.statusText} ${detail}`);
  }
  return r.json();
}

export async function postJSON(url, body) {
  return fetchJSON(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
}
