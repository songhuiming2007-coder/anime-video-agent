// renderer ↔ host 的 MessagePort 自有信封（Spec 8 §3.2，v1 冻结）。
// 方法闭集：新增方法 = 修订 spec。任何方法的参数里都没有文件系统路径字段（红队 R2-M5）。
import type { ApprovalJson, EpisodeStatusJson, EventJson, Reach, StopType } from "./contracts";
import type { OutFrame } from "./convFrames";

export const PROTOCOL_VERSION = 1 as const;

export type Method =
  | "app.health" // repoRoot/python/capabilities/codeFreeze/buildProvenance/reach
  | "app.requestRepoRootChange" // 无参数：由 main 弹原生对话框（PR4）
  | "episodes.list"
  | "episode.subscribe" // { epKey } → EpisodeSnapshot；不触发 heal
  | "episode.activate" // { epKey }：设为活跃期，触发 H1
  | "episode.unsubscribe" // { epKey }
  | "episode.resnapshot" // { epKey } → EpisodeSnapshot（协议跳号时内部使用，不触发 heal）
  | "episode.refresh" // { epKey } → EpisodeSnapshot；用户点击刷新，触发 H3
  | "tree.list" // { epKey, relDir } → TreeEntry[]
  | "shots.list" // 无参数 → ShotsEntry[]：data/library/shots 顶层的 *.html（S21 修订）
  | "approval.decide" // §3.2.1（PR4）；09 定稿另带 cover/title（Spec 12 S8-R19）
  | "episode.create" // { name } → { epKey, migrated, sid, messages }：spawn NEW_EPISODE（恒带 --from-idea，Spec 18 §3.3）；校验全在 core（Spec 10 S8-R2）
  // ---- Spec 10 S8-R2：会话方法（§3.1）----
  | "conv.send" // { convKey, text } → { turnId }：无活进程则先起 new 会话、等 ready
  | "conv.resume" // { convKey } → ConvSnapshot：ep:* 以 --continue 起会话（idea 以 --idea 起，core 启动即恒恢复，Spec 18 §3.3）；已有活进程 → E_BUSY
  | "conv.interrupt" // { convKey, turnId }：turnId 须等于 host 记录的当前回合，否则 E_STALE 且零写入
  | "conv.answer" // { convKey, requestId, decision, feedback? } → { decision }：§2.4 第 3、5 层
  | "conv.command" // { convKey, name, arg? }：name ∈ {memory_ack, scope}
  | "conv.end" // { convKey } → { code, signal }：§2.10 结束序列
  | "conv.snapshot" // { convKey } → ConvSnapshot
  // ---- D45：会话管理（仅 ep:*；idea 是单一滚动段，E_BAD_REQUEST）----
  | "conv.sessions" // { convKey } → SessionRow[]（spawn LIST_SESSIONS，纯读）；live = 当前活进程的会话
  | "conv.enter" // { convKey, sid } → ConvSnapshot：有回合在跑 E_BUSY；先结束空闲活会话，再 --continue <sid>
  | "conv.fresh" // { convKey } → ConvSnapshot：结束活会话并清空对话区；下一条消息开新会话
  | "conv.delete" // { convKey, sid } → { deleted, moved }：原生确认框后 spawn DELETE_SESSION（移进 _agent/session-trash/）
  // ---- D66：回收站查看与选择性彻底清空（人裁决：恢复不做 / 逐段确认 / 仅人手动）----
  | "conv.trashList" // { convKey } → TrashRow[]（spawn LIST_TRASH，纯读）
  | "conv.purgeTrash" // { convKey, file } → { purged }：原生确认框（写明不可恢复）后 spawn PURGE_TRASH
  // ---- Spec 11 §4.3/§4.4：02.5 编辑器 / 03.5 顺听 / 人时；Spec 12 §4.2：封面导入 ----
  | "script.stat" // { epKey } → ScriptStatJson：打开编辑器时的基线指纹（只读）
  | "script.save" // { epKey, text, expectSize, expectMtimeNs } → SavedFingerprintJson（spawn SAVE_SCRIPT）
  | "script.seal" // { epKey } → { bytes }（spawn SEAL_SCRIPT）
  | "script.check" // { epKey } → { code, stdoutTail, stderrTail }（spawn CHECK_SCRIPT，UI 不解析判定）
  | "voice.info" // { epKey } → VoiceInfoJson（spawn VOICE_INFO，纯读）
  | "voice.parse" // { epKey, text } → VoicePatchJson（spawn VOICE_PARSE，纯算）
  | "voice.add" // { epKey, text } → 落盘条目 dict（spawn VOICE_ADD，core 重解析原文）
  | "voice.revert" // { epKey, label }（spawn VOICE_REVERT）
  | "voice.retract" // { epKey, id }（spawn VOICE_RETRACT）
  | "voice.applyPatch" // { epKey }：长任务（RUN_TTS_APPLY_PATCH，无超时、退出不发信号）
  // D50-A S6：段落表就地纠错（plans/2026-10-08-preview-voice-build-spec.md）
  | "voice.check" // { epKey, word, pinyin? | homophone?+expect? } → VoiceCheckJson（spawn VOICE_CHECK，只读）
  | "voice.global" // { epKey, word, pinyin? | homophone?+expect?, supersede? } → VoiceCheckJson：原生确认框后 spawn VOICE_GLOBAL（写 config/voice.json 一个键）
  | "voice.retts" // { epKey }：长任务，原生确认框后 spawn RUN_TTS（普通重跑，只重配念法变了的段）
  | "voice.review" // { epKey, review: "voice=N,prosody=N,misread=N" } → { lines }（spawn TTS_REVIEW；D50-A S7：03.5 卡内打点）
  | "time.surface" // { epKey, stop, visible }：审阅面可见性（计时口径的唯一入口，Spec 11 §2.4）
  | "time.read" // { epKey } → TimeReadJson（host 直读 human_time.json）
  | "cover.import"; // { epKey, name, bytes } → ImportedCoverJson（spawn IMPORT_COVER，字节走 stdin）

