// TV-2 / TV-3：对话流折叠（Spec 10 §2.3）。
import { describe, expect, it } from "vitest";
import type { OutFrame } from "../../src/shared/convFrames";
import { foldConv } from "../../src/shared/convFold";
import type { ConvEntry } from "../../src/shared/protocol";

const F = (t: string, o: Record<string, unknown>, seq = 1): OutFrame => ({ v: 1, t: t as OutFrame["t"], seq, sid: "s1", ...o });

const toolStart = (name: string, summary: string, index: number, seq: number) =>
  F("tool", { turn_id: "t1", phase: "start", index, name, summary, ok: null, observation: null, duplicate: false }, seq);
const toolEnd = (name: string, index: number, ok: boolean, seq: number, observation: string | null = null, duplicate = false) =>
  F("tool", { turn_id: "t1", phase: "end", index, name, summary: "", ok, observation, duplicate }, seq);

const request = (id: string, seq: number) =>
  F("request", { request_id: id, kind: "tool_call", turn_id: "t1", title: "写稿", card_text: "…", fields: { tool: "write_episode_file" }, options: ["approve", "reject"], feedback_allowed: true }, seq);

const TURN_FINISHED = () =>
  F("turn_finished", { turn_id: "t1", stopped: "done", llm_calls: 2, tool_calls: 3, tool_executions: 3, duplicates_rejected: 0, checkpoints: 0, wrapup: "none", duration_s: 14, prompt_chars: 100 }, 9);

describe("TV-2 foldConv 一回合", () => {
  const entries: ConvEntry[] = [
    { k: "user", at: 1, text: "把第 3 段改一下" },
    { k: "frame", at: 2, frame: toolStart("read_episode_file", "02-script.md", 0, 1) },
    { k: "frame", at: 3, frame: F("log", { stream: "stdout", text: "line1\nline2" }, 2) },
    { k: "frame", at: 5, frame: toolEnd("read_episode_file", 0, true, 3) },
    { k: "frame", at: 6, frame: toolStart("run_pipeline", "clips 罪恶王冠-EP07", 1, 4) },
    { k: "frame", at: 10, frame: toolEnd("run_pipeline", 1, false, 5, "退出码 1：02-script.md 第 3 段「集」字段缺失，检索掩码无法生效") },
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
    expect(t2.observation).toBe("退出码 1：02-script.md 第 3 段「集」字段缺失，检索掩码无法生效");
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
    expect(rows.map((r) => r.k)).toEqual(["historySeparator", "user", "assistant", "assistant", "tool", "tool"]);
    const note = rows[3];
    if (note.k === "assistant") expect(note.label).toBe("系统注入");
    const failed = rows[4];
    if (failed.k === "tool") expect(failed.observation).toBe("失败原文");
    const okTool = rows[5];
    if (okTool.k === "tool") expect(okTool.observation).toBeNull();
  });
});
