import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";
import ts from "typescript";

const source = ts.transpileModule(readFileSync(new URL("../components/chat/SitePreview.tsx", import.meta.url), "utf8"),
  { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText;
const waiting = { running: true, ready: false, port: 3000, log: "" };
const ready = { ...waiting, ready: true, phase: "ready" };
const stopped = { ...waiting, running: false, phase: "stopped" };

function frame({ backend = async () => ready, configured = true } = {}) {
  const hooks = [], calls = [], effects = [], timers = new Map();
  let cursor = 0, time = 0, timerId = 0;
  const props = { file: { id: "first", path: "output/dist/index.html", updated_at: null, size: 10 } };
  const jsx = (type, props) => ({ type, props });
  const react = {
    useState(initial) {
      const index = cursor++;
      if (!(index in hooks)) hooks[index] = initial;
      return [hooks[index], (value) => { hooks[index] = typeof value === "function" ? value(hooks[index]) : value; }];
    },
    useRef(initial) { const index = cursor++; return hooks[index] ||= { current: initial }; },
    useEffect(fn, deps) {
      const index = cursor++;
      const previous = hooks[index];
      if (!previous || deps.some((value, i) => !Object.is(value, previous.deps[i]))) {
        effects.push(() => { previous?.cleanup?.(); hooks[index] = { deps, cleanup: fn() }; });
      }
    },
  };
  const modules = {
    react,
    "react/jsx-runtime": { jsx, jsxs: jsx },
    "lucide-react": Object.fromEntries(["Loader2", "Maximize2", "Monitor", "RotateCw", "Square"].map((key) => [key, key])),
    "@/lib/api": { api: {
      fileSite: async (id) => ({ url: `/preview/${id}`, backend: configured ? { port: 3000 } : null }),
      fileBackend: async (id, action = "start", signal) => {
        calls.push({ id, action, signal });
        return backend(id, action);
      },
    }, errText: (error, fallback) => error.message || fallback },
  };
  const context = vm.createContext({ exports: {}, require: (name) => {
    assert.ok(name in modules, name); return modules[name];
  }, AbortController, Date: class extends Date { static now() { return time; } },
  setTimeout: (fn, delay) => { timers.set(++timerId, { fn, at: time + delay }); return timerId; },
  clearTimeout: (id) => timers.delete(id) });
  vm.runInContext(source, context);
  function render() {
    cursor = 0;
    const tree = context.exports.SiteFrame(props);
    const nodes = [], strings = [];
    function visit(node) {
      if (typeof node === "string" || typeof node === "number") { strings.push(String(node)); return; }
      if (!node || typeof node !== "object") return;
      nodes.push(node);
      for (const child of [node.props?.children].flat(Infinity)) visit(child);
    }
    visit(tree);
    for (const effect of effects.splice(0)) effect();
    return { text: strings.join(" "), iframe: nodes.find(n => n.type === "iframe"),
      alert: nodes.find(n => n.props?.role === "alert"),
      stop: nodes.find(n => n.props?.["aria-label"] === "停止预览后端")?.props,
      fullscreen: nodes.find(n => n.props?.["aria-label"] === "全屏预览")?.props,
      refresh: nodes.find(n => n.props?.["aria-label"] === "刷新预览")?.props };
  }
  const flush = () => new Promise(setImmediate);
  render();
  return { render, calls, timers, flush,
    async advance(milliseconds = 1500) {
      time += milliseconds;
      for (const [id, timer] of [...timers]) if (timer.at <= time) { timers.delete(id); timer.fn(); }
      await flush();
    },
    changeFile(id) { props.file = { ...props.file, id }; render(); },
    unmount() { for (const hook of hooks) hook?.cleanup?.(); },
  };
}

test("running=true、ready=false、空日志继续轮询，延迟就绪后自动打开页面", async () => {
  const states = [waiting, { ...waiting, phase: "starting", elapsed_seconds: 25, log: "installing dependencies" }, ready];
  const f = frame({ backend: async () => states.shift() });
  await f.flush();
  assert.equal(f.render().alert, undefined);
  assert.equal(f.render().iframe, undefined);
  assert.match(f.render().text, /正在准备预览/);
  assert.match(f.render().text, /暂未输出日志/);
  assert.ok(f.render().stop);
  await f.advance();
  assert.match(f.render().text, /installing dependencies/);
  await f.advance();
  assert.equal(f.render().iframe.props.src, "/preview/first");
  assert.equal(f.render().alert, undefined);
  assert.doesNotMatch(f.render().text, /output\/dist\/index\.html|后端已就绪|3000/);
  assert.ok(f.render().refresh);
  assert.ok(f.render().fullscreen);
  assert.deepEqual(f.calls.map(c => c.action), ["start", "status", "status"]);
  assert.equal(f.timers.size, 0);
});

test("进程退出或超时后结束轮询，保留具体诊断和日志", async () => {
  for (const state of [
    { ...stopped, phase: "failed", message: "进程已退出", log: "Cannot find module express" },
    { ...waiting, phase: "timeout", message: "3000 端口等待 300 秒后仍未监听", log: "npm install" },
  ]) {
    const f = frame({ backend: async () => state });
    await f.flush();
    assert.ok(f.render().alert);
    assert.ok(f.render().text.includes(state.message));
    assert.ok(f.render().text.includes(state.log));
    assert.equal(f.timers.size, 0);
    assert.ok(f.render().stop);
  }
});

test("停止期间取消轮询，迟到的就绪响应不能重新打开预览", async () => {
  let finishStatus;
  const f = frame({ backend: async (_, action) => action === "start" ? waiting : action === "stop" ? stopped
    : new Promise(resolve => { finishStatus = resolve; }) });
  await f.flush();
  await f.advance();
  await f.render().stop.onClick();
  finishStatus(ready);
  await f.flush();
  assert.equal(f.render().iframe, undefined);
  assert.match(f.render().text, /后端已停止/);
  assert.equal(f.calls[1].signal.aborted, true);
  assert.equal(f.timers.size, 0);
});

test("初次启动请求尚未完成时不能抢先停止，拿到进程状态后可停止等待", async () => {
  let finishStart;
  const f = frame({ backend: async () => new Promise(resolve => { finishStart = resolve; }) });
  await f.flush();
  assert.equal(f.render().stop.disabled, true);
  finishStart(waiting);
  await f.flush();
  assert.equal(f.render().stop.disabled, false);
  f.unmount();
});

test("切换页面或卸载时清理轮询和旧响应", async () => {
  let finishStatus;
  const f = frame({ backend: async (id, action) => id === "second" ? ready : action === "start" ? waiting
    : new Promise(resolve => { finishStatus = resolve; }) });
  await f.flush();
  await f.advance();
  f.changeFile("second");
  await f.flush();
  finishStatus(ready);
  await f.flush();
  assert.equal(f.render().iframe.props.src, "/preview/second");
  const pending = frame({ backend: async () => waiting });
  await pending.flush();
  pending.unmount();
  await pending.advance();
  assert.equal(pending.calls.length, 1);
  assert.equal(pending.timers.size, 0);
});

test("旧版四字段状态也有等待上限，纯静态页面无需启动后端", async () => {
  const f = frame({ backend: async () => waiting });
  await f.flush();
  await f.advance(300001);
  assert.match(f.render().text, /端口 3000 长时间未开始监听/);
  assert.equal(f.timers.size, 0);
  const html = frame({ configured: false });
  await html.flush();
  assert.equal(html.render().iframe.props.src, "/preview/first");
  assert.equal(html.calls.length, 0);
});
