// TD-2 parseVoiceInfo 容错（Spec 11 §4.2/§7.2）：未知字段忽略；缺必需字段 malformed。
// 真实 core 输出的契约对拍在 tests/host/stopPoint.test.ts（需要夹具 repo）。
import { describe, expect, it } from "vitest";
import type { SynthProgressJson, VoiceInfoJson } from "../../src/shared/protocol";
import { clock, heardKey, lockState, parseVoiceInfo, segStatus, synthSummary } from "../../src/shared/voiceInfo";

const GOOD = {
  v: 1,
  engine: "mlx",
  engine_cloud: false,
  segments: [{ label: "1", index: 1, wav: "seg-01.wav", wav_exists: true, text: "测试台词。", has_attic: false }],
  heteronyms: [{ char: "雪", readings: ["xue3", "xue4"] }],
  pending_corrections: [{ id: 3, segment: 1, kind: "inject", scope: "segment", applied: false }],
  apply_patch_lock: { exists: false, pid: null, pid_alive: null },
};

describe("parseVoiceInfo 容错", () => {
  it("真实形状：全部字段读出", () => {
    const r = parseVoiceInfo(JSON.stringify(GOOD));
    expect(r.ok).toBe(true);
    if (!r.ok) return;
    expect(r.info.segments[0].wav).toBe("seg-01.wav");
    expect(r.info.pending_corrections[0].id).toBe(3);
    expect(r.info.apply_patch_lock).toEqual({ exists: false, pid: null, pid_alive: null });
  });

  it("D72 rerun_segments：数组原样读出；null 与缺席都按「判不了」；类型不符 malformed", () => {
    const withRerun = parseVoiceInfo(JSON.stringify({ ...GOOD, rerun_segments: ["2", "5"] }));
    expect(withRerun.ok && withRerun.info.rerun_segments).toEqual(["2", "5"]);
    const nul = parseVoiceInfo(JSON.stringify({ ...GOOD, rerun_segments: null }));
    expect(nul.ok && nul.info.rerun_segments).toBeNull();
    const absent = parseVoiceInfo(JSON.stringify(GOOD));
    expect(absent.ok && absent.info.rerun_segments).toBeNull();
    expect(parseVoiceInfo(JSON.stringify({ ...GOOD, rerun_segments: [2] })).ok).toBe(false);
    expect(parseVoiceInfo(JSON.stringify({ ...GOOD, rerun_segments: "2" })).ok).toBe(false);
  });

  it("未知字段忽略（核心不枚举全量条目字段：seed_pin/applied_at 等原样透传）", () => {
    const withExtras = {
      ...GOOD,
      future_field: 1,
      segments: [{ ...GOOD.segments[0], duration_s: 1.2 }],
      pending_corrections: [{ ...GOOD.pending_corrections[0], seed_pin: 123456, raw: "原文" }],
    };
    const r = parseVoiceInfo(JSON.stringify(withExtras));
    expect(r.ok).toBe(true);
    if (r.ok) expect(r.info.pending_corrections[0].seed_pin).toBe(123456);
  });

  const malformed: [string, unknown][] = [
    ["非 JSON", "not json"],
    ["非对象", [1, 2]],
    ["缺 segments 键", { ...GOOD, segments: undefined }],
    ["segments 非数组", { ...GOOD, segments: {} }],
    ["segments 元素缺字段", { ...GOOD, segments: [{ label: "1" }] }],
    ["v 不为 1", { ...GOOD, v: 2 }],
    ["engine 非字符串", { ...GOOD, engine: 3 }],
    ["engine_cloud 非布尔", { ...GOOD, engine_cloud: "no" }],
    ["pending_corrections 元素非对象", { ...GOOD, pending_corrections: [1] }],
    ["apply_patch_lock 缺 exists", { ...GOOD, apply_patch_lock: { pid: null } }],
    ["apply_patch_lock.pid 类型错", { ...GOOD, apply_patch_lock: { exists: true, pid: "x", pid_alive: true } }],
    ["heteronyms 元素缺 readings", { ...GOOD, heteronyms: [{ char: "雪" }] }],
  ];
  it.each(malformed)("malformed：%s", (_name, v) => {
    expect(parseVoiceInfo(typeof v === "string" ? v : JSON.stringify(v)).ok).toBe(false);
  });

  it("锁残留形态：exists=true、pid_alive=false 可解析（TI-11 的显示分支依据）", () => {
    const r = parseVoiceInfo(JSON.stringify({ ...GOOD, apply_patch_lock: { exists: true, pid: 4242, pid_alive: false } }));
    expect(r.ok).toBe(true);
    if (r.ok) expect(r.info.apply_patch_lock.pid_alive).toBe(false);
  });

  it("锁三态：只有 pid_alive===true 算「进行中」；pid 读不出的锁一律判残留（红队 🔵：错误态也曾被当作进行中）", () => {
    expect(lockState({ exists: false, pid: null, pid_alive: null })).toBe("none");
    expect(lockState({ exists: true, pid: 4242, pid_alive: true })).toBe("busy");
    expect(lockState({ exists: true, pid: 999999, pid_alive: false })).toBe("stale");
    expect(lockState({ exists: true, pid: null, pid_alive: null })).toBe("stale"); // 写一半的锁
  });
});

