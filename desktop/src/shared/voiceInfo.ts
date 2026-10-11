// `/voice-info` JSON 的容错解析（Spec 11 §3.3/§4.2）。纯函数、零 DOM/Node。
// 纪律与 Spec 8 §3.1 规则 1-3 同精神：未知字段忽略；缺必需字段或类型不符 → malformed。
// 段表与 wav 文件名由 core 单源供给（RF-1）——TS 不解析稿件、不拼规则。
import type { ApplyPatchLockJson, VoiceHeteronymJson, VoiceInfoJson, VoiceSegmentJson } from "./protocol";

export type VoiceInfoParse = { ok: true; info: VoiceInfoJson } | { ok: false };

const isObj = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const str = (v: unknown): v is string => typeof v === "string";
const num = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const bool = (v: unknown): v is boolean => typeof v === "boolean";

function parseSegment(v: unknown): VoiceSegmentJson | null {
  if (!isObj(v)) return null;
  const { label, index, wav, wav_exists, text, has_attic } = v;
  if (!str(label) || !num(index) || !str(wav) || !bool(wav_exists) || !str(text) || !bool(has_attic)) return null;
  return { label, index, wav, wav_exists, text, has_attic };
}

function parseHeteronym(v: unknown): VoiceHeteronymJson | null {
  if (!isObj(v) || !str(v.char) || !Array.isArray(v.readings)) return null;
  const readings = v.readings.filter(str);
  if (readings.length !== v.readings.length) return null;
  return { char: v.char, readings };
}

function parseLock(v: unknown): ApplyPatchLockJson | null {
  if (!isObj(v) || !bool(v.exists)) return null;
  const pid = v.pid === null ? null : num(v.pid) ? v.pid : undefined;
  const alive = v.pid_alive === null ? null : bool(v.pid_alive) ? v.pid_alive : undefined;
  if (pid === undefined || alive === undefined) return null;
  return { exists: v.exists, pid, pid_alive: alive };
}

export function parseVoiceInfo(text: string): VoiceInfoParse {
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch {
    return { ok: false };
  }
  if (!isObj(raw) || raw.v !== 1) return { ok: false };
  if (!str(raw.engine) || !bool(raw.engine_cloud) || !Array.isArray(raw.segments) || !Array.isArray(raw.heteronyms)) return { ok: false };
  if (!Array.isArray(raw.pending_corrections) || !raw.pending_corrections.every(isObj)) return { ok: false };
  const segments = raw.segments.map(parseSegment);
  if (segments.some((s) => s === null)) return { ok: false };
  const heteronyms = raw.heteronyms.map(parseHeteronym);
  if (heteronyms.some((h) => h === null)) return { ok: false };
  const lock = parseLock(raw.apply_patch_lock);
  if (lock === null) return { ok: false };
  // D72 新增字段：缺席按「判不了」（null）处理；在场就必须是字符串数组或 null
  const rerun = raw.rerun_segments;
  if (rerun !== undefined && rerun !== null && !(Array.isArray(rerun) && rerun.every(str))) return { ok: false };
  return {
    ok: true,
    info: {
      v: 1,
      engine: raw.engine,
      engine_cloud: raw.engine_cloud,
      segments: segments as VoiceSegmentJson[],
      heteronyms: heteronyms as VoiceHeteronymJson[],
      pending_corrections: raw.pending_corrections,
      rerun_segments: rerun === undefined || rerun === null ? null : (rerun as string[]),
      apply_patch_lock: lock,
    },
  };
}

/**
 * `.apply_patch.lock` 的三态（Spec 11 §2.3 的「锁残留」显示分支，TI-11）。
 *
 * 只有 `pid_alive === true` 才算真的「进行中」——`write_text` 不是原子写，
 * 崩溃在写一半会让锁文件读不出 pid（core 侧 pid/pid_alive 都是 null），
 * 那种锁同样是残留：判成「进行中」会把面板钉在一个永不给清除路径的分支上，
 * 而此刻纠错链是锁死的。
 */
export function lockState(lock: VoiceInfoJson["apply_patch_lock"]): "none" | "busy" | "stale" {
  if (!lock.exists) return "none";
  return lock.pid_alive === true ? "busy" : "stale";
}