export type ErrCode =
  | "E_BAD_REQUEST"
  | "E_UNREACHABLE"
  | "E_CAPABILITY"
  | "E_STALE"
  | "E_BUSY"
  | "E_CORE"
  | "E_GATE_MISMATCH"
  | "E_UNVERIFIED"
  | "E_TIMEOUT"
  // Spec 10 S8-R2：会话进程未就绪 / 已退出 / 回了协议错误（附原错误码与原文）；期租约被占
  | "E_SESSION"
  | "E_SESSION_LOCKED";

export type PushTopic = "episode.delta" | "episode.snapshot" | "episodes.summary" | "reach" | "diag" | "conv.snapshot" | "conv.delta";

export interface RpcError {
  code: ErrCode;
  message: string;
  stdoutTail?: string;
  stderrTail?: string;
}

export type Envelope =
  | { v: 1; kind: "req"; id: number; method: Method; params: unknown }
  | { v: 1; kind: "res"; id: number; ok: true; result: unknown }
  | { v: 1; kind: "res"; id: number; ok: false; error: RpcError }
  | { v: 1; kind: "push"; topic: PushTopic; epKey?: string; convKey?: string; generation?: number | string; seq?: number; data: unknown };

/** 逐方法参数键闭集（exact-keys：多一个、少一个都拒，TI-8）。 */
export const PARAM_KEYS: Record<Method, { required: readonly string[]; optional: readonly string[] }> = {
  "app.health": { required: [], optional: [] },
  "app.requestRepoRootChange": { required: [], optional: [] },
  "episodes.list": { required: [], optional: [] },
  "episode.subscribe": { required: ["epKey"], optional: [] },
  "episode.activate": { required: ["epKey"], optional: [] },
  "episode.unsubscribe": { required: ["epKey"], optional: [] },
  "episode.resnapshot": { required: ["epKey"], optional: [] },
  "episode.refresh": { required: ["epKey"], optional: [] },
  "tree.list": { required: ["epKey", "relDir"], optional: [] },
  "shots.list": { required: [], optional: [] },
  "approval.decide": { required: ["epKey", "approvalId", "stop", "decision"], optional: ["feedback", "cover", "title"] },
  "episode.create": { required: ["name"], optional: [] },
  "conv.send": { required: ["convKey", "text"], optional: [] },
  "conv.resume": { required: ["convKey"], optional: [] },
  "conv.interrupt": { required: ["convKey", "turnId"], optional: [] },
  // feedback 的类型由 host 自行校验（checkParamKeys 对名为 feedback 的键跳过类型检查，§3.1 🔵-1）
  "conv.answer": { required: ["convKey", "requestId", "decision"], optional: ["feedback"] },
  "conv.command": { required: ["convKey", "name"], optional: ["arg"] },
  "conv.end": { required: ["convKey"], optional: [] },
  "conv.snapshot": { required: ["convKey"], optional: [] },
  "conv.sessions": { required: ["convKey"], optional: [] },
  "conv.enter": { required: ["convKey", "sid"], optional: [] },
  "conv.fresh": { required: ["convKey"], optional: [] },
  "conv.delete": { required: ["convKey", "sid"], optional: [] },
  "conv.trashList": { required: ["convKey"], optional: [] },
  "conv.purgeTrash": { required: ["convKey", "file"], optional: [] },
  "script.stat": { required: ["epKey"], optional: [] },
  "script.save": { required: ["epKey", "text", "expectSize", "expectMtimeNs"], optional: [] },
  "script.seal": { required: ["epKey"], optional: [] },
  "script.check": { required: ["epKey"], optional: [] },
  "voice.info": { required: ["epKey"], optional: [] },
  "voice.parse": { required: ["epKey", "text"], optional: [] },
  "voice.add": { required: ["epKey", "text"], optional: [] },
  "voice.revert": { required: ["epKey", "label"], optional: [] },
  "voice.retract": { required: ["epKey", "id"], optional: [] },
  "voice.applyPatch": { required: ["epKey"], optional: [] },
  "voice.check": { required: ["epKey", "word"], optional: ["pinyin", "homophone", "expect"] },
  "voice.global": { required: ["epKey", "word"], optional: ["pinyin", "homophone", "expect", "supersede"] },
  "voice.retts": { required: ["epKey"], optional: [] },
  "voice.review": { required: ["epKey", "review"], optional: [] },
  "time.surface": { required: ["epKey", "stop", "visible"], optional: [] },
  "time.read": { required: ["epKey"], optional: [] },
  "cover.import": { required: ["epKey", "name", "bytes"], optional: [] },
};

