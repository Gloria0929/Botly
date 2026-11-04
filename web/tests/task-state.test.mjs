import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";
import ts from "typescript";

const source = ts.transpileModule(readFileSync(new URL("../lib/task-state.ts", import.meta.url), "utf8"),
  { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
const context = vm.createContext({ exports: {} });
vm.runInContext(source, context);
const { canRetryTask, taskCounts, teamStatus, taskFiles, memberActivity, showMemberActivity, applyRunCompletion } = context.exports;

test("群聊仍在执行或等待时，失败子任务不能手动重试", () => {
  for (const status of ["queued", "running", "waiting", "waiting_children"]) {
    assert.equal(canRetryTask({ status: "failed" }, { status }), false);
  }
  assert.equal(canRetryTask({ status: "failed" }, { status: "completed" }), true);
  assert.equal(canRetryTask({ status: "canceled" }, null), true);
  assert.equal(canRetryTask({ status: "completed" }, null), false);
});

test("任务状态概览区分执行、排队、等待、完成和失败记录", () => {
  const counts = taskCounts(["running", "queued", "waiting_approval", "waiting_reply", "completed", "failed", "canceled"].map(status => ({ status })));
  assert.deepEqual({ ...counts }, { total: 7, running: 1, queued: 1, waiting: 2, completed: 1, failed: 1 });
  assert.equal(teamStatus({ status: "waiting_children" }, [{ status: "waiting_reply" }]), "等待子任务返回 · 1 项需你处理");
  assert.equal(teamStatus({ status: "waiting", resume: { kind: "approval" } }, []), "等待你处理审批");
  assert.equal(teamStatus({ status: "waiting", resume: { kind: "plan" } }, []), "等待你确认执行计划");
});

test("并行同名产物按执行与成员归属关联，歧义文件不自动关联", () => {
  const own = { id: "a", path: "tasks/a/report.md", run_id: "run-a", created_by_bot_id: "bot-a" };
  const other = { id: "b", path: "tasks/b/report.md", run_id: "run-b", created_by_bot_id: "bot-b" };
  const message = { content: "已交付 report.md", run_id: "run-a", sender_bot_id: "bot-a" };
  assert.deepEqual(taskFiles([own, other], message).map(file => file.id), ["a"]);
  assert.deepEqual(taskFiles([own, { ...own, id: "c", path: "tasks/a/sub/report.md" }], message), []);
  assert.deepEqual(taskFiles([own, other], { ...message, content: other.path }).map(file => file.id), ["b"]);
});

test("等待同伴和执行任务不能显示成正在输入", () => {
  const task = { id: "task-a", run_id: "child-a", assigned_bot_id: "researcher", bot_name: "研究员",
    thread_id: "thread-a", status: "running", instruction: "整理歌曲文档" };
  const note = { sender_bot_id: "supervisor", meta: { delegation: true, task_id: task.id } };
  const supervisor = memberActivity({ id: "supervisor", status: "working" }, [note], [task]);
  const researcher = memberActivity({ id: "researcher", status: "working" }, [note], [task]);
  assert.equal(supervisor.kind, "waiting");
  assert.equal(supervisor.label, "等待研究员完成任务");
  assert.equal(researcher.kind, "working");
  assert.equal(researcher.label, "正在执行任务");
  assert.equal(researcher.task.id, "task-a");
  assert.equal(memberActivity({ id: "other", status: "working" }, [], []).label, "正在思考…");
  assert.equal(memberActivity({ id: "researcher", status: "idle" }, [], [{ ...task, status: "completed" }]), null);
});

test("执行结束事件立即清除输入状态，快照未返回也不会一直转圈", () => {
  const run = { id: "root-a", status: "waiting_children", end_reason: null };
  const chat = { active_run: run, active_runs: [run], last_run: run,
    members: [{ id: "supervisor", status: "working", typing_run_ids: [run.id] },
              { id: "researcher", status: "working", typing_run_ids: [] }] };
  for (const status of ["completed", "failed", "stopped"]) {
    const next = applyRunCompletion(chat, { run_id: run.id, status, end_reason: "执行已结束" });
    assert.equal(next.active_run, null);
    assert.equal(next.active_runs.length, 0);
    assert.equal(next.last_run.status, status);
    assert.equal(next.last_run.end_reason, "执行已结束");
    assert(next.members.every(bot => bot.status === "idle" && !bot.typing_run_ids.length));
  }
  assert.equal(chat.members[0].status, "working");
  assert.equal(applyRunCompletion(chat, { run_id: "child-a", status: "completed" }), chat);
});

test("一条消息结束不会清除另一条并行消息的输入提示", () => {
  const a = { id: "a", status: "waiting_children" }, b = { id: "b", status: "running" };
  const chat = { active_run: a, active_runs: [a, b], members: [
    { id: "both", status: "working", typing_run_ids: ["a", "b"] },
    { id: "only-a", status: "working", typing_run_ids: ["a"] },
  ] };
  const next = applyRunCompletion(chat, { run_id: "a", status: "completed" });
  assert.equal(next.active_run.id, "b");
  assert.equal(next.members[0].status, "working");
  assert.equal(next.members[0].typing_run_ids.join(), "b");
  assert.equal(next.members[1].status, "idle");
});

test("自由群聊不预先弹出成员思考气泡，实际任务进度仍可见", () => {
  const bot = { id: "bot-a", status: "working" };
  assert.equal(showMemberActivity(bot, [], [], true), false);
  assert.equal(showMemberActivity(bot, [], [], false), true);
  const task = { id: "task-a", assigned_bot_id: bot.id, status: "running" };
  assert.equal(showMemberActivity(bot, [], [task], true), true);
  const waiting = { id: "task-b", assigned_bot_id: "bot-b", bot_name: "同伴", status: "running" };
  assert.equal(showMemberActivity(bot, [{ sender_bot_id: bot.id, meta: { delegation: true, task_id: waiting.id } }], [waiting], true), true);
});
