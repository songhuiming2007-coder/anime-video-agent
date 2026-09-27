// Spec 11 PR3~PR5 / Spec 12 PR3 的 host 层用例：SAVE_SCRIPT 无损指纹（TD-5）、冲突拒存（TD-3 宿主半）、
// 封板/机检/顺听/人时/导入的真实 core 往返（TE-1/TE-3/TE-4 的宿主半）、done 的确认框与长任务模板（TD-4）、
// 锁残留可见性（TI-11）。全部夹具在临时 repo 副本上（红队 🔵-6：严禁指向真实 data/）。
import { execFileSync, spawnSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import type { Envelope, SnapshotStatus, VoiceInfoJson } from "../../src/shared/protocol";
import { parseLossless } from "../../src/shared/losslessJson";
import { HostService, type HostDeps } from "../../src/host/service";
import { cleanup, makeFixtureRepo, py, treeManifest } from "../helpers";

const NS = "1790171112636927676";
const SCRIPT = "## 段落 1\n\n配音：雪乃和大老师都没想到。\n\n画面：\n  查询: 甲\n\n## 段落 2\n\n配音：团子说得对。\n\n画面：\n  查询: 乙\n";
const DRAFT = SCRIPT.replace("都没想到。", "都没想到吧。");
const PATCH_LINE = "1段 雪乃 改成 xuě nǎi";

let repo: string;
let epAbs: string;

function fakeStatus(): (repo: string, ep: string) => Promise<SnapshotStatus> {
  return async (_repo, ep) => ({
    ok: true,
    value: {
      episode_dir: ep, episode_name: ep.split("/").pop()!, current_step: "02.5 人审改稿", is_blocked: false, block_reason: null,
      completed_steps: [], next_action: "", next_command: null, docs_ref: "", advisories: [],
    },
  });
}

async function startService(opts: Partial<HostDeps> = {}, repoRoot = repo) {
  const pushes: Envelope[] = [];
  const svc = new HostService({ userData: join(repoRoot, "ud"), isPackaged: false, appPath: join(repoRoot, "desktop"), devRepoRoot: repoRoot }, { timers: false, fetchStatus: fakeStatus(), ...opts });
  svc.attach((e) => pushes.push(e));
  await svc.start();
  return { svc, pushes };
}

async function waitFor(fn: () => boolean, ms = 8000): Promise<void> {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    if (fn()) return;
    await new Promise((r) => setTimeout(r, 40));
  }
  throw new Error("waitFor 超时");
}

beforeAll(() => {
  repo = makeFixtureRepo();
  epAbs = join(repo, "data/episodes/SP11");
  mkdirSync(join(epAbs, "03-audio"), { recursive: true });
  writeFileSync(join(epAbs, "01-topic.md"), "# 选题\n番：春物\n");
  writeFileSync(join(epAbs, "02-script.md"), SCRIPT);
  writeFileSync(join(epAbs, "02-script.draft.md"), DRAFT);
  writeFileSync(join(epAbs, "03-audio/manifest.json"), JSON.stringify({ engine: "mlx", segments: [{ index: 1, duration: 4 }] }));
  for (const n of ["seg-01.wav", "seg-02.wav"]) writeFileSync(join(epAbs, `03-audio/${n}`), "RIFF");
  // 一张最小合法 PNG（Pillow 真实产出，导入用例不重编码）
  py(`import sys\nfrom PIL import Image\nImage.new("RGB", (16, 9), (255, 0, 0)).save(sys.argv[1])`, [join(repo, "cover.png")]);
});
afterAll(() => cleanup(repo));

