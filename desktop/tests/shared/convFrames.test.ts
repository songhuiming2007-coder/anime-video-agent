// TV-1：出站帧校验（Spec 10 §3.2 规则 3~5）。
import { describe, expect, it } from "vitest";
import { OUT_TYPES, parseOutFrame } from "../../src/shared/convFrames";

const base = { v: 1, seq: 1, sid: "s1" };
const valid: Record<string, Record<string, unknown>> = {
  ready: { episode: "E", scope: "pipeline", continue_status: "new", llm: "ok", degrade_reason: null, code_freeze_ok: true, history_count: 0, session_bytes: 0, other_sessions: [] },
  history: { index: 0, role: "user", text: "hi", name: null },
  turn_started: { turn_id: "t1" },
  assistant: { turn_id: "t1", kind: "answer", text: "ok" },
  tool: { turn_id: "t1", phase: "start", index: 0, name: "read_episode_file", summary: "02-script.md", ok: null, observation: null, duplicate: false },
  request: { request_id: "q1", kind: "tool_call", turn_id: "t1", title: "t", card_text: "c", fields: { tool: "browser" }, options: ["approve", "reject"], feedback_allowed: true },
  request_closed: { request_id: "q1", reason: "answered", decision: "approve" },
  command_result: { name: "scope", ok: true, text: "asset" },
  stop_points: { items: [], turn_id: "t1" },
  turn_finished: { turn_id: "t1", stopped: "done", llm_calls: 1, tool_calls: 1, tool_executions: 1, duplicates_rejected: 0, checkpoints: 0, wrapup: "none", duration_s: 1, prompt_chars: 10, lookups: null },
  log: { stream: "stdout", text: "x" },
  notice: { level: "warn", code: "code_freeze", text: "n" },
  error: { code: "E_BUSY", message: "busy" },
  bye: { reason: "eof" },
};

describe("TV-1 parseOutFrame", () => {
  it("出站闭集每种各一个合法样例通过", () => {
    expect(Object.keys(valid).sort()).toEqual([...OUT_TYPES].sort());
    for (const t of OUT_TYPES) {
      const r = parseOutFrame(JSON.stringify({ ...base, t, ...valid[t] }));
      expect(r.ok, t).toBe(true);
    }
  });

  it("history 的 tool 条目要求 ok；其余角色不要求", () => {
    expect(parseOutFrame(JSON.stringify({ ...base, t: "history", index: 0, role: "tool", text: "x", name: "t", ok: false })).ok).toBe(true);
    expect(parseOutFrame(JSON.stringify({ ...base, t: "history", index: 0, role: "tool", text: "x", name: "t" })).ok).toBe(false);
    expect(parseOutFrame(JSON.stringify({ ...base, t: "history", index: 0, role: "user", text: "x", name: null })).ok).toBe(true);
  });

  it("缺必需键、类型不符、v: 2、未知 t、非对象 → malformed", () => {
    expect(parseOutFrame(JSON.stringify({ ...base, t: "turn_started" }))).toEqual({ ok: false, reason: "malformed" });
    expect(parseOutFrame(JSON.stringify({ ...base, t: "turn_started", turn_id: 7 }))).toEqual({ ok: false, reason: "malformed" });
    expect(parseOutFrame(JSON.stringify({ ...base, v: 2, t: "bye", reason: "x" }))).toEqual({ ok: false, reason: "malformed" });
    expect(parseOutFrame(JSON.stringify({ ...base, t: "no_such", x: 1 }))).toEqual({ ok: false, reason: "malformed" });
    expect(parseOutFrame("[1,2]")).toEqual({ ok: false, reason: "malformed" });
    expect(parseOutFrame("null")).toEqual({ ok: false, reason: "malformed" });
    expect(parseOutFrame("not json")).toEqual({ ok: false, reason: "malformed" });
  });

  it("seq 为超过 2^53 的整数（以文本构造）→ malformed；多出的键被忽略", () => {
    const text = `{"v":1,"t":"bye","seq":9007199254740993,"sid":null,"reason":"x"}`;
    expect(parseOutFrame(text)).toEqual({ ok: false, reason: "malformed" });
    const extra = parseOutFrame(JSON.stringify({ ...base, t: "bye", reason: "x", future: { deep: [1, 2] }, more: "y" }));
    expect(extra.ok).toBe(true);
    if (extra.ok) expect((extra.frame as { future?: unknown }).future).toEqual({ deep: [1, 2] });
  });

  it("sid 允许 null，不允许数字", () => {
    expect(parseOutFrame(JSON.stringify({ v: 1, t: "bye", seq: 1, sid: null, reason: "x" })).ok).toBe(true);
    expect(parseOutFrame(JSON.stringify({ v: 1, t: "bye", seq: 1, sid: 3, reason: "x" })).ok).toBe(false);
  });
});
