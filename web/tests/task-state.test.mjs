import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";
import ts from "typescript";

const source = ts.transpileModule(readFileSync(new URL("../lib/task-state.ts", import.meta.url), "utf8"),
  { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
const context = vm.createContext({ exports: {} });
vm.runInContext(source, context);
const { canRetryTask, taskCounts, teamStatus, taskFiles } = context.exports;

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