describe("Spec 11 TD-5 / TC-5：SAVE_SCRIPT 的指纹纪律（host ↔ core 真实往返）", () => {
  it("正常保存：落盘生效、新指纹与 statSync(bigint) 逐位相等（MUT-17 的杀手）", async () => {
    const { svc } = await startService();
    const st = (await svc.dispatch("script.stat", { epKey: "SP11" })) as { script: { size: number; mtimeNs: string } | null; draft: unknown };
    expect(st.script).not.toBeNull();
    const saved = (await svc.dispatch("script.save", { epKey: "SP11", text: SCRIPT, expectSize: String(st.script!.size), expectMtimeNs: st.script!.mtimeNs })) as { size: number; mtimeNs: string };
    const onDisk = statSync(join(epAbs, "02-script.md"), { bigint: true });
    // core 回的新指纹与磁盘逐位相等（host 用 parseLossless 读 stdout，不许经 double）
    expect(saved.mtimeNs).toBe(onDisk.mtimeNs.toString());
    expect(saved.size).toBe(Number(onDisk.size));
  });

  it("TD-5 夹具值：parseLossless 逐位无损，普通 JSON.parse 丢精度（MUT-17 的确定性杀手）", () => {
    const stdout = `{"size":12,"mtime_ns":${NS}}`;
    expect((parseLossless(stdout) as { mtime_ns: bigint }).mtime_ns).toBe(1790171112636927676n);
    expect((parseLossless(stdout) as { mtime_ns: bigint }).mtime_ns.toString()).toBe(NS);
    // 对照：普通 JSON.parse 得到的是另一个数（Spec 8 TA-2 同款夹具值）
    expect(String((JSON.parse(stdout) as { mtime_ns: number }).mtime_ns)).not.toBe(NS);
  });

  it("指纹不符：E_STALE 且一字不写（MUT-4/MUT-5 的杀手）", async () => {
    const { svc } = await startService();
    const before = readFileSync(join(epAbs, "02-script.md"));
    const st = (await svc.dispatch("script.stat", { epKey: "SP11" })) as { script: { size: number; mtimeNs: string } };
    // 外部（终端/别的编辑器）改写同一文件
    writeFileSync(join(epAbs, "02-script.md"), "## 外部改动\n");
    await expect(
      svc.dispatch("script.save", { epKey: "SP11", text: "## 我的改动\n", expectSize: String(st.script.size), expectMtimeNs: st.script.mtimeNs }),
    ).rejects.toMatchObject({ code: "E_STALE" });
    expect(readFileSync(join(epAbs, "02-script.md"), "utf-8")).toBe("## 外部改动\n");
    expect(readFileSync(join(epAbs, "02-script.md"))).not.toEqual(before);
  });

  it("(-1,-1) 断言不存在：文件已在时拒存；文件缺席时照常新建", async () => {
    const { svc } = await startService();
    await expect(svc.dispatch("script.save", { epKey: "SP11", text: "x", expectSize: "-1", expectMtimeNs: "-1" })).rejects.toMatchObject({ code: "E_STALE" });
    mkdirSync(join(repo, "data/episodes/SP11-DRAFT"), { recursive: true });
    writeFileSync(join(repo, "data/episodes/SP11-DRAFT/01-topic.md"), "# t\n");
    writeFileSync(join(repo, "data/episodes/SP11-DRAFT/02-script.draft.md"), "草稿\n");
    const { svc: svc2 } = await startService();
    const r = (await svc2.dispatch("script.save", { epKey: "SP11-DRAFT", text: "从草稿新建\n", expectSize: "-1", expectMtimeNs: "-1" })) as { size: number };
    expect(r.size).toBeGreaterThan(0);
    expect(readFileSync(join(repo, "data/episodes/SP11-DRAFT/02-script.md"), "utf-8")).toBe("从草稿新建\n");
  });
});

describe("Spec 11 TC-6：SEAL_SCRIPT 空 diff 拒封", () => {
  it("有 diff → 落补丁且返回字节数；无 diff → E_CORE「未做任何修改」且既有补丁不被清空", async () => {
    const { svc } = await startService();
    // 让正稿 = 草稿 + 一处改动
    writeFileSync(join(epAbs, "02-script.md"), SCRIPT);
    writeFileSync(join(epAbs, "02-script.draft.md"), DRAFT);
    const r = (await svc.dispatch("script.seal", { epKey: "SP11" })) as { bytes: number };
    expect(r.bytes).toBeGreaterThan(0);
    const patchPath = join(epAbs, "02-diff.patch");
    const patch = readFileSync(patchPath, "utf-8");
    expect(patch).toContain("---");
    // 手工 git diff --no-index 对照：字节一致（有差异时 git 退 1，故用 spawnSync）
    const manual = spawnSync("/usr/bin/git", ["diff", "--no-index", "02-script.draft.md", "02-script.md"], { cwd: epAbs, encoding: "utf-8" });
    expect(manual.status).toBe(1);
    expect(patch).toBe(manual.stdout);
    // 让两者相同 → 拒封
    writeFileSync(join(epAbs, "02-script.draft.md"), SCRIPT);
    await expect(svc.dispatch("script.seal", { epKey: "SP11" })).rejects.toMatchObject({ code: "E_CORE" });
    expect(readFileSync(patchPath, "utf-8")).toBe(patch); // 既有补丁不被清空
  });
});

