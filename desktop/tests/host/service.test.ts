// HostService 层：TE-7（计数）、TE-8（串扰隔离）、TE-13（纳秒无损通路）、TI-3a（I1 只读夹具）、heal 触发闭集（服务层）。
// 临时 repo 副本夹具（假设 5）：probe 在副本里真实 spawn Python。
import { execFileSync } from "node:child_process";
import { appendFileSync, chmodSync, mkdirSync, readFileSync, renameSync, symlinkSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import type { Envelope, EpisodeDelta, EpisodeSnapshot, SnapshotStatus } from "../../src/shared/protocol";
import { parseEventLine } from "../../src/shared/fold";
import { toWireApproval, toWireEvent } from "../../src/shared/losslessJson";
import { realFs } from "../../src/host/fsio";
import { HostService, RpcFail } from "../../src/host/service";
import { spawnLog, spawnTotal } from "../../src/host/spawner";
import { fingerprintsMatch, readApprovalRecords, readApprovalStore } from "../../src/host/store";
import { emptyStore, reduce, type Store } from "../../src/renderer/store";
import { cleanup, makeFixtureRepo, mkEpisode, py, treeManifest } from "../helpers";

const NS = "1790171112636927676";

function fakeStatus(step: () => string) {
  return async (_repo: string, ep: string): Promise<SnapshotStatus> => ({
    ok: true,
    value: {
      episode_dir: ep, episode_name: ep.split("/").pop()!, current_step: step(), is_blocked: false, block_reason: null,
      completed_steps: [], next_action: "", next_command: null, docs_ref: "", advisories: [],
    },
  });
}

function evLine(episode: string, type: string, payload: object, i: number): string {
  return py(
    `import json, sys
from pipeline.jobs import Event
e = Event(event_id=f"evt_17900000000{int(sys.argv[3]):02d}_abcd", timestamp="2026-09-24T00:00:%02d.000000Z" % int(sys.argv[3]), episode=sys.argv[1], type=sys.argv[2], payload=json.loads(sys.argv[4]))
sys.stdout.write(e.to_jsonl_line())`,
    [episode, type, String(i), JSON.stringify(payload)],
  );
}

let repo: string;
beforeAll(() => {
  repo = makeFixtureRepo();
});
afterAll(() => cleanup(repo));

async function startService(opts: Partial<ConstructorParameters<typeof HostService>[1]> = {}, repoRoot = repo) {
  const pushes: Envelope[] = [];
  const svc = new HostService({ userData: join(repoRoot, "ud"), isPackaged: false, appPath: join(repoRoot, "desktop"), devRepoRoot: repoRoot }, { timers: false, ...opts });
  svc.attach((e) => pushes.push(e));
  await svc.start();
  return { svc, pushes };
}

const deltasOf = (pushes: Envelope[], epKey: string) =>
  pushes.filter((p): p is Extract<Envelope, { kind: "push" }> => p.kind === "push" && p.topic === "episode.delta" && p.epKey === epKey).map((p) => p.data as EpisodeDelta);

describe("TE-13 纳秒 mtime 无损通路（Python 真实写端）", () => {
  let ep: string;
  let storeText: string;
  beforeAll(() => {
    ep = mkEpisode(repo, "LOSSLESS", { "04-clips.json": '{"segments":[]}\n' });
    mkdirSync(join(ep, "_agent"));
    py(
      `import json, os, sys
from pathlib import Path
from pipeline import approvals
from pipeline.jobs import Event, EventType
ep = Path(sys.argv[1]); ns = int(sys.argv[2])
os.utime(ep / "04-clips.json", ns=(ns, ns))
fp = approvals._fingerprint(ep, ("04-clips.json",))
a = approvals.Approval(approval_id="appr_1790171112636_ab12", episode="LOSSLESS", type="05", artifacts=fp)
(ep / "_agent" / "approvals_store.json").write_text(json.dumps([a.to_dict()], ensure_ascii=False, indent=2) + "\\n", encoding="utf-8")
payload = {"approval_id": a.approval_id, "stop": "05", "artifacts": [x.to_dict() for x in fp], "options": a.options, "note": ""}
line = Event(event_id="evt_1790171112636_ab12", timestamp="2026-09-24T00:00:00.000000Z", episode="LOSSLESS", type=EventType.APPROVAL_REQUESTED, payload=payload).to_jsonl_line()
(ep / "events.jsonl").write_text(line, encoding="utf-8")`,
      [ep, NS],
    );
    storeText = readFileSync(join(ep, "_agent/approvals_store.json"), "utf-8");
  });

  it("① readApprovalStore 读出 mtime_ns === 1790171112636927676n，fingerprintsMatch 为 true", () => {
    const r = readApprovalRecords(join(ep, "_agent/approvals_store.json"), realFs);
    if (r.state !== "ok") throw new Error(r.state);
    expect(r.items[0].artifacts[0].mtime_ns).toBe(1790171112636927676n);
    expect(fingerprintsMatch(ep, r.items[0].artifacts, realFs)).toBe(true);
    // 对照：普通 JSON.parse 走同一通路必然失配
    const lossy = JSON.parse(storeText)[0].artifacts[0].mtime_ns as number;
    expect(BigInt(lossy)).not.toBe(1790171112636927676n);
  });
  it("② parseEventLine 读出 approval_requested 载荷的 mtime_ns 同样相等", () => {
    const r = parseEventLine(readFileSync(join(ep, "events.jsonl"), "utf-8").trimEnd());
    if (!r.ok) throw new Error("parse");
    expect((r.ev.payload.artifacts as { mtime_ns: bigint }[])[0].mtime_ns).toBe(1790171112636927676n);
  });
  it("③ String(bigint) 逐字节等于对象库原文里的数字串", () => {
    const r = readApprovalRecords(join(ep, "_agent/approvals_store.json"), realFs);
    if (r.state !== "ok") throw new Error(r.state);
    const raw = /"mtime_ns":\s*(-?\d+)/.exec(storeText)![1];
    expect(String(r.items[0].artifacts[0].mtime_ns)).toBe(raw);
    expect(raw).toBe(NS);
  });
  it("④ 下发给 renderer 的是十进制字符串", async () => {
    const { svc } = await startService({ fetchStatus: fakeStatus(() => "05 审时间码") });
    const snap = (await svc.dispatch("episode.activate", { epKey: "LOSSLESS" })) as EpisodeSnapshot;
    expect(snap.approvals.state).toBe("ok");
    if (snap.approvals.state !== "ok") return;
    expect(snap.approvals.items[0].artifacts[0].mtime_ns).toBe(NS);
    expect((snap.events.items[0].payload.artifacts as { mtime_ns: unknown }[])[0].mtime_ns).toBe(NS);
    expect(() => structuredClone(snap)).not.toThrow();
    // 对照：toWire* 是这条转换的唯一出口
    const rec = readApprovalRecords(join(ep, "_agent/approvals_store.json"), realFs);
    if (rec.state === "ok") expect(toWireApproval(rec.items[0]).artifacts[0].mtime_ns).toBe(NS);
    const ev = parseEventLine(readFileSync(join(ep, "events.jsonl"), "utf-8").trimEnd());
    if (ev.ok) expect((toWireEvent(ev.ev).payload.artifacts as { mtime_ns: unknown }[])[0].mtime_ns).toBe(NS);
  });
  it("⑤ 自检失败：losslessJson false、approvals 能力缺席、approval.decide 返回 E_CAPABILITY 且零 spawn", async () => {
    const { svc } = await startService({ selfCheck: () => false, fetchStatus: fakeStatus(() => "05") });
    const h = svc.health();
    expect(h.losslessJson).toBe(false);
    expect(h.capabilities.approvals).toBe(false);
    const before = spawnTotal();
    const err = await svc
      .dispatch("approval.decide", { epKey: "LOSSLESS", approvalId: "appr_1790171112636_ab12", stop: "05", decision: "approve" })
      .catch((e: unknown) => e);
    expect(err).toBeInstanceOf(RpcFail);
    expect((err as RpcFail).code).toBe("E_CAPABILITY");
    expect(spawnTotal()).toBe(before);
    const snap = (await svc.dispatch("episode.activate", { epKey: "LOSSLESS" })) as EpisodeSnapshot;
    expect(snap.approvals).toEqual({ state: "unsupported" });
  });
  it("自检通过时能力在场（对照组）", async () => {
    const { svc } = await startService({ fetchStatus: fakeStatus(() => "05") });
    expect(svc.health().losslessJson).toBe(true);
    expect(svc.health().capabilities.approvals).toBe(true);
  });
  it("⑥⑦ 对象库 mtime_ns 写成 1.79e18 / 字符串 / null → 整份解析失败（state: error），不回退", () => {
    for (const bad of ["1.79e18", `"${NS}"`, "null"]) {
      const f = join(ep, "_agent/bad.json");
      writeFileSync(f, storeText.replace(NS, bad));
      expect(readApprovalStore(f, realFs).state).toBe("error");
    }
  });
});

describe("TE-8 host 侧串扰隔离 / TE-7 计数", () => {
  it("A 的行永不出现在 B 的 delta；行内 episode 写成 B 的 A 文件行仍归 A；sidecar_degraded 不进 jobs", async () => {
    const a = mkEpisode(repo, "ISO-A");
    const b = mkEpisode(repo, "ISO-B");
    writeFileSync(join(a, "events.jsonl"), evLine("ISO-A", "job_created", { job_id: "job_a1", command: "tts" }, 1));
    writeFileSync(join(b, "events.jsonl"), evLine("ISO-B", "job_created", { job_id: "job_b1", command: "clips" }, 2));
    const { svc, pushes } = await startService({ fetchStatus: fakeStatus(() => "03 配音") });
    const snapA = (await svc.dispatch("episode.subscribe", { epKey: "ISO-A" })) as EpisodeSnapshot;
    const snapB = (await svc.dispatch("episode.activate", { epKey: "ISO-B" })) as EpisodeSnapshot;
    expect(snapA.jobs.map((j) => j.jobId)).toEqual(["job_a1"]);
    expect(snapB.jobs.map((j) => j.jobId)).toEqual(["job_b1"]);
    appendFileSync(join(a, "events.jsonl"), evLine("ISO-A", "job_started", { job_id: "job_a1", pid: 999999 }, 3));
    appendFileSync(join(a, "events.jsonl"), evLine("ISO-B", "job_created", { job_id: "job_a2", command: "mislabeled" }, 4));
    appendFileSync(join(a, "events.jsonl"), evLine("ISO-A", "sidecar_degraded", { dropped_during_circuit: 7 }, 5));
    appendFileSync(join(a, "events.jsonl"), "this is not json\n");
    appendFileSync(join(b, "events.jsonl"), evLine("ISO-B", "job_started", { job_id: "job_b1", pid: 999998 }, 6));
    await svc.tickActive();
    await svc.tickBackground();
    const idsIn = (ds: EpisodeDelta[]) => ds.flatMap((d) => d.newEvents.map((e) => e.event_id));
    const dA = deltasOf(pushes, "ISO-A");
    const dB = deltasOf(pushes, "ISO-B");
    expect(idsIn(dB)).toEqual(["evt_1790000000006_abcd"]);
    expect(idsIn(dA)).toEqual(["evt_1790000000003_abcd", "evt_1790000000004_abcd", "evt_1790000000005_abcd"]);
    for (const d of dB) for (const j of d.jobs ?? []) expect(j.jobId.startsWith("job_b")).toBe(true);
    const snap = (await svc.dispatch("episode.resnapshot", { epKey: "ISO-A" })) as EpisodeSnapshot;
    expect(snap.events.episodeFieldMismatch).toBe(1);
    expect(snap.events.malformed).toBe(1); // TE-7：非 JSON 行跳过且计数
    expect(snap.jobs.map((j) => j.jobId)).toEqual(["job_a1", "job_a2"]);
    expect(snap.degradedNotices).toEqual([{ at: "2026-09-24T00:00:05.000000Z", droppedDuringCircuit: 7 }]);
  });
  it("TE-7 超长半行 → 诊断 +1", async () => {
    const c = mkEpisode(repo, "GARBAGE");
    writeFileSync(join(c, "events.jsonl"), "g".repeat(1024 * 1024 + 1));
    const { svc, pushes } = await startService({ fetchStatus: fakeStatus(() => "03") });
    const before = svc.diagnostics.length;
    await svc.dispatch("episode.activate", { epKey: "GARBAGE" });
    expect(svc.diagnostics.length).toBe(before + 1);
    expect(pushes.filter((p) => p.kind === "push" && p.topic === "diag")).toHaveLength(1);
  });
  it("未知 epKey、多余参数键 → E_BAD_REQUEST（TI-8 的 host 侧部分）", async () => {
    const { svc } = await startService({ fetchStatus: fakeStatus(() => "03") });
    await expect(svc.dispatch("episode.subscribe", { epKey: "NOPE" })).rejects.toMatchObject({ code: "E_BAD_REQUEST" });
    await expect(svc.dispatch("episode.subscribe", { epKey: "ISO-A", path: "/tmp/x" })).rejects.toMatchObject({ code: "E_BAD_REQUEST" });
    await expect(svc.dispatch("tree.list", { epKey: "ISO-A", relDir: "../ISO-B" })).rejects.toMatchObject({ code: "E_BAD_REQUEST" });
    await expect(svc.dispatch("app.requestRepoRootChange", { path: "/tmp/x" })).rejects.toMatchObject({ code: "E_BAD_REQUEST" });
  });
});

describe("S22 🟡-2 同一期 tick 重叠：上一次 tick 未完成时跳过本次，事件不重复下发", () => {
  it("status 慢 100 ms：tick① 卡在 status 期间追加 evt_3，再触发 tick② → evt_3 恰下发一次，seq 连续", async () => {
    const ep = mkEpisode(repo, "OVERLAP");
    writeFileSync(join(ep, "events.jsonl"), evLine("OVERLAP", "job_created", { job_id: "job_o1", command: "tts" }, 1) + evLine("OVERLAP", "job_started", { job_id: "job_o1", pid: 999997 }, 2));
    let clock = 1_000_000;
    const base = fakeStatus(() => "03 配音");
    const { svc, pushes } = await startService({
      now: () => clock,
      fetchStatus: async (r, e) => {
        await new Promise((res) => setTimeout(res, 100));
        return base(r, e);
      },
    });
    await svc.dispatch("episode.activate", { epKey: "OVERLAP" });
    clock += 6000; // 让 tick 走到 status 采样（await 100 ms）
    const t1 = svc.tickActive();
    appendFileSync(join(ep, "events.jsonl"), evLine("OVERLAP", "job_finished", { job_id: "job_o1", status: "succeeded", returncode: 0 }, 3));
    const t2 = svc.tickActive();
    await Promise.all([t1, t2]);
    await svc.tickActive(); // 重叠结束后的下一次 tick 照常读到 evt_3（若 tick① 没读到）
    const ds = deltasOf(pushes, "OVERLAP");
    expect(ds.flatMap((d) => d.newEvents.map((e) => e.event_id))).toEqual(["evt_1790000000003_abcd"]);
    expect(ds.map((d) => d.seq)).toEqual(ds.map((_d, i) => i + 1));
  });
});

describe("S22 🔵4 重新挂载：generation 跨重新挂载单调递增、事件不重复、重跑探针", () => {
  it("resync 到 generation 1 → 脱盘 → 重新挂载 → snapshot generation 为 2，事件恰为文件内容，PROBE_APPROVALS 多跑一次", async () => {
    const r2 = makeFixtureRepo();
    try {
      const ep = mkEpisode(r2, "REMOUNT");
      const l1 = evLine("REMOUNT", "job_created", { job_id: "job_r1", command: "tts" }, 1);
      const l2 = evLine("REMOUNT", "job_started", { job_id: "job_r1", pid: 999996 }, 2);
      writeFileSync(join(ep, "events.jsonl"), l1 + l2);
      const { svc, pushes } = await startService({ fetchStatus: fakeStatus(() => "03 配音") }, r2);
      const s0 = (await svc.dispatch("episode.activate", { epKey: "REMOUNT" })) as EpisodeSnapshot;
      expect(s0.generation).toBe(0);
      // 以新 inode 替换文件 → resync，generation 1
      writeFileSync(join(ep, "events.new"), l1);
      renameSync(join(ep, "events.new"), join(ep, "events.jsonl"));
      await svc.tickActive();
      appendFileSync(join(ep, "events.jsonl"), l2);
      await svc.tickActive();
      const snaps = () => pushes.filter((p): p is Extract<Envelope, { kind: "push" }> => p.kind === "push" && p.topic === "episode.snapshot").map((p) => p.data as EpisodeSnapshot);
      expect(snaps().map((x) => x.generation)).toEqual([1]);
      // 脱盘
      renameSync(join(r2, "data"), join(r2, "data.off"));
      svc.pollReach();
      expect(svc.reach).toBe("missing");
      const probes = () => spawnLog.filter((x) => x.template === "PROBE_APPROVALS" && x.argv[0].startsWith(r2)).length;
      const probesBefore = probes();
      // 重新挂载
      renameSync(join(r2, "data.off"), join(r2, "data"));
      svc.pollReach();
      expect(svc.reach).toBe("ok");
      await vi.waitFor(() => expect(snaps()).toHaveLength(2), { timeout: 10_000 });
      const after = snaps()[1];
      expect(after.generation).toBe(2); // 单调：不归零，也不回到 1
      expect(after.events.items.map((e) => e.event_id)).toEqual(["evt_1790000000001_abcd", "evt_1790000000002_abcd"]);
      expect(after.jobs.map((j) => j.jobId)).toEqual(["job_r1"]);
      expect(probes()).toBe(probesBefore + 1);
    } finally {
      cleanup(r2);
    }
  });
});

describe("S22 🔵7 tree.list：子目录的实路径必须严格落在期目录实路径之内", () => {
  let other: string;
  beforeAll(() => {
    // 期根与链接目标都不放 01-topic.md：否则期列表会把含 01-topic.md 的子目录展平成子期（cli.py 规则）
    const ep = mkEpisode(repo, "TREE-LINK", { "02-script.md": "# t\n", "03-audio/seg-01.wav": "RIFF", "nest/x.md": "x" });
    other = join(repo, "outside-other");
    mkdirSync(other);
    writeFileSync(join(other, "secret.md"), "s");
    symlinkSync("03-audio", join(ep, "alias")); // 期内 → 期内：允许
    symlinkSync(other, join(ep, "out")); // 期内 → 期外目录：拒
    symlinkSync(".", join(ep, "self")); // 指回期目录本身：不是严格后代，拒
    symlinkSync("..", join(ep, "nest/up")); // 多层后回到期目录本身：拒
    symlinkSync("/tmp", join(ep, "nest/tmp")); // 期外绝对路径：拒
    // 软链期目录（TI-5 同款）：期目录本身经符号链接，期内子目录照常可浏览
    const real = join(repo, "outside-real-ep");
    mkdirSync(join(real, "sub"), { recursive: true });
    writeFileSync(join(real, "01-topic.md"), "# s\n");
    writeFileSync(join(real, "sub/a.md"), "a");
    symlinkSync(real, join(repo, "data/episodes/TREE-SOFT"));
  });

  it("正例：期内符号链接目录、普通子目录、软链期目录的子目录", async () => {
    const { svc } = await startService({ fetchStatus: fakeStatus(() => "03") });
    const rels = async (epKey: string, relDir: string) => ((await svc.dispatch("tree.list", { epKey, relDir })) as { rel: string }[]).map((t) => t.rel);
    expect(await rels("TREE-LINK", "alias")).toEqual(["alias/seg-01.wav"]);
    expect(await rels("TREE-LINK", "03-audio")).toEqual(["03-audio/seg-01.wav"]);
    // 只看终点：途经 self 链接、终点仍是期内子目录 → 允许（与 §3.5 规则 2 同一语义）
    expect(await rels("TREE-LINK", "self/03-audio")).toEqual(["self/03-audio/seg-01.wav"]);
    expect(await rels("TREE-SOFT", "sub")).toEqual(["sub/a.md"]);
    expect(await rels("TREE-SOFT", "")).toEqual(["sub", "01-topic.md"]);
  });
  it("反例：指向期外目录、指回期目录本身、经 .. 回到期目录、指向期外绝对路径 → E_BAD_REQUEST", async () => {
    const { svc } = await startService({ fetchStatus: fakeStatus(() => "03") });
    for (const relDir of ["out", "self", "nest/up", "nest/tmp", "self/out"]) {
      await expect(svc.dispatch("tree.list", { epKey: "TREE-LINK", relDir }), relDir).rejects.toMatchObject({ code: "E_BAD_REQUEST" });
    }
  });
});

describe("S22 第 7 条 载入（activate/subscribe/refresh/resnapshot/重新挂载）与 tick 互斥：快照之后的 delta 不重复快照里已有的事件", () => {
  /** status 延迟按调用序取自 delays（缺省 0） */
  function scriptedStatus(delays: number[]) {
    const base = fakeStatus(() => "03 配音");
    let n = 0;
    return async (r: string, e: string) => {
      const d = delays[n++] ?? 0;
      if (d) await new Promise((res) => setTimeout(res, d));
      return base(r, e);
    };
  }
  /** 模拟 renderer：应用 RPC 返回的快照，再按序应用快照返回之后收到的推送 */
  function rendererView(snap: EpisodeSnapshot, later: Envelope[]): string[] {
    let st: Store = reduce(emptyStore, { type: "snapshot", snap }).store;
    for (const p of later) {
      if (p.kind !== "push" || p.epKey !== snap.epKey) continue;
      if (p.topic === "episode.snapshot") st = reduce(st, { type: "snapshot", snap: p.data as EpisodeSnapshot }).store;
      if (p.topic === "episode.delta") st = reduce(st, { type: "delta", delta: p.data as EpisodeDelta }).store;
    }
    return st.episodes[snap.epKey].events.map((e) => e.event_id);
  }
  const ids3 = ["evt_1790000000001_abcd", "evt_1790000000002_abcd", "evt_1790000000003_abcd"];

  it("A：tick 卡在 status 时 activate 发生 → activate 等 tick 跑完再载入", async () => {
    const ep = mkEpisode(repo, "RACE-A");
    writeFileSync(join(ep, "events.jsonl"), evLine("RACE-A", "job_created", { job_id: "job_ra", command: "tts" }, 1) + evLine("RACE-A", "job_started", { job_id: "job_ra", pid: 999995 }, 2));
    let clock = 1_000_000;
    // 调用序：① 首次 activate 0 ms；② tick 300 ms；③ 第二次 activate 0 ms
    const { svc, pushes } = await startService({ now: () => clock, fetchStatus: scriptedStatus([0, 300, 0]) });
    await svc.dispatch("episode.activate", { epKey: "RACE-A" });
    appendFileSync(join(ep, "events.jsonl"), evLine("RACE-A", "job_finished", { job_id: "job_ra", status: "succeeded", returncode: 0 }, 3));
    clock += 6000;
    const t = svc.tickActive(); // 同步读到 evt_3，随后卡在 status
    const snap = (await svc.dispatch("episode.activate", { epKey: "RACE-A" })) as EpisodeSnapshot;
    const mark = pushes.length;
    await t;
    await svc.tickActive();
    expect(rendererView(snap, pushes.slice(mark))).toEqual(ids3);
  });

  it("B：activate 卡在 status 时 tick 到来 → tick 跳过，由下一次 tick 下发", async () => {
    const ep = mkEpisode(repo, "RACE-B");
    writeFileSync(join(ep, "events.jsonl"), evLine("RACE-B", "job_created", { job_id: "job_rb", command: "tts" }, 1) + evLine("RACE-B", "job_started", { job_id: "job_rb", pid: 999994 }, 2));
    let clock = 1_000_000;
    // 调用序：① 首次 activate 0 ms；② 第二次 activate 300 ms；③ 重叠的 tick 600 ms（比 activate 晚结束）；之后 0 ms
    const { svc, pushes } = await startService({ now: () => clock, fetchStatus: scriptedStatus([0, 300, 600]) });
    await svc.dispatch("episode.activate", { epKey: "RACE-B" });
    const a = svc.dispatch("episode.activate", { epKey: "RACE-B" }) as Promise<EpisodeSnapshot>; // 读完事件后卡在 status
    await new Promise((r) => setTimeout(r, 20));
    appendFileSync(join(ep, "events.jsonl"), evLine("RACE-B", "job_finished", { job_id: "job_rb", status: "succeeded", returncode: 0 }, 3));
    clock += 6000;
    const t = svc.tickActive(); // 与在途的 activate 重叠：同步读到 evt_3，随后卡在 status
    const snap = await a;
    const mark = pushes.length;
    await t;
    clock += 6000;
    await svc.tickActive();
    await svc.tickActive();
    expect(rendererView(snap, pushes.slice(mark))).toEqual(ids3);
  });
});

describe("shots.list（S21 修订 §3.2）", () => {
  it("只列 shots 根顶层的 html 文件，自然序；子目录、非 html、点前缀不列；带参数即拒", async () => {
    const shots = join(repo, "data/library/shots");
    mkdirSync(join(shots, "frames/sub"), { recursive: true });
    for (const f of ["B_S01E10_gallery.html", "B_S01E02_gallery.html", "B_S01E02.json", ".hidden.html", "frames/sub/x.html"]) writeFileSync(join(shots, f), "<p>x</p>");
    mkdirSync(join(shots, "dir.html"));
    const { svc } = await startService({ fetchStatus: fakeStatus(() => "03") });
    const list = (await svc.dispatch("shots.list", {})) as { name: string }[];
    expect(list.map((g) => g.name)).toEqual(["B_S01E02_gallery.html", "B_S01E10_gallery.html"]);
    await expect(svc.dispatch("shots.list", { dir: "frames" })).rejects.toMatchObject({ code: "E_BAD_REQUEST" });
  });
});

describe("heal 触发闭集（服务层；TA-11 的端到端版本属 PR4）", () => {
  it("subscribe 不 heal；activate → H1；refresh → H3；step 变化 → H2（基线不触发）；后台期永不 heal", async () => {
    mkEpisode(repo, "HEAL-A");
    mkEpisode(repo, "HEAL-B");
    let step = "03 配音";
    let clock = 1_000_000;
    const calls: string[] = [];
    const { svc } = await startService({
      fetchStatus: fakeStatus(() => step),
      now: () => clock,
      healExecutor: async (ep, t) => {
        calls.push(`${ep}:${t}`);
        return { ok: true, stderrTail: "" };
      },
    });
    await svc.dispatch("episode.subscribe", { epKey: "HEAL-A" });
    expect(calls).toEqual([]);
    await svc.dispatch("episode.activate", { epKey: "HEAL-A" });
    await svc.dispatch("episode.activate", { epKey: "HEAL-B" });
    step = "05 审时间码"; // A 在后台被推进
    for (let i = 0; i < 3; i++) {
      clock += 6000;
      await svc.tickBackground();
    }
    await svc.dispatch("episode.refresh", { epKey: "HEAL-B" });
    clock += 6000;
    await svc.tickActive(); // H2 基线：该次不触发
    step = "06 渲染";
    clock += 6000;
    await svc.tickActive(); // 变化 → H2 恰一次
    clock += 6000;
    await svc.tickActive();
    await new Promise((r) => setImmediate(r));
    expect(calls).toEqual(["HEAL-A:H1-activated", "HEAL-B:H1-activated", "HEAL-B:H3-user-refresh", "HEAL-B:H2-step-changed"]);
  });
});

describe("TI-3a I1：关闭 heal（能力缺席）后，对只读夹具树执行全部读功能 → 功能正常、树清单不变", () => {
  let ro: string;
  beforeAll(() => {
    ro = makeFixtureRepo({ withApprovals: false });
    const ep = mkEpisode(ro, "RO", { "01-topic.md": "# t\n类型: 杂谈\n", "02-script.md": "# s\n", "03-audio/seg-01.wav": "RIFF" });
    writeFileSync(join(ep, "events.jsonl"), evLine("RO", "job_created", { job_id: "job_ro", command: "tts" }, 1));
    mkEpisode(ro, "PROJ/01-sub", { "01-topic.md": "# t\n" });
  });
  afterAll(() => cleanup(ro));

  it("期列表、订阅、tail、树浏览（真实 status spawn）", async () => {
    execFileSync("/bin/chmod", ["-R", "a-w", join(ro, "data")]);
    const before = treeManifest(join(ro, "data"));
    try {
      const { svc } = await startService({}, ro);
      expect(svc.health().capabilities.approvals).toBe(false);
      const list = (await svc.dispatch("episodes.list", {})) as { episodes: { epKey: string }[] };
      expect(list.episodes.map((e) => e.epKey).sort()).toEqual(["PROJ/01-sub", "RO"]);
      const snap = (await svc.dispatch("episode.activate", { epKey: "RO" })) as EpisodeSnapshot;
      expect(snap.status.ok).toBe(true);
      expect(snap.jobs.map((j) => j.jobId)).toEqual(["job_ro"]);
      expect(snap.approvals).toEqual({ state: "unsupported" });
      await svc.dispatch("episode.subscribe", { epKey: "PROJ/01-sub" });
      await svc.tickActive();
      await svc.tickBackground();
      const tree = (await svc.dispatch("tree.list", { epKey: "RO", relDir: "" })) as { rel: string }[];
      expect(tree.map((t) => t.rel)).toEqual(["03-audio", "01-topic.md", "02-script.md", "events.jsonl"]);
      const sub = (await svc.dispatch("tree.list", { epKey: "RO", relDir: "03-audio" })) as { rel: string }[];
      expect(sub.map((t) => t.rel)).toEqual(["03-audio/seg-01.wav"]);
      await svc.dispatch("episode.refresh", { epKey: "RO" });
    } finally {
      expect(treeManifest(join(ro, "data"))).toEqual(before);
      chmodSync(join(ro, "data"), 0o755);
    }
  });
});
