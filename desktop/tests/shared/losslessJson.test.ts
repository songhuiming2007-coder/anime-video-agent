// §3.1 规则 8/9 的单元层（TE-13 ⑤⑥⑦ 的解析部分；通路部分见 tests/host/lossless.test.ts）
import { describe, expect, it } from "vitest";
import { losslessSelfCheck, parseLossless, stringifyLossless, toWireApproval, toWireEvent } from "../../src/shared/losslessJson";
import type { ApprovalRecord } from "../../src/shared/contracts";

describe("parseLossless", () => {
  it("mtime_ns 取原文为 bigint，普通 JSON.parse 会丢精度", () => {
    const text = '{"mtime_ns":1790171112636927676,"size":12}';
    expect(parseLossless(text)).toEqual({ mtime_ns: 1790171112636927676n, size: 12 });
    expect((JSON.parse(text) as { mtime_ns: number }).mtime_ns).toBe(1790171112636927700);
  });
  it("嵌套数组里的 mtime_ns 也无损；-1（缺失指纹）为 -1n", () => {
    expect(parseLossless('[{"artifacts":[{"mtime_ns":-1},{"mtime_ns":9007199254740993}]}]')).toEqual([
      { artifacts: [{ mtime_ns: -1n }, { mtime_ns: 9007199254740993n }] },
    ]);
  });
  it("⑥ 1.79e18 这类非整数字面量 → 整份解析失败，不回退", () => {
    expect(() => parseLossless('{"mtime_ns":1.79e18}')).toThrow();
    expect(() => parseLossless('{"mtime_ns":1790171112636927676.0}')).toThrow();
  });
  it("⑦ 字符串或 null → 整份解析失败（形状一变就响亮失败）", () => {
    expect(() => parseLossless('{"mtime_ns":"1790171112636927676"}')).toThrow();
    expect(() => parseLossless('{"mtime_ns":null}')).toThrow();
    expect(() => parseLossless('{"mtime_ns":{"x":1}}')).toThrow();
  });
  it("其他键不受影响", () => {
    expect(parseLossless('{"size":1790171112636927676}')).toEqual({ size: 1790171112636927700 });
  });
});

describe("losslessSelfCheck", () => {
  it("当前运行时通过", () => {
    expect(losslessSelfCheck()).toBe(true);
  });
  it("⑤ 注入忽略 ctx.source 的解析器 → false", () => {
    const lossy = (t: string) => JSON.parse(t, (k, v) => (k === "mtime_ns" ? BigInt(v) : v));
    expect(losslessSelfCheck(lossy)).toBe(false);
    expect(losslessSelfCheck(() => { throw new TypeError("no ctx"); })).toBe(false);
  });
});

describe("toWire* 与 stringifyLossless（规则 9）", () => {
  const rec: ApprovalRecord = {
    approval_id: "appr_1_abcd", episode: "EP", type: "05", status: "pending",
    artifacts: [{ path: "04-clips.json", size: 10, mtime_ns: 1790171112636927676n }],
    options: ["approve", "reject"], created_at: "2026-09-24T00:00:00.000000Z", resolved_at: null, resolved_by: null,
    confirmed_by: null, confirmed_at: null, feedback: null, note: "",
  };
  it("toWireApproval 把 bigint 转十进制字符串，结构化克隆安全", () => {
    const w = toWireApproval(rec);
    expect(w.artifacts[0].mtime_ns).toBe("1790171112636927676");
    expect(() => structuredClone(w)).not.toThrow();
    expect(() => JSON.stringify(w)).not.toThrow();
  });
  it("toWireEvent 深度转换 payload 里的 bigint", () => {
    const w = toWireEvent({ event_id: "e", timestamp: "t", episode: "EP", type: "approval_requested", kind: "approval_requested",
      payload: { artifacts: [{ path: "a", size: 1, mtime_ns: 5n }] } });
    expect(w.payload).toEqual({ artifacts: [{ path: "a", size: 1, mtime_ns: "5" }] });
  });
  it("stringifyLossless 输出十进制数字字面量，可被 parseLossless 还原", () => {
    const s = stringifyLossless({ mtime_ns: 1790171112636927676n });
    expect(s).toBe('{"mtime_ns":1790171112636927676}');
    expect(parseLossless(s)).toEqual({ mtime_ns: 1790171112636927676n });
  });
});
