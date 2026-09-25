// renderer ↔ host 的 MessagePort 自有信封（Spec 8 §3.2，v1 冻结）。
// 方法闭集：新增方法 = 修订 spec。任何方法的参数里都没有文件系统路径字段（红队 R2-M5）。
import type { ApprovalJson, EpisodeStatusJson, EventJson, Reach, StopType } from "./contracts";

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
  | "approval.decide"; // §3.2.1（PR4）

export type ErrCode =
  | "E_BAD_REQUEST"
  | "E_UNREACHABLE"
  | "E_CAPABILITY"
  | "E_STALE"
  | "E_BUSY"
  | "E_CORE"
  | "E_GATE_MISMATCH"
  | "E_UNVERIFIED"
  | "E_TIMEOUT";

export type PushTopic = "episode.delta" | "episode.snapshot" | "episodes.summary" | "reach" | "diag";

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
  | { v: 1; kind: "push"; topic: PushTopic; epKey?: string; generation?: number; seq?: number; data: unknown };

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
  "approval.decide": { required: ["epKey", "approvalId", "stop", "decision"], optional: ["feedback"] },
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
export type DecideParams =
  | { epKey: string; approvalId: string; stop: StopType; decision: "approve" }
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
export interface EpisodeSummary {
  epKey: string;
  mtimeMs: number;
  /** 后台期徽标：只显示 status --json 的 current_step 原文（§2.12），未取到时为 null */
  currentStep: string | null;
  isBlocked: boolean | null;
}

export interface EpisodesList {
  reach: Reach;
  reachDetail: string;
  episodes: EpisodeSummary[];
  hiddenUnderscore: number;
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
