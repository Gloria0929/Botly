import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";
import ts from "typescript";

const source = ts.createSourceFile("bots.tsx", readFileSync(new URL("../app/bots/page.tsx", import.meta.url), "utf8"),
  ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
let click, condition;
function visit(node) {
  if (ts.isJsxElement(node) && node.openingElement.tagName.getText(source) === "button"
      && node.children.some(child => ts.isJsxText(child) && child.text.includes("同步最新模板"))) {
    const attribute = node.openingElement.attributes.properties.find(item => item.name?.getText(source) === "onClick");
    click = attribute.initializer.expression.getText(source);
    condition = node.parent.left.getText(source);
  }
  ts.forEachChild(node, visit);
}
visit(source);
assert.ok(click && condition);

function environment(patch = {}) {
  const original = { id: "bot", name: "我的研究伙伴", system_prompt: "old", persona: "我自己的语气",
    template_id: "researcher", template_update_available: true, provider_id: "provider", model: "model",
    avatar: "custom", tools: ["file_read"], skills: ["skill"], proactivity: .8 };
  let draft;
  const context = vm.createContext({ exports: {}, editing: { ...original, ...patch }, bots: [original],
    templates: [{ id: "researcher", system_prompt: "new", persona: "default style", tools: ["web_search"] }],
    setEditing: value => { draft = value; } });
  const code = `exports.available = () => Boolean(${condition}); exports.update = ${click};`;
  vm.runInContext(ts.transpileModule(code, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText, context);
  return { context, original, draft: () => draft };
}

test("同步模板只填入职责草稿，保留自定义性格、身份、模型和工具", () => {
  const env = environment();
  assert.equal(env.context.exports.available(), true);
  env.context.exports.update();
  const draft = env.draft();
  assert.equal(draft.system_prompt, "new");
  assert.equal(draft.template_update_available, false);
  for (const key of ["name", "persona", "provider_id", "model", "avatar", "tools", "skills", "proactivity"]) {
    assert.deepEqual(draft[key], env.original[key]);
  }
  assert.equal(env.context.editing.system_prompt, "old", "点击同步不会改写已保存的对象");
});

test("没有单独人设时同步模板性格，修改过职责草稿则隐藏同步入口", () => {
  const empty = environment({ persona: "" });
  empty.context.exports.update();
  assert.equal(empty.draft().persona, "default style");
  assert.equal(environment({ system_prompt: "custom draft" }).context.exports.available(), false);
  assert.equal(environment({ template_id: null }).context.exports.available(), false);
});
