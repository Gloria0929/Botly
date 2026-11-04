import type { Message } from "./types";

export type MessageSync = {
  cursor: number;
  updated: Map<string, number>;
  deleted: Map<string, number>;
};

export function createMessageSync(): MessageSync {
  return { cursor: 0, updated: new Map(), deleted: new Map() };
}

export function isVisibleChatMessage(message: Message): boolean {
  if (message.type === "tool_result") return false;
  if (message.type === "tool_call" || (message.type === "text" && message.status === "streaming")) {
    return !!message.content?.trim();
  }
  return true;
}

export function applyAgentUsage(messages: Message[], usage: {
  run_id: string; bot_id?: string; task_id?: string | null; agent_tokens?: number;
}): Message[] {
  const tokens = usage.agent_tokens;
  if (!usage.bot_id || usage.task_id || typeof tokens !== "number") return messages;
  return messages.map((message) => message.sender_type === "bot"
    && message.run_id === usage.run_id && message.sender_bot_id === usage.bot_id && !message.meta?.task_id
    ? { ...message, meta: { ...message.meta, agent_tokens: Math.max(message.meta?.agent_tokens ?? 0, tokens) } } : message);
}

export function acceptMessageEvent(sync: MessageSync, id: string, eventId: number, deleted = false): boolean {
  if (!Number.isFinite(eventId) || eventId <= 0) return true;
  if (eventId <= Math.max(sync.cursor, sync.updated.get(id) || 0, sync.deleted.get(id) || 0)) return false;
  sync.updated.set(id, eventId);
  if (deleted) sync.deleted.set(id, eventId);
  return true;
}

/** SSE 重放或较旧快照不能把已经显示的文字缩短，也不能把完成态退回流式态。 */
export function mergeMessageUpdate(current: Message, incoming: Partial<Message>): Message {
  const next = { ...current, ...incoming, meta: { ...current.meta, ...incoming.meta } };
  if (typeof current.meta?.agent_tokens === "number") {
    next.meta.agent_tokens = Math.max(current.meta.agent_tokens, incoming.meta?.agent_tokens ?? 0);
  }
  if (incoming.status === "streaming" && current.status !== "streaming") {
    return { ...next, content: current.content, status: current.status, type: current.type };
  }
  if ((incoming.status || current.status) === "streaming"
    && incoming.content !== undefined && current.content.startsWith(incoming.content)) {
    next.content = current.content;
  }
  return next;
}

/** offset 按 Unicode 字符计数，与服务端一致；null 表示缺片段，需要重新读取快照。 */
export function mergeMessageDelta(current: Message, payload: {
  delta?: string; offset?: number; content?: string;
}): Message | null {
  if (current.status !== "streaming") return current;
  if (typeof payload.delta !== "string") {
    return typeof payload.content === "string" ? mergeMessageUpdate(current, { content: payload.content }) : current;
  }
  const characters = Array.from(current.content);
  const offset = payload.offset;
  if (!Number.isInteger(offset) || offset! < 0 || offset! > characters.length) return null;
  const delta = Array.from(payload.delta);
  const overlap = characters.length - offset!;
  if (overlap >= delta.length) return current;
  return { ...current, content: current.content + delta.slice(overlap).join("") };
}

/** 快照补历史与卡片状态；快照游标之后收到的消息和删除事件以 SSE 为准。 */
export function mergeMessageSnapshot(
  current: Message[], incoming: Message[], cursor: number, sync: MessageSync, keepOlder = true,
): Message[] {
  const existing = new Map(current.map((m) => [m.id, m]));
  const first = incoming[0]?.seq ?? Infinity;
  const next = incoming.filter((m) => (sync.deleted.get(m.id) || 0) <= cursor).map((m) => {
    const old = existing.get(m.id);
    if (!old) return m;
    return (sync.updated.get(m.id) || 0) > cursor ? old : mergeMessageUpdate(old, m);
  });
  const ids = new Set(next.map((m) => m.id));
  for (const m of current) {
    if (ids.has(m.id) || (sync.deleted.get(m.id) || 0) > cursor) continue;
    if (m.id.startsWith("tmp-") || (sync.updated.get(m.id) || 0) > cursor || (keepOlder && incoming.length > 0 && m.seq < first)) {
      next.push(m);
    }
  }
  return next.sort((a, b) => a.seq - b.seq);
}
