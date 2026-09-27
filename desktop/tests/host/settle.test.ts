// TH-20：host 侧回合结算（Spec 10 §2.7）。注入了单调时钟与对象库读取钩子，逐步驱动。
import { describe, expect, it } from "vitest";
import type { ConvEntry, ConvSnapshot, Envelope } from "../../src/shared/protocol";
import type { SessionProc } from "../../src/host/spawner";
import { SessionManager, type SessionTarget } from "../../src/host/sessions";

class FakeProc implements SessionProc {
  pid = 12345;
  writes: string[] = [];
  signals: string[] = [];
  private out: ((b: Buffer) => void)[] = [];
  private exitFns: ((c: number | null, s: string | null) => void)[] = [];
  write(line: string): void {
    this.writes.push(line);
  }
  onStdout(fn: (b: Buffer) => void): void {
    this.out.push(fn);
  }
  onStderr(): void {}
  onExit(fn: (c: number | null, s: string | null) => void): void {
    this.exitFns.push(fn);
  }
  signal(sig: string): void {
    this.signals.push(sig);
  }
  feed(frame: Record<string, unknown>): void {
    for (const fn of this.out) fn(Buffer.from(`${JSON.stringify({ v: 1, sid: "s1", seq: 1, ...frame })}\n`));
  }
  exit(code: number | null = 0, signal: string | null = null): void {
    for (const fn of this.exitFns) fn(code, signal);
  }
}

const TAG = { t: "turn_finished", turn_id: "t1", stopped: "done", llm_calls: 1, tool_calls: 0, tool_executions: 0, duplicates_rejected: 0, checkpoints: 0, wrapup: "none", duration_s: 0, prompt_chars: 1 };

function harness(opts: { active?: boolean; canRead?: boolean; settleMs?: number } = {}) {
  const clock = { t: 1000 };
  const pushes: Envelope[] = [];
  const diags: string[] = [];
  const procs: FakeProc[] = [];
  const swallow = () => undefined;
  const mgr = new SessionManager({
    spawnSession: () => {
      const p = new FakeProc();
      procs.push(p);
      queueMicrotask(() => p.feed({ t: "ready", episode: "E", scope: "pipeline", continue_status: "new", llm: "ok", degrade_reason: null, code_freeze_ok: true, history_count: 0, session_bytes: 0, other_sessions: [] }));
      return p;
    },
    resolveKey: async () => ({ name: "K", value: "V" }),
    confirm: async () => true,
    now: () => clock.t,
    bootId: "boot1",
    push: (e) => pushes.push(e),
    diag: (s) => diags.push(s),
    blocked: () => false,
    repoRoot: () => "/repo",
    isActive: () => opts.active ?? true,
    canReadApprovals: () => opts.canRead ?? true,
    killGroup: swallow,
    groupAlive: () => false,
    changed: swallow,
    timing: { readyMs: 5000, coalesceMs: 0, settleMs: opts.settleMs ?? 60_000, sendAckMs: 1000, answerAckMs: 1000, endWaitMs: 100, killGraceMs: 100 },
  });
  const target: SessionTarget = { key: "ep:E", epKey: "E", abs: "/repo/data/episodes/E", mode: "continue" };
  const start = async () => {
    await mgr.resume("ep:E", target);
    return procs[procs.length - 1];
  };
  const settled = (): Extract<ConvEntry, { k: "settled" }>[] =>
    pushes.flatMap((p) => (p.kind === "push" && p.topic === "conv.delta" ? ((p.data as { entries: ConvEntry[] }).entries.filter((e) => e.k === "settled") as Extract<ConvEntry, { k: "settled" }>[]) : []));
  const settledCount = () => settled().length;
  const kinds = () => pushes.map((p) => (p.kind === "push" ? p.topic : p.kind));
  return { mgr, clock, pushes, diags, start, settled, settledCount, kinds };
}

