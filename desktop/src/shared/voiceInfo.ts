// `/voice-info` JSON 的容错解析（Spec 11 §3.3/§4.2）。纯函数、零 DOM/Node。
// 纪律与 Spec 8 §3.1 规则 1-3 同精神：未知字段忽略；缺必需字段或类型不符 → malformed。
// 段表与 wav 文件名由 core 单源供给（RF-1）——TS 不解析稿件、不拼规则。
import type { ApplyPatchLockJson, SynthProgressJson, VoiceHeteronymJson, VoiceInfoJson, VoiceSegmentJson } from "./protocol";

export type VoiceInfoParse = { ok: true; info: VoiceInfoJson } | { ok: false };

const isObj = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const str = (v: unknown): v is string => typeof v === "string";
const num = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const bool = (v: unknown): v is boolean => typeof v === "boolean";

function parseSegment(v: unknown): VoiceSegmentJson | null {
  if (!isObj(v)) return null;
  const { label, index, wav, wav_exists, text, has_attic, mtime } = v;
  if (!str(label) || !num(index) || !str(wav) || !bool(wav_exists) || !str(text) || !bool(has_attic)) return null;
  // D87 新增：缺席按 null；在场就必须是数或 null
  if (mtime !== undefined && mtime !== null && !num(mtime)) return null;
  return { label, index, wav, wav_exists, text, has_attic, mtime: mtime ?? null };
}

const SYNTH_STATES = ["running", "ok", "failed", "interrupted"] as const;
const strOrNull = (v: unknown): v is string | null => v === null || str(v);

/** D87 `synth`：缺席 / null → null（从没跑过）；在场就必须形状完整，否则 malformed（同 D72 纪律） */
function parseSynth(v: unknown): SynthProgressJson | null | undefined {
  if (v === undefined || v === null) return null;
  if (!isObj(v)) return undefined;
  const { state, started_at, finished_at, queue, done, current, note, error } = v;
  if (!SYNTH_STATES.includes(state as SynthProgressJson["state"])) return undefined;
  if (![started_at, finished_at, current, note, error].every(strOrNull)) return undefined;
  if (!Array.isArray(queue) || !queue.every(str) || !isObj(done) || !Object.values(done).every(str)) return undefined;
  return {
    state: state as SynthProgressJson["state"],
    started_at: started_at as string | null,
    finished_at: finished_at as string | null,
    queue: queue as string[],
    done: done as Record<string, string>,
    current: current as string | null,
    note: note as string | null,
    error: error as string | null,
  };
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
  const synth = parseSynth(raw.synth);
  if (synth === undefined) return { ok: false };
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
      synth,
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

/** 待重配的段（D72 起的两层：期级待应用条目 + core 现算的「普通重跑会重配」），按稿件顺序 */
export function redoSegments(info: VoiceInfoJson): string[] {
  const pending = new Set(info.pending_corrections.map((c) => String(c.segment)));
  const rerun = new Set(info.rerun_segments ?? []);
  return info.segments.map((s) => s.label).filter((l) => pending.has(l) || rerun.has(l));
}

/** 「已复听」的记账键：同一段每重配一次换一个键，上一轮听过不算这一轮 */
export const heardKey = (label: string, doneAt: string) => `${label}@${doneAt}`;

export type SegStatusKind = "running" | "queued" | "failed" | "interrupted" | "unfinished" | "redo" | "redone" | "heard";
export interface SegStatus {
  kind: SegStatusKind;
  text: string;
  /** 悬停全文（重试原因、失败原因） */
  title?: string;
}

/** ISO 本地时刻 → 「19:02」；不是今天就带日期「10-13 19:02」 */
export function clock(iso: string, today: string): string {
  const hm = iso.slice(11, 16);
  return iso.slice(0, 10) === today ? hm : `${iso.slice(5, 10)} ${hm}`;
}

/**
 * D87：一段在 03.5 预览栏上显示什么状态。优先级从高到低：
 * 合成中 > 排队中 > 失败 / 中断 / 本轮未完成 > 待重配 > 已重配·待复听 / 已复听。
 * 「待重配」压过「已重配」：重配完又标了新错，眼下要做的是再配一次，不是去听上一版。
 */
export function segStatus(info: VoiceInfoJson, label: string, heard: ReadonlySet<string>, today: string): SegStatus | null {
  const sy = info.synth;
  const doneAt = sy?.done[label];
  if (sy !== null && sy.queue.includes(label) && doneAt === undefined) {
    if (sy.state === "running") {
      if (sy.current === label) return { kind: "running", text: "合成中", title: sy.note ?? undefined };
      return { kind: "queued", text: `排队 ${sy.queue.indexOf(label) + 1}/${sy.queue.length}` };
    }
    if (sy.current === label) {
      return sy.state === "failed"
        ? { kind: "failed", text: "重配失败", title: sy.error ?? undefined }
        : { kind: "interrupted", text: "重配中断", title: "合成进程已不在（被杀、断电或重启），这一段没配完" };
    }
    if (sy.state !== "ok") return { kind: "unfinished", text: "本轮未配到" };
  }
  if (redoSegments(info).includes(label)) return { kind: "redo", text: "待重配" };
  if (doneAt !== undefined) {
    return heard.has(heardKey(label, doneAt))
      ? { kind: "heard", text: `已重配 ${clock(doneAt, today)} · 已复听` }
      : { kind: "redone", text: `已重配 ${clock(doneAt, today)} · 待复听` };
  }
  return null;
}

/** D87：段落列表上方的一行进度摘要；从没跑过 → null */
export function synthSummary(info: VoiceInfoJson, heard: ReadonlySet<string>, today: string): { tone: "wait" | "ok" | "danger" | "warn"; text: string; title?: string } | null {
  const sy = info.synth;
  if (sy === null) return null;
  const doneLabels = sy.queue.filter((l) => sy.done[l] !== undefined);
  const n = `${doneLabels.length}/${sy.queue.length}`;
  if (sy.state === "running") {
    const cur = sy.current === null ? "" : ` · 段 ${sy.current} 合成中`;
    return { tone: "wait", text: `重配进行中 ${n}${cur}${sy.note ? `（${sy.note}）` : ""}` };
  }
  const when = sy.finished_at ?? sy.started_at;
  const at = when === null ? "" : `${clock(when, today)} `;
  if (sy.state === "ok") {
    const unheard = doneLabels.filter((l) => !heard.has(heardKey(l, sy.done[l]))).length;
    return { tone: "ok", text: `上次重配 ${at}完成：段 ${doneLabels.join("、")}${unheard > 0 ? `（待复听 ${unheard} 段）` : "（都已复听）"}` };
  }
  const where = sy.current === null ? "" : `段 ${sy.current}`;
  if (sy.state === "failed") {
    const why = (sy.error ?? "").split("\n")[0];
    return { tone: "danger", text: `重配失败 ${at}${where}，已完成 ${n}${why ? `：${why}` : ""}`, title: sy.error ?? undefined };
  }
  return { tone: "warn", text: `重配中断 ${at}${where}，已完成 ${n}（合成进程已不在）` };
}
