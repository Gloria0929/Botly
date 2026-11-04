import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";
import ts from "typescript";

function environment(worker = false) {
  const connections = [], timers = [], intervals = [], microtasks = [], clock = [0];
  class Source extends EventTarget {
    readyState = 0;
    constructor(url) { super(); this.url = url; connections.push(this); }
    close() { this.readyState = 2; }
    open() { this.readyState = 1; this.onopen?.(new Event("open")); }
    fail() { this.onerror?.(new Event("error")); }
    message(id) { this.dispatchEvent(new MessageEvent("message.delta", { data: JSON.stringify({ delta: "text" }), lastEventId: String(id) })); }
  }
  const context = vm.createContext({ exports: {}, self: {}, window: new EventTarget(), EventTarget, Event, MessageEvent,
    EventSource: Source, Date: class extends Date { static now() { return clock[0]; } },
    Math: Object.assign(Object.create(Math), { random: () => 0.5 }),
    setTimeout: (fn, delay) => { const timer = { fn, delay }; timers.push(timer); return timer; },
    clearTimeout: timer => { if (timer) timer.canceled = true; },
    setInterval: fn => { intervals.push(fn); return fn; }, clearInterval: () => {},
    queueMicrotask: fn => microtasks.push(fn),
  });
  const path = worker ? "../public/chat-events-worker.js" : "../lib/chat-events.ts";
  const input = readFileSync(new URL(path, import.meta.url), "utf8");
  vm.runInContext(worker ? input : ts.transpileModule(input, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText, context);
  return { context, connections, timers, intervals, microtasks, clock };
}

test("页面订阅共享连接，但不会覆盖彼此的 open 回调或泄漏已释放监听器", () => {
  const env = environment(), { acquireChatEvents } = env.context.exports;
  const first = acquireChatEvents("a".repeat(32), 10), second = acquireChatEvents("a".repeat(32), 10);
  let opened = 0, received = 0;
  first.source.onopen = () => opened++;
  second.source.onopen = () => opened++;
  first.source.addEventListener("message.delta", () => received += 100);
  second.source.addEventListener("message.delta", () => received++);
  assert.equal(env.connections.length, 1);
  env.connections[0].open(); assert.equal(opened, 2);
  first.release(); env.connections[0].message(11); assert.equal(received, 1);
  assert.equal(env.connections[0].readyState, 1);
  second.release(); second.release(); assert.equal(env.connections[0].readyState, 2);
});

test("重连从最近游标继续，退避增长，online 立即重连，pagehide 后可以恢复", () => {
  const env = environment(), lease = env.context.exports.acquireChatEvents("b".repeat(32), 10);
  env.connections[0].message(15); env.connections[0].fail();
  assert.equal(env.timers[0].delay, 1000);
  env.timers[0].fn(); assert.ok(env.connections[1].url.endsWith("after=15"));
  env.connections[1].fail(); assert.equal(env.timers[1].delay, 2000);
  env.context.window.dispatchEvent(new Event("online")); assert.equal(env.connections.length, 3);
  env.context.window.dispatchEvent(new Event("pagehide")); assert.equal(env.connections[2].readyState, 2);
  env.context.window.dispatchEvent(new Event("pageshow")); assert.equal(env.connections.length, 4);
  lease.release();
});

function port(env, key, after = 0) {
  const peer = { messages: [], start() {}, postMessage(packet) { this.messages.push(packet); } };
  env.context.self.onconnect({ ports: [peer] });
  peer.send = type => peer.onmessage({ data: { type, key, after, types: ["message.delta"] } });
  peer.send("subscribe");
  return peer;
}

test("SharedWorker 中两个标签页共用一条 SSE，后加入的页面补齐缓存，最后退出才关闭", () => {
  const env = environment(true), key = `chat:${"c".repeat(32)}`;
  const first = port(env, key, 10);
  env.connections[0].open(); env.connections[0].message(11);
  const second = port(env, key, 10);
  assert.equal(env.connections.length, 1);
  assert.equal(second.messages.filter(m => m.type === "event")[0].id, "11");
  first.send("unsubscribe"); env.connections[0].message(12);
  assert.equal(second.messages.at(-1).id, "12");
  second.send("unsubscribe"); assert.equal(env.connections[0].readyState, 2);
});

test("SharedWorker 按账号隔离通知，按会话隔离事件，并回收失联页面", () => {
  const env = environment(true);
  port(env, `notifications:${"a".repeat(32)}`);
  port(env, `notifications:${"b".repeat(32)}`);
  assert.equal(env.connections.length, 2);
  assert.notEqual(env.connections[0].url, env.connections[1].url);
  const peer = port(env, `chat:${"d".repeat(32)}`, 20);
  env.clock[0] = 120_000; env.intervals[0]();
  assert.ok(env.connections.every(source => source.readyState === 2));
  peer.send("ping"); assert.equal(env.connections.length, 4);
  assert.ok(env.connections[3].url.endsWith("after=20"));
});