describe("TH-20 host 侧回合结算（§2.7）", () => {
  it("① 读取开始时刻早于 stop_points 不算结算；更晚的读取结算一次且 episode.delta 在前", async () => {
    const h = harness();
    const p = await h.start();
    h.clock.t = 1000;
    p.feed(TAG);
    h.clock.t = 1005;
    p.feed({ t: "stop_points", items: [], turn_id: "t1" });
    expect(h.settledCount()).toBe(0);

    // 一次「stop_points 之前开始、之后读完」的读取
    h.pushes.push({ v: 1, kind: "push", topic: "episode.delta", epKey: "E", generation: 1, seq: 1, data: {} });
    h.mgr.onApprovalsRead("E", 1000);
    expect(h.settledCount()).toBe(0);

    h.clock.t = 1010;
    h.pushes.push({ v: 1, kind: "push", topic: "episode.delta", epKey: "E", generation: 1, seq: 2, data: {} });
    h.mgr.onApprovalsRead("E", 1010);
    const s = h.settled();
    expect(s).toHaveLength(1);
    expect(s[0]).toMatchObject({ turnId: "t1", timedOut: false });
    // 推送顺序：本次读取的 episode.delta 先于 settled
    const lastDelta = h.kinds().lastIndexOf("episode.delta");
    const settledIdx = h.kinds().lastIndexOf("conv.delta");
    expect(lastDelta).toBeLessThan(settledIdx);
    expect(h.clock.t - 1000).toBeLessThan(60_000 / 2);
    expect(h.diags).toEqual([]);
  });

  it("② items 为空、对象库无变化 → 同样恰 1 条 settled", async () => {
    const h = harness();
    const p = await h.start();
    p.feed(TAG);
    p.feed({ t: "stop_points", items: [], turn_id: "t1" });
    h.clock.t = 1010;
    h.mgr.onApprovalsRead("E", 1010);
    expect(h.settled()).toHaveLength(1);
    expect(h.settled()[0].timedOut).toBe(false);
  });

  it("③ 期不活跃 → stop_points 到达即结算", async () => {
    const h = harness({ active: false });
    const p = await h.start();
    p.feed(TAG);
    p.feed({ t: "stop_points", items: [], turn_id: "t1" });
    expect(h.settled()).toHaveLength(1);
    expect(h.settled()[0].timedOut).toBe(false);
  });

  it("④ 进程退出且没有 stop_points → 退出后首次读取即结算", async () => {
    const h = harness();
    const p = await h.start();
    p.feed(TAG);
    p.exit(0, null);
    expect(h.settledCount()).toBe(0); // 等读取
    h.clock.t = 1010;
    h.mgr.onApprovalsRead("E", 1010);
    expect(h.settled()).toHaveLength(1);
    expect(h.settled()[0].timedOut).toBe(false);
  });

  it("⑤ 不发 stop_points → 兜底超时 settled{timedOut:true} + 诊断", async () => {
    const h = harness({ settleMs: 40 });
    const p = await h.start();
    p.feed(TAG);
    await new Promise((r) => setTimeout(r, 120));
    expect(h.settled()).toHaveLength(1);
    expect(h.settled()[0].timedOut).toBe(true);
    expect(h.diags.join("\n")).toContain("回合结算超时");
  });

  it("⑥ 两帧之间夹 log 与 notice → 照常结算、timedOut 为 false", async () => {
    const h = harness();
    const p = await h.start();
    p.feed(TAG);
    p.feed({ t: "log", stream: "stdout", text: "[approvals] 05 待审批已更新：01" });
    p.feed({ t: "notice", level: "warn", code: "x", text: "n" });
    p.feed({ t: "stop_points", items: [], turn_id: "t1" });
    h.clock.t = 1010;
    h.mgr.onApprovalsRead("E", 1010);
    expect(h.settled()).toHaveLength(1);
    expect(h.settled()[0].timedOut).toBe(false);
  });

  it("⑦ 按 turn_id 精确匹配：迟到旧帧不推进新槽位、null 帧从不推进、超时后迟到帧被忽略", async () => {
    const h = harness();
    const p = await h.start();
    p.feed(TAG); // 槽位 t1
    p.feed({ ...TAG, turn_id: "t2" }); // 覆盖为 t2
    p.feed({ t: "stop_points", items: [], turn_id: "t1" }); // 迟到旧帧：不推进
    p.feed({ t: "stop_points", items: [], turn_id: null }); // ready 帧形态：从不推进
    h.clock.t = 1005;
    p.feed({ t: "stop_points", items: [], turn_id: "t2" });
    h.clock.t = 1010;
    h.mgr.onApprovalsRead("E", 1010);
    const s = h.settled();
    expect(s.map((x) => x.turnId)).toEqual(["t2"]);
    expect(s[0].timedOut).toBe(false);
    p.feed({ t: "stop_points", items: [], turn_id: "t1" }); // 槽位已清：无第二条
    expect(h.settled()).toHaveLength(1);
  });

  it("⑦变体：t1 超时结算后迟到的 stop_points{t1} 不产生第二条", async () => {
    const h = harness({ settleMs: 40 });
    const p = await h.start();
    p.feed(TAG);
    await new Promise((r) => setTimeout(r, 120));
    expect(h.settled()).toHaveLength(1);
    p.feed({ t: "stop_points", items: [], turn_id: "t1" });
    expect(h.settled()).toHaveLength(1);
  });

  it("⑧ approvals 能力缺席 / 不可达 → stop_points 到达即结算、零读取、无超时诊断", async () => {
    const h = harness({ canRead: false });
    const p = await h.start();
    p.feed(TAG);
    p.feed({ t: "stop_points", items: [], turn_id: "t1" });
    expect(h.settled()).toHaveLength(1);
    expect(h.settled()[0].timedOut).toBe(false);
    expect(h.diags).toEqual([]);
  });

  it("截图：settlePending 在 snapshot 顶层字段里（截头也不影响，四轮 🔵-3）", async () => {
    const h = harness();
    const p = await h.start();
    p.feed(TAG);
    const snap = h.mgr.snapshot("ep:E") as ConvSnapshot;
    expect(snap.settlePending).toBe("t1");
    expect(snap.phase).toBe("idle");
  });
});
