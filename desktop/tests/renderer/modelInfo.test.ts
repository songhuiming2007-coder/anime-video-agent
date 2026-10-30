// D91：输入框工具栏的模型 / 推理显示——从帧流取最近一份本会话设置。
import { describe, expect, it } from "vitest";
import type { ConvEntry } from "../../src/shared/protocol";
import { effortText, modelInfo } from "../../src/renderer/SessionHeader";

const frame = (t: string, extra: Record<string, unknown>): ConvEntry =>
  ({ k: "frame", frame: { v: 1, t, seq: 1, sid: "s", ...extra } }) as unknown as ConvEntry;

describe("modelInfo", () => {
  it("没有会话帧 → null；旧 core 的 ready 不带 model 键 → 不算", () => {
    expect(modelInfo([])).toBeNull();
    expect(modelInfo([frame("ready", { scope: "auto" })])).toBeNull();
  });
  it("取后到的那份：ready 之后的 model_config 覆盖它", () => {
    const entries = [
      frame("ready", { model: "flash", reasoning_effort: null, model_source: "default", effort_source: "default" }),
      frame("notice", { level: "info", code: "x", text: "y" }),
      frame("model_config", { model: "kimi-k3", reasoning_effort: "low", model_source: "session", effort_source: "session" }),
    ];
    expect(modelInfo(entries)).toEqual({ model: "kimi-k3", effort: "low", modelSource: "session", effortSource: "session" });
  });
  it("缺配置时 model 为 null；来源只认 session，其余当 default", () => {
    expect(modelInfo([frame("model_config", { model: null, reasoning_effort: null, model_source: "weird", effort_source: "default" })])).toEqual({
      model: null, effort: null, modelSource: "default", effortSource: "default",
    });
  });
});

describe("effortText", () => {
  it("档位 → 中文词；null = 默认；不认识的原样", () => {
    expect([effortText(null), effortText("none"), effortText("xhigh"), effortText("turbo")]).toEqual(["默认", "关", "超高", "turbo"]);
  });
});


// D149/D147：运行中与会话未启动时的可用性与文案（TG-10 下 UI 逻辑抽纯函数测）
import { modelCtlHint, modelCtlUsable, modelPopNote } from "../../src/renderer/SessionHeader";

describe("modelCtlUsable / modelCtlHint / modelPopNote", () => {
  it("none/idle/running 可用；starting/ending/exited 禁（D149：running 放行；D147：none 存 pending）", () => {
    for (const p of ["none", "idle", "running"] as const) expect(modelCtlUsable(p)).toBe(true);
    for (const p of ["starting", "ending", "exited"] as const) expect(modelCtlUsable(p)).toBe(false);
  });
  it("running 提示「下个回合生效」；none 提示存下待发；idle/starting/ending/exited 无覆盖提示", () => {
    expect(modelCtlHint("running")).toContain("下个回合");
    expect(modelCtlHint("none")).toContain("发第一条消息时生效");
    for (const p of ["idle", "starting", "ending", "exited"] as const) expect(modelCtlHint(p)).toBeUndefined();
  });
  it("popover 生效提示行只在 running / none 显示（idle 立即生效不误导）", () => {
    expect(modelPopNote("running")).toContain("本回合结束后");
    expect(modelPopNote("none")).toContain("会话未启动");
    for (const p of ["idle", "starting", "ending", "exited"] as const) expect(modelPopNote(p)).toBeNull();
  });
});

// D156：Shift+Tab 循环档位的纯函数 + 每档描述
import { nextEffort, EFFORT_DESCRIPTIONS } from "../../src/renderer/SessionHeader";

describe("nextEffort（Shift+Tab 循环）", () => {
  const gemini = ["none", "minimal", "low", "medium", "high"]; // gemini-3.8-flash-high 实测集
  it("在实测支持集内循环，绕到末尾回头；null（默认态）起步进第一档", () => {
    expect(nextEffort(gemini, "low")).toBe("medium");
    expect(nextEffort(gemini, "high")).toBe("none"); // 绕回
    expect(nextEffort(gemini, null)).toBe("none"); // 默认 → 第一档
    expect(nextEffort(gemini, "none")).toBe("minimal");
  });
  it("当前档不在支持集里（比如改了模型）→ 从第一档开始", () => {
    expect(nextEffort(gemini, "xhigh")).toBe("none"); // xhigh 不在 gemini 支持集
  });
  it("没实测/只吃 none 的模型（devin）→ 不循环（只有默认一档）", () => {
    expect(nextEffort(["none"], "none")).toBe("none"); // 原地
    expect(nextEffort(null, "high")).toBeNull();
    expect(nextEffort([], null)).toBeNull();
  });
});

describe("EFFORT_DESCRIPTIONS（每档描述）", () => {
  it("七个档都有描述", () => {
    for (const lv of ["none", "minimal", "low", "medium", "high", "xhigh", "max"]) {
      expect(EFFORT_DESCRIPTIONS[lv]).toBeTruthy();
    }
    expect(EFFORT_DESCRIPTIONS.none).toBe("不启用推理");
    expect(EFFORT_DESCRIPTIONS.max).toBe("最大推理");
  });
});
