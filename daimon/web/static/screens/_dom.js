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

// ---------------------------------------------------------------------------
// promptText() — drop-in replacement for window.prompt()
// ---------------------------------------------------------------------------
//
// Returns a Promise<string|null>: trimmed input on confirm, null on cancel.
// Renders a modal overlay (.dm-prompt-overlay) with a labelled text input,
// CONFIRM + CANCEL buttons, Enter-to-confirm and Escape-to-cancel. The
// modal autofocuses the input. Closes itself + cleans up listeners on
// resolve.
//
// Validation: an optional `validate(value) -> string|null` callback runs
// on each Enter / CONFIRM click. Returning a string shows it as an inline
// error and keeps the modal open. Returning null/undefined accepts.

export function promptText({
  title = "",
  label = "",
  placeholder = "",
  defaultValue = "",
  confirmLabel = "OK",
  cancelLabel = "CANCEL",
  validate = null,
} = {}) {
  return new Promise((resolve) => {
    const overlay = el("div", { class: "dm-prompt-overlay" });
    const stage   = el("div", { class: "dm-prompt-stage" });
    const titleEl = title ? el("div", { class: "dm-prompt-title" }, title) : null;
    const labelEl = label ? el("label", { class: "dm-prompt-label" }, label) : null;
    const errorEl = el("div", { class: "dm-prompt-error" });
    errorEl.setAttribute("hidden", "");

    const input = el("input", {
      class: "dm-prompt-input",
      type: "text",
      placeholder,
      autocomplete: "off",
      spellcheck: "false",
    });
    input.value = defaultValue;

    const finish = (result) => {
      overlay.remove();
      document.removeEventListener("keydown", onKey);
      resolve(result);
    };
    const tryConfirm = () => {
      const value = (input.value || "").trim();
      if (validate) {
        const msg = validate(value);
        if (msg) {
          errorEl.textContent = msg;
          errorEl.removeAttribute("hidden");
          input.focus();
          input.select();
          return;
        }
      }
      finish(value || null);
    };
    const cancel = () => finish(null);

    function onKey(e) {
      if (e.key === "Enter") { e.preventDefault(); tryConfirm(); }
      else if (e.key === "Escape") { e.preventDefault(); cancel(); }
    }

    const confirmBtn = el("button", { class: "dm-prompt-btn primary",
      onClick: tryConfirm, type: "button" }, confirmLabel);
    const cancelBtn  = el("button", { class: "dm-prompt-btn",
      onClick: cancel, type: "button" }, cancelLabel);

    if (titleEl) stage.appendChild(titleEl);
    if (labelEl) stage.appendChild(labelEl);
    stage.appendChild(input);
    stage.appendChild(errorEl);
    stage.appendChild(el("div", { class: "dm-prompt-actions" },
      cancelBtn, confirmBtn));
    overlay.appendChild(stage);
    overlay.addEventListener("click", (e) => {
      // Click on the dark backdrop (not the stage) cancels.
      if (e.target === overlay) cancel();
    });
    document.body.appendChild(overlay);
    document.addEventListener("keydown", onKey);

    // Autofocus + select-all so the user can immediately type or replace.
    setTimeout(() => { input.focus(); input.select(); }, 0);
  });
}
