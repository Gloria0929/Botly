import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";
import ts from "typescript";

// 使用项目已有的 TypeScript 编译器加载真实客户端，隔离浏览器与网络依赖。
const sources = Object.fromEntries(["api", "push"].map((name) => [name, ts.transpileModule(
  readFileSync(new URL(`../lib/${name}.ts`, import.meta.url), "utf8"),
  { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } },
).outputText]));

function client(respond, pathname = "/login", browser = {}) {
  const calls = [];
  const notices = [];
  const location = { pathname, href: pathname, hostname: "localhost" };
  const modules = { "./ui": { toast: (text, kind) => notices.push({ text, kind }) } };
  const context = vm.createContext({
    FormData, URLSearchParams, Uint8Array, atob, location,
    window: { isSecureContext: true, PushManager: {}, Notification: {}, ...browser.window },
    navigator: browser.navigator,
    Notification: browser.Notification,
    fetch: async (url, init) => {
      calls.push({ url, init });
      return respond(url, init);
    },
    require: (name) => {
      assert.ok(name in modules, `Unexpected import: ${name}`);
      return modules[name];
    },
  });
  for (const name of ["api", "push"]) {
    context.exports = {};
    vm.runInContext(sources[name], context, { filename: `${name}.js` });
    modules[`./${name}`] = context.exports;
  }
  return { ...modules["./api"], ...modules["./push"], calls, notices, location };
}

function jsonError(status, detail) {
  return () => Response.json({ detail }, { status });
}

test("取消预览轮询不弹网络错误，启动诊断留给预览面板展示", async () => {
  const controller = new AbortController();
  controller.abort();
  const canceled = client(() => { throw new DOMException("aborted", "AbortError"); }, "/chat");
  await assert.rejects(canceled.api.fileBackend("page", "status", controller.signal));
  assert.equal(canceled.calls[0].init.signal, controller.signal);
  assert.deepEqual(canceled.notices, []);
  const failed = client(jsonError(502, "启动命令执行失败"), "/chat");
  await assert.rejects(failed.api.fileBackend("page", "start", new AbortController().signal), { message: "启动命令执行失败" });
  assert.deepEqual(failed.notices, []);
});

test("错误账号密码显示后端 detail，停留在登录页", async () => {
  const c = client(jsonError(401, "邮箱或密码错误"));
  await assert.rejects(c.api.login("wrong@example.com", "wrong-password"), (error) => {
    assert.equal(error.status, 401);
    assert.equal(c.errText(error, "登录失败"), "邮箱或密码错误");
    return true;
  });
  assert.equal(c.location.href, "/login");
  assert.deepEqual(c.notices, [{ text: "邮箱或密码错误", kind: "error" }]);
});

test("401 会话失效保留 detail，并跳转登录页", async () => {
  const c = client(jsonError(401, "登录状态已失效，请重新登录"), "/settings");
  await assert.rejects(c.api.me(), { status: 401, message: "登录状态已失效，请重新登录" });
  assert.equal(c.location.href, "/login");
});

for (const page of ["/register", "/forgot-password", "/reset-password", "/verify-email"]) {
  test(`401 不打断公开认证页面 ${page}`, async () => {
    const c = client(jsonError(401, "链接无效或已过期"), page);
    await assert.rejects(c.api.verifyEmail("expired"), { message: "链接无效或已过期" });
    assert.equal(c.location.href, page);
  });
}

for (const [status, detail] of [
  [400, "当前密码不正确"], [403, "账号不可用"], [404, "资源不存在"],
  [409, "该 Bot 正在执行任务"], [429, "连续登录失败过多，请在 15 分钟后重试"],
  [503, "邮件发送失败，请稍后重试"],
]) {
  test(`${status} 优先显示 detail`, async () => {
    const c = client(jsonError(status, detail), "/settings");
    await assert.rejects(c.api.changePassword("old", "new"), { status, message: detail });
    assert.equal(c.location.href, "/settings");
  });
}

