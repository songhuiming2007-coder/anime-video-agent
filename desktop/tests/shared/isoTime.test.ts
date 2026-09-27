// TV-11：compareIso 的边界（Spec 10 S8-R12 / 🔵-12）。
import { describe, expect, it } from "vitest";
import { compareIso } from "../../src/shared/isoTime";

describe("TV-11 compareIso", () => {
  it("微秒为 0 被省略的时间戳排在 .000001 之前（字符串比较会判反）", () => {
    expect(Math.sign(compareIso("2026-09-25T10:00:00Z", "2026-09-25T10:00:00.000001Z"))).toBe(-1);
    expect(Math.sign(compareIso("2026-09-25T10:00:00.000001Z", "2026-09-25T10:00:00Z"))).toBe(1);
  });
  it("小数秒补足 6 位后按数值相等", () => {
    expect(compareIso("2026-09-25T10:00:00.5Z", "2026-09-25T10:00:00.500000Z")).toBe(0);
    expect(compareIso("2026-09-25T10:00:00.5Z", "2026-09-25T10:00:00.500001Z")).toBeLessThan(0);
  });
  it("同一时刻的不同偏移写法相等", () => {
    expect(compareIso("2026-09-25T10:00:00+00:00", "2026-09-25T10:00:00Z")).toBe(0);
    expect(compareIso("2026-09-25T18:00:00+08:00", "2026-09-25T10:00:00Z")).toBe(0);
  });
  it("常规先后关系", () => {
    expect(compareIso("2026-09-25T10:00:00Z", "2026-09-25T10:00:01Z")).toBeLessThan(0);
    expect(compareIso("2026-09-24T10:00:00Z", "2026-09-25T10:00:00Z")).toBeLessThan(0);
  });
  it("不可解析 → 抛错，不静默退回字符串比较", () => {
    for (const bad of ["", "2026-09-25", "not-a-time", "2026-09-25T10:00:00"]) {
      expect(() => compareIso(bad, "2026-09-25T10:00:00Z"), bad).toThrow();
      expect(() => compareIso("2026-09-25T10:00:00Z", bad), bad).toThrow();
    }
  });
});
