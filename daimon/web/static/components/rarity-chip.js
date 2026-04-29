// <rarity-chip rarity="legendary"> — small colored pill for card rarity.

const COLORS = {
  common:    { fg: "#cfd2d8", bg: "#2a2f44" },
  uncommon:  { fg: "#9bd9a3", bg: "#243a2c" },
  rare:      { fg: "#7ab8ff", bg: "#1f2c4a" },
  epic:      { fg: "#cf9bff", bg: "#322347" },
  legendary: { fg: "#ffdc78", bg: "#3d2c0f" },
};

class RarityChip extends HTMLElement {
  static get observedAttributes() { return ["rarity"]; }

  constructor() {
    super();
    this.attachShadow({ mode: "open" });
  }

  connectedCallback() { this._render(); }
  attributeChangedCallback() { this._render(); }

  _render() {
    const r = (this.getAttribute("rarity") || "common").toLowerCase();
    const c = COLORS[r] || COLORS.common;
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: inline-block; }
        .chip {
          display: inline-block;
          padding: 0.15rem 0.6rem;
          border-radius: 999px;
          background: ${c.bg};
          color: ${c.fg};
          font-family: "Cinzel", serif;
          font-size: 0.7rem;
          font-weight: 700;
          letter-spacing: 0.2em;
          text-transform: uppercase;
          border: 1px solid ${c.fg};
        }
      </style>
      <span class="chip">${r}</span>
    `;
  }
}

customElements.define("rarity-chip", RarityChip);