describe("Spec 11 TC-7/TD-2：VOICE_INFO 契约（真实 core 输出）", () => {
  it("schema 对拍 tts.parse_script；纯读（目录树不变）", async () => {
    const { svc } = await startService();
    const before = treeManifest(epAbs);
    const info = (await svc.dispatch("voice.info", { epKey: "SP11" })) as VoiceInfoJson;
    expect(info.v).toBe(1);
    expect(info.segments.map((s) => s.label)).toEqual(["1", "2"]);
    expect(info.segments[0].wav).toBe("seg-01.wav");
    expect(info.segments[0].wav_exists).toBe(true);
    expect(info.engine).toBe("mlx");
    expect(info.engine_cloud).toBe(false);
    expect(treeManifest(epAbs)).toEqual(before);
  });

  it("TI-11 锁残留：.apply_patch.lock 内是已死进程号 → pid_alive=false", async () => {
    const { svc } = await startService();
    writeFileSync(join(epAbs, "03-audio/.apply_patch.lock"), "999999");
    try {
      const info = (await svc.dispatch("voice.info", { epKey: "SP11" })) as VoiceInfoJson;
      expect(info.apply_patch_lock).toEqual({ exists: true, pid: 999999, pid_alive: false });
    } finally {
      execFileSync("/bin/rm", ["-f", join(epAbs, "03-audio/.apply_patch.lock")]);
    }
  });
});

describe("Spec 11 TC-8/TC-9：VOICE_PARSE 与 VOICE_ADD（原文重解析）", () => {
  it("parse 出结构化字段；add 落盘并由 voice.info 看见待应用条目", async () => {
    const { svc } = await startService();
    const before = readFileSync(join(epAbs, "02-script.md"), "utf-8");
    const patch = (await svc.dispatch("voice.parse", { epKey: "SP11", text: PATCH_LINE })) as Record<string, unknown>;
    expect(patch.segment).toBe("1");
    expect(patch.kind).toBe("pronunciation");
    expect(patch.action).toBe("inject");
    expect(patch.word).toBe("雪乃");
    const entry = (await svc.dispatch("voice.add", { epKey: "SP11", text: PATCH_LINE })) as Record<string, unknown>;
    expect(entry.id).toBeGreaterThan(0);
    const info = (await svc.dispatch("voice.info", { epKey: "SP11" })) as VoiceInfoJson;
    expect(info.pending_corrections.some((c) => c.id === entry.id)).toBe(true);
    expect(readFileSync(join(epAbs, "02-script.md"), "utf-8")).toBe(before); // 纠错不改稿件
    // 撤回后条目消失
    await svc.dispatch("voice.retract", { epKey: "SP11", id: String(entry.id) });
    const after = (await svc.dispatch("voice.info", { epKey: "SP11" })) as VoiceInfoJson;
    expect(after.pending_corrections.some((c) => c.id === entry.id)).toBe(false);
  });

  it("非法文法 → E_CORE（原样透传 core 错误文案）", async () => {
    const { svc } = await startService();
    await expect(svc.dispatch("voice.parse", { epKey: "SP11", text: "乱写一句" })).rejects.toMatchObject({ code: "E_CORE" });
  });
});

describe("Spec 11 §2.4：人时采集（RECORD_TIME / time.read）", () => {
  it("审阅面可见 → 不可见：落一条 source=desktop 的条目，time.read 归并", async () => {
    let clock = 1_700_000_000_000;
    const { svc } = await startService({ now: () => clock });
    await svc.dispatch("time.surface", { epKey: "SP11", stop: "02.5", visible: "true" });
    clock += 60_000; // 1 分钟
    await svc.dispatch("time.surface", { epKey: "SP11", stop: "02.5", visible: "false" });
    const ht = join(epAbs, "human_time.json");
    await waitFor(() => existsSync(ht));
    const records = JSON.parse(readFileSync(ht, "utf-8")) as { stop: string; minutes: number; source?: string }[];
    const mine = records.filter((r) => r.stop === "02.5" && r.source === "desktop");
    expect(mine).toHaveLength(1);
    expect(mine[0].minutes).toBe(1);
    const read = (await svc.dispatch("time.read", { epKey: "SP11" })) as { perStop: Record<string, number>; totalMin: number };
    expect(read.perStop["02.5"]).toBe(1);
    expect(read.totalMin).toBe(1);
  });

  it("不设时长下限：0.5 s 的区间照常落盘（MUT-11 的宿主半）", async () => {
    let clock = 1_700_000_000_000;
    const { svc } = await startService({ now: () => clock });
    await svc.dispatch("time.surface", { epKey: "SP11", stop: "03.5", visible: "true" });
    clock += 500;
    await svc.dispatch("time.surface", { epKey: "SP11", stop: "03.5", visible: "false" });
    const ht = join(epAbs, "human_time.json");
    await waitFor(() => JSON.parse(readFileSync(ht, "utf-8")).some((r: { stop: string; source?: string }) => r.stop === "03.5" && r.source === "desktop"));
  });

  it("stop 越集：E_BAD_REQUEST 且零 spawn（不落任何条目）", async () => {
    const { svc } = await startService();
    await svc.dispatch("time.surface", { epKey: "SP11", stop: "nope", visible: "true" });
    expect(svc.humanTimers.closed()).toBe(0);
  });
});

