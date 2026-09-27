// VU-2：filterEpisodes / groupByStep（Spec 14 §3.5 / §7.1）。
import { describe, expect, it } from "vitest";
import { filterEpisodes, groupByStep } from "../../src/shared/episodeView";

const EP = (epKey: string, isBlocked: boolean | null) => ({ epKey, isBlocked });

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
  it("三组组序与组名固定；组内保序", () => {
    const list = [EP("a", false), EP("b", true), EP("c", null), EP("d", true), EP("e", false)];
    const g = groupByStep(list);
    expect(g.map((x) => [x.key, x.label])).toEqual([
      ["stopped", "停机点"],
      ["running", "非停机点"],
      ["unknown", "未取到"],
    ]);
    expect(g[0].items.map((e) => e.epKey)).toEqual(["b", "d"]);
    expect(g[1].items.map((e) => e.epKey)).toEqual(["a", "e"]);
    expect(g[2].items.map((e) => e.epKey)).toEqual(["c"]);
  });
  it("空组省略；null 进「未取到」、false 进「非停机点」（M15：null 并入 false 组时这里红）", () => {
    const g = groupByStep([EP("a", null), EP("b", false)]);
    expect(g.map((x) => x.key)).toEqual(["running", "unknown"]);
    expect(g[0].items.map((e) => e.epKey)).toEqual(["b"]);
    expect(g[1].items.map((e) => e.epKey)).toEqual(["a"]);
    expect(groupByStep([])).toEqual([]);
  });
});
