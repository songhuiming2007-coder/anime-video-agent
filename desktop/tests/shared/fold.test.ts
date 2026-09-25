// TE-9 折叠与缺口呈现：Spec 2 §3.3 六个示例载荷逐字段；finishedEventMissing 须连续 2 个 tick；noFollowupEvents。
import { describe, expect, it } from "vitest";
import { foldEvents, goneRunningJobIds, parseEventLine } from "../../src/shared/fold";
import type { EventRecord } from "../../src/shared/contracts";

// Spec 2 §3.3 示例 1–6 原样（示例 4、5、6 的 episode 字段与 1–3 不同，折叠不看该字段）
export const SPEC2_EXAMPLES = [
  { event_id: "evt_1790089200100_0001", episode: "EGOIST/01-Live", timestamp: "2026-09-22T14:30:00.100000Z", type: "job_created",
    payload: { job_id: "job_1790089200100_01a2", command: "tts --redo 3", scope: "pipeline", argv: ["/Users/.../venv/bin/python", "-m", "pipeline.tts", "/Users/.../data/episodes/EGOIST/01-Live", "--redo", "3"] } },
  { event_id: "evt_1790089200200_0002", episode: "EGOIST/01-Live", timestamp: "2026-09-22T14:30:00.200000Z", type: "job_started",
    payload: { job_id: "job_1790089200100_01a2", pid: 48102, started_at: "2026-09-22T14:30:00.198000Z" } },
  { event_id: "evt_1790089205500_0003", episode: "EGOIST/01-Live", timestamp: "2026-09-22T14:30:05.500000Z", type: "job_finished",
    payload: { job_id: "job_1790089200100_01a2", status: "succeeded", returncode: 0, duration_s: 5.3, message: "退出码 0", stdout_tail: "[OK] 段落 3 合成完毕 (1.4s)\n", stderr_tail: "", truncated: false } },
  { event_id: "evt_1790089210000_0004", episode: "01-test", timestamp: "2026-09-22T14:30:10.000000Z", type: "job_blocked",
    payload: { job_id: "job_1790089210000_01b3", command: "render --force", reason: "拒绝执行：禁止在 ava 中使用 --force 全量覆盖！" } },
  { event_id: "evt_1790089215000_0005", episode: "01-test", timestamp: "2026-09-22T14:30:15.000000Z", type: "approval_resolved",
    payload: { command: "tts --redo 3", decision: "rejected", source: "cli_card" } },
  { event_id: "evt_1790089230000_0006", episode: "01-test", timestamp: "2026-09-22T14:30:30.000000Z", type: "job_heartbeat",
    payload: { job_id: "job_1790089200100_01a2", elapsed_s: 30.0, heartbeat_at: "2026-09-22T14:30:30.000000Z" } },
];

function records(objs: object[]): EventRecord[] {
  return objs.map((o) => {
    const r = parseEventLine(JSON.stringify(o));
    if (!r.ok) throw new Error("fixture 解析失败");
    return r.ev;
  });
}

const NOW = Date.parse("2026-09-22T14:31:00Z");
const alive = () => true;

describe("TE-9 foldEvents", () => {
  it("六个示例载荷逐字段", () => {
    const jobs = foldEvents(records(SPEC2_EXAMPLES), NOW, alive, new Set());
    expect(jobs).toEqual([
      { jobId: "job_1790089200100_01a2", command: "tts --redo 3", state: "succeeded", lastEventAt: "2026-09-22T14:30:30.000000Z",
        pid: 48102, returncode: 0, durationS: 5.3, stderrTail: "", message: "退出码 0", noFollowupEvents: false, finishedEventMissing: false },
      { jobId: "job_1790089210000_01b3", command: "render --force", state: "blocked", lastEventAt: "2026-09-22T14:30:10.000000Z",
        pid: null, returncode: null, durationS: null, stderrTail: null, message: "拒绝执行：禁止在 ava 中使用 --force 全量覆盖！",
        noFollowupEvents: false, finishedEventMissing: false },
    ]);
  });
  it("未知 type 保留为 kind unknown；缺必需字段 → 解析失败", () => {
    const r = parseEventLine(JSON.stringify({ ...SPEC2_EXAMPLES[0], type: "future_type" }));
    expect(r.ok && r.ev.kind).toBe("unknown");
    expect(parseEventLine(JSON.stringify({ ...SPEC2_EXAMPLES[0], event_id: undefined })).ok).toBe(false);
    expect(parseEventLine("not json").ok).toBe(false);
  });
  it("删去 job_finished、pid 已退出：第 1 个 tick false，第 2 个 tick true；pid 存活恒 false", () => {
    const evs = records(SPEC2_EXAMPLES.slice(0, 2));
    const dead = () => false;
    const t1 = foldEvents(evs, NOW, dead, new Set());
    expect(t1[0].state).toBe("running");
    expect(t1[0].finishedEventMissing).toBe(false);
    const prev = goneRunningJobIds(t1, dead);
    const t2 = foldEvents(evs, NOW, dead, prev);
    expect(t2[0].finishedEventMissing).toBe(true);
    const t3 = foldEvents(evs, NOW, alive, goneRunningJobIds(t2, alive));
    expect(t3[0].finishedEventMissing).toBe(false);
    expect(foldEvents(evs, NOW, alive, new Set(["job_1790089200100_01a2"]))[0].finishedEventMissing).toBe(false);
  });
  it("job_created 后无后续且超过 PENDING_STALE_MS → noFollowupEvents", () => {
    const evs = records(SPEC2_EXAMPLES.slice(0, 1));
    const created = Date.parse("2026-09-22T14:30:00.100Z");
    expect(foldEvents(evs, created + 9_999, alive, new Set())[0].noFollowupEvents).toBe(false);
    expect(foldEvents(evs, created + 10_001, alive, new Set())[0].noFollowupEvents).toBe(true);
  });
  it("job_started 即使缺 created 也是 running；sidecar_degraded 与无 job_id 事件不进折叠", () => {
    const evs = records([
      SPEC2_EXAMPLES[1],
      { event_id: "evt_1_0000", episode: "x", timestamp: "2026-09-22T14:30:01.000000Z", type: "sidecar_degraded", payload: { dropped_during_circuit: 3 } },
      SPEC2_EXAMPLES[4],
    ]);
    const jobs = foldEvents(evs, NOW, alive, new Set());
    expect(jobs.map((j) => [j.jobId, j.state])).toEqual([["job_1790089200100_01a2", "running"]]);
  });
});
