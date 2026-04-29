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
  // Marks the modal's own card so its click handler skips reopening
  // — every OTHER detail-sized card (collection / shop right panels)
  // should still be clickable and open this same modal.
  card.setAttribute("data-in-modal", "");
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

  // Unified ability list. Each row pairs ONE move (flavor name) with
  // ONE trigger (the mechanic) by matching `when` — so "Wailing-River
  // Spring" + "ON_BATTLE_START / APPLY_POISON" render as a single
  // entry, not two disconnected lines. Triggers without a flavor move
  // and moves without a mechanic still render, just with one half
  // missing. See _buildAbilities below for the pairing logic.
  const abilities = el("ul", "dm-card-abilities");

  // Rule line — only present on legendaries with a rule_change tag.
  // Holds the human-readable mutation description (e.g. "every heal
  // trickles +1 to all allies") fetched from the engine registry via
  // /api/card. Hidden until populated.
  const rule      = el("div", "dm-card-rule");
  const ruleTag   = el("span", "dm-card-rule-tag");
  const ruleText  = el("span", "dm-card-rule-text");
  rule.appendChild(ruleTag);
  rule.appendChild(ruleText);

  const flavor = el("div", "dm-card-flavor");

  // Bottom info panel — stats + abilities + rule (legendary only) +
  // flavor. Visible only at size="detail" (the modal). Other sizes
  // collapse to "art + name" per the Snap-style hierarchy.
  const info = el("div", "dm-card-info");
  info.appendChild(stats);
  info.appendChild(abilities);
  info.appendChild(rule);
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
    statRefs, abilities, rule, ruleTag, ruleText, flavor,
  };
}

// ---------------------------------------------------------------------------
// Ability rendering — translates the engine's enum-coded triggers into
// readable English and pairs them with their flavor names.
// ---------------------------------------------------------------------------

/** Humanize a TriggerWhen tag for the small label on each ability row. */
const _WHEN_LABEL = {
  ON_BATTLE_START:        "battle start",
  ON_ROUND_START:         "round start",
  ON_ATTACK:              "on attack",
  ON_TAKE_DAMAGE:         "when hit",
  ON_DEATH:               "on death",
  ON_ALLY_DEATH:          "on ally death",
  ON_TURN_END:            "turn end",
  ON_KILL:                "on kill",
  ON_LOW_HP:              "low HP",
  ON_OPENING_ATTACK:      "first attack",
  ON_HEAL_RECEIVED:       "when healed",
  ON_DAMAGE_TAKEN:        "when damaged",
  ON_EXTRA_ACTION_GRANTED:"extra action",
};
function whenLabel(when) {
  if (!when) return "";
  if (_WHEN_LABEL[when]) return _WHEN_LABEL[when];
  if (when.startsWith("RULE_CHANGE_")) return "passive (rule)";
  // Fallback: lowercase + spaces.
  return when.replace(/^ON_/, "on ").replace(/_/g, " ").toLowerCase();
}

/** Humanize a TargetFilter into a noun phrase. */
const _TARGET_LABEL = {
  SELF:              "self",
  ALL_ALLIES:        "all allies",
  ALL_ENEMIES:       "all enemies",
  LOWEST_HP_ENEMY:   "lowest-HP enemy",
  HIGHEST_HP_ENEMY:  "highest-HP enemy",
  RANDOM_ENEMY:      "random enemy",
  RANDOM_ALLY:       "random ally",
};
function targetLabel(t) {
  return _TARGET_LABEL[t] || (t || "").toLowerCase().replace(/_/g, " ");
}

/** Translate one (op, target, value) tuple into one English sentence.
 *  Verb-leading where natural so the row reads as an instruction. */
function effectSentence(trig) {
  if (!trig) return "";
  const op = trig.op;
  const tgt = targetLabel(trig.target);
  const v = trig.value;
  switch (op) {
    case "DAMAGE":             return `deal ${v} damage to ${tgt}`;
    case "HEAL":               return `heal ${tgt} for ${v}`;
    case "BUFF_ATK":           return `+${v} ATK on ${tgt}`;
    case "DEBUFF_ATK":         return `−${v} ATK on ${tgt}`;
    case "BUFF_DEF":           return `+${v} DEF on ${tgt}`;
    case "DEBUFF_DEF":         return `−${v} DEF on ${tgt}`;
    case "BUFF_SPD":           return `+${v} SPD on ${tgt}`;
    case "ADD_SHIELD":         return `shield ${tgt} for ${v}`;
    case "APPLY_BURN":         return `burn ${tgt} for ${v} round${v === 1 ? "" : "s"}`;
    case "APPLY_POISON":       return `poison ${tgt} for ${v} round${v === 1 ? "" : "s"}`;
    case "APPLY_STUN":         return `stun ${tgt} for ${v} round${v === 1 ? "" : "s"}`;
    case "APPLY_SILENCE":      return `silence ${tgt} for ${v} round${v === 1 ? "" : "s"}`;
    case "APPLY_TAUNT":        return `taunt ${tgt} for ${v} round${v === 1 ? "" : "s"}`;
    case "LIFESTEAL":          return `lifesteal — heal half of damage dealt`;
    case "APPLY_BURN_STACK":   return `+${v} burn stack on ${tgt}`;
    case "THORNS":             return `thorns ${v} on self`;
    case "GRANT_EXTRA_ACTION": return `grant extra action to ${tgt}`;
    case "SACRIFICE_SELF":     return `sacrifice self`;
  }
  // Unknown op — fall back to the raw enum so it's still legible.
  return `${(op || "").toLowerCase()} ${tgt} ${v}`.trim();
}

