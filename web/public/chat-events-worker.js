/* 一个同源 Worker 管理所有标签页的 SSE，连接与重连状态按会话独立。 */
const streams = new Map();
const LIMIT = 128;
function send(port, packet) { try { port.postMessage(packet); } catch { /* 离开的页面由心跳清理。 */ } }
function broadcast(stream, packet) { stream.ports.forEach((_, port) => send(port, { ...packet, key: stream.key })); }
function connect(stream) {
  if (!stream.ports.size) return;
  const suffix = stream.cursor === undefined ? "" : `${stream.url.includes("?") ? "&" : "?"}after=${stream.cursor}`;
  const source = stream.source = new EventSource(stream.url + suffix);
  source.onopen = () => { stream.attempts = 0; stream.open = true; broadcast(stream, { type: "open" }); };
  stream.types.forEach(type => source.addEventListener(type, event => {
    if (source !== stream.source || !stream.ports.size) return;
    stream.cursor = Math.max(stream.cursor ?? 0, Number(event.lastEventId) || 0);
    const packet = { type: "event", event: type, id: event.lastEventId, data: event.data };
    stream.history.push(packet); if (stream.history.length > LIMIT) stream.history.shift(); broadcast(stream, packet);
  }));
  source.onerror = () => {
    if (source !== stream.source || !stream.ports.size) return;
    source.close(); stream.open = false; broadcast(stream, { type: "error" });
    const delay = Math.min(30_000, 1000 * 2 ** Math.min(stream.attempts++, 5)) * (0.8 + Math.random() * 0.4);
    stream.timer = setTimeout(() => connect(stream), delay);
  };
}
function remove(stream, port) {
  stream.ports.delete(port);
  if (!stream.ports.size) { stream.source?.close(); clearTimeout(stream.timer); streams.delete(stream.key); }
}
self.onconnect = event => {
  const port = event.ports[0];
  port.onmessage = ({ data }) => {
    const { type, key, after, types } = data;
    const match = /^(chat|notifications):([a-f0-9]{32})$/.exec(key || "");
    if (!match) return;
    let stream = streams.get(key);
    if (type === "unsubscribe") { if (stream) remove(stream, port); return; }
    if (!stream) {
      const url = match[1] === "chat" ? `/api/chats/${match[2]}/events` : `/api/notifications/events?expected_user_id=${match[2]}`;
      stream = { key, url, cursor: after, types: Array.isArray(types) ? types : [], ports: new Map(),
        history: [], attempts: 0, open: false, source: null, timer: null };
      streams.set(key, stream);
    }
    const joined = !stream.ports.has(port);
    stream.ports.set(port, Date.now());
    if (!stream.source) connect(stream);
    if (joined && stream.open) {
      // 新标签页校准快照，同时补齐握手前仍在缓冲区内的事件。
      send(port, { type: "open", key });
      if (typeof after === "number") stream.history.filter(item => Number(item.id) > after).forEach(item => send(port, { ...item, key }));
    }
    if (type === "online" && !stream.open) { clearTimeout(stream.timer); stream.source?.close(); stream.attempts = 0; connect(stream); }
  };
  port.start();
};
setInterval(() => {
  for (const stream of streams.values()) for (const [port, stamp] of stream.ports) {
    if (Date.now() - stamp > 90_000) remove(stream, port);
  }
}, 30_000);