export const METHODS = Object.keys(PARAM_KEYS) as Method[];

export function isMethod(v: unknown): v is Method {
  return typeof v === "string" && Object.prototype.hasOwnProperty.call(PARAM_KEYS, v);
}

/** 值不是字符串的参数键（§3.1 唯一例外）：feedback 是对象，bytes 是封面图字节。 */
export const NON_STRING_PARAM_KEYS: readonly string[] = ["feedback", "bytes"];

/** exact-keys 校验：params 必须是普通对象（无参方法允许 undefined/{}），键集合恰在闭集内且必填齐全，值为字符串（NON_STRING_PARAM_KEYS 除外）。 */
export function checkParamKeys(method: Method, params: unknown): string | null {
  const spec = PARAM_KEYS[method];
  if (params === undefined || params === null) {
    return spec.required.length === 0 ? null : "缺少参数";
  }
  if (typeof params !== "object" || Array.isArray(params)) return "参数必须是对象";
  const keys = Object.keys(params);
  const allowed = new Set([...spec.required, ...spec.optional]);
  for (const k of keys) if (!allowed.has(k)) return `未知参数键 ${k}`;
  for (const k of spec.required) if (!keys.includes(k)) return `缺少参数键 ${k}`;
  for (const k of keys) {
    const v = (params as Record<string, unknown>)[k];
    if (NON_STRING_PARAM_KEYS.includes(k)) continue;
    if (typeof v !== "string") return `参数 ${k} 必须是字符串`;
  }
  return null;
}

