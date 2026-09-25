// 上游契约的 TS 镜像（Spec 8 §3.1）。Python 侧是唯一定义处：
//   Event          → pipeline/jobs.py EventType / Event.to_jsonl_line
//   Approval       → pipeline/approvals.py Approval.to_dict
//   EpisodeStatus  → pipeline/status.py EpisodeStatus（asdict）
// 内部形状（*Record，mtime_ns 为 bigint）只在 host 内流通；出 host 一律经 toWire* 转成 *Json（§3.1 规则 9）。

export type StopType = "02.5" | "03.5" | "05" | "09";
export const STOP_TYPES: readonly StopType[] = ["02.5", "03.5", "05", "09"];

export function isStopType(v: unknown): v is StopType {
  return typeof v === "string" && (STOP_TYPES as readonly string[]).includes(v);
}

// pipeline/jobs.py EventType：Spec 2 的 9 个值，外加 Spec 5 增补的 browser_session_started
export const EVENT_TYPES = [
  "job_created",
  "job_blocked",
  "job_started",
  "job_heartbeat",
  "job_finished",
  "approval_requested",
  "approval_resolved",
  "human_time_recorded",
  "sidecar_degraded",
  "browser_session_started",
] as const;
export type EventType = (typeof EVENT_TYPES)[number];

export interface ArtifactFingerprint {
  path: string;
  size: number;
  mtime_ns: bigint;
}

export interface ArtifactFingerprintJson {
  path: string;
  size: number;
  mtime_ns: string;
}

interface ApprovalCommon {
  approval_id: string;
  episode: string;
  type: string; // 规则 5：只认 StopType，其他值只读显示
  status: string; // pending | approved | rejected | superseded
  options: string[];
  created_at: string;
  resolved_at: string | null;
  resolved_by: string | null;
  confirmed_by: string | null;
  confirmed_at: string | null;
  feedback: { target: string; problem: string } | null;
  note: string;
}

export interface ApprovalRecord extends ApprovalCommon {
  artifacts: ArtifactFingerprint[];
}

export interface ApprovalJson extends ApprovalCommon {
  artifacts: ArtifactFingerprintJson[];
}

interface EventCommon {
  event_id: string;
  timestamp: string;
  episode: string;
  type: string;
  /** 规则 2：未知 type 保留为 unknown 并原样显示 */
  kind: EventType | "unknown";
}

/** host 内部形状：payload 里的 mtime_ns 为 bigint */
export interface EventRecord extends EventCommon {
  payload: Record<string, unknown>;
}

/** 线上形状：payload 里的 bigint 已转十进制字符串 */
export interface EventJson extends EventCommon {
  payload: Record<string, unknown>;
}

export interface EpisodeStatusJson {
  episode_dir: string;
  episode_name: string;
  current_step: string;
  is_blocked: boolean;
  block_reason: string | null;
  completed_steps: string[];
  next_action: string;
  next_command: string | null;
  docs_ref: string;
  advisories: string[];
}

export type Reach = "ok" | "volume-unmounted" | "missing" | "skeleton-incomplete" | "permission-denied";
