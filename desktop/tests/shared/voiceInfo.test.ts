// TD-2 parseVoiceInfo 容错（Spec 11 §4.2/§7.2）：未知字段忽略；缺必需字段 malformed。
// 真实 core 输出的契约对拍在 tests/host/stopPoint.test.ts（需要夹具 repo）。
import { describe, expect, it } from "vitest";
import { lockState, parseVoiceInfo } from "../../src/shared/voiceInfo";

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
