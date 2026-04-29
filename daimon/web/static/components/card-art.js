// <card-art card-id="..."> — fetches /art/{card_id} and renders the PNG.
// Falls back to a placeholder block when the art is missing or unreachable.

const TEMPLATE = `
  <style>
    :host {
      display: block;
      width: 100%;
      height: 100%;
      position: relative;
      overflow: hidden;
    }
    img {
      width: 100%;
      height: 100%;
      object-fit: cover;
      display: block;
    }
    .placeholder {
      width: 100%;
      height: 100%;
      display: flex;
      align-items: center;
      justify-content: center;
      font-family: "Cinzel", serif;
      font-size: 1.2rem;
      letter-spacing: 0.3em;
      color: #b48220;
      background: #181e30;
    }
  </style>
  <slot></slot>
`;

class CardArt extends HTMLElement {
  static get observedAttributes() { return ["card-id"]; }

  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this.shadowRoot.innerHTML = TEMPLATE;
  }

  connectedCallback() { this._render(); }
  attributeChangedCallback() { this._render(); }

  _render() {
    const cardId = this.getAttribute("card-id");
    const root = this.shadowRoot;
    // Wipe previous content (keep style + slot).
    [...root.querySelectorAll("img,.placeholder")].forEach(n => n.remove());

    if (!cardId) {
      const ph = document.createElement("div");
      ph.className = "placeholder";
      ph.textContent = "DAIMON";
      root.appendChild(ph);
      return;
    }

    const img = document.createElement("img");
    img.src = `/art/${encodeURIComponent(cardId)}`;
    img.alt = cardId;
    img.onerror = () => {
      img.remove();
      const ph = document.createElement("div");
      ph.className = "placeholder";
      ph.textContent = cardId.toUpperCase();
      root.appendChild(ph);
    };
    root.appendChild(img);
  }
}

customElements.define("card-art", CardArt);