// ---------------- D87：重配进度 ----------------

const SYNTH: SynthProgressJson = {
  state: "running",
  started_at: "2026-10-13T19:00:00",
  finished_at: null,
  queue: ["6", "7", "15"],
  done: { "6": "2026-10-13T19:02:00" },
  current: "7",
  note: "第 2 句第 1 次不过：句内静音 5.8s（门槛 2.0s）",
  error: null,
};
const TODAY = "2026-10-13";

function infoWith(synth: SynthProgressJson | null, over: Partial<VoiceInfoJson> = {}): VoiceInfoJson {
  const r = parseVoiceInfo(JSON.stringify({ ...GOOD, segments: ["6", "7", "15", "16"].map((l, i) => ({ ...GOOD.segments[0], label: l, index: i + 1 })), pending_corrections: [], synth, ...over }));
  if (!r.ok) throw new Error("fixture malformed");
  return r.info;
}
const st = (info: VoiceInfoJson, label: string, heard: string[] = []) => segStatus(info, label, new Set(heard), TODAY);

describe("D87 parseVoiceInfo：synth 与 mtime", () => {
  it("缺席 → null（老 core 照常装载）；在场原样读出", () => {
    const old = parseVoiceInfo(JSON.stringify(GOOD));
    expect(old.ok && old.info.synth).toBeNull();
    expect(old.ok && old.info.segments[0].mtime).toBeNull();
    const r = parseVoiceInfo(JSON.stringify({ ...GOOD, synth: SYNTH, segments: [{ ...GOOD.segments[0], mtime: 1760353320.5 }] }));
    expect(r.ok && r.info.synth).toEqual(SYNTH);
    expect(r.ok && r.info.segments[0].mtime).toBe(1760353320.5);
  });

  it("在场但形状不对 → malformed（同 D72 纪律）", () => {
    expect(parseVoiceInfo(JSON.stringify({ ...GOOD, synth: { ...SYNTH, state: "done" } })).ok).toBe(false);
    expect(parseVoiceInfo(JSON.stringify({ ...GOOD, synth: { ...SYNTH, queue: [6] } })).ok).toBe(false);
    expect(parseVoiceInfo(JSON.stringify({ ...GOOD, synth: { ...SYNTH, done: { "6": 1 } } })).ok).toBe(false);
    expect(parseVoiceInfo(JSON.stringify({ ...GOOD, segments: [{ ...GOOD.segments[0], mtime: "x" }] })).ok).toBe(false);
  });
});

