// WebSocket bootstrap — opens /ws and rebroadcasts payloads as
// document-level CustomEvents so screens can subscribe without
// holding a socket reference each.

let socket = null;
let reconnectDelayMs = 250;

function dispatch(payload) {
  document.dispatchEvent(new CustomEvent("daimon:live", { detail: payload }));
  if (payload?.kind && payload.balance !== undefined) {
    document.dispatchEvent(new CustomEvent("daimon:balance", {
      detail: { balance: payload.balance, source: payload.kind },
    }));
  }
}

export function startLiveSocket() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const url = `${proto}//${location.host}/ws`;
  try {
    socket = new WebSocket(url);
  } catch (err) {
    console.warn("ws construct failed", err);
    return;
  }
  socket.onopen = () => { reconnectDelayMs = 250; };
  socket.onmessage = (e) => {
    try { dispatch(JSON.parse(e.data)); }
    catch (err) { console.warn("bad ws frame", err); }
  };
  socket.onclose = () => {
    socket = null;
    setTimeout(startLiveSocket, reconnectDelayMs);
    reconnectDelayMs = Math.min(reconnectDelayMs * 2, 5000);
  };
  socket.onerror = (e) => { console.warn("ws error", e); };
}
