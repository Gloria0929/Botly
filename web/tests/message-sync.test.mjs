import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";
import ts from "typescript";

const source = ts.transpileModule(
  readFileSync(new URL("../lib/message-sync.ts", import.meta.url), "utf8"),
  { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } },
).outputText;
const context = vm.createContext({ exports: {} });
vm.runInContext(source, context);
const { createMessageSync, acceptMessageEvent, applyAgentUsage, mergeMessageUpdate, mergeMessageSnapshot, isVisibleChatMessage } = context.exports;
const message = (patch = {}) => ({
  id: "reply", thread_id: "main", seq: 2, sender_type: "bot", type: "text",
  content: "第一段", status: "streaming", meta: {}, ...patch,
});

test("快照请求期间新增的气泡和增量不会被旧快照删除或缩短", () => {
  const sync = createMessageSync();
  acceptMessageEvent(sync, "reply", 11);
  const live = message({ content: "第一段第二段" });
  assert.equal(mergeMessageSnapshot([live], [], 10, sync)[0], live);
  assert.equal(mergeMessageSnapshot([live], [message()], 10, sync)[0].content, live.content);
});

test("初始快照比重放的流式事件完整时，内容保持增长", () => {
  const live = message({ content: "第一段第二段第三段" });
  assert.equal(mergeMessageUpdate(live, message({ content: "" })).content, live.content);
  assert.equal(mergeMessageUpdate(live, { content: "第一段第二段" }).content, live.content);
  assert.equal(mergeMessageUpdate(live, { content: `${live.content}第四段` }).content, `${live.content}第四段`);
});

test("已完成的气泡不会被重放事件改回空草稿或流式状态", () => {
  const completed = message({ content: "完整回复", status: "done", type: "tool_call" });
  const result = mergeMessageUpdate(completed, message({ content: "" }));
  assert.equal(result.content, completed.content);
  assert.equal(result.status, "done");
  assert.equal(result.type, "tool_call");
});

test("重连拒绝已被快照覆盖、重复或倒序的事件", () => {
  const sync = createMessageSync();
  sync.cursor = 20;
  assert.equal(acceptMessageEvent(sync, "reply", 19), false);
  assert.equal(acceptMessageEvent(sync, "reply", 21), true);
  assert.equal(acceptMessageEvent(sync, "reply", 21), false);
  assert.equal(acceptMessageEvent(sync, "reply", 20), false);
});

test("删除事件不会被晚到的旧快照复活", () => {
  const sync = createMessageSync();
  acceptMessageEvent(sync, "reply", 12, true);
  assert.equal(mergeMessageSnapshot([], [message()], 11, sync).length, 0);
  assert.equal(mergeMessageSnapshot([message()], [], 11, sync).length, 0);
});

test("最新消息快照保留已加载的历史和等待确认的用户消息", () => {
  const sync = createMessageSync();
  const older = message({ id: "older", seq: 1, status: "done" });
  const pending = message({ id: "tmp-user", seq: 1e9, sender_type: "user", status: "done" });
  const result = mergeMessageSnapshot([older, pending], [message()], 10, sync);
  assert.deepEqual(Array.from(result, (m) => m.id), ["older", "reply", "tmp-user"]);
  assert.equal(mergeMessageSnapshot([message()], [], 10, sync).length, 0);
});

test("工具轮保留已输出文字，纯工具调用与空草稿不闪空气泡", () => {
  assert.equal(isVisibleChatMessage(message({ type: "tool_call", status: "done" })), true);
  assert.equal(isVisibleChatMessage(message({ type: "tool_call", content: "" })), false);
  assert.equal(isVisibleChatMessage(message({ content: "" })), false);
  assert.equal(isVisibleChatMessage(message({ type: "tool_result" })), false);
  assert.equal(isVisibleChatMessage(message({ type: "approval", content: "" })), true);
});

test("主 Agent 用量随事件更新，按执行和发送者隔离并排除子任务", () => {
  const main = message({run_id: "run", sender_bot_id: "main"});
  const other = message({id: "other", run_id: "run", sender_bot_id: "other"});
  const history = message({id: "history", run_id: "older", sender_bot_id: "main"});
  const subtask = message({id: "subtask", run_id: "run", sender_bot_id: "main", meta: {task_id: "task"}});
  const result = applyAgentUsage([main, other, history, subtask], {run_id: "run", bot_id: "main", task_id: null, agent_tokens: 410});
  assert.equal(result[0].meta.agent_tokens, 410);
  assert.equal(result[1], other);
  assert.equal(result[2], history);
  assert.equal(result[3], subtask);
  assert.equal(applyAgentUsage(result, {run_id: "run", bot_id: "main", task_id: "task", agent_tokens: 999}), result);
});

test("用量更新后较旧的完成事件和消息快照不会把累计 tokens 清零", () => {
  const live = message({meta: {agent_tokens: 410}});
  assert.equal(mergeMessageUpdate(live, {status: "done", meta: {agent_tokens: 150}}).meta.agent_tokens, 410);
  assert.equal(mergeMessageSnapshot([live], [message({meta: {agent_tokens: 0}})], 10, createMessageSync())[0].meta.agent_tokens, 410);
});