/** Pair triggers with moves by `when`. Returns one row per ability:
 *    {name, when, trigger}
 *  - moves with a matching trigger absorb that trigger's mechanic
 *  - extra triggers (no flavor name) get rendered with name=null
 *  - extra moves (no matching trigger) get a row with trigger=null
 *    (just the flavor name + when tag, no body) */
function buildAbilities(triggerArr, moveArr) {
  const rows = [];
  const pool = (triggerArr || []).slice();
  for (const m of (moveArr || [])) {
    const idx = pool.findIndex(t => t.when === m.when);
    const trig = idx >= 0 ? pool.splice(idx, 1)[0] : null;
    rows.push({ name: m.name || null, when: m.when, trigger: trig });
  }
  for (const t of pool) {
    rows.push({ name: null, when: t.when, trigger: t });
  }
  return rows;
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
    // (.shop-tile, .coll-tile) own the click for selection. Cards that
    // ARE the modal's own card (data-in-modal) also skip, so clicking
    // inside the modal doesn't recurse. Every other size — including
    // detail-sized side-panel cards in collection/shop — opens the
    // modal for fullscreen inspection.
    this.addEventListener("click", () => {
      if (this.getAttribute("size") === "tile") return;
      if (this.hasAttribute("data-in-modal")) return;
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
    syncSlots(r.abilities, 0, () => el("li", "dm-card-ability"));
    r.rule.setAttribute("hidden", "");
    r.ruleTag.textContent = "";
    r.ruleText.textContent = "";
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

    // Abilities — pair each move with the trigger of the same `when`.
    // Each row reads as: flavor name (top) + when tag (top right) +
    // mechanic in plain English (bottom). Cards usually have 1–3.
    const triggerArr = Array.isArray(p.triggers) ? p.triggers : [];
    const moveArr    = Array.isArray(p.moves)    ? p.moves    : [];
    const abilityRows = buildAbilities(triggerArr, moveArr);

    syncSlots(r.abilities, abilityRows.length, () => {
      const li     = el("li",   "dm-card-ability");
      const head   = el("div",  "dm-card-ability-head");
      const nm     = el("span", "dm-card-ability-name");
      const tag    = el("span", "dm-card-ability-when");
      const body   = el("div",  "dm-card-ability-body");
      head.appendChild(nm);
      head.appendChild(tag);
      li.appendChild(head);
      li.appendChild(body);
      return li;
    });
    abilityRows.forEach((a, i) => {
      const li     = r.abilities.children[i];
      const [head, body] = li.children;
      const [nameEl, tagEl] = head.children;
      // No flavor name → fall back to the when label as the headline
      // so the row still has a top line.
      if (a.name) {
        nameEl.textContent = a.name;
        tagEl.textContent  = whenLabel(a.when);
        tagEl.removeAttribute("hidden");
      } else {
        nameEl.textContent = whenLabel(a.when).toUpperCase();
        tagEl.textContent  = "";
        tagEl.setAttribute("hidden", "");
      }
      body.textContent = effectSentence(a.trigger);
      // Hide the body line entirely when there's no mechanic to show
      // (move without a matching trigger) so the row is just one line.
      if (body.textContent) body.removeAttribute("hidden");
      else                  body.setAttribute("hidden", "");
    });

    // Rule-change description (legendary mutations only). The opaque
    // ID (e.g. "L3") is meaningless to a player on its own — the
    // routes layer joins it with the engine description registry.
    if (p.rule_change && p.rule_change_text) {
      r.ruleTag.textContent  = `RULE ${p.rule_change}`;
      r.ruleText.textContent = p.rule_change_text;
      r.rule.removeAttribute("hidden");
    } else {
      r.ruleTag.textContent  = "";
      r.ruleText.textContent = "";
      r.rule.setAttribute("hidden", "");
    }

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
