import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";
import ts from "typescript";

const source = ts.transpileModule(readFileSync(new URL("../lib/fmt.ts", import.meta.url), "utf8"),
  { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
const context = vm.createContext({ exports: {} });
vm.runInContext(source, context);
const { parseTimestamp, elapsedTime } = context.exports;

test("无时区的 UTC 开始时间在各浏览器时区都显示 1 分 47 秒，不多算 8 小时", () => {
  const previous = process.env.TZ;
  try {
    const now = Date.parse("2026-11-05T07:01:47Z");
    for (const timezone of ["Asia/Shanghai", "UTC", "America/Los_Angeles"]) {
      process.env.TZ = timezone;
      assert.equal(elapsedTime("2026-11-05T07:00:00", null, now), "1 分 47 秒", timezone);
    }
  } finally {
    if (previous === undefined) delete process.env.TZ;
    else process.env.TZ = previous;
  }
});

test("时间解析保留 Z、正负时区偏移，并兼容后端微秒格式", () => {
  const expected = Date.parse("2026-11-05T07:00:00.123Z");
  for (const value of ["2026-11-05T07:00:00.123456", "2026-11-05T07:00:00.123Z",
    "2026-11-05T15:00:00.123+08:00", "2026-11-04T23:00:00.123-08:00",
    "2026-11-04T23:00:00.123-0800"]) {
    assert.equal(parseTimestamp(value), expected, value);
  }
});

test("已结束的任务固定使用结束时间，混合时区格式也能正确计算", () => {
  for (const now of ["2026-11-05T08:00:00Z", "2026-11-06T08:00:00Z"]) {
    assert.equal(elapsedTime("2026-11-05T07:00:00", "2026-11-05T15:01:47+08:00", Date.parse(now)), "1 分 47 秒");
  }
});

test("计时只统计完整秒，缺失或无效时间不显示 NaN，时钟提前不出现负数", () => {
  const now = Date.parse("2026-11-05T07:00:59.900Z");
  assert.equal(elapsedTime("2026-11-05T07:00:00", null, now), "59 秒");
  assert.equal(elapsedTime("2026-11-05T07:01:00", null, now), "0 秒");
  for (const value of [undefined, null, "", "invalid"]) {
    assert.equal(elapsedTime(value, null, now), "");
  }
  assert.equal(elapsedTime("2026-11-05T07:00:00", "invalid", now), "");
});
