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
