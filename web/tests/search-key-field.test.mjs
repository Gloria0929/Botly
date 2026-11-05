import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";
import ts from "typescript";

// 执行真实组件的事件处理器；替换 hooks 和 JSX 宿主，避免访问真实账号的密钥。
const source = ts.transpileModule(
  readFileSync(new URL("../components/settings/SettingFields.tsx", import.meta.url), "utf8"),
  { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } },
).outputText;

function field({ configured = true, disabled = false, save = async () => true } = {}) {
  const states = [];
  const saves = [];
  let cursor = 0;
  const jsx = (type, props) => ({ type, props });
  const modules = {
    "react": { useEffect() {}, useState(initial) {
      const index = cursor++;
      if (!(index in states)) states[index] = initial;
      return [states[index], (value) => { states[index] = typeof value === "function" ? value(states[index]) : value; }];
    } },
    "react/jsx-runtime": { jsx, jsxs: jsx },
    "lucide-react": { Loader2: "Loader2", Save: "Save", X: "X" },
  };
  const context = vm.createContext({ exports: {}, require: (name) => {
    assert.ok(name in modules, `Unexpected import: ${name}`);
    return modules[name];
  } });
  vm.runInContext(source, context);
  const props = { configured, disabled, onSave: async (key) => {
    saves.push(key);
    const succeeded = await save(key);
    if (succeeded) props.configured = true;
    return succeeded;
  } };
  function render() {
    cursor = 0;
    const tree = context.exports.SearchKeyField(props);
    const controls = [];
    function visit(node) {
      if (!node || typeof node !== "object") return;
      if (node.type === "input" || node.type === "button") controls.push(node);
      for (const child of [node.props?.children].flat(Infinity)) visit(child);
    }
    visit(tree);
    return {
      tree,
      input: controls.find((node) => node.type === "input")?.props,
      save: controls.find((node) => node.type === "button" && node.props.className.includes("btn-primary"))?.props,
      edit: controls.find((node) => node.props["aria-label"] === "重新配置搜索 Key")?.props,
      cancel: controls.find((node) => node.props["aria-label"] === "取消重新配置搜索 Key")?.props,
    };
  }
  return { render, saves };
}

function enter(input, isComposing = false) {
  let prevented = false;
  input.onKeyDown({ key: "Enter", nativeEvent: { isComposing }, preventDefault() { prevented = true; } });
  return prevented;
}

test("已配置时在输入框容器内显示标签与 ×，隐藏编辑输入和保存按钮", () => {
  const f = field();
  const controls = f.render();
  assert.equal(controls.input, undefined);
  assert.equal(controls.save, undefined);
  assert.equal(controls.cancel, undefined);
  const container = controls.tree.props.children[0].props.children[0];
  assert.match(container.props.className, /field/);
  assert.equal(container.props.children.props.children[0], "已配置");
  assert.equal(controls.edit.disabled, false);
  controls.edit.onClick();
  assert.deepEqual(f.saves, []);
  assert.equal(f.render().input.type, "password");
  assert.equal(f.render().input.value, "");
  assert.equal(f.render().input.autoFocus, true);
  assert.equal(f.render().edit, undefined);
});

test("重新配置时，空输入和纯空格回车都不会发送请求", () => {
  const f = field();
  f.render().edit.onClick();
  for (const value of ["", "  \n\t "]) {
    f.render().input.onChange({ target: { value } });
    const controls = f.render();
    assert.equal(controls.save.disabled, true);
    assert.equal(enter(controls.input), true);
  }
  assert.deepEqual(f.saves, []);
});

test("首次配置和替换 Key 都在保存成功后恢复已配置标签", async () => {
  for (const configured of [false, true]) {
    const f = field({ configured });
    if (configured) f.render().edit.onClick();
    f.render().input.onChange({ target: { value: "  tvly-new-key  " } });
    assert.equal(f.render().save.disabled, false);
    await f.render().save.onClick();
    assert.deepEqual(f.saves, ["tvly-new-key"]);
    assert.equal(f.render().input, undefined);
    assert.ok(f.render().edit);
    f.render().edit.onClick();
    assert.equal(f.render().input.value, "");
  }
});

test("非空回车保存时锁定输入和按钮，后续回车不重复提交", async () => {
  let complete;
  const f = field({ save: () => new Promise((resolve) => { complete = resolve; }) });
  f.render().edit.onClick();
  f.render().input.onChange({ target: { value: "tvly-new-key" } });
  enter(f.render().input);
  const pending = f.render();
  assert.equal(pending.input.disabled, true);
  assert.equal(pending.save.disabled, true);
  assert.equal(pending.cancel.disabled, true);
  pending.cancel.onClick();
  assert.equal(f.render().input.value, "tvly-new-key");
  enter(pending.input);
  assert.deepEqual(f.saves, ["tvly-new-key"]);
  complete(true);
  await new Promise(setImmediate);
  assert.equal(f.render().input, undefined);
  assert.ok(f.render().edit);
});

test("保存失败保留输入，允许重新提交", async () => {
  const f = field({ save: async () => false });
  f.render().edit.onClick();
  f.render().input.onChange({ target: { value: "tvly-new-key" } });
  await f.render().save.onClick();
  assert.equal(f.render().input.value, "tvly-new-key");
  assert.equal(f.render().save.disabled, false);
});

test("取消按钮和 Escape 都返回已配置状态，不修改原 Key", () => {
  for (const escape of [false, true]) {
    const f = field();
    f.render().edit.onClick();
    f.render().input.onChange({ target: { value: "tvly-new-key" } });
    if (escape) {
      f.render().input.onKeyDown({ key: "Escape", nativeEvent: { isComposing: false }, preventDefault() {} });
    } else {
      f.render().cancel.onClick();
    }
    assert.deepEqual(f.saves, []);
    assert.equal(f.render().input, undefined);
    assert.ok(f.render().edit);
    f.render().edit.onClick();
    assert.equal(f.render().input.value, "");
  }
});

test("未配置时直接输入，中文输入中的回车不会保存，禁用时不能提交", async () => {
  const unconfigured = field({ configured: false }).render();
  assert.ok(unconfigured.input);
  assert.equal(unconfigured.edit, undefined);
  assert.equal(unconfigured.cancel, undefined);
  const f = field();
  f.render().edit.onClick();
  f.render().input.onChange({ target: { value: "tvly-new-key" } });
  assert.equal(enter(f.render().input, true), false);
  assert.deepEqual(f.saves, []);
  assert.equal(field({ disabled: true }).render().edit.disabled, true);
  const locked = field({ configured: false, disabled: true });
  locked.render().input.onChange({ target: { value: "tvly-new-key" } });
  await locked.render().save.onClick();
  assert.deepEqual(locked.saves, []);
});
