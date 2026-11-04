const TYPES = ["message.created", "message.delta", "message.completed", "message.deleted", "bot.typing",
  "run.status", "task.status", "approval.created", "approval.resolved", "question.created",
  "files.updated", "usage.updated", "chat.always_allow", "chat.members", "notification.created", "terminal.updated"];
type Packet = { type: "open" | "error" | "event"; key: string; event?: string; data?: string; id?: string };
type Bridge = { stop(): void; online(): void };
type Entry = { key: string; url: string; cursor?: number; state: number;
  clients: Set<ChatEventSource>; bridge?: Bridge; cleanup?: () => void };
const sources = new Map<string, Entry>();

/** 每个订阅者独立持有监听器，连接由同页面和 SharedWorker 共同复用。 */
export class ChatEventSource extends EventTarget {
  readyState = 0;
  onopen: ((event: Event) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  private released = false;
  constructor(private entry: Entry) { super(); }
  receive(packet: Packet) {
    if (this.released) return;
    if (packet.type === "event") {
      this.dispatchEvent(new MessageEvent(packet.event!, { data: packet.data, lastEventId: packet.id }));
      return;
    }
    this.readyState = packet.type === "open" ? 1 : 0;
    const event = new Event(packet.type);
    this.dispatchEvent(event);
    (packet.type === "open" ? this.onopen : this.onerror)?.(event);
  }
  close() {
    if (this.released) return;
    this.released = true;
    this.readyState = 2;
    this.entry.clients.delete(this);
    if (!this.entry.clients.size) {
      this.entry.bridge?.stop(); this.entry.cleanup?.(); sources.delete(this.entry.key);
    }
    this.onopen = this.onerror = null;
  }
}

function receive(entry: Entry, packet: Packet) {
  if (packet.key !== entry.key) return;
  if (packet.type === "event") entry.cursor = Math.max(entry.cursor ?? 0, Number(packet.id) || 0);
  else entry.state = packet.type === "open" ? 1 : 0;
  entry.clients.forEach(client => client.receive(packet));
}

function localBridge(entry: Entry): Bridge {
  let source: EventSource | null = null, timer: ReturnType<typeof setTimeout> | null = null;
  let stopped = false, attempts = 0;
  const connect = () => {
    if (stopped) return;
    const suffix = entry.cursor === undefined ? "" : `${entry.url.includes("?") ? "&" : "?"}after=${entry.cursor}`;
    const active = source = new EventSource(entry.url + suffix);
    active.onopen = () => { attempts = 0; receive(entry, { type: "open", key: entry.key }); };
    TYPES.forEach(type => active.addEventListener(type, event => {
      if (stopped || source !== active) return;
      const message = event as MessageEvent;
      receive(entry, { type: "event", key: entry.key, event: type, data: message.data, id: message.lastEventId });
    }));
    active.onerror = () => {
      if (stopped || source !== active) return;
      active.close(); receive(entry, { type: "error", key: entry.key });
      const delay = Math.min(30_000, 1000 * 2 ** Math.min(attempts++, 5)) * (0.8 + Math.random() * 0.4);
      timer = setTimeout(connect, delay);
    };
  };
  connect();
  return {
    stop() { stopped = true; source?.close(); if (timer) clearTimeout(timer); },
    online() { if (timer) clearTimeout(timer); source?.close(); attempts = 0; connect(); },
  };
}

function bridge(entry: Entry): Bridge {
  if (typeof SharedWorker === "undefined") return localBridge(entry);
  let worker: SharedWorker;
  try { worker = new SharedWorker("/chat-events-worker.js", { name: "botly-events-v1" }); }
  catch { return localBridge(entry); }
  let fallback: Bridge | undefined, stopped = false;
  const subscribe = (type: string) => worker.port.postMessage({ type, key: entry.key, url: entry.url,
    after: entry.cursor, types: TYPES });
  worker.port.onmessage = event => { if (!stopped) receive(entry, event.data); };
  worker.onerror = () => {
    if (stopped || fallback) return;
    worker.port.close(); clearInterval(heartbeat); fallback = localBridge(entry);
  };
  worker.port.start(); subscribe("subscribe");
  const heartbeat = setInterval(() => subscribe("ping"), 30_000);
  return {
    stop() { stopped = true; clearInterval(heartbeat); fallback?.stop();
      if (!fallback) subscribe("unsubscribe"); worker.port.close(); },
    online() { if (fallback) fallback.online(); else subscribe("online"); },
  };
}

function acquire(key: string, url: string, after?: number) {
  let entry = sources.get(key);
  if (!entry) {
    entry = { key, url, cursor: after, state: 0, clients: new Set() }; sources.set(key, entry);
    const current = entry;
    const online = () => current.bridge?.online();
    const hide = () => { current.bridge?.stop(); current.bridge = undefined; current.state = 0; };
    const show = () => { if (current.clients.size && !current.bridge) current.bridge = bridge(current); };
    window.addEventListener("online", online); window.addEventListener("pagehide", hide); window.addEventListener("pageshow", show);
    current.cleanup = () => {
      window.removeEventListener("online", online); window.removeEventListener("pagehide", hide); window.removeEventListener("pageshow", show);
    };
  }
  const current = entry, source = new ChatEventSource(current);
  current.clients.add(source);
  if (!current.bridge) current.bridge = bridge(current);
  else if (current.state === 1) queueMicrotask(() => source.receive({ type: "open", key }));
  return { source, release: () => source.close() };
}

export function acquireChatEvents(chatId: string, after: number) {
  return acquire(`chat:${chatId}`, `/api/chats/${encodeURIComponent(chatId)}/events`, after);
}
export function acquireNotificationEvents(userId: string) {
  return acquire(`notifications:${userId}`, `/api/notifications/events?expected_user_id=${encodeURIComponent(userId)}`);
}
