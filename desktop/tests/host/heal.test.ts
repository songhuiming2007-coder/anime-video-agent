// TE-14：H5 调度纯函数（基线、最新对象、稳定期、段检查）；HealScheduler 合并语义。
import { describe, expect, it } from "vitest";
import type { ApprovalRecord } from "../../src/shared/contracts";
import { h5Step, HealScheduler, newH5State, type H5Stat } from "../../src/host/heal";

function obj(id: string, type: string, status: string, created: string, artifacts: [string, number, bigint][]): ApprovalRecord {
  return {
    approval_id: id, episode: "EP", type, status, created_at: created,
    artifacts: artifacts.map(([path, size, mtime_ns]) => ({ path, size, mtime_ns })),
    options: ["approve", "reject"], resolved_at: null, resolved_by: null, confirmed_by: null, confirmed_at: null,
    feedback: status === "rejected" ? { target: "t", problem: "p" } : null, note: "",
  };
}

const disk = (m: Record<string, [bigint, bigint] | "missing">): H5Stat => (rel) => {
  const v = m[rel];
  if (v === undefined || v === "missing") return "missing";
  return { size: v[0], mtimeNs: v[1] };
};

describe("TE-14 h5Step", () => {
  const pinned = obj("A", "05", "pending", "2026-09-24T00:00:00.000000Z", [["04-clips.json", 10, 100n]]);

  it("① 设为活跃后第一次采样即使存在漂移也不触发（基线）", () => {
    const r = h5Step(newH5State(), [pinned], disk({ "04-clips.json": [11n, 200n] }));
    expect(r.fire).toBe(false);
    // 基线时就存在的元组此后也不触发
    const r2 = h5Step(r.state, [pinned], disk({ "04-clips.json": [11n, 200n] }));
    expect(r2.fire).toBe(false);
  });

  it("② 历史 REJECTED + 更新的 APPROVED：基线后磁盘再变，任意多次采样均不触发（只看最新对象）", () => {
    const oldRejected = obj("R", "05", "rejected", "2026-09-20T00:00:00.000000Z", [["04-clips.json", 1, 1n]]);
    const approved = obj("B", "05", "approved", "2026-09-21T00:00:00.000000Z", [["04-clips.json", 10, 100n]]);
    let st = h5Step(newH5State(), [oldRejected, approved], disk({ "04-clips.json": [10n, 100n] })).state; // 基线
    // 批准之后又改了文件：历史 REJECTED 的指纹与磁盘出现一个基线里没有的新元组
    for (let i = 0; i < 5; i++) {
      const r = h5Step(st, [oldRejected, approved], disk({ "04-clips.json": [99n, 999n] }));
      expect(r.fire).toBe(false);
      st = r.state;
    }
  });

  it("③ 稳定期：T1 第一次不触发、第二次触发恰一次；T1→T2→T3 各只一次采样 → 零触发", () => {
    let st = h5Step(newH5State(), [pinned], disk({ "04-clips.json": [10n, 100n] })).state; // 基线：无漂移
    let r = h5Step(st, [pinned], disk({ "04-clips.json": [12n, 300n] }));
    expect(r.fire).toBe(false);
    r = h5Step(r.state, [pinned], disk({ "04-clips.json": [12n, 300n] }));
    expect(r.fire).toBe(true);
    r = h5Step(r.state, [pinned], disk({ "04-clips.json": [12n, 300n] }));
    expect(r.fire).toBe(false); // 同一元组至多触发一次
    st = h5Step(newH5State(), [pinned], disk({ "04-clips.json": [10n, 100n] })).state;
    let fires = 0;
    for (const t of [[1n, 1n], [2n, 2n], [3n, 3n]] as [bigint, bigint][]) {
      const x = h5Step(st, [pinned], disk({ "04-clips.json": t }));
      fires += x.fire ? 1 : 0;
      st = x.state;
    }
    expect(fires).toBe(0);
  });

  it("文件被删（-1,-1）也是一个指纹元组", () => {
    let st = h5Step(newH5State(), [pinned], disk({ "04-clips.json": [10n, 100n] })).state;
    st = h5Step(st, [pinned], disk({})).state;
    expect(h5Step(st, [pinned], disk({})).fire).toBe(true);
  });

  it("④ 段检查：../x、/etc/hosts、a//b 一次都不 stat，rejectedPaths 恰为这三者", () => {
    const bad = obj("C", "09", "pending", "2026-09-24T00:00:00.000000Z", [["../x", 1, 1n], ["/etc/hosts", 1, 1n], ["a//b", 1, 1n]]);
    const seen: string[] = [];
    const r = h5Step(newH5State(), [bad], (rel) => {
      seen.push(rel);
      return "missing";
    });
    expect(seen).toEqual([]);
    expect(r.rejectedPaths).toEqual(["../x", "/etc/hosts", "a//b"]);
  });
});

describe("HealScheduler", () => {
  it("同期至多一个在途，在途期间的多次触发合并为「完成后再跑一次」", async () => {
    const calls: string[] = [];
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    const s = new HealScheduler(async (ep, t) => {
      calls.push(`${ep}:${t}`);
      if (calls.length === 1) await gate;
      return { ok: true, stderrTail: "" };
    });
    const p1 = s.requestHeal("A", "H1-activated");
    const p2 = s.requestHeal("A", "H3-user-refresh");
    const p3 = s.requestHeal("A", "H5-artifact-drift");
    const pb = s.requestHeal("B", "H1-activated");
    release();
    await Promise.all([p1, p2, p3, pb]);
    expect(calls).toEqual(["A:H1-activated", "B:H1-activated", "A:H3-user-refresh"]);
  });
  it("executor 为 null（能力缺席 / PR4 未接入）：只记录、不 spawn", async () => {
    const s = new HealScheduler(null);
    const r = await s.requestHeal("A", "H1-activated");
    expect(r.ok).toBe(false);
    expect(s.log.map((l) => l.spawned)).toEqual([false]);
  });
  it("失败只记诊断，不重试", async () => {
    const fails: string[] = [];
    let n = 0;
    const s = new HealScheduler(async () => ({ ok: ++n > 99, stderrTail: "boom" }), (ep, r) => fails.push(`${ep}:${r.stderrTail}`));
    await s.requestHeal("A", "H4-pre-ack");
    expect(n).toBe(1);
    expect(fails).toEqual(["A:boom"]);
  });
});
