import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";
import ts from "typescript";

const source = ts.createSourceFile("ChatList.tsx", readFileSync(new URL("../components/chat/ChatList.tsx", import.meta.url), "utf8"),
  ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
let submit;
let createDisabled;
let inlineAlert = false;
function visit(node) {
  if (ts.isVariableDeclaration(node) && node.name.getText(source) === "submit") {
    submit = node.initializer.getText(source);
  }
  if (ts.isJsxElement(node) && node.openingElement.tagName.getText(source) === "button"
      && node.children.some(child => ts.isJsxText(child) && child.text.includes("创建会话"))) {
    createDisabled = node.openingElement.attributes.properties
      .find(item => item.name?.getText(source) === "disabled")?.initializer.expression.getText(source);
  }
  if (ts.isJsxOpeningElement(node) && node.tagName.getText(source) === "p"
      && node.attributes.properties.some(item => item.name?.getText(source) === "role"
        && item.initializer?.text === "alert")) inlineAlert = true;
  ts.forEachChild(node, visit);
}
visit(source);
assert.ok(submit);
assert.match(createDisabled || "", /loading/);
assert.doesNotMatch(createDisabled || "", /picked/);
assert.equal(inlineAlert, true);

function validationCase({ name, picked, chatMode }) {
  const state = { errors: [], focused: [], created: [] };
  const context = vm.createContext({ exports: {}, name, picked, chatMode, sup: "",
    submitting: { current: false }, setLoading() {},
    setFormError: value => state.errors.push(value), setErrorField: value => { state.errorField = value; },
    nameInput: { current: { focus: () => state.focused.push("name") } },
    membersField: { current: { focus: () => state.focused.push("members") } },
    api: { createChat: async value => { state.created.push(value); return { id: "new-chat" }; } },
    onCreated: id => { state.createdId = id; }, toastError() {}, errText: error => error.message,
  });
  const javascript = ts.transpileModule(`exports.submit = ${submit};`, {
    compilerOptions: { target: ts.ScriptTarget.ES2022 },
  }).outputText;
  vm.runInContext(javascript, context);
  return { run: () => context.exports.submit(), state };
}

test("空名称显示持续的字段错误并将焦点放回名称输入框", async () => {
  const result = validationCase({ name: "  ", picked: ["bot-1"], chatMode: "task" });
  await result.run();
  assert.equal(result.state.errors.at(-1), "请填写群聊名称后再创建。");
  assert.equal(result.state.errorField, "name");
  assert.deepEqual(result.state.focused, ["name"]);
  assert.equal(result.state.created.length, 0);
});

test("未选成员及自由模式只选一人时都会指出缺少的输入", async () => {
  const none = validationCase({ name: "项目群", picked: [], chatMode: "task" });
  await none.run();
  assert.equal(none.state.errorField, "members");
  assert.equal(none.state.focused[0], "members");

  const free = validationCase({ name: "自由聊天", picked: ["bot-1"], chatMode: "free" });
  await free.run();
  assert.equal(free.state.errors.at(-1), "自由群聊需要选择 2–6 位成员。");
  assert.equal(free.state.errorField, "members");
  assert.equal(free.state.created.length, 0);
});
