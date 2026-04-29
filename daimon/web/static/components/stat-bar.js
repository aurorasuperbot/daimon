// <stat-bar label="ATK" value="8" max="20"> — labelled mini progress bar.

class StatBar extends HTMLElement {
  static get observedAttributes() { return ["label", "value", "max", "color"]; }
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
  }
  connectedCallback() { this._render(); }
  attributeChangedCallback() { this._render(); }

  _render() {
    const label = this.getAttribute("label") || "";
    const value = parseInt(this.getAttribute("value") || "0", 10);
    const max = Math.max(1, parseInt(this.getAttribute("max") || "20", 10));
    const color = this.getAttribute("color") || "#f0c458";
    const fraction = Math.max(0, Math.min(1, value / max));
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; font-family: "Inter", sans-serif; font-size: 0.75rem; }
        .row { display: grid; grid-template-columns: 3rem 1fr 2rem; align-items: center; gap: 0.5rem; }
        .label { color: #9b9b9b; letter-spacing: 0.1em; text-transform: uppercase; font-weight: 700; }
        .track { height: 6px; background: #181e30; border-radius: 999px; overflow: hidden; }
        .fill { height: 100%; background: ${color}; transition: width 0.3s ease; }
        .val { text-align: right; color: #fff; font-weight: 700; font-variant-numeric: tabular-nums; }
      </style>
      <div class="row">
        <div class="label">${label}</div>
        <div class="track"><div class="fill" style="width:${(fraction * 100).toFixed(1)}%"></div></div>
        <div class="val">${value}</div>
      </div>
    `;
  }
}

customElements.define("stat-bar", StatBar);
