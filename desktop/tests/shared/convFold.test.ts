// TV-2 / TV-3：对话流折叠（Spec 10 §2.3）。
import { describe, expect, it } from "vitest";
import type { OutFrame } from "../../src/shared/convFrames";
import { charsText, foldConv, lastContextReading, lookupsText, tokensText } from "../../src/shared/convFold";
import type { ConvEntry } from "../../src/shared/protocol";

const F = (t: string, o: Record<string, unknown>, seq = 1): OutFrame => ({ v: 1, t: t as OutFrame["t"], seq, sid: "s1", ...o });

const toolStart = (name: string, summary: string, index: number, seq: number) =>
  F("tool", { turn_id: "t1", phase: "start", index, name, summary, ok: null, observation: null, duplicate: false }, seq);
// 失败原文刻意超过 500 字：observation 逐字不截断（判据 4）。M9：此前原文很短，「截断到 500 字」的
// 变异（MUT-32）对它毫无影响，TV-2 抓不住，只剩真实 core 的 TX-1 在守
const LONG_OBS = `退出码 1：02-script.md 第 3 段「集」字段缺失，检索掩码无法生效\n${"stderr 尾部：Traceback 第 N 行…\n".repeat(40)}`;
const toolEnd = (name: string, index: number, ok: boolean, seq: number, observation: string | null = null, duplicate = false) =>
  F("tool", { turn_id: "t1", phase: "end", index, name, summary: "", ok, observation, duplicate }, seq);

const request = (id: string, seq: number) =>
  F("request", { request_id: id, kind: "tool_call", turn_id: "t1", title: "写稿", card_text: "…", fields: { tool: "write_episode_file" }, options: ["approve", "reject"], feedback_allowed: true }, seq);

const TURN_FINISHED = () =>
  F("turn_finished", { turn_id: "t1", stopped: "done", llm_calls: 2, tool_calls: 3, tool_executions: 3, duplicates_rejected: 0, checkpoints: 0, wrapup: "none", duration_s: 14, prompt_chars: 100, lookups: null, prompt_tokens: null }, 9);

describe("TV-2 foldConv 一回合", () => {
  const entries: ConvEntry[] = [
    { k: "user", at: 1, text: "把第 3 段改一下" },
    { k: "frame", at: 2, frame: toolStart("read_episode_file", "02-script.md", 0, 1) },
    { k: "frame", at: 3, frame: F("log", { stream: "stdout", text: "line1\nline2" }, 2) },
    { k: "frame", at: 5, frame: toolEnd("read_episode_file", 0, true, 3) },
    { k: "frame", at: 6, frame: toolStart("run_pipeline", "clips 罪恶王冠-EP07", 1, 4) },
    { k: "frame", at: 10, frame: toolEnd("run_pipeline", 1, false, 5, LONG_OBS) },
    { k: "frame", at: 11, frame: F("assistant", { turn_id: "t1", kind: "answer", text: "第 3 段缺了「集」字段。" }, 6) },
    { k: "frame", at: 12, frame: request("q1", 7) },
    { k: "frame", at: 14, frame: F("request_closed", { request_id: "q1", reason: "answered", decision: "reject" }, 8) },
    { k: "answered_local", at: 14, requestId: "q1", decision: "reject", feedback: "改成第三人称" },
    { k: "frame", at: 15, frame: TURN_FINISHED() },
  ];
  const { rows, running, turnId } = foldConv(entries);

  it("行序列逐项相等", () => {
    expect(rows.map((r) => r.k)).toEqual(["user", "tool", "tool", "assistant", "card", "footer"]);
    expect(running).toBe(false);
    expect(turnId).toBeNull();
  });

  it("工具行的摘要 / 成功 / 耗时 / 作业输出", () => {
    const [t1, t2] = rows.filter((r) => r.k === "tool");
    expect(t1).toMatchObject({ name: "read_episode_file", ok: true, durationMs: 3, observation: null });
    expect(t1.logs).toEqual(["line1", "line2"]); // log 挂在第一个工具行下
    expect(t2).toMatchObject({ name: "run_pipeline", ok: false, durationMs: 4 });
    // 失败原文逐字节保留（判据 4）
    expect(t2.observation).toBe(LONG_OBS);
  });

  it("助手回复与卡片留痕", () => {
    const a = rows[3];
    expect(a.k).toBe("assistant");
    if (a.k === "assistant") expect(a.text).toBe("第 3 段缺了「集」字段。");
    const card = rows[4];
    expect(card.k).toBe("card");
    if (card.k === "card") {
      expect(card.status).toBe("rejected");
      expect(card.text).toBe("[工具卡] write_episode_file · 已拒绝（附反馈：改成第三人称）");
    }
    const foot = rows[5];
    if (foot.k === "footer") expect(foot.text).toContain("模型调用 2 · 工具 3（执行 3、重复拒绝 0）");
  });
});

