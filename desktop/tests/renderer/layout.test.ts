// D39 S2：分栏布局纯函数。期望值先手算（见各行注释）再跑。
import { describe, expect, test } from "vitest";
import { clampLeft, DEFAULT_LAYOUT, effectivePreviewW, gridColumns, nudge, parseLayout, previewMax, serializeLayout, type Layout } from "../../src/renderer/layout";

const open = (over: Partial<Layout> = {}): Layout => ({ ...DEFAULT_LAYOUT, previewOpen: true, ...over });

describe("默认布局（人 2026-09-29：方案 B、预览默认收起）", () => {
  test("左栏 240 + 手柄 8 + 对话 1fr + 预览窄条 40", () => {
    expect(gridColumns(1276, DEFAULT_LAYOUT)).toBe("240px 8px minmax(0, 1fr) 40px");
  });
  test("左栏收起：只剩对话与窄条", () => {
    expect(gridColumns(1276, { ...DEFAULT_LAYOUT, leftOpen: false })).toBe("minmax(0, 1fr) 40px");
  });
});

describe("预览展开宽度", () => {
  test("未拖过：左栏右侧宽度的 40%（1276 视口内容宽：(1276-248)*0.4 = 411.2 → 411）", () => {
    expect(effectivePreviewW(1276, open())).toBe(411);
    expect(gridColumns(1276, open())).toBe("240px 8px minmax(0, 1fr) 8px 411px");
  });
  test("拖过的值原样用（在上下限内）", () => {
    expect(effectivePreviewW(1436, open({ previewW: 500 }))).toBe(500);
  });
  test("上限保住对话列 360：1276-248-8-360 = 660", () => {
    expect(previewMax(1276, open())).toBe(660);
    expect(effectivePreviewW(1276, open({ previewW: 9999 }))).toBe(660);
  });
  test("下限 320", () => {
    expect(effectivePreviewW(1276, open({ previewW: 10 }))).toBe(320);
  });
  test("窗口太窄放不下对话下限时，预览退到自己的下限而不是负数（800-248-8-360 = 184 < 320）", () => {
    expect(previewMax(800, open())).toBe(320);
    expect(effectivePreviewW(800, open())).toBe(320);
  });
  test("左栏收起后上限随之放宽：1276-0-8-360 = 908", () => {
    expect(previewMax(1276, open({ leftOpen: false }))).toBe(908);
  });
});

describe("左栏宽度夹紧", () => {
  test("200–400，取整", () => {
    expect(clampLeft(150)).toBe(200);
    expect(clampLeft(999)).toBe(400);
    expect(clampLeft(300.6)).toBe(301);
  });
});

describe("手柄键盘（不做键盘自动化，D37；这里只验纯函数）", () => {
  test("左栏手柄（dir=1）：→ 变宽 16、← 变窄 16", () => {
    expect(nudge(240, "ArrowRight", 200, 400, 1)).toBe(256);
    expect(nudge(240, "ArrowLeft", 200, 400, 1)).toBe(224);
  });
  test("预览手柄在预览左侧（dir=-1）：← 让预览变宽", () => {
    expect(nudge(411, "ArrowLeft", 320, 660, -1)).toBe(427);
    expect(nudge(411, "ArrowRight", 320, 660, -1)).toBe(395);
  });
  test("夹在上下限内；Home / End 直达", () => {
    expect(nudge(392, "ArrowRight", 200, 400, 1)).toBe(400);
    expect(nudge(240, "Home", 200, 400, 1)).toBe(200);
    expect(nudge(240, "End", 200, 400, 1)).toBe(400);
  });
  test("其余键不处理（返回 null，调用方不 preventDefault，Tab 照常移焦）", () => {
    expect(nudge(240, "Tab", 200, 400, 1)).toBeNull();
    expect(nudge(240, "Enter", 200, 400, 1)).toBeNull();
  });
});

describe("持久化读回（D39 S3：坏数据回落默认，永不抛错）", () => {
  const saved: Layout = { leftOpen: false, leftW: 312, previewOpen: true, previewW: 520, filesOpen: false };
  test("存了什么读回什么", () => {
    expect(parseLayout(serializeLayout(saved))).toEqual(saved);
    expect(parseLayout(serializeLayout(DEFAULT_LAYOUT))).toEqual(DEFAULT_LAYOUT);
  });
  test("整份不可用 → 整份默认：缺失 / 截断的 JSON / 非对象 / 数组 / 版本不对 / 没有版本", () => {
    for (const raw of [null, "", '{"v":1,"leftW":3', "garbage", "null", "42", '"x"', "[]", JSON.stringify({ ...saved, v: 2 }), JSON.stringify(saved)]) {
      expect(parseLayout(raw), String(raw)).toEqual(DEFAULT_LAYOUT);
    }
  });
  test("个别字段坏 → 只那个字段取默认，其余照用", () => {
    const bad = { v: 1, leftOpen: "no", leftW: "wide", previewOpen: true, previewW: "big", filesOpen: 0 };
    expect(parseLayout(JSON.stringify(bad))).toEqual({ ...DEFAULT_LAYOUT, previewOpen: true });
  });
  test("数值越界按上下限夹紧：左栏 9999 → 400、-5 → 200；预览 10 → 320、1e9 → 4000", () => {
    expect(parseLayout(JSON.stringify({ ...saved, v: 1, leftW: 9999, previewW: 10 }))).toMatchObject({ leftW: 400, previewW: 320 });
    expect(parseLayout(JSON.stringify({ ...saved, v: 1, leftW: -5, previewW: 1e9 }))).toMatchObject({ leftW: 200, previewW: 4000 });
  });
  test("previewW 为 null（人没拖过）原样保留，仍按窗口比例算", () => {
    expect(parseLayout(JSON.stringify({ ...saved, v: 1, previewW: null })).previewW).toBeNull();
  });
});
