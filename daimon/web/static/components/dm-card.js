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
// Card-detail modal — singleton shared across the app
// ---------------------------------------------------------------------------
//
// Marvel-Snap-style behaviour: clicking any non-tile <dm-card> opens a
// fullscreen overlay with the same card rendered at size="detail" — the
// only size variant that shows stats, abilities, and flavor. Click the
// backdrop or press Escape to close. A single modal element is reused
// across the lifetime of the app; subsequent opens just swap card-id.

let _modal = null;

function _ensureModal() {
  if (_modal) return _modal;

  const overlay = document.createElement("div");
  overlay.className = "dm-card-modal-overlay";
  overlay.setAttribute("hidden", "");

  const stage = document.createElement("div");
  stage.className = "dm-card-modal-stage";

  const card = document.createElement("dm-card");
  card.setAttribute("size", "detail");
  card.setAttribute("face", "front");
  stage.appendChild(card);
  overlay.appendChild(stage);

  document.body.appendChild(overlay);

  const close = () => {
    overlay.setAttribute("hidden", "");
    document.removeEventListener("keydown", onKey);
  };
  const onKey = (e) => { if (e.key === "Escape") close(); };
  overlay.addEventListener("click", (e) => {
    // Backdrop click closes; clicks inside .dm-card-modal-stage don't.
    if (e.target === overlay) close();
  });

  _modal = { overlay, stage, card, close, onKey };
  return _modal;
}

export function openCardModal(card_id) {
  if (!card_id) return;
  const m = _ensureModal();
  m.card.setAttribute("card-id", card_id);
  m.overlay.removeAttribute("hidden");
  document.addEventListener("keydown", m.onKey);
}

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

  // Holo overlay — element-themed tiled motif painted over the art at
  // tier >= uncommon. Empty by default; CSS fills it in based on
  // [data-rarity] + [data-element] on the host. Lives in the art layer
  // so it sits below the headline/info text.
  const holo = el("div", "dm-card-holo");
  art.appendChild(holo);

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

  // The clip wrapper holds the 3D-rotating frame. Silhouette-break
  // (legendary's clip-path) lives on this wrapper instead of on the
  // frame itself — clip-path on a 3D-transformed element with
  // preserve-3d forces flattening and breaks backface-visibility.
  // Around-card effects (krackle particles) live on :scope::after,
  // OUTSIDE the wrapper, so they bleed past the silhouette cleanly.
  const clip = el("div", "dm-card-clip");
  clip.appendChild(frame);

  host.appendChild(clip);

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

    // Click → open detail modal. Tiles skip this — their parent wrappers
    // (shop tile, collection tile) own the click for selection. The
    // modal itself uses size="detail" cards which short-circuit the
    // handler so clicking inside the modal doesn't recurse.
    this.addEventListener("click", () => {
      const sz = this.getAttribute("size");
      if (sz === "tile" || sz === "detail") return;
      if (this._currentId) openCardModal(this._currentId);
    });

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
    this.removeAttribute("data-element");
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
    if (p.element) this.setAttribute("data-element", p.element);
    this.setAttribute("data-loaded", "");

    // Parallax: rare/epic/legendary lean toward the cursor on the front
    // face. Single pointer listener per card; the CSS reads --parallax-x
    // and --parallax-y to drive 3D rotations on each layer.
    this._maybeAttachParallax();
  }

  /** Attach (or release) a pointer-driven parallax handler on the card.
   *  Only tiers >= "rare" get parallax; common/uncommon stay flat. The
   *  handler writes --parallax-x / --parallax-y (range -1..1) to the host
   *  on pointermove, which the CSS consumes in transforms. Idle state
   *  resets to 0 so cards don't lock at a tilted angle. */
  _maybeAttachParallax() {
    const PARALLAX_TIERS = new Set(["rare", "epic", "legendary"]);
    const wanted = PARALLAX_TIERS.has(this.getAttribute("data-rarity") || "");
    if (wanted && !this._parallaxHandlers) {
      const onMove = (ev) => {
        const r = this.getBoundingClientRect();
        if (!r.width || !r.height) return;
        const x = (ev.clientX - r.left) / r.width  - 0.5;
        const y = (ev.clientY - r.top)  / r.height - 0.5;
        this.style.setProperty("--parallax-x", x.toFixed(3));
        this.style.setProperty("--parallax-y", y.toFixed(3));
      };
      const onLeave = () => {
        this.style.setProperty("--parallax-x", "0");
        this.style.setProperty("--parallax-y", "0");
      };
      this.addEventListener("pointermove", onMove);
      this.addEventListener("pointerleave", onLeave);
      this._parallaxHandlers = { onMove, onLeave };
    } else if (!wanted && this._parallaxHandlers) {
      const { onMove, onLeave } = this._parallaxHandlers;
      this.removeEventListener("pointermove", onMove);
      this.removeEventListener("pointerleave", onLeave);
      this._parallaxHandlers = null;
      this.style.removeProperty("--parallax-x");
      this.style.removeProperty("--parallax-y");
    }
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
