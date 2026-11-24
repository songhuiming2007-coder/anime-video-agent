// VU-2：filterEpisodes / groupByStep（Spec 14 §3.5 / §7.1）。
import { describe, expect, it } from "vitest";
import { filterEpisodes, groupByStep } from "../../src/shared/episodeView";

const EP = (epKey: string, isBlocked: boolean | null, currentStep: string | null = null) => ({ epKey, isBlocked, currentStep });

describe("VU-2 filterEpisodes", () => {
  const list = [EP("E2E-A", true), EP("罪恶王冠-EP07", false), EP("E2E-b", null)];
  it("空查询（含纯空白）原样返回", () => {
    expect(filterEpisodes(list, "")).toEqual(list);
    expect(filterEpisodes(list, "   ")).toEqual(list);
  });
  it("大小写不敏感的子串匹配，保序", () => {
    expect(filterEpisodes(list, "e2e").map((e) => e.epKey)).toEqual(["E2E-A", "E2E-b"]);
    expect(filterEpisodes(list, "王冠").map((e) => e.epKey)).toEqual(["罪恶王冠-EP07"]);
  });
  it("无命中 → 空数组", () => {
    expect(filterEpisodes(list, "不存在的期")).toEqual([]);
  });
});

describe("VU-2 groupByStep", () => {
  it("四组组序与组名固定（含 09 拆入「待发布」）；组内保序", () => {
    const list = [
      EP("a", false, "04 排片"),
      EP("b", true, "02.5 人审改稿"),
      EP("c", null, null),
      EP("d", true, "09 人工发布"),
      EP("e", false, "06 渲染"),
      EP("f", true, "05 审时间码"),
    ];
    const g = groupByStep(list);
    expect(g.map((x) => [x.key, x.label])).toEqual([
      ["stopped", "待你处理"],
      ["running", "进行中"],
      ["ready", "待发布"],
      ["unknown", "状态未取到"],
    ]);
    expect(g[0].items.map((e) => e.epKey)).toEqual(["b", "f"]);
    expect(g[1].items.map((e) => e.epKey)).toEqual(["a", "e"]);
    expect(g[2].items.map((e) => e.epKey)).toEqual(["d"]);
    expect(g[3].items.map((e) => e.epKey)).toEqual(["c"]);
  });
  it("空组省略；null 进「状态未取到」、false 进「进行中」（M15：null 并入 false 组时这里红）", () => {
    const g = groupByStep([EP("a", null), EP("b", false)]);
    expect(g.map((x) => x.key)).toEqual(["running", "unknown"]);
    expect(g[0].items.map((e) => e.epKey)).toEqual(["b"]);
    expect(g[1].items.map((e) => e.epKey)).toEqual(["a"]);
    expect(groupByStep([])).toEqual([]);
  });
});