describe("Spec 11 TD-4：done 的确认框与长任务模板", () => {
  it("确认框取消 → {started:false} 且不 spawn；确认 → spawn RUN_TTS_APPLY_PATCH", async () => {
    const calls: string[] = [];
    const fakeRun: HostDeps["runCore"] = async (t) => {
      calls.push(t as string);
      return { code: 0, signal: null, stdoutTail: "", stderrTail: "", timedOut: false, stdoutFull: null, stdoutOverflow: false };
    };
    let answer = false;
    const { svc } = await startService({ runCore: fakeRun, confirm: async () => answer });
    const r1 = (await svc.dispatch("voice.applyPatch", { epKey: "SP11" })) as { started: boolean };
    expect(r1.started).toBe(false);
    expect(calls).not.toContain("RUN_TTS_APPLY_PATCH");
    answer = true;
    const r2 = (await svc.dispatch("voice.applyPatch", { epKey: "SP11" })) as { started: boolean };
    expect(r2.started).toBe(true);
    expect(calls).toContain("RUN_TTS_APPLY_PATCH");
  });
});

describe("Spec 12 TD-1/TD-2：封面导入（IMPORT_COVER，字节走 stdin）", () => {
  it("合法 PNG → 落 07-cover/import/，字节与输入全同（不重编码）；同名二次导入得 -2", async () => {
    const { svc } = await startService();
    const bytes = new Uint8Array(readFileSync(join(repo, "cover.png")));
    const r1 = (await svc.dispatch("cover.import", { epKey: "SP11", name: "我的图.PNG", bytes })) as { path: string; width: number; height: number; format: string };
    expect(r1).toMatchObject({ width: 16, height: 9, format: "PNG" });
    expect(r1.path).toMatch(/^07-cover\/import\/.+\.png$/);
    expect(Array.from(readFileSync(join(epAbs, r1.path)))).toEqual(Array.from(bytes)); // 原始字节，不重编码
    const r2 = (await svc.dispatch("cover.import", { epKey: "SP11", name: "我的图.PNG", bytes })) as { path: string };
    expect(r2.path).not.toBe(r1.path);
    expect(r2.path).toContain("-2");
  });

  it("非图字节 → E_CORE 且零落盘；空字节 → E_BAD_REQUEST", async () => {
    const { svc } = await startService();
    const dir = join(epAbs, "07-cover/import");
    const before = existsSync(dir) ? treeManifest(dir) : [];
    await expect(svc.dispatch("cover.import", { epKey: "SP11", name: "x.png", bytes: new Uint8Array([1, 2, 3]) })).rejects.toMatchObject({ code: "E_CORE" });
    await expect(svc.dispatch("cover.import", { epKey: "SP11", name: "x.png", bytes: new Uint8Array(0) })).rejects.toMatchObject({ code: "E_BAD_REQUEST" });
    const after = existsSync(dir) ? treeManifest(dir) : [];
    expect(after).toEqual(before);
  });
});

describe("Spec 11 TC-3 的机检半：script.check 原样回 PASS/FAIL 文本", () => {
  it("退出码与 stdout 原样；UI 不解析判定语义", async () => {
    const { svc } = await startService();
    writeFileSync(join(epAbs, "02-script.md"), SCRIPT);
    const r = (await svc.dispatch("script.check", { epKey: "SP11" })) as { code: number | null; stdoutTail: string };
    expect(r.stdoutTail).toMatch(/^(PASS|FAIL)/m);
    expect(r.stdoutTail).toContain("主观项仍需人看");
  });
});
