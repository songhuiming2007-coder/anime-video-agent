// TV-6 ①②③④⑤⑥⑦：autoOpen 逐条驱动（Spec 10 §2.7 的 R1–R6）。
// ⑧~⑪（回合结算与 R6 从 snapshot 重建 awaiting）属 PR3，见后续用例。
import { describe, expect, it } from "vitest";
import type { ApprovalJson } from "../../src/shared/contracts";
import { initialAutoOpen, step, type AutoOpenEnv, type AutoOpenEvent, type AutoOpenState } from "../../src/renderer/autoOpen";

const noMedia: AutoOpenEnv = { mediaPlaying: false };
const playing: AutoOpenEnv = { mediaPlaying: true };

function obj(id: string, type: string, created = "2026-09-25T10:00:00Z"): ApprovalJson {
  return {
    approval_id: id, episode: "E", type, status: "pending", options: ["approve", "reject"], created_at: created,
    resolved_at: null, resolved_by: null, confirmed_by: null, confirmed_at: null, feedback: null, note: "",
    artifacts: [{ path: "04-clips.json", size: 3, mtime_ns: "1" }],
  };
}

/** 依次投喂事件，返回每次呼出的候选（null = 没换预览）与终态 */
function drive(events: AutoOpenEvent[], env: AutoOpenEnv = noMedia, start: AutoOpenState = initialAutoOpen) {
  let s = start;
  const opened: (ApprovalJson | null)[] = [];
  for (const e of events) {
    const r = step(s, e, env);
    s = r.state;
    opened.push(r.open);
  }
  return { opened, state: s };
}

describe("TV-6 autoOpen", () => {
  it("① 空预览 + 新 pending → 呼出", () => {
    const { opened } = drive([{ kind: "pendings", items: [obj("a1", "03.5")] }]);
    expect(opened.map((o) => o?.approval_id ?? null)).toEqual(["a1"]);
  });

  it("② owner=human → 只出「已就绪」条，不替换预览", () => {
    const { opened, state } = drive([{ kind: "human" }, { kind: "pendings", items: [obj("a1", "03.5")] }]);
    expect(opened).toEqual([null, null]);
    expect(state.strip?.approval_id).toBe("a1");
    expect(state.owner).toBe("human");
  });

  it("③ 有媒体在播放 → 只出「已就绪」条", () => {
    const { opened, state } = drive([{ kind: "pendings", items: [obj("a1", "03.5")] }], playing);
    expect(opened).toEqual([null]);
    expect(state.strip?.approval_id).toBe("a1");
  });

  it("④ 回合中先后出现两个同类 pending → 回合中零呼出", () => {
    const { opened, state } = drive([
      { kind: "turn_started", turnId: "t1" },
      { kind: "pendings", items: [obj("v1", "05", "2026-09-25T10:00:01Z")] },
      { kind: "pendings", items: [obj("v2", "05", "2026-09-25T10:00:02Z")] },
    ]);
    expect(opened).toEqual([null, null, null]);
    expect(state.deferred.get("05")?.approval_id).toBe("v2"); // 同类型保留最新者
  });

  it("⑤ 回合中两个不同类 → 结算后只呼出最新者（一次）", () => {
    const { opened, state } = drive([
      { kind: "turn_started", turnId: "t1" },
      { kind: "pendings", items: [obj("b1", "03.5", "2026-09-25T10:00:05Z")] },
      { kind: "pendings", items: [obj("c1", "05", "2026-09-25T10:00:03Z")] },
      { kind: "settled", turnId: "t1", items: [obj("b1", "03.5", "2026-09-25T10:00:05Z"), obj("c1", "05", "2026-09-25T10:00:03Z")] },
    ]);
    expect(opened.map((o) => o?.approval_id ?? null)).toEqual([null, null, null, "b1"]);
    expect(state.awaiting).toBeNull();
    expect(state.deferred.size).toBe(0);
    expect(state.owner).toBe("auto");
  });

  it("⑤附带：结算时已离开 pending 的候选不呼出", () => {
    const { opened } = drive([
      { kind: "turn_started", turnId: "t1" },
      { kind: "pendings", items: [obj("b1", "03.5")] },
      { kind: "settled", turnId: "t1", items: [] },
    ]);
    expect(opened).toEqual([null, null, null]);
  });

  it("⑤附带：微秒恰为 0 的时间戳不因字符串比较被判成更新（MUT-55）", () => {
    // "…:00Z" 与 "…:00.000001Z"：按字符串 `.`(0x2E) < `Z`(0x5A)，前者会被当成更新的那个
    const a = obj("whole", "03.5", "2026-09-25T10:00:00Z");
    const b = obj("micro", "05", "2026-09-25T10:00:00.000001Z");
    const { opened } = drive([
      { kind: "turn_started", turnId: "t1" },
      { kind: "pendings", items: [a] },
      { kind: "pendings", items: [b] },
      { kind: "settled", turnId: "t1", items: [a, b] },
    ]);
    expect(opened.at(-1)?.approval_id).toBe("micro");
  });

  it("⑥ 点「已就绪 · 查看」→ 呼出该候选且 owner=auto", () => {
    const { opened, state } = drive([
      { kind: "human" },
      { kind: "pendings", items: [obj("a1", "03.5")] },
      { kind: "strip" },
    ]);
    expect(opened.map((o) => o?.approval_id ?? null)).toEqual([null, null, "a1"]);
    expect(state.owner).toBe("auto");
    expect(state.strip).toBeNull();
  });

  it("⑦ 切期后同一个 approvalId 不再触发；owner 归 none", () => {
    const { opened, state } = drive([
      { kind: "pendings", items: [obj("a1", "03.5")] },
      { kind: "reset", phase: "none", turnId: null, settlePending: null },
      { kind: "pendings", items: [obj("a1", "03.5")] },
    ]);
    expect(opened.map((o) => o?.approval_id ?? null)).toEqual(["a1", null, null]);
    expect(state.owner).toBe("none");
    expect(state.strip).toBeNull();
  });
});
