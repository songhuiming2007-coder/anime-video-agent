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
  | "episode.create" // { name } → { epKey }：spawn NEW_EPISODE；校验全在 core（Spec 10 S8-R2）
  // ---- Spec 10 S8-R2：会话方法（§3.1）----
  | "conv.send" // { convKey, text } → { turnId }：无活进程则先起 new 会话、等 ready
  | "conv.resume" // { convKey } → ConvSnapshot：仅 ep:*；以 --continue 起会话；已有活进程 → E_BUSY
  | "conv.interrupt" // { convKey, turnId }：turnId 须等于 host 记录的当前回合，否则 E_STALE 且零写入
  | "conv.answer" // { convKey, requestId, decision, feedback? } → { decision }：§2.4 第 3、5 层
  | "conv.command" // { convKey, name, arg? }：name ∈ {memory_ack, scope}
  | "conv.end" // { convKey } → { code, signal }：§2.10 结束序列
  | "conv.snapshot"; // { convKey } → ConvSnapshot

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
};

export const METHODS = Object.keys(PARAM_KEYS) as Method[];

export function isMethod(v: unknown): v is Method {
  return typeof v === "string" && Object.prototype.hasOwnProperty.call(PARAM_KEYS, v);
}

/** exact-keys 校验：params 必须是普通对象（无参方法允许 undefined/{}），键集合恰在闭集内且必填齐全，值为字符串（feedback 除外）。 */
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
    if (k === "feedback") continue;
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
