import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";
import ts from "typescript";

const source = ts.createSourceFile("page.tsx", readFileSync(new URL("../app/chat/page.tsx", import.meta.url), "utf8"),
  ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const names = new Set(["enterBranch", "branchFrom", "backToMain", "openBranches"]);
const handlers = [];
function visit(node) {
  if (ts.isVariableDeclaration(node) && names.has(node.name.getText(source))) {
    handlers.push(`exports.${node.name.getText(source)} = ${node.initializer.getText(source)};`);
  }
  ts.forEachChild(node, visit);
}
visit(source);
assert.equal(handlers.length, names.size);

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
function environment() {
  const requests = [], creates = [], lists = [];
  const state = { messages: [{ id: "initial" }], errors: [], branch: null, branches: [] };
  const context = vm.createContext({ exports: {}, chatId: "chat-A", chatIdRef: { current: "chat-A" },
    navigationVersion: { current: 0 }, branchRef: { current: null }, focusRef: { current: "old-search" },
    api: {
      messages(id, options) { const pending = deferred(); requests.push({ id, options, ...pending }); return pending.promise; },
      createBranch() { const pending = deferred(); creates.push(pending); return pending.promise; },
      branches() { const pending = deferred(); lists.push(pending); return pending.promise; },
    },
    setMessages: value => { state.messages = value; }, setBranch: value => { state.branch = value; },
    setBranches: value => { state.branches = value; },
    setHasEarlier() {}, setFocusMessage() {}, setBranchesOpen() {}, toast() {},
    toastError: error => state.errors.push(error),
  });
  vm.runInContext(ts.transpileModule(handlers.join("\n"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText, context);
  return { context, state, requests, creates, lists };
}

for (const handler of ["enterBranch", "backToMain", "branchFrom"]) {
  test(`${handler} 的旧消息请求不会覆盖已切换的群聊`, async () => {
    const env = environment();
    const pending = env.context.exports[handler]({ id: "branch-A" });
    if (handler === "branchFrom") {
      env.creates[0].resolve({ id: "branch-A" });
      await new Promise(setImmediate);
    }
    assert.equal(env.requests.length, 1);
    env.context.chatIdRef.current = "chat-B";
    env.context.branchRef.current = null;
    env.state.messages = [{ id: "message-B" }];
    env.requests[0].resolve({ messages: [{ id: "message-A" }] });
    await pending;
    assert.deepEqual(env.state.messages, [{ id: "message-B" }]);
    assert.equal(env.context.branchRef.current, null);
  });
}

test("同群连续切换分支，先发后到的请求不会覆盖较新的选择", async () => {
  const env = environment();
  const first = env.context.exports.enterBranch({ id: "first" });
  const second = env.context.exports.enterBranch({ id: "second" });
  env.requests[1].resolve({ messages: [{ id: "second-answer" }] });
  await second;
  env.requests[0].resolve({ messages: [{ id: "first-answer" }] });
  await first;
  assert.equal(env.context.branchRef.current.id, "second");
  assert.deepEqual(env.state.messages, [{ id: "second-answer" }]);
  assert.equal(env.context.focusRef.current, null);
});

test("返回主线程会作废尚未完成的分支加载", async () => {
  const env = environment();
  const branch = env.context.exports.enterBranch({ id: "branch" });
  const main = env.context.exports.backToMain();
  env.requests[1].resolve({ messages: [{ id: "main-answer" }] });
  await main;
  env.requests[0].resolve({ messages: [{ id: "branch-answer" }] });
  await branch;
  assert.equal(env.context.branchRef.current, null);
  assert.deepEqual(env.state.messages, [{ id: "main-answer" }]);
});

test("创建分支和加载分支列表期间换群，不会打开旧群分支", async () => {
  const env = environment();
  const creating = env.context.exports.branchFrom({ id: "anchor" });
  const listing = env.context.exports.openBranches();
  env.context.chatIdRef.current = "chat-B";
  env.creates[0].resolve({ id: "old-branch" });
  env.lists[0].resolve([{ id: "old-branch" }]);
  await Promise.all([creating, listing]);
  assert.equal(env.context.branchRef.current, null);
  assert.equal(env.requests.length, 0);
  assert.deepEqual(env.state.branches, []);
});
