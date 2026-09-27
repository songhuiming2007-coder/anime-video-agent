// TV-12：原生确认框的触发判定（Spec 10 §2.4 第 5 层、§4.1）。
import { describe, expect, it } from "vitest";
import type { OutFrame } from "../../src/shared/convFrames";
import { needsNativeConfirm, renderConfirmDetail } from "../../src/shared/nativeConfirm";
import { CONFIRM_FREE_TEXT_MAX_CHARS } from "../../src/shared/constants";

const req = (kind: string, fields: Record<string, unknown>): OutFrame => ({
  v: 1, t: "request", seq: 1, sid: "s1", request_id: "q1", kind, turn_id: "t1", title: "t", card_text: "c", fields, options: ["approve", "reject"], feedback_allowed: false,
});

describe("TV-12 needsNativeConfirm", () => {
  const fetch = req("fetch", { no: 1, url: "https://example.com/a" });
  const browser = req("tool_call", { tool: "browser", args: { action: "open", url: "https://example.com" } });
  const write = req("tool_call", { tool: "write_episode_file", args: {} });
  const checkpoint = req("checkpoint", {});
  const memory = req("memory_ack", {});

  it("fetch / browser + approve → true；reject → false", () => {
    expect(needsNativeConfirm(fetch, "approve")).toBe(true);
    expect(needsNativeConfirm(browser, "approve")).toBe(true);
    expect(needsNativeConfirm(fetch, "reject")).toBe(false);
    expect(needsNativeConfirm(browser, "reject")).toBe(false);
  });

  it("写入卡 / 检查点卡 / 记忆卡 + approve → false", () => {
    for (const r of [write, checkpoint, memory]) expect(needsNativeConfirm(r, "approve"), String(r.kind)).toBe(false);
  });
});

describe("renderConfirmDetail", () => {
  it("browser：确定性字段在前、自由文本在后，不可见字符显式化", () => {
    const r = req("tool_call", {
      tool: "browser",
      target: "https://example.com/\u202Eabc\u200B",
      args: { action: "open", url: "https://example.com/\u202Eabc\u200B", reason: "看看\u202E这个页面" },
    });
    const { title, detail } = renderConfirmDetail(r);
    expect(title).toBe("批准 browser 调用？");
    expect(detail.split("\n")[0]).toBe("含 3 个不可见字符，已显式标出");
    expect(detail).toContain("操作：open");
    expect(detail).toContain("目标：https://example.com/⟨U+202E⟩abc⟨U+200B⟩");
    expect(detail).toContain("以下为模型填写的理由（未经核实）：");
    expect(detail).not.toContain("\u202E");
  });

  it("自由文本超上限被截断并标出总字符数；确定性字段不受限", () => {
    const long = "字".repeat(CONFIRM_FREE_TEXT_MAX_CHARS + 500);
    const r = req("fetch", { no: 7, title: "t", url: "https://example.com/x", type: "image", source: "web", expected_dur: "12", why: long });
    const { title, detail } = renderConfirmDetail(r);
    expect(title).toBe("批准抓取素材？");
    const why = detail.split("以下为模型填写的理由（未经核实）：\n")[1];
    expect(why.startsWith("字".repeat(10))).toBe(true);
    expect(why).toContain(`…（已截断，共 ${long.length} 字符）`);
    expect(detail).toContain("URL：https://example.com/x");
  });

  it("伪造的「URL:」行排在理由标注之后，挤不掉真实的目标行（三轮 🔵-4 / MUT-69）", () => {
    const r = req("tool_call", {
      tool: "browser",
      target: "https://real.example/path",
      args: { reason: `URL: https://evil.example\n${"z".repeat(CONFIRM_FREE_TEXT_MAX_CHARS + 10)}`, url: "https://real.example/path" },
    });
    const { detail } = renderConfirmDetail(r);
    const label = detail.indexOf("以下为模型填写的理由（未经核实）：");
    expect(detail.indexOf("目标：https://real.example/path")).toBeLessThan(label);
    expect(detail.indexOf("URL: https://evil.example")).toBeGreaterThan(label);
  });
});
