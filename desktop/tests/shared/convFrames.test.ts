// TV-1：出站帧校验（Spec 10 §3.2 规则 3~5）。
import { describe, expect, it } from "vitest";
import { OUT_TYPES, parseOutFrame } from "../../src/shared/convFrames";

const base = { v: 1, seq: 1, sid: "s1" };
const valid: Record<string, Record<string, unknown>> = {
  ready: { episode: "E", scope: "pipeline", continue_status: "new", llm: "ok", degrade_reason: null, code_freeze_ok: true, history_count: 0, session_bytes: 0, other_sessions: [], resume_prompt_tokens: null, resume_prompt_chars: null, context_window: null, session_kind: "volume", project: "P" },
  history: { index: 0, role: "user", text: "hi", name: null },
  turn_started: { turn_id: "t1" },
  assistant: { turn_id: "t1", kind: "answer", text: "ok" },
  tool: { turn_id: "t1", phase: "start", index: 0, name: "read_episode_file", summary: "02-script.md", ok: null, observation: null, duplicate: false },
  request: { request_id: "q1", kind: "tool_call", turn_id: "t1", title: "t", card_text: "c", fields: { tool: "browser" }, options: ["approve", "reject"], feedback_allowed: true },
  request_closed: { request_id: "q1", reason: "answered", decision: "approve" },
  command_result: { name: "scope", ok: true, text: "asset" },
  stop_points: { items: [], turn_id: "t1" },
  turn_finished: { turn_id: "t1", stopped: "done", llm_calls: 1, tool_calls: 1, tool_executions: 1, duplicates_rejected: 0, checkpoints: 0, wrapup: "none", duration_s: 1, prompt_chars: 10, lookups: null, prompt_tokens: null, compacted: false, tokens_before: null, tokens_after: null, context_window: null },
  log: { stream: "stdout", text: "x" },
  notice: { level: "warn", code: "code_freeze", text: "n" },
  error: { code: "E_BUSY", message: "busy" },
  bye: { reason: "eof" },
  queued: { id: "q1", text: "改成 X" },
  steer_delivered: { turn_id: "t1", ids: ["q1"] },
  steer_returned: { items: [{ id: "q1", text: "改成 X" }] },
  session_title: { title: "检查稿件字数", source: "generated" },
};

describe("TV-1 parseOutFrame", () => {
  it("D82 PR-C：request 帧的 card_summary 有键才查，必须是字符串", () => {
    const req = { ...base, t: "request", request_id: "r1", kind: "tool_call", turn_id: "t1", title: "x", card_text: "全文", fields: {}, options: ["approve", "reject"], feedback_allowed: true };
    expect(parseOutFrame(JSON.stringify(req)).ok).toBe(true);
    expect(parseOutFrame(JSON.stringify({ ...req, card_summary: "摘要" })).ok).toBe(true);
    expect(parseOutFrame(JSON.stringify({ ...req, card_summary: ["摘要"] })).ok).toBe(false);
    expect(parseOutFrame(JSON.stringify({ ...req, card_summary: null })).ok).toBe(false);
  });
  it("D82：tool / history 帧的 handoff 有键才查，必须是字符串", () => {
    const tool = { ...base, t: "tool", ...valid.tool, name: "run_pipeline", phase: "end", ok: true };
    expect(parseOutFrame(JSON.stringify(tool)).ok).toBe(true);
    expect(parseOutFrame(JSON.stringify({ ...tool, handoff: "请处理工单 /x/tickets/T-1.md" })).ok).toBe(true);
    expect(parseOutFrame(JSON.stringify({ ...tool, handoff: 3 })).ok).toBe(false);
    const hist = { ...base, t: "history", index: 0, role: "tool", text: "", name: "run_pipeline", ok: true };
    expect(parseOutFrame(JSON.stringify({ ...hist, handoff: "x" })).ok).toBe(true);
    expect(parseOutFrame(JSON.stringify({ ...hist, handoff: null })).ok).toBe(false);
  });
  it("D81：ready 缺 session_kind → malformed；project 为 null 合法、为数字 malformed", () => {
    const { session_kind: _drop, ...noKind } = valid.ready;
    expect(parseOutFrame(JSON.stringify({ ...base, t: "ready", ...noKind })).ok).toBe(false);
    expect(parseOutFrame(JSON.stringify({ ...base, t: "ready", ...valid.ready, project: null })).ok).toBe(true);
    expect(parseOutFrame(JSON.stringify({ ...base, t: "ready", ...valid.ready, project: 3 })).ok).toBe(false);
  });
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

  it("D76 / D80：bash 的 tool 帧要求 command 与 description（字符串）；其余工具不要求", () => {
    const bash = { ...base, t: "tool", ...valid.tool, name: "bash", summary: "ls" };
    expect(parseOutFrame(JSON.stringify({ ...bash, command: "ls -la", description: "列目录" })).ok).toBe(true);
    expect(parseOutFrame(JSON.stringify({ ...bash, command: "ls -la", description: "" })).ok).toBe(true);
    expect(parseOutFrame(JSON.stringify(bash)).ok).toBe(false);
    expect(parseOutFrame(JSON.stringify({ ...bash, command: null, description: "" })).ok).toBe(false);
    expect(parseOutFrame(JSON.stringify({ ...bash, command: "ls -la" })).ok).toBe(false); // D80：缺 description
    expect(parseOutFrame(JSON.stringify({ ...bash, command: "ls -la", description: null })).ok).toBe(false);
    expect(parseOutFrame(JSON.stringify({ ...base, t: "tool", ...valid.tool })).ok).toBe(true);
  });

  it("D76 / D80：history 的 bash 条目要求 command 与 description；其余工具条目与非 tool 角色不要求", () => {
    const h = { ...base, t: "history", index: 0, role: "tool", text: "", ok: true };
    expect(parseOutFrame(JSON.stringify({ ...h, name: "bash", command: "ls", description: "" })).ok).toBe(true);
    expect(parseOutFrame(JSON.stringify({ ...h, name: "bash" })).ok).toBe(false);
    expect(parseOutFrame(JSON.stringify({ ...h, name: "bash", command: "ls" })).ok).toBe(false); // D80：缺 description
    expect(parseOutFrame(JSON.stringify({ ...h, name: "read_artifact" })).ok).toBe(true);
    expect(parseOutFrame(JSON.stringify({ ...h, name: null })).ok).toBe(true);
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
