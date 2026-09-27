// VU-3：theme.ts 的读写纪律（Spec 14 §3.3 / §7.1）。vitest 环境是 node：localStorage / document 一律桩出来。
import { afterEach, describe, expect, it } from "vitest";
import { applyTheme, readEpisodeView, readTheme, saveEpisodeView, saveTheme } from "../../src/renderer/theme";

const g = globalThis as unknown as Record<string, unknown>;

// 用完即摘掉桩（不去读 Node 22 那个「未启用」的 localStorage getter，免得刷无谓告警）
afterEach(() => {
  delete g.localStorage;
  delete g.document;
});

/** 每次访问都抛错的 localStorage（隐私模式 / 被禁用的形状） */
const throwingStorage = () => ({
  getItem: () => {
    throw new Error("denied");
  },
  setItem: () => {
    throw new Error("denied");
  },
});

const memStorage = () => {
  const m = new Map<string, string>();
  return { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v), m };
};

describe("VU-3 theme.ts", () => {
  it("localStorage 抛错时 readTheme() 为 system 且不抛（M32：去掉 try/catch 时这里红）", () => {
    g.localStorage = throwingStorage();
    expect(readTheme()).toBe("system");
    expect(readEpisodeView()).toBe("time");
  });
  it("非法值与缺失一律回默认", () => {
    const s = memStorage();
    g.localStorage = s;
    expect(readTheme()).toBe("system");
    s.setItem("ava.theme", "blue");
    expect(readTheme()).toBe("system");
    s.setItem("ava.episodeView", "weird");
    expect(readEpisodeView()).toBe("time");
  });
  it("saveTheme 抛错时返回 false；正常时返回 true 且可读回", () => {
    g.localStorage = throwingStorage();
    expect(saveTheme("dark")).toBe(false);
    expect(saveEpisodeView("step")).toBe(false);
    g.localStorage = memStorage();
    expect(saveTheme("dark")).toBe(true);
    expect(readTheme()).toBe("dark");
    expect(saveEpisodeView("step")).toBe(true);
    expect(readEpisodeView()).toBe("step");
  });
  it("applyTheme：system 删属性，dark 写属性", () => {
    const attrs = new Map<string, string>();
    g.document = {
      documentElement: {
        setAttribute: (k: string, v: string) => void attrs.set(k, v),
        removeAttribute: (k: string) => void attrs.delete(k),
      },
    };
    applyTheme("dark");
    expect(attrs.get("data-theme")).toBe("dark");
    applyTheme("light");
    expect(attrs.get("data-theme")).toBe("light");
    applyTheme("system");
    expect(attrs.has("data-theme")).toBe(false);
  });
});