describe("TV-3 foldConv 缺口与作废", () => {
  it("frames_lost / 孤立 tool{end} / voided_local 各自显式成行且不崩", () => {
    const entries: ConvEntry[] = [
      { k: "frames_lost", at: 1, count: 2, reason: "oversize" },
      { k: "frame", at: 2, frame: toolEnd("read_episode_file", 0, false, 1, "被拒") }, // 截头后的孤立 end
      { k: "frame", at: 3, frame: request("q1", 2) },
      { k: "voided_local", at: 4, requestIds: ["q1"], cause: "session_exited" },
    ];
    const { rows } = foldConv(entries);
    expect(rows.map((r) => r.k)).toEqual(["framesLost", "tool", "card"]);
    const lost = rows[0];
    if (lost.k === "framesLost") expect([lost.count, lost.reason]).toEqual([2, "oversize"]);
    const tool = rows[1];
    if (tool.k === "tool") expect(tool.observation).toBe("被拒");
    const card = rows[2];
    if (card.k === "card") {
      expect(card.status).toBe("voided");
      expect(card.text).toBe("[工具卡] write_episode_file · 已作废（会话已结束）");
    }
  });

  it("无 voided_local 的 request_closed{reason:'voided'} 显示「已作废」不带原因", () => {
    const entries: ConvEntry[] = [
      { k: "frame", at: 1, frame: request("q1", 1) },
      { k: "frame", at: 2, frame: F("request_closed", { request_id: "q1", reason: "voided", decision: null }, 2) },
    ];
    const { rows } = foldConv(entries);
    const card = rows[0];
    if (card.k === "card") expect(card.text).toBe("[工具卡] write_episode_file · 已作废");
  });

  it("history 首条前插分隔；system_note 折叠为「系统注入」；tool 条目按 ok 判显示", () => {
    const entries: ConvEntry[] = [
      { k: "frame", at: 1, frame: F("history", { index: 0, role: "user", text: "旧消息", name: null }, 1) },
      { k: "frame", at: 2, frame: F("history", { index: 1, role: "assistant", text: "旧回复", name: null }, 2) },
      { k: "frame", at: 3, frame: F("history", { index: 2, role: "system_note", text: "注入", name: null }, 3) },
      { k: "frame", at: 4, frame: F("history", { index: 3, role: "tool", text: "失败原文", name: "run_pipeline", ok: false }, 4) },
      { k: "frame", at: 5, frame: F("history", { index: 4, role: "tool", text: "", name: "read_episode_file", ok: true }, 5) },
    ];
    const { rows } = foldConv(entries);
    expect(rows.map((r) => r.k)).toEqual(["historySeparator", "user", "assistant", "injection", "tool", "tool"]);
    // D57：系统注入单独成行（渲染为默认收起的 <details>），不再混进 assistant 平铺正文
    expect(rows[3]).toEqual({ k: "injection", at: 3, text: "注入" });
    const failed = rows[4];
    if (failed.k === "tool") expect(failed.observation).toBe("失败原文");
    const okTool = rows[5];
    if (okTool.k === "tool") expect(okTool.observation).toBeNull();
  });
});

// D48 ①：回合脚注照实列出本回合查证调用（0 也列），不判断模型编没编
describe("D48 ① 查证计数", () => {
  const fin = (lookups: unknown) =>
    F("turn_finished", { turn_id: "t1", stopped: "done", llm_calls: 1, tool_calls: 0, tool_executions: 0, duplicates_rejected: 0, checkpoints: 0, wrapup: "none", duration_s: 1, prompt_chars: 9192, lookups }, 1);
  const foot = (lookups: unknown) => {
    const r = foldConv([{ k: "frame", at: 1, frame: fin(lookups) }]).rows.at(-1)!;
    return r.k === "footer" ? r.text : "";
  };

  it("四类按 字幕 · 在场 · 笔记 · 网页 列出，0 也列，排在上下文读数之前", () => {
    expect(foot({ subs: 0, presence: 1, notes: 2, web: 0 }).endsWith(" · done · none · 查证 字幕 0 · 在场 1 · 笔记 2 · 网页 0 · 上下文约 9,192 字")).toBe(true);
  });

  it("旧 core（null / 缺键）或形状不对：不显示，也不影响其余脚注", () => {
    for (const bad of [null, undefined, [], "x", { subs: 1 }, { subs: -1, presence: 0, notes: 0, web: 0 }, { subs: 1.5, presence: 0, notes: 0, web: 0 }]) {
      expect(lookupsText(bad)).toBeNull();
      expect(foot(bad)).not.toContain("查证");
      expect(foot(bad)).toContain("上下文约 9,192 字");
    }
  });
});

