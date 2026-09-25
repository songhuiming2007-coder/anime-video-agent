// TE-10 renderer reducer 协议；TE-12 多期 reducer（按 epKey 分桶）。
import { describe, expect, it } from "vitest";
import { approvalsOf, emptyStore, reduce, select, type Store } from "../../src/renderer/store";
import type { ApprovalJson, EventJson } from "../../src/shared/contracts";
import type { EpisodeDelta, EpisodeSnapshot, JobView } from "../../src/shared/protocol";

const ev = (id: string, ep: string): EventJson => ({ event_id: id, timestamp: "t", episode: ep, type: "job_created", kind: "job_created", payload: { job_id: `job_${id}` } });
const job = (id: string): JobView => ({ jobId: id, command: null, state: "pending", lastEventAt: "t", pid: null, returncode: null, durationS: null, stderrTail: null, message: null, noFollowupEvents: false, finishedEventMissing: false });
const appr = (id: string): ApprovalJson => ({ approval_id: id, episode: "x", type: "05", status: "pending", artifacts: [], options: [], created_at: "t", resolved_at: null, resolved_by: null, confirmed_by: null, confirmed_at: null, feedback: null, note: "" });

function snap(epKey: string, generation: number, ids: string[]): EpisodeSnapshot {
  return {
    epKey, generation, seq: 0, reach: "ok",
    status: { ok: false, code: "E_STALE", message: "" },
    approvals: { state: "ok", items: ids.map((i) => appr(`appr_${i}`)), skipped: 0, healedAt: null },
    events: { state: "ok", offset: 0, truncatedHead: false, items: ids.map((i) => ev(i, epKey)), malformed: 0, episodeFieldMismatch: 0 },
    jobs: ids.map((i) => job(`job_${i}`)),
    degradedNotices: [],
  };
}
const delta = (epKey: string, generation: number, seq: number, ids: string[]): EpisodeDelta => ({
  epKey, generation, seq, newEvents: ids.map((i) => ev(i, epKey)), jobs: ids.map((i) => job(`job_${i}`)),
});

describe("TE-10 reducer 协议", () => {
  it("generation 不同的 delta 被丢弃；seq 连续则应用；seq 跳号触发 resnapshot 请求", () => {
    let s: Store = reduce(emptyStore, { type: "snapshot", snap: snap("A", 3, ["a1"]) }).store;
    const stale = reduce(s, { type: "delta", delta: delta("A", 2, 1, ["old"]) });
    expect(stale.store).toBe(s);
    expect(stale.resnapshot).toBeNull();
    const ok = reduce(s, { type: "delta", delta: delta("A", 3, 1, ["a2"]) });
    expect(ok.resnapshot).toBeNull();
    s = ok.store;
    expect(select(s, "A")!.events.map((e) => e.event_id)).toEqual(["a1", "a2"]);
    expect(select(s, "A")!.seq).toBe(1);
    const gap = reduce(s, { type: "delta", delta: delta("A", 3, 3, ["a4"]) });
    expect(gap.resnapshot).toBe("A");
    expect(gap.store).toBe(s);
  });
  it("新 snapshot 整体替换本地状态（含 seq 归零）", () => {
    let s = reduce(emptyStore, { type: "snapshot", snap: snap("A", 1, ["a1"]) }).store;
    s = reduce(s, { type: "delta", delta: delta("A", 1, 1, ["a2"]) }).store;
    s = reduce(s, { type: "snapshot", snap: snap("A", 2, ["z1"]) }).store;
    expect(select(s, "A")!.events.map((e) => e.event_id)).toEqual(["z1"]);
    expect(select(s, "A")!.seq).toBe(0);
  });
});

describe("TE-12 多期 reducer", () => {
  it("交错投喂 A、B 两期；select(store, B) 的 jobs 与决策条对象集合中不含任何 A 的 id", () => {
    let s: Store = emptyStore;
    s = reduce(s, { type: "snapshot", snap: snap("A", 0, ["a1"]) }).store;
    s = reduce(s, { type: "snapshot", snap: snap("B", 0, ["b1"]) }).store;
    s = reduce(s, { type: "delta", delta: delta("A", 0, 1, ["a2"]) }).store;
    s = reduce(s, { type: "delta", delta: { ...delta("B", 0, 1, ["b2"]), approvals: { state: "ok", items: [appr("appr_b2")], skipped: 0, healedAt: null } } }).store;
    s = reduce(s, { type: "delta", delta: delta("A", 0, 2, ["a3"]) }).store;
    const b = select(s, "B")!;
    expect(b.jobs.map((j) => j.jobId)).toEqual(["job_b2"]);
    expect(b.events.map((e) => e.event_id)).toEqual(["b1", "b2"]);
    expect(approvalsOf(b).map((a) => a.approval_id)).toEqual(["appr_b2"]);
    const a = select(s, "A")!;
    expect(a.events.map((e) => e.event_id)).toEqual(["a1", "a2", "a3"]);
    expect(approvalsOf(a).map((x) => x.approval_id)).toEqual(["appr_a1"]);
  });
  it("不属于任何订阅的 delta 直接丢弃", () => {
    const s = reduce(emptyStore, { type: "snapshot", snap: snap("A", 0, ["a1"]) }).store;
    const r = reduce(s, { type: "delta", delta: delta("C", 0, 1, ["c1"]) });
    expect(r.store).toBe(s);
    expect(select(r.store, "C")).toBeUndefined();
  });
});
