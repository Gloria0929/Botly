import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";
import ts from "typescript";

const source = ts.transpileModule(readFileSync(new URL("../lib/cube-connection.ts", import.meta.url), "utf8"),
  { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
const context = vm.createContext({ exports: {}, URL });
vm.runInContext(source, context);
const { withHttpScheme, normalizeCubeAddresses, formatCubeProxy, parseCubeProxy } = context.exports;
const form = { api_url: "cube.test:3000", proxy_address: "203.0.113.10:18080" };

test("代理地址补齐 HTTP 前缀和端口，后端 HTTPS 保留", () => {
  const normalized = normalizeCubeAddresses(form);
  assert.equal(normalized.api_url, "http://cube.test:3000");
  assert.equal(normalized.proxy_address, "http://203.0.113.10:18080");
  assert.equal(withHttpScheme(" https://cube.test:3000/api/ "), "https://cube.test:3000/api");
  assert.equal(withHttpScheme(""), "");
});

test("合并后的代理地址正确拆分成 API 字段，兼容粘贴 URL 和显式默认端口", () => {
  for (const value of ["203.0.113.10:80", "http://203.0.113.10:80/", "https://203.0.113.10:80"]) {
    const normalized = normalizeCubeAddresses({ ...form, proxy_address: value });
    assert.equal(normalized.proxy_address, `${value.startsWith("https:") ? "https" : "http"}://203.0.113.10:80`);
    assert.deepEqual({ ...parseCubeProxy(normalized.proxy_address) }, { proxy_node_ip: "203.0.113.10", proxy_port: 80 });
    assert.deepEqual(normalizeCubeAddresses(normalized), normalized);
  }
  assert.equal(parseCubeProxy("https://203.0.113.10:443").proxy_port, 443);
  assert.equal(parseCubeProxy(form.proxy_address).proxy_port, 18080);
});

test("已有 IPv4 / IPv6 配置合并显示后再次提交不改变 IP 或端口", () => {
  for (const ip of ["203.0.113.10", "2001:db8::1"]) {
    const value = formatCubeProxy(ip, 18080);
    const normalized = normalizeCubeAddresses({ ...form, proxy_address: value });
    assert.deepEqual({ ...parseCubeProxy(normalized.proxy_address) }, { proxy_node_ip: ip, proxy_port: 18080 });
    assert.deepEqual(normalizeCubeAddresses(normalized), normalized);
  }
  assert.equal(formatCubeProxy("2001:db8::1", 18080), "http://[2001:db8::1]:18080");
  assert.equal(normalizeCubeAddresses({ ...form, proxy_address: "http://[2001:db8::1]:18080/" }).proxy_address, "http://[2001:db8::1]:18080");
});

test("只填 IP 补齐默认端口，清空代理也清除旧端口", () => {
  assert.equal(normalizeCubeAddresses({ ...form, proxy_address: "203.0.113.10" }).proxy_address, "http://203.0.113.10:80");
  assert.equal(normalizeCubeAddresses({ ...form, proxy_address: "2001:db8::1" }).proxy_address, "http://[2001:db8::1]:80");
  assert.deepEqual({ ...parseCubeProxy(" ") }, { proxy_node_ip: "", proxy_port: 80 });
  assert.equal(formatCubeProxy("", 18080), "");
});

test("非法代理 IP、路径、账号和端口保留原文，提交时给出明确错误", () => {
  for (const value of ["http://203.0.113.10/admin", "http://203.0.113.10/admin/..", "http://u:p@203.0.113.10",
    "http://203.0.113.10?key=secret", "203.0.113.10:99999", "203.0.113.10:0", "203.0.113.10:",
    "203.0.113.10:abc", "999.0.0.1:80", "proxy.example:80"]) {
    assert.equal(normalizeCubeAddresses({ ...form, proxy_address: value }).proxy_address, value);
    assert.throws(() => parseCubeProxy(value), /IP:端口/);
  }
});