describe("D87 segStatus：每段状态与优先级", () => {
  it("在跑：当前段合成中（悬停看重试原因）、后面的排队、已完成的待复听、不在本轮的无标记", () => {
    const info = infoWith(SYNTH);
    expect(st(info, "7")).toEqual({ kind: "running", text: "合成中", title: SYNTH.note });
    expect(st(info, "15")).toEqual({ kind: "queued", text: "排队 3/3" });
    expect(st(info, "6")).toEqual({ kind: "redone", text: "已重配 19:02 · 待复听" });
    expect(st(info, "16")).toBeNull();
  });

  it("复听按「这一版」记：听过本轮的才算已复听，上一轮听过的不算", () => {
    const info = infoWith(SYNTH);
    expect(st(info, "6", [heardKey("6", "2026-10-13T19:02:00")])?.kind).toBe("heard");
    expect(st(info, "6", [heardKey("6", "2026-10-13T18:40:00")])?.kind).toBe("redone");
  });

  it("失败：失败段带原因、没轮到的段标本轮未配到", () => {
    const info = infoWith({ ...SYNTH, state: "failed", error: "FAIL 段落 7 重试 3 次仍不达标" });
    expect(st(info, "7")).toEqual({ kind: "failed", text: "重配失败", title: "FAIL 段落 7 重试 3 次仍不达标" });
    expect(st(info, "15")?.kind).toBe("unfinished");
    expect(st(info, "6")?.kind).toBe("redone");
  });

  it("中断（进程已不在）", () => {
    expect(st(infoWith({ ...SYNTH, state: "interrupted" }), "7")?.kind).toBe("interrupted");
  });

  it("重配完又标了新错 → 待重配压过已重配；在跑时合成中 / 排队压过待重配", () => {
    const pending = [{ id: 9, segment: "6", applied: false }, { id: 10, segment: "15", applied: false }];
    const info = infoWith(SYNTH, { pending_corrections: pending });
    expect(st(info, "6")?.kind).toBe("redo");
    expect(st(info, "15")?.kind).toBe("queued");
    expect(st(infoWith({ ...SYNTH, state: "ok", current: null, done: { "6": "x", "7": "y", "15": "z" } }, { pending_corrections: [] }), "16")).toBeNull();
  });

  it("从没跑过：只剩待重配判据（D72 行为不变）", () => {
    const info = infoWith(null, { rerun_segments: ["16"] });
    expect(st(info, "16")).toEqual({ kind: "redo", text: "待重配" });
    expect(st(info, "6")).toBeNull();
  });
});

describe("D87 synthSummary：一行进度摘要", () => {
  it("在跑 / 完成（数待复听）/ 失败（取原因首行）/ 中断 / 从没跑过", () => {
    const h = new Set<string>();
    expect(synthSummary(infoWith(SYNTH), h, TODAY)?.text).toBe(`重配进行中 1/3 · 段 7 合成中（${SYNTH.note}）`);
    const ok = infoWith({ ...SYNTH, state: "ok", current: null, note: null, finished_at: "2026-10-13T19:09:00", done: { "6": "2026-10-13T19:02:00", "7": "2026-10-13T19:05:00", "15": "2026-10-13T19:09:00" } });
    expect(synthSummary(ok, h, TODAY)).toEqual({ tone: "ok", text: "上次重配 19:09 完成：段 6、7、15（待复听 3 段）" });
    const all = new Set(["6@2026-10-13T19:02:00", "7@2026-10-13T19:05:00", "15@2026-10-13T19:09:00"]);
    expect(synthSummary(ok, all, TODAY)?.text).toBe("上次重配 19:09 完成：段 6、7、15（都已复听）");
    const failed = synthSummary(infoWith({ ...SYNTH, state: "failed", finished_at: "2026-10-12T08:00:00", error: "FAIL 段落 7 不达标\n原文：…" }), h, TODAY);
    expect(failed?.tone).toBe("danger");
    expect(failed?.text).toBe("重配失败 10-12 08:00 段 7，已完成 1/3：FAIL 段落 7 不达标");
    expect(synthSummary(infoWith({ ...SYNTH, state: "interrupted" }), h, TODAY)?.text).toBe("重配中断 19:00 段 7，已完成 1/3（合成进程已不在）");
    expect(synthSummary(infoWith(null), h, TODAY)).toBeNull();
  });

  it("clock：今天只给时分，不是今天带月日", () => {
    expect(clock("2026-10-13T19:02:00", TODAY)).toBe("19:02");
    expect(clock("2026-10-12T19:02:00", TODAY)).toBe("10-12 19:02");
  });
});
