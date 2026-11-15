// D91：输入框工具栏的模型 / 推理显示——从帧流取最近一份本会话设置。
import { describe, expect, it } from "vitest";
import type { AuthStatusResult, ConvEntry, ModelSourceInfo } from "../../src/shared/protocol";
import {
  EFFORT_LABELS,
  clientFamilyEfforts,
  effortBarLevel,
  effortText,
  groupModelsBySource,
  modelInfo,
  resolveModelSource,
  resolveSupportedEfforts,
  shortSourceLabel,
  sourceKindLabel,
} from "../../src/renderer/SessionHeader";

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
    expect(EFFORT_DESCRIPTIONS.max).toBe("最大推理（~64k token）");
    expect(EFFORT_LABELS.max).toBe("最大");
  });
});

describe("D168 resolveSupportedEfforts / effortBarLevel", () => {
  it("优先命中 modelEfforts，其次 directEfforts，否则走家族启发式永不锁死", () => {
    expect(
      resolveSupportedEfforts(
        "gemini-3.8-flash-high",
        { "gemini-3.8-flash-high": ["none", "low", "medium", "high"] },
        ["none", "minimal", "low", "medium", "high", "xhigh", "auto"],
      ),
    ).toEqual(["none", "low", "medium", "high"]);

    expect(resolveSupportedEfforts("devin/gpt-6-luna", null, null)).toEqual([
      "none", "minimal", "low", "medium", "high", "xhigh",
    ]);
    expect(resolveSupportedEfforts("claude-opus-5-5-high", null, null)).toEqual([
      "none", "minimal", "low", "medium", "high", "xhigh", "max",
    ]);
    expect(resolveSupportedEfforts("openrouter/anthropic/claude-opus-4.6", null, null)).toEqual([
      "none", "minimal", "low", "medium", "high", "xhigh", "max",
    ]);
    expect(resolveSupportedEfforts("anthropic/claude-sonnet-4.6", null, null)).toEqual([
      "none", "minimal", "low", "medium", "high", "xhigh", "max",
    ]);
    expect(clientFamilyEfforts("deepseek-r1")).toEqual(["none", "low", "medium", "high"]);
    expect(clientFamilyEfforts(null).length).toBe(7);
  });

  it("effortBarLevel 映射 0..5 信号格", () => {
    expect(effortBarLevel(null)).toBe(0);
    expect(effortBarLevel("none")).toBe(0);
    expect(effortBarLevel("minimal")).toBe(1);
    expect(effortBarLevel("low")).toBe(2);
    expect(effortBarLevel("medium")).toBe(3);
    expect(effortBarLevel("auto")).toBe(3);
    expect(effortBarLevel("high")).toBe(4);
    expect(effortBarLevel("xhigh")).toBe(5);
    expect(effortBarLevel("max")).toBe(5);
  });
});