// D41 → D56：上下文用量读数（只显示，不压缩）。口径 = 服务商返回的输入 token；拿不到回落消息正文字数。
describe("D41 / D56 上下文用量读数", () => {
  const ready = (seq: number) => F("ready", { episode: "E", scope: "creative", continue_status: "new", llm: "ok", degrade_reason: null }, seq);

  it("charsText：万以下千分位整数，万及以上一位小数的「万字」", () => {
    expect(charsText(0)).toBe("0 字");
    expect(charsText(999)).toBe("999 字");
    expect(charsText(9192)).toBe("9,192 字");
    expect(charsText(9999)).toBe("9,999 字");
    expect(charsText(10_000)).toBe("1.0 万字");
    expect(charsText(14_031)).toBe("1.4 万字");
    expect(charsText(123_456)).toBe("12.3 万字");
  });

  it("D56 tokensText：<1000 整数，其余一位小数的 k", () => {
    expect(tokensText(0)).toBe("0 token");
    expect(tokensText(999)).toBe("999 token");
    expect(tokensText(1000)).toBe("1.0k token");
    expect(tokensText(12_345)).toBe("12.3k token");
    expect(tokensText(128_000)).toBe("128.0k token");
  });

  // D56：服务商给了 prompt_tokens 就显示 token；没给（null / 旧 core 缺键）回落「约 N 字」
  const finT = (tokens: unknown, chars: unknown, seq: number) =>
    F("turn_finished", { turn_id: "t1", stopped: "done", llm_calls: 1, tool_calls: 0, tool_executions: 0, duplicates_rejected: 0, checkpoints: 0, wrapup: "none", duration_s: 1, prompt_chars: chars, prompt_tokens: tokens }, seq);

  it("回合脚注：有 token 显示「上下文 12.3k token」，没有回落「上下文约 N 字」，都没有不追加", () => {
    const foot = (tokens: unknown, chars: unknown) => {
      const r = foldConv([{ k: "frame", at: 1, frame: finT(tokens, chars, 1) }]).rows.at(-1)!;
      return r.k === "footer" ? r.text : "";
    };
    expect(foot(12_345, 14_031).endsWith(" · done · none · 上下文 12.3k token")).toBe(true);
    expect(foot(null, 14_031).endsWith(" · done · none · 上下文约 1.4 万字")).toBe(true);
    expect(foot(undefined, 9192).endsWith(" · 上下文约 9,192 字")).toBe(true);
    for (const bad of [-1, 1.5, "100", Number.MAX_SAFE_INTEGER + 2]) expect(foot(bad, 9192).endsWith(" · 上下文约 9,192 字")).toBe(true);
    for (const bad of [undefined, null, -1, 1.5, "100"]) expect(foot(bad, bad)).not.toContain("上下文");
  });

  it("lastContextReading：取当前会话最近一次回合结束的读数；新 ready 之后清零重计", () => {
    expect(lastContextReading([])).toBeNull();
    const a: ConvEntry[] = [
      { k: "frame", at: 1, frame: ready(1) },
      { k: "frame", at: 2, frame: finT(2000, 3000, 2) },
      { k: "frame", at: 3, frame: finT(4000, 5000, 3) },
    ];
    expect(lastContextReading(a)).toEqual({ tokens: 4000, chars: 5000 });
    // 两样都不合法的帧不覆盖上一次的读数
    expect(lastContextReading([...a, { k: "frame", at: 4, frame: finT("x", "x", 4) }])).toEqual({ tokens: 4000, chars: 5000 });
    // 服务商这回没给 token：读数跟着最近一回合，回落字数
    expect(lastContextReading([...a, { k: "frame", at: 4, frame: finT(null, 6000, 4) }])).toEqual({ tokens: null, chars: 6000 });
    // 结束会话后起的新会话：还没有回合结束 → null，不沿用旧会话的读数
    expect(lastContextReading([...a, { k: "frame", at: 5, frame: ready(1) }])).toBeNull();
    expect(lastContextReading([...a, { k: "frame", at: 5, frame: ready(1) }, { k: "frame", at: 6, frame: finT(700, 800, 2) }])).toEqual({ tokens: 700, chars: 800 });
  });
});