test("detail 支持校验数组、嵌套对象，并忽略无法显示的值", async () => {
  const detail = [{ loc: ["body", "email"], msg: "邮箱格式不正确" }, { msg: "密码至少 8 位" }];
  const c = client(jsonError(422, detail));
  await assert.rejects(c.api.register({}), { message: "邮箱格式不正确；密码至少 8 位" });
  assert.equal(c.detailText({ detail, message: "通用提示" }), "邮箱格式不正确；密码至少 8 位");
  assert.equal(c.detailText([null, {}, " ", { msg: { invalid: true } }, " 有效提示 "]), "有效提示");
  assert.equal(c.errText({ detail: "实际原因", message: "通用提示" }), "实际原因");
  assert.equal(c.errText({ detail: [] }, "保存失败"), "保存失败");
});

for (const [body, status, message] of [
  ["<html>Bad Gateway</html>", 502, "服务暂时不可用（502），请确认后端已启动后重试"],
  ["{}", 400, "请求失败（400）"],
  ['{"detail":"  "}', 401, "未登录，请重新登录"],
]) {
  test(`错误响应没有可读 detail 时兜底（${status}）`, async () => {
    const c = client(() => new Response(body, { status }));
    await assert.rejects(c.api.me(), { status, message });
  });
}

const file = new File(["contents"], "sample.txt", { type: "text/plain" });
const operations = {
  图片上传: (api) => api.uploadImage(file),
  群聊文件上传: (api) => api.uploadChatFile("chat", file),
  群聊导出: (api) => api.exportChat("chat"),
  文件预览: (api) => api.fileContent("file"),
  推送配置: (api) => api.pushConfig(),
  推送订阅: (api) => api.pushSubscribe({ endpoint: "test", keys: {} }),
  推送退订: (api) => api.pushUnsubscribe("test"),
};

for (const [name, run] of Object.entries(operations)) {
  test(`${name} 使用统一的 detail、401 和网络错误处理`, async () => {
    const c = client(jsonError(401, "会话已过期"), "/chat");
    await assert.rejects(run(c.api), { status: 401, message: "会话已过期" });
    assert.equal(c.location.href, "/login");
    const offline = client(() => { throw new TypeError("Failed to fetch"); });
    await assert.rejects(run(offline.api), { status: 0, message: "网络请求失败，请检查连接或稍后重试" });
  });
}

test("正常响应保留 JSON、204、文件与上传协议", async () => {
  const c = client((url) => {
    if (url.endsWith("/content")) return new Response("文件正文");
    if (url.endsWith("/export")) return new Response("# 聊天记录");
    if (url.endsWith("/logout")) return new Response(null, { status: 204 });
    return Response.json({ id: "ok" });
  });
  assert.equal((await c.api.login("user@example.com", "password")).id, "ok");
  const loginRequest = c.calls.at(-1).init;
  assert.equal(loginRequest.credentials, "same-origin");
  assert.equal(loginRequest.headers["X-Botly-Client"], "web");
  assert.equal(loginRequest.headers["Content-Type"], "application/json");
  assert.equal(await c.api.logout(), undefined);
  assert.equal(await c.api.fileContent("file"), "文件正文");
  assert.equal(await (await c.api.exportChat("chat")).text(), "# 聊天记录");
  for (const upload of [operations.图片上传, operations.群聊文件上传]) {
    assert.equal((await upload(c.api)).id, "ok");
    const init = c.calls.at(-1).init;
    assert.ok(init.body instanceof FormData);
    assert.equal(init.headers["Content-Type"], undefined);
    assert.equal(init.body.get("file").name, "sample.txt");
  }
  assert.equal(c.notices.length, 0);
});

test("推送配置失败显示 detail，退订失败不会误报成功", async () => {
  let unsubscribed = false;
  const reg = { pushManager: { getSubscription: async () => ({
    endpoint: "https://push.example.com/sub",
    unsubscribe: async () => { unsubscribed = true; },
  }) } };
  const browser = {
    Notification: { requestPermission: async () => "granted" },
    navigator: { serviceWorker: { register: async () => reg, getRegistration: async () => reg } },
  };
  const c = client(jsonError(503, "推送服务暂时不可用"), "/settings", browser);
  await assert.rejects(c.subscribePush(), { message: "推送服务暂时不可用" });
  await assert.rejects(c.unsubscribePush(), { message: "推送服务暂时不可用" });
  assert.equal(unsubscribed, false);
});
