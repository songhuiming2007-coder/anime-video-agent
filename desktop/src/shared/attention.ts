// D98（人选方案 C，2026-10-15）：AVA 窗口不在前台时，用 Dock 告诉人「它在等你」或「它做完了一件事」。
// 不发系统横幅（人：Dock 弹跳比较好）。纯函数：host 用前半段把会话帧 / 作业变化分类，main 用后半段算 Dock 动作。
import type { JobView } from "./protocol";

/** wait = 不回去就卡住（审批卡、停机点、失败）；info = 做完了，回不回去都行 */
export type AttentionKind = "wait" | "info";
export interface Attention {
  kind: AttentionKind;
  /** 去重键：同一张卡、同一回合、同一作业只算一次 */
  key: string;
}

/**
 * 回合结束帧（`turn_finished.stopped`）→ 类别。
 * done = 正常答复；interrupted = 人自己点了停止、command = 斜杠命令，人就在窗口前，不打扰；
 * 其余（error / degraded / rollback / blocked …）都是回合没走完，按等人处理——失败了不回去就不会继续。
 */
export function turnAttention(stopped: string): AttentionKind | null {
  if (stopped === "done") return "info";
  if (stopped === "interrupted" || stopped === "command") return null;
  return "wait";
}

/** 前后两次折叠的作业表 → 新结束的作业（成功 info、失败 wait）。只认「上次在跑、这次结束」，开机首折不补报旧作业 */
export function jobAttentions(epKey: string, prev: readonly JobView[], next: readonly JobView[]): Attention[] {
  const wasRunning = new Set(prev.filter((j) => j.state === "running").map((j) => j.jobId));
  return next
    .filter((j) => wasRunning.has(j.jobId) && (j.state === "succeeded" || j.state === "failed"))
    .map((j) => ({ kind: j.state === "failed" ? "wait" : "info", key: `job:${epKey}:${j.jobId}` }));
}

export type DockAction = { do: "badge"; text: string } | { do: "bounce" } | { do: "cancelBounce" };
export interface DockState {
  unseen: number;
  bouncing: boolean;
  seen: ReadonlySet<string>;
}
export const DOCK_IDLE: DockState = { unseen: 0, bouncing: false, seen: new Set() };
/** 去重表上限：键只在本次窗口失焦期间有意义，切回即清；封顶只防极端情况下无界增长 */
const SEEN_CAP = 500;

/** 收到一条 attention。窗口在前台 → 什么都不做（人正看着）。 */
export function onAttention(state: DockState, a: Attention, focused: boolean): { state: DockState; actions: DockAction[] } {
  if (focused || state.seen.has(a.key)) return { state, actions: [] };
  const seen = new Set(state.seen);
  if (seen.size >= SEEN_CAP) seen.clear();
  seen.add(a.key);
  const unseen = state.unseen + 1;
  const actions: DockAction[] = [{ do: "badge", text: String(unseen) }];
  // 方案 C：等人时 Dock 一直弹（critical），直到人切回来；做完了只加角标
  const bounce = a.kind === "wait" && !state.bouncing;
  if (bounce) actions.push({ do: "bounce" });
  return { state: { unseen, bouncing: state.bouncing || bounce, seen }, actions };
}

/** 窗口回到前台：角标清零、停止弹跳 */
export function onFocus(state: DockState): { state: DockState; actions: DockAction[] } {
  if (state.unseen === 0 && !state.bouncing) return { state: DOCK_IDLE, actions: [] };
  const actions: DockAction[] = [{ do: "badge", text: "" }];
  if (state.bouncing) actions.push({ do: "cancelBounce" });
  return { state: DOCK_IDLE, actions };
}
