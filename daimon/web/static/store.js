// DAIMON client-side stores — small pub/sub primitives used by components
// and screens. Two stores ship today:
//
//   cardStore — memoized lookup of catalog payloads by card_id. Backed by
//               GET /api/card/{card_id}. Used by <dm-card> so every card
//               element on screen shares one fetch per unique id.
//
//   liveStore — singleton state derived from /ws push events. Right now
//               that's just `balance`, but it's the right home for any
//               server-pushed state shared across screens.
//
// Both expose the same shape:
//   .get(...)         — synchronous read (cardStore returns Promise on miss)
//   .subscribe(cb)    — register a listener; returns unsubscribe()
//
// No virtual DOM, no diff layer. Subscribers are expected to mutate text
// nodes / attributes / CSS variables in-place. That discipline is the
// whole point — re-rendering the world on every change is the bug we're
// here to fix.

// ---------------------------------------------------------------------------
// cardStore
// ---------------------------------------------------------------------------

const _cardCache = new Map();   // card_id -> Promise<payload>
const _cardSubs  = new Set();   // (card_id, payload) => void

function _emitCard(card_id, payload) {
  for (const cb of _cardSubs) {
    try { cb(card_id, payload); } catch (e) { console.error("cardStore sub", e); }
  }
}

export const cardStore = {
  /** Fetch (or return cached) the full catalog payload for a card_id.
   *  Returns a Promise<payload>. Failures reject; callers can soft-fail. */
  get(card_id) {
    if (!card_id) return Promise.reject(new Error("cardStore.get: empty id"));
    const hit = _cardCache.get(card_id);
    if (hit) return hit;
    const p = fetch(`/api/card/${encodeURIComponent(card_id)}`)
      .then(r => {
        if (!r.ok) throw new Error(`/api/card/${card_id} → ${r.status}`);
        return r.json();
      })
      .then(envelope => {
        const payload = envelope.payload || envelope;
        _emitCard(card_id, payload);
        return payload;
      })
      .catch(err => {
        // Eject the failed promise so a subsequent retry can try again.
        if (_cardCache.get(card_id) === p) _cardCache.delete(card_id);
        throw err;
      });
    _cardCache.set(card_id, p);
    return p;
  },

  /** Synchronous accessor — returns undefined on miss. Safe to call from
   *  render paths that already pre-loaded via get(). */
  peek(card_id) {
    const hit = _cardCache.get(card_id);
    if (!hit || typeof hit.then !== "function") return undefined;
    // The Map holds the promise; consumers wanting sync access should
    // have awaited it once already.
    return undefined;
  },

  /** Subscribe to "card payload landed" events. Returns unsubscribe. */
  subscribe(cb) {
    _cardSubs.add(cb);
    return () => _cardSubs.delete(cb);
  },

  /** Clear the cache — only useful for tests / dev hot-reload. */
  _reset() {
    _cardCache.clear();
    _cardSubs.clear();
  },
};

// ---------------------------------------------------------------------------
// liveStore
// ---------------------------------------------------------------------------

const _live = {
  balance: null,
  // Future fields land here as the daemon learns more push kinds.
};
const _liveSubs = new Set();    // (state) => void

function _emitLive() {
  for (const cb of _liveSubs) {
    try { cb(_live); } catch (e) { console.error("liveStore sub", e); }
  }
}

export const liveStore = {
  /** Current snapshot. Mutating the returned object is undefined behaviour. */
  get() { return _live; },

  /** Subscribe; returns unsubscribe. The callback fires once immediately
   *  with the current state so subscribers don't have to read .get()
   *  separately right after subscribing. */
  subscribe(cb) {
    _liveSubs.add(cb);
    try { cb(_live); } catch (e) { console.error("liveStore initial", e); }
    return () => _liveSubs.delete(cb);
  },

  /** Internal — called from the WS bootstrap. Not part of the public API
   *  but exported to keep the wiring in one file for readability. */
  _ingest(payload) {
    let changed = false;
    if (typeof payload?.balance === "number" && payload.balance !== _live.balance) {
      _live.balance = payload.balance;
      changed = true;
    }
    if (changed) _emitLive();
  },
};

// ---------------------------------------------------------------------------
// WebSocket bootstrap — the daemon pushes balance/state frames here and
// liveStore fans them out to subscribers.
// ---------------------------------------------------------------------------

let _socket = null;
let _reconnectMs = 250;

export function startLiveSocket() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const url = `${proto}//${location.host}/ws`;
  try {
    _socket = new WebSocket(url);
  } catch (err) {
    console.warn("ws construct failed", err);
    return;
  }
  _socket.onopen    = () => { _reconnectMs = 250; };
  _socket.onmessage = (e) => {
    let frame;
    try { frame = JSON.parse(e.data); }
    catch (err) { console.warn("bad ws frame", err); return; }
    liveStore._ingest(frame);
  };
  _socket.onclose = () => {
    _socket = null;
    setTimeout(startLiveSocket, _reconnectMs);
    _reconnectMs = Math.min(_reconnectMs * 2, 5000);
  };
  _socket.onerror = (e) => { console.warn("ws error", e); };
}