// ---- §3.2.1 approval.decide ----
export type FinalizeInput = { cover: string; title: string };
export type DecideParams =
  | { epKey: string; approvalId: string; stop: StopType; decision: "approve"; finalize?: FinalizeInput | null }
  | { epKey: string; approvalId: string; stop: StopType; decision: "reject"; feedback: { target: string; problem: string } };

// ---- §3.3 snapshot / delta ----
export type SnapshotApprovals =
  | { state: "absent" }
  | { state: "unsupported" }
  | { state: "ok"; items: ApprovalJson[]; skipped: number; healedAt: string | null }
  | { state: "error"; message: string; lastGood: ApprovalJson[] | null };

export type SnapshotStatus = { ok: true; value: EpisodeStatusJson } | { ok: false; code: ErrCode; message: string };

export interface JobView {
  jobId: string;
  command: string | null;
  state: "pending" | "running" | "succeeded" | "failed" | "blocked";
  lastEventAt: string;
  /** D71：job_started 事件的时间戳（作业条算已运行时长）；没见过 started 为 null */
  startedAt: string | null;
  pid: number | null;
  returncode: number | null;
  durationS: number | null;
  stderrTail: string | null;
  message: string | null;
  noFollowupEvents: boolean;
  finishedEventMissing: boolean;
}

export interface DegradedNotice {
  at: string;
  droppedDuringCircuit: number;
}

export interface EpisodeSnapshot {
  epKey: string;
  generation: number;
  seq: 0;
  reach: Reach;
  status: SnapshotStatus;
  approvals: SnapshotApprovals;
  events: {
    state: "absent" | "ok";
    offset: number;
    truncatedHead: boolean;
    items: EventJson[];
    malformed: number;
    episodeFieldMismatch: number;
  };
  jobs: JobView[];
  degradedNotices: DegradedNotice[];
}

export interface EpisodeDelta {
  epKey: string;
  generation: number;
  seq: number;
  newEvents: EventJson[];
  approvals?: SnapshotApprovals;
  status?: SnapshotStatus;
  jobs?: JobView[];
  degradedNotices?: DegradedNotice[];
}

// ---- 期列表、产物树、健康数据 ----
/** 会话键（Spec 10 §2.1）：idea 全局至多一个；ep:<期名> 每期至多一个进程 */
export type ConvKey = "idea" | `ep:${string}`;

/** `episode.create` 的结果（Spec 18 §3.3）：migrated 只认 core 的单行 marker，缺失/不合式按 false。 */
export interface CreatedEpisode {
  epKey: string;
  migrated: boolean;
  /** 迁入段的会话号；未带入为 null */
  sid: string | null;
  /** 迁入段的消息数（core 的 SessionSummary.messages）；未带入为 0 */
  messages: number;
}

/** core `ava new --from-idea` 的机器可读标注（Spec 18 §3.2 🔵-2）：stdout 恰好一行。 */
const FROM_IDEA_MARKER_RE = /^\[from-idea\] migrated=(true|false) sid=([0-9a-f]+|-) messages=(\d+)$/;

/**
 * 从 core stdout 里取 marker：以 `[from-idea]` 开头的行必须恰好一行且合式，否则 null（调用方按
 * migrated=false 处理并发 notice——建期本身已成功，不许因解析失败把期卡死）。
 * migrated=false 时 sid 须为 `-`、messages 须为 0；migrated=true 时 sid 须为十六进制。
 */
