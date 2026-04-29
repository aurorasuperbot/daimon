// <dm-card> — the single source of truth for "what a card looks like".
//
// One element, three sizes, two faces, one styling surface.
//   <dm-card card-id="iron_boar" size="hero|tile|full" face="front|back">
//
// Design rules this component enforces:
//   1. Stable DOM. The internal tree is created ONCE in connectedCallback.
//      Subsequent attribute changes mutate text nodes / attributes / CSS
//      variables — never innerHTML. Setting card-id to the same value is
//      a no-op. This is what kills the pull blink.
//   2. Light DOM + @scope CSS. The card lives in the real document tree
//      so view-transition-name binds, devtools shows real elements, and
//      the global theme variables propagate without ::part() gymnastics.
//   3. Tokens drive visuals. No hardcoded colors. Setting data-rarity
//      on the card swaps the entire palette through CSS variable
//      inheritance.
//
// Variable-length sub-content (triggers, moves) uses a "sync slots" helper
// that adds/removes child elements only when the count changes — never a
// full rebuild of the list.

import { cardStore } from "/store.js";

// ---------------------------------------------------------------------------
// Tiny DOM helpers — local to keep the component self-contained.
// ---------------------------------------------------------------------------

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls)  n.className = cls;
  if (text != null) n.textContent = text;
  return n;
}

/** Resize an element-list child pool to exactly `count` items.
 *  Existing children are preserved (caller updates their content);
 *  new ones are created via `make()`; surplus ones are removed.
 *  Stable DOM in steady state — no churn unless count changes. */
function syncSlots(parent, count, make) {
  while (parent.children.length < count) parent.appendChild(make());
  while (parent.children.length > count) parent.lastChild.remove();
}

// ---------------------------------------------------------------------------
// Layout shell — built once per <dm-card> instance.
// ---------------------------------------------------------------------------

/** Build the static skeleton. Returns a refs map so the component can
 *  patch leaves without ever re-querying. */
function buildShell(host) {
  const back = el("div", "dm-card-back");
  back.appendChild(el("div", "dm-card-back-mark", "DAIMON"));

  const art       = el("div", "dm-card-art");
  const artImg    = el("img", "dm-card-art-img");
  artImg.alt      = "";
  artImg.draggable = false;
  // Soft-fail: when /art/{id} 404s (or any image error), mark the host
  // so CSS can swap to a tasteful placeholder instead of the broken-
  // image glyph. Successful loads clear the marker.
  artImg.addEventListener("error", () => host.setAttribute("data-art-error", ""));
  artImg.addEventListener("load",  () => host.removeAttribute("data-art-error"));
  art.appendChild(artImg);

  const headline   = el("div", "dm-card-headline");
  const name       = el("div", "dm-card-name");
  const sub        = el("div", "dm-card-sub");
  const elementTxt = el("span", "dm-card-element");
  const archetype  = el("span", "dm-card-archetype");
  sub.appendChild(elementTxt);
  sub.appendChild(archetype);
  headline.appendChild(name);
  headline.appendChild(sub);

  // Stat row — fixed 4 cells (atk/def/hp/spd), always present. Each cell
  // carries data-stat so the CSS can color-code labels per stat type.
  const stats = el("div", "dm-card-stats");
  const statRefs = {};
  for (const [key, label] of [["atk","ATK"], ["def","DEF"], ["hp","HP"], ["spd","SPD"]]) {
    const cell = el("div", "dm-card-stat");
    cell.setAttribute("data-stat", key);
    const lab  = el("span", "dm-card-stat-label", label);
    const val  = el("span", "dm-card-stat-value", "—");
    cell.appendChild(lab);
    cell.appendChild(val);
    stats.appendChild(cell);
    statRefs[key] = val;
  }

  // Variable-length lists — managed by syncSlots on update.
  const abilities = el("div", "dm-card-abilities");
  const triggers  = el("ul",  "dm-card-triggers");
  const moves     = el("ul",  "dm-card-moves");
  abilities.appendChild(triggers);
  abilities.appendChild(moves);

  const flavor = el("div", "dm-card-flavor");

  // Bottom info panel — stats + abilities + flavor stack on a single
  // overlay strip so they can share a frosted backdrop and don't drift
  // up into the art when sub-content is short.
  const info = el("div", "dm-card-info");
  info.appendChild(stats);
  info.appendChild(abilities);
  info.appendChild(flavor);

  const front = el("div", "dm-card-front");
  front.appendChild(art);
  front.appendChild(headline);
  front.appendChild(info);

  const frame = el("div", "dm-card-frame");
  frame.appendChild(back);
  frame.appendChild(front);

  host.appendChild(frame);

  return {
    frame, front, back,
    artImg, name, elementTxt, archetype,
    statRefs, triggers, moves, flavor,
  };
}

// ---------------------------------------------------------------------------
// <dm-card> element
// ---------------------------------------------------------------------------

class DMCard extends HTMLElement {
  static get observedAttributes() {
    return ["card-id", "size", "face"];
  }

