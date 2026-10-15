// D98（人选方案 C）：Dock 提醒的纯逻辑——事件分类、去重、窗口在前台不打扰、等人才弹跳、切回清零。
import { describe, expect, it } from "vitest";
import { DOCK_IDLE, jobAttentions, onAttention, onFocus, turnAttention, type DockState } from "../../src/shared/attention";
import type { JobView } from "../../src/shared/protocol";

const job = (jobId: string, state: JobView["state"]): JobView => ({
  jobId, command: "tts", state, lastEventAt: "", startedAt: null, pid: 1, returncode: null, durationS: null,
  stderrTail: null, message: null, noFollowupEvents: false, finishedEventMissing: false,
});

describe("turnAttention：回合结束怎么算", () => {
  it.each([
    ["done", "info"],
    ["interrupted", null],
    ["command", null],
    ["error", "wait"],
    ["rollback", "wait"],
    ["degraded", "wait"],
    ["blocked", "wait"],
  ] as const)("%s → %s", (stopped, kind) => {
    expect(turnAttention(stopped)).toBe(kind);
  });
});

describe("jobAttentions：只报上次在跑、这次结束的作业", () => {
  it("成功 info、失败 wait，键带期号与作业号", () => {
    const prev = [job("a", "running"), job("b", "running"), job("c", "running")];
    const next = [job("a", "succeeded"), job("b", "failed"), job("c", "running")];
    expect(jobAttentions("E", prev, next)).toEqual([
      { kind: "info", key: "job:E:a" },
      { kind: "wait", key: "job:E:b" },
    ]);
  });
  it("开机首折（上次为空）不补报旧作业", () => {
    expect(jobAttentions("E", [], [job("a", "succeeded")])).toEqual([]);
  });
});

describe("onAttention / onFocus：Dock 状态机", () => {
  const run = (events: [string, "wait" | "info", boolean][]) => {
    let st: DockState = DOCK_IDLE;
    const all: unknown[] = [];
    for (const [key, kind, focused] of events) {
      const r = onAttention(st, { key, kind }, focused);
      st = r.state;
      all.push(...r.actions);
    }
    return { st, all };
  };

  it("窗口在前台：什么都不做", () => {
    expect(run([["req:1", "wait", true]]).all).toEqual([]);
  });

  it("做完了只加角标；等人加角标并开始弹跳，只弹一次", () => {
    const { st, all } = run([["turn:1", "info", false], ["req:1", "wait", false], ["req:2", "wait", false]]);
    expect(all).toEqual([
      { do: "badge", text: "1" },
      { do: "badge", text: "2" },
      { do: "bounce" },
      { do: "badge", text: "3" },
    ]);
    expect(st.bouncing).toBe(true);
  });

  it("同一个键只算一次", () => {
    expect(run([["req:1", "wait", false], ["req:1", "wait", false]]).all).toEqual([{ do: "badge", text: "1" }, { do: "bounce" }]);
  });

  it("切回窗口：清角标、停弹跳；没弹跳时不发 cancel；空状态不发任何动作", () => {
    const bouncing = run([["req:1", "wait", false]]).st;
    expect(onFocus(bouncing).actions).toEqual([{ do: "badge", text: "" }, { do: "cancelBounce" }]);
    const badgeOnly = run([["turn:1", "info", false]]).st;
    expect(onFocus(badgeOnly).actions).toEqual([{ do: "badge", text: "" }]);
    expect(onFocus(DOCK_IDLE).actions).toEqual([]);
  });

  it("切回后清空去重表：之后同键再来（新一轮失焦）照常计数", () => {
    const st = onFocus(run([["turn:1", "info", false]]).st).state;
    expect(onAttention(st, { key: "turn:1", kind: "info" }, false).actions).toEqual([{ do: "badge", text: "1" }]);
  });
});