export function parseFromIdeaMarker(stdout: string): Omit<CreatedEpisode, "epKey"> | null {
  const lines = stdout.split(/\r?\n/).filter((l) => l.startsWith("[from-idea]"));
  if (lines.length !== 1) return null;
  const m = FROM_IDEA_MARKER_RE.exec(lines[0]);
  if (!m) return null;
  const migrated = m[1] === "true";
  const messages = Number(m[3]);
  if (migrated ? m[2] === "-" : m[2] !== "-" || messages !== 0) return null;
  return { migrated, sid: migrated ? m[2] : null, messages };
}

/** 会话徽标（Spec 10 §2.6）：由 host 从帧折叠得出，随 episodes.summary 下发 */
export interface ConvSummary {
  live: boolean;
  running: boolean;
  openRequests: number;
}

export interface EpisodeSummary {
  epKey: string;
  mtimeMs: number;
  /** 后台期徽标：只显示 status --json 的 current_step 原文（§2.12），未取到时为 null */
  currentStep: string | null;
  isBlocked: boolean | null;
  /** Spec 10 S8-R8：该期的会话徽标；无活会话为 null */
  conv: ConvSummary | null;
}

export interface EpisodesList {
  reach: Reach;
  reachDetail: string;
  episodes: EpisodeSummary[];
  hiddenUnderscore: number;
  /** Spec 10 S8-R8：选题（idea）会话徽标；无活会话为 null */
  idea: ConvSummary | null;
}

/** shots 根顶层的 html（镜头画廊）；name 即 ava-media://shots/<name> 的相对路径 */
export interface ShotsEntry {
  name: string;
  size: number;
  mtimeMs: number;
}

export interface TreeEntry {
  name: string;
  /** 相对期目录的 posix 路径 */
  rel: string;
  kind: "dir" | "file" | "other";
  size: number;
  mtimeMs: number;
}

// ---- Spec 11/12 的窄接口（§4.3/§4.4）----

/** 期目录内单文件的指纹（出 host 一律十进制字符串，Spec 8 §3.1 规则 9）。 */
export interface FingerJson {
  size: number;
  mtimeNs: string;
}

/** 02.5 编辑器的基线指纹：两项都缺席表示「从草稿新建」也不可用。 */
export interface ScriptStatJson {
  script: FingerJson | null;
  draft: FingerJson | null;
}

/** SAVE_SCRIPT 的 stdout（`{"size":…,"mtime_ns":…}`）经 parseLossless 读出的新基线。 */
export interface SavedFingerprintJson {
  size: number;
  mtimeNs: string;
}

/** `/voice-info` schema v1（Spec 11 §3.3，冻结）。 */
export interface VoiceSegmentJson {
  label: string;
  index: number;
  wav: string;
  wav_exists: boolean;
  text: string;
  has_attic: boolean;
}

export interface VoiceHeteronymJson {
  char: string;
  readings: string[];
}

export interface ApplyPatchLockJson {
  exists: boolean;
  pid: number | null;
  pid_alive: boolean | null;
}

export interface VoiceInfoJson {
  v: 1;
  engine: string;
  engine_cloud: boolean;
  segments: VoiceSegmentJson[];
  heteronyms: VoiceHeteronymJson[];
  /** 条目 dict 原样透传（消费方忽略未知键，Spec 11 §3.3） */
  pending_corrections: Record<string, unknown>[];
  /** D72：普通重跑会重配的已配段（全局读音表 / 文本 / 钉种子变了），core 从盘上现算；null = 判不了（无清单、引擎与当前配置不符） */
  rerun_segments: string[] | null;
  apply_patch_lock: ApplyPatchLockJson;
}

/** D50-A S6：`pipeline.corrections check|global` 的结果——ok 与 core 打印的原文行（UI 照传，不解析判定） */
export interface VoiceCheckJson {
  ok: boolean;
  lines: string[];
}