describe("模型来源识别与分组过滤", () => {
  const sampleSources: Record<string, ModelSourceInfo> = {
    "devin/deepseek-v4-1-flash": {
      source_id: "default:devin",
      source_name: "Devin (默认代理)",
      source_kind: "proxy",
      display_model: "deepseek-v4-1-flash",
      wire_model: "devin/deepseek-v4-1-flash",
      owned_by: "deepseek",
      endpoint: "127.0.0.1:8317",
      channel: "Devin",
    },
    "opencode/deepseek-v4-1-flash": {
      source_id: "opencode",
      source_name: "OpenCode Zen",
      source_kind: "api_key",
      display_model: "deepseek-v4-1-flash",
      wire_model: "deepseek-v4-1-flash",
      endpoint: "opencode.ai",
    },
    "opencode-go/deepseek-v4-1-flash": {
      source_id: "opencode-go",
      source_name: "OpenCode Go",
      source_kind: "api_key",
      display_model: "deepseek-v4-1-flash",
      wire_model: "deepseek-v4-1-flash",
      endpoint: "opencode.ai",
    },
    "deepseek/deepseek-v4-1-flash": {
      source_id: "deepseek",
      source_name: "DeepSeek",
      source_kind: "api_key",
      display_model: "deepseek-v4-1-flash",
      wire_model: "deepseek-v4-1-flash",
      owned_by: "deepseek",
      endpoint: "api.deepseek.com",
    },
    "claude-opus-5-5-high": {
      source_id: "cliproxyapi",
      source_name: "CLIProxyAPI",
      source_kind: "proxy",
      display_model: "claude-opus-5-5-high",
      wire_model: "claude-opus-5-5-high",
      owned_by: "antigravity",
      endpoint: "127.0.0.1:8317",
      channel: "Antigravity",
    },
  };

  it("shortSourceLabel 与 sourceKindLabel 生成简洁标签", () => {
    expect(shortSourceLabel(sampleSources["devin/deepseek-v4-1-flash"])).toBe("Devin · 代理");
    expect(shortSourceLabel(sampleSources["claude-opus-5-5-high"])).toBe("CLIProxy · Antigravity");
    expect(shortSourceLabel(sampleSources["opencode/deepseek-v4-1-flash"])).toBe("OpenCode Zen");
    expect(sourceKindLabel("proxy")).toBe("代理");
    expect(sourceKindLabel("api_key")).toBe("API Key");
    expect(sourceKindLabel("oauth_pkce")).toBe("OAuth");
    expect(sourceKindLabel("config")).toBe("配置");
  });

  it("resolveModelSource 优先使用后端 modelSources / activeModelSource，否则回退 authStatus / 前缀", () => {
    const authStatus: AuthStatusResult = {
      ok: true,
      auth_file: "config/auth.local.json",
      connected_count: 1,
      providers: [
        {
          id: "deepseek",
          name: "DeepSeek",
          desc: "DeepSeek 官方 API",
          category: "flagship",
          auth_modes: ["api_key"],
          default_base_url: "https://api.deepseek.com/v1",
          api_protocol: "openai",
          supports_custom_base_url: true,
          connected: true,
          auth_type: "api_key",
          base_url: "https://api.deepseek.com/v1",
          masked_secret: "sk-****",
          expires_at_ms: null,
          models: ["deepseek-chat", "deepseek-reasoner"],
          default_models: ["deepseek-chat"],
        },
      ],
    };

    const exact = resolveModelSource("devin/deepseek-v4-1-flash", sampleSources, authStatus);
    expect(exact).toMatchObject({
      id: "default:devin",
      label: "Devin (默认代理)",
      shortLabel: "Devin · 代理",
      displayModel: "deepseek-v4-1-flash",
      kindLabel: "代理",
      channel: "Devin",
    });

    const active = resolveModelSource(
      "deepseek/deepseek-v4-1-flash",
      null,
      authStatus,
      sampleSources["deepseek/deepseek-v4-1-flash"],
    );
    expect(active).toMatchObject({
      id: "deepseek",
      label: "DeepSeek",
      shortLabel: "DeepSeek",
      displayModel: "deepseek-v4-1-flash",
    });

    const fallbackAuth = resolveModelSource("deepseek/deepseek-reasoner", null, authStatus);
    expect(fallbackAuth).toMatchObject({
      id: "deepseek",
      label: "DeepSeek",
      kindLabel: "API Key",
      displayModel: "deepseek-reasoner",
    });

    const fallbackPrefix = resolveModelSource("devin/custom-model", null, null);
    expect(fallbackPrefix).toMatchObject({
      id: "default:devin",
      label: "Devin (默认代理)",
      shortLabel: "Devin · 代理",
      displayModel: "custom-model",
    });
  });

  it("groupModelsBySource 区分同名模型并支持多关键字搜索和来源过滤", () => {
    const models = Object.keys(sampleSources);
    const authStatus: AuthStatusResult = {
      ok: true,
      auth_file: "config/auth.local.json",
      connected_count: 3,
      providers: [
        {
          id: "opencode",
          name: "OpenCode Zen",
          desc: "",
          auth_modes: ["api_key"],
          default_base_url: "https://opencode.ai/zen/v1",
          api_protocol: "openai",
          supports_custom_base_url: false,
          connected: true,
          auth_type: "api_key",
          base_url: "https://opencode.ai/zen/v1",
          masked_secret: null,
          expires_at_ms: null,
          models: [],
          default_models: [],
        },
        {
          id: "opencode-go",
          name: "OpenCode Go",
          desc: "",
          auth_modes: ["api_key"],
          default_base_url: "https://opencode.ai/zen/go/v1",
          api_protocol: "openai",
          supports_custom_base_url: false,
          connected: true,
          auth_type: "api_key",
          base_url: "https://opencode.ai/zen/go/v1",
          masked_secret: null,
          expires_at_ms: null,
          models: [],
          default_models: [],
        },
        {
          id: "deepseek",
          name: "DeepSeek",
          desc: "",
          auth_modes: ["api_key"],
          default_base_url: "https://api.deepseek.com/v1",
          api_protocol: "openai",
          supports_custom_base_url: false,
          connected: true,
          auth_type: "api_key",
          base_url: "https://api.deepseek.com/v1",
          masked_secret: null,
          expires_at_ms: null,
          models: [],
          default_models: [],
        },
        {
          id: "cliproxyapi",
          name: "CLIProxyAPI",
          desc: "",
          auth_modes: ["proxy"],
          default_base_url: "http://127.0.0.1:8317/v1",
          api_protocol: "openai",
          supports_custom_base_url: true,
          connected: true,
          auth_type: "proxy",
          base_url: "http://127.0.0.1:8317/v1",
          masked_secret: null,
          expires_at_ms: null,
          models: [],
          default_models: [],
        },
      ],
    };

    const initial = groupModelsBySource(models, sampleSources, authStatus, "", null);
    expect(initial.allGroups.map((g) => g.label)).toEqual([
      "OpenCode Zen",
      "OpenCode Go",
      "DeepSeek",
      "CLIProxyAPI",
      "Devin (默认代理)",
    ]);
    expect(initial.filteredGroups).toHaveLength(5);

    const searchByNameAndSource = groupModelsBySource(
      models,
      sampleSources,
      authStatus,
      "deepseek zen",
      null,
    );
    expect(searchByNameAndSource.filteredGroups).toHaveLength(1);
    expect(searchByNameAndSource.filteredGroups[0].label).toBe("OpenCode Zen");
    expect(searchByNameAndSource.filteredGroups[0].items.map((x) => x.id)).toEqual([
      "opencode/deepseek-v4-1-flash",
    ]);

    const filteredByPill = groupModelsBySource(
      models,
      sampleSources,
      authStatus,
      "",
      "default:devin",
    );
    expect(filteredByPill.allGroups).toHaveLength(5);
    expect(filteredByPill.filteredGroups).toHaveLength(1);
    expect(filteredByPill.filteredGroups[0].items[0].id).toBe("devin/deepseek-v4-1-flash");
  });
});
