import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";
import ts from "typescript";

function module(path, process = {}) {
  const source = ts.transpileModule(readFileSync(new URL(path, import.meta.url), "utf8"),
    { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
  const context = vm.createContext({ exports: {}, process });
  vm.runInContext(source, context);
  return context.exports;
}

const { isSafeMarkdownImage } = module("../lib/markdown-images.ts");
const id = "a".repeat(32);

test("消息图片仅自动加载鉴权文件接口，阻止外链和路径穿越", () => {
  assert.equal(isSafeMarkdownImage(`/api/uploads/${id}`), true);
  assert.equal(isSafeMarkdownImage(`/api/files/${id}/preview`), true);
  for (const source of ["https://attacker.example/?q=secret", "//attacker.example/image", "data:image/svg+xml,test",
    "/api/../redirect", "/api/\\attacker.example", "/api/auth/oauth/google/start",
    `/api/files/${id}/preview?redirect=https://attacker.example`, "/api/%2e%2e/redirect", undefined]) {
    assert.equal(isSafeMarkdownImage(source), false, String(source));
  }
});

test("直接访问 Next 的生产响应也带 CSP，开发模式保留热更新", async () => {
  for (const environment of ["production", "development"]) {
    const config = module("../next.config.ts", { env: { NODE_ENV: environment } }).default;
    assert.equal(config.distDir, environment === "development" ? ".next-dev" : ".next");
    const rules = await config.headers();
    const headers = new Map(rules[0].headers.map((h) => [h.key, h.value]));
    const policy = headers.get("Content-Security-Policy");
    assert.ok(policy.includes("img-src 'self' data:"));
    assert.ok(policy.includes("frame-ancestors 'none'"));
    assert.equal(policy.includes("'unsafe-eval'"), environment === "development");
    assert.equal(headers.get("X-Content-Type-Options"), "nosniff");
    assert.equal(headers.get("Referrer-Policy"), "no-referrer");
  }
});