/** `/voice-parse` 输出 = Patch 的 8 个字段（不含 raw / seed_pin，Spec 11 §3.3）。 */
export interface VoicePatchJson {
  v: 1;
  segment: number | null;
  kind: string | null;
  word: string | null;
  heard: string | null;
  target_tone3: string | null;
  issue: string | null;
  action: string | null;
  scope: string | null;
}

/** 人时读数（host 直读 human_time.json；Spec 11 §2.4） */
export interface TimeReadJson {
  perStop: Record<string, number>;
  otherMin: number;
  totalMin: number;
  count: number;
}

/** `/import-cover` 的 stdout（Spec 12 §2.1） */
export interface ImportedCoverJson {
  path: string;
  width: number;
  height: number;
  format: string;
}

// ---- 会话状态（Spec 10 §3.1）----
/** host 缓冲的单位，只追加 */
export type ConvEntry =
  | { k: "frame"; at: number; frame: OutFrame }
  | { k: "user"; at: number; text: string }
  | { k: "answered_local"; at: number; requestId: string; decision: string; feedback: string | null }
  | { k: "settled"; at: number; turnId: string; timedOut: boolean }
  | { k: "spawned"; at: number; mode: "new" | "continue" | "idea"; pid: number }
  | { k: "exited"; at: number; code: number | null; signal: string | null; stderrTail: string }
  | { k: "voided_local"; at: number; requestIds: string[]; cause: "session_exited" }
  | { k: "frames_lost"; at: number; count: number; reason: "oversize" | "malformed" };

export type ConvPhase = "none" | "starting" | "idle" | "running" | "ending" | "exited";

export interface ConvSnapshot {
  convKey: ConvKey;
  /** "<hostBootId>:<n>"（🟡-9） */
  generation: string;
  seq: number;
  phase: ConvPhase;
  turnId: string | null;
  entries: ConvEntry[];
  truncatedHead: boolean;
  open: OutFrame[];
  framesLost: number;
  /** host 待结算槽位里的 turnId（§2.7 第 2 条），无则 null；单独保存，截头不影响（四轮 🔵-3） */
  settlePending: string | null;
  /** §2.9 第 6 条：密钥未就绪时头部显示的原因与可复制的命令；就绪或未起会话为 null */
  keyProblem: string | null;
}

export interface ConvDelta {
  convKey: ConvKey;
  generation: string;
  seq: number;
  entries: ConvEntry[];
  phase: ConvPhase;
  turnId: string | null;
  open: OutFrame[];
  keyProblem: string | null;
}

export type Provenance =
  | { kind: "dev" }
  | { kind: "unknown"; message: string }
  | { kind: "consistent"; gitHead: string }
  | { kind: "dirty"; gitHead: string }
  | { kind: "stale"; gitHead: string }
  | { kind: "incomparable"; gitHead: string };

export interface Health {
  protocolVersion: typeof PROTOCOL_VERSION;
  isPackaged: boolean;
  repoRoot: string | null;
  repoRootProblem: string | null;
  repoHead: string | null;
  python: string | null;
  losslessJson: boolean;
  capabilities: { approvals: boolean; approvalsDetail: string };
  codeFreeze: { ok: boolean | null; detail: string };
  buildProvenance: Provenance;
  reach: Reach;
  reachDetail: string;
  diagnostics: string[];
}

/** D45：会话列表的一行（core `/list-sessions` 的字段 + host 标出的 live）。 */
export interface SessionRow {
  sid: string;
  messages: number;
  assistants: number;
  lastActivity: string;
  firstUser: string;
  resumable: boolean;
  live: boolean;
}

/** D66：回收站一行（core `/list-trash` 的字段改名；empty/parseError/partial 见 core `_trash_rows` 判据）。 */
export interface TrashRow {
  file: string;
  sid: string | null;
  lastTs: string;
  messageCount: number;
  bytes: number;
  empty: boolean;
  parseError: boolean;
  partial: boolean;
}