  constructor() {
    super();
    this._refs = null;          // populated on connectedCallback
    this._currentId = null;     // last id we pulled from the store
    this._loadToken = 0;        // monotonic — drops stale fetch results
  }

  connectedCallback() {
    if (this._refs) return;     // already set up; reconnects re-use the tree
    this._refs = buildShell(this);

    // Defaults — the component is renderable even before card-id arrives.
    if (!this.hasAttribute("size")) this.setAttribute("size", "hero");
    if (!this.hasAttribute("face")) this.setAttribute("face", "front");

    if (this.hasAttribute("card-id")) {
      this._loadCard(this.getAttribute("card-id"));
    }
  }

  attributeChangedCallback(name, oldVal, newVal) {
    if (oldVal === newVal) return;
    if (name === "card-id") {
      // Defer load until we're connected — connectedCallback re-reads.
      if (this._refs) this._loadCard(newVal);
    }
    // size / face changes are pure CSS — no JS work needed. The
    // attribute selector inside dm-card.css drives the layout shift.
  }

  /** Internal: fetch + apply card payload. Idempotent on identical ids. */
  _loadCard(card_id) {
    if (!card_id) {
      this._applyEmpty();
      return;
    }
    if (card_id === this._currentId) return;   // already showing this card
    this._currentId = card_id;
    this._applyEmpty();                        // immediately blank stale data
    const token = ++this._loadToken;
    cardStore.get(card_id).then(payload => {
      if (token !== this._loadToken) return;   // stale; a newer load won
      this._applyPayload(card_id, payload);
    }).catch(err => {
      if (token !== this._loadToken) return;
      this._applyError(card_id, err);
    });
  }

  _applyEmpty() {
    const r = this._refs;
    if (!r) return;
    r.artImg.removeAttribute("src");
    r.name.textContent = "";
    r.elementTxt.textContent = "";
    r.archetype.textContent = "";
    for (const v of Object.values(r.statRefs)) v.textContent = "—";
    syncSlots(r.triggers, 0, () => el("li", "dm-card-trigger"));
    syncSlots(r.moves,    0, () => el("li", "dm-card-move"));
    r.flavor.textContent = "";
    this.removeAttribute("data-rarity");
    this.removeAttribute("data-loaded");
  }

  _applyPayload(card_id, p) {
    const r = this._refs;
    if (!r) return;

    // The art-pack route is keyed off card_id, not the catalog id —
    // they're identical for v1_alpha but kept separate for forward-compat.
    r.artImg.src = `/art/${encodeURIComponent(card_id)}`;
    r.artImg.alt = p.name || card_id;

    r.name.textContent       = (p.name || card_id).toUpperCase();
    r.elementTxt.textContent = (p.element || "").toUpperCase();
    r.archetype.textContent  = (p.canon || p.archetype || "").toUpperCase();

    r.statRefs.atk.textContent = String(p.atk ?? "—");
    r.statRefs.def.textContent = String(p.def ?? "—");
    r.statRefs.hp.textContent  = String(p.hp  ?? "—");
    r.statRefs.spd.textContent = String(p.spd ?? "—");

    // Triggers — passive abilities. Cards usually have 0-2.
    const triggerArr = Array.isArray(p.triggers) ? p.triggers : [];
    syncSlots(r.triggers, triggerArr.length, () => {
      const li  = el("li",   "dm-card-trigger");
      const tag = el("span", "dm-card-trigger-when");
      const op  = el("span", "dm-card-trigger-op");
      li.appendChild(tag);
      li.appendChild(op);
      return li;
    });
    triggerArr.forEach((t, i) => {
      const li = r.triggers.children[i];
      li.children[0].textContent = (t.when || "").replace(/_/g, " ");
      const opTxt = [t.op, t.target, t.value].filter(x => x !== undefined && x !== null && x !== "").join(" ");
      li.children[1].textContent = opTxt.replace(/_/g, " ").toLowerCase();
    });

    // Moves — active abilities.
    const moveArr = Array.isArray(p.moves) ? p.moves : [];
    syncSlots(r.moves, moveArr.length, () => {
      const li = el("li", "dm-card-move");
      const nm = el("span", "dm-card-move-name");
      const wn = el("span", "dm-card-move-when");
      li.appendChild(nm);
      li.appendChild(wn);
      return li;
    });
    moveArr.forEach((m, i) => {
      const li = r.moves.children[i];
      li.children[0].textContent = m.name || "";
      li.children[1].textContent = (m.when || "").replace(/_/g, " ").toLowerCase();
    });

    r.flavor.textContent = p.flavor || "";

    if (p.rarity) this.setAttribute("data-rarity", p.rarity);
    this.setAttribute("data-loaded", "");
  }

  _applyError(card_id, _err) {
    // Soft-fail: blank the layout but keep the frame visible. The art
    // route also soft-fails, so the user sees an empty card instead of
    // a crash.
    this._applyEmpty();
    if (this._refs) this._refs.name.textContent = (card_id || "").toUpperCase();
  }
}

customElements.define("dm-card", DMCard);
