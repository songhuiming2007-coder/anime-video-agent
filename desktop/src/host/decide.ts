// approval.decide 全流程（Spec 8 §2.5、§2.6、§3.2.1、§4.3；冻结顺序）：
// 能力探针 → epKey 映射 → 可达性 → stat 期目录 → 期内 ack 互斥 → HEAL（H4；失败只记诊断）→ 读对象库做陈旧校验
// → stat 关联产物做指纹校验 → 按 §2.6 表 spawn（05 approve 在 ① 与 ② 之间做解封物指纹核验）→ 退出码 → 后置核验
// → finally 推一次 resnapshot。
// 写路径只有 core 一条：host 自己从不写对象库、从不删文件（I1）。
import type { ApprovalRecord, Reach, StopType } from "../shared/contracts";
import { isStopType } from "../shared/contracts";
import type { DecideParams, RpcError } from "../shared/protocol";
import type { EpisodeEntry } from "./episodes";
import type { FsProbe } from "./fsio";
import { gateMatches } from "./gateCheck";
import type { HealResult } from "./heal";
import type { CoreResult, SpawnTag, Template, TemplateArgs } from "./spawner";
import type { StoreRead } from "./store";
import { fingerprintsMatch } from "./store";

export class DecideFail extends Error {
  constructor(readonly error: RpcError) {
    super(error.message);
  }
}

const fail = (code: RpcError["code"], message: string, tails: { stdoutTail?: string; stderrTail?: string } = {}): never => {
  throw new DecideFail({ code, message, ...tails });
};

/** §3.2.1 参数形状（checkParamKeys 只管键集合；这里管值）。不合法 → E_BAD_REQUEST。 */
export function parseDecideParams(raw: Record<string, unknown>): DecideParams {
  const { epKey, approvalId, stop, decision, feedback } = raw;
  if (typeof epKey !== "string" || typeof approvalId !== "string" || !approvalId.trim()) fail("E_BAD_REQUEST", "epKey / approvalId 不合法");
  if (!isStopType(stop)) fail("E_BAD_REQUEST", `未知停机点 ${String(stop)}`);
  if (decision === "approve") {
    if (feedback !== undefined) fail("E_BAD_REQUEST", "approve 不带 feedback");
    return { epKey: epKey as string, approvalId: approvalId as string, stop: stop as StopType, decision };
  }
  if (decision !== "reject") fail("E_BAD_REQUEST", `未知决定 ${String(decision)}`);
  if (feedback === null || typeof feedback !== "object" || Array.isArray(feedback)) fail("E_BAD_REQUEST", "reject 必须带 feedback { target, problem }");
  const f = feedback as Record<string, unknown>;
  const keys = Object.keys(f).sort();
  if (keys.length !== 2 || keys[0] !== "problem" || keys[1] !== "target") fail("E_BAD_REQUEST", "feedback 只含 target 与 problem");
  if (typeof f.target !== "string" || typeof f.problem !== "string" || !f.target.trim() || !f.problem.trim()) {
    fail("E_BAD_REQUEST", "「哪段」与「问题」trim 后都不能为空");
  }
  return { epKey: epKey as string, approvalId: approvalId as string, stop: stop as StopType, decision: "reject", feedback: { target: f.target as string, problem: f.problem as string } };
}

/** approve：PENDING，或 artifact 已对齐、待确认（Spec 3 v0.4 确认路径）；reject：PENDING。 */
export function ackable(obj: ApprovalRecord, decision: DecideParams["decision"]): boolean {
  if (obj.status === "pending") return true;
  return decision === "approve" && obj.status === "approved" && obj.resolved_by === "artifact" && obj.confirmed_by === null;
}

/** 后置核验：目标对象确实到达预期状态。reject 的 feedback 两字段逐字节相等。 */
export function verified(obj: ApprovalRecord | undefined, p: DecideParams): boolean {
  if (!obj) return false;
  if (p.decision === "approve") return obj.status === "approved" && (obj.resolved_by === "cli" || obj.confirmed_by === "cli");
  return obj.status === "rejected" && obj.feedback !== null && obj.feedback.target === p.feedback.target && obj.feedback.problem === p.feedback.problem;
}

export type Step = { [T in Template]: { t: T; args: TemplateArgs[T] } }[Template];

/** §2.6 表：05 PENDING = [REVIEW_APPROVE, APPROVE]；05 已对齐待确认 = [APPROVE]；其余停机点一步。 */
export function plan(p: DecideParams, obj: ApprovalRecord, ep: string): Step[] {
  if (p.decision === "reject") {
    return [{ t: "REJECT", args: { ep, stop: p.stop, approvalId: p.approvalId, target: p.feedback.target, problem: p.feedback.problem } }];
  }
  const approve: Step = { t: "APPROVE", args: { ep, stop: p.stop, approvalId: p.approvalId } };
  if (p.stop === "05" && obj.status === "pending") {
    const clips = obj.artifacts.find((a) => a.path === "04-clips.json");
    if (!clips) fail("E_STALE", "对象未钉住 04-clips.json 的指纹，无法核验审阅版本，请刷新");
    return [{ t: "REVIEW_APPROVE", args: { ep, size: clips!.size, mtimeNs: clips!.mtime_ns } }, approve];
  }
  return [approve];
}

export interface DecideCtx {
  isPackaged: boolean;
  capApprovals: () => boolean;
  capDetail: () => string;
  episode: (epKey: string) => EpisodeEntry | undefined;
  reach: () => Reach;
  reachDetail: () => string;
  existsDir: (abs: string) => boolean;
  refreshEpisodes: () => void;
  /** 期内 ack 互斥（同期 ack 串行）；repoRoot 切换中也返回 false */
  tryAcquire: (epKey: string) => boolean;
  release: (epKey: string) => void;
  heal: (epKey: string, decideId: number) => Promise<HealResult>;
  diag: (msg: string) => void;
  testHook: (name: string) => Promise<void>;
  readStore: (ep: EpisodeEntry) => StoreRead;
  fs: FsProbe;
  run: <T extends Template>(t: T, args: TemplateArgs[T], tag: SpawnTag) => Promise<CoreResult>;
  resnapshot: (epKey: string) => Promise<void>;
}

const tailsOf = (r: CoreResult) => ({ stdoutTail: r.stdoutTail, stderrTail: r.stderrTail });

function findById(r: StoreRead, id: string): ApprovalRecord | undefined {
  return r.state === "ok" ? r.items.find((o) => o.approval_id === id) : undefined;
}

export async function decide(ctx: DecideCtx, p: DecideParams, decideId: number): Promise<{ approvalId: string; decision: DecideParams["decision"] }> {
  if (!ctx.capApprovals()) fail("E_CAPABILITY", ctx.capDetail()); // 闸 1
  const ep = ctx.episode(p.epKey);
  if (!ep) fail("E_BAD_REQUEST", `未知期 ${p.epKey}`);
  if (ctx.reach() !== "ok") fail("E_UNREACHABLE", ctx.reachDetail());
  if (!ctx.existsDir(ep!.abs)) {
    ctx.refreshEpisodes(); // 红队 m7：期目录改名后传旧绝对路径会让 resolve_episode_target 走到 rglob
    fail("E_STALE", "期目录已不存在");
  }
  if (!ctx.tryAcquire(p.epKey)) fail("E_BUSY", "该期已有操作在途，或正在切换仓库");
  try {
    // TA-4 / TA-9 变体 2：终端 ack 或产物改写发生在 H4 之前（UI 的 1 s 轮询会先把卡片刷掉，只能在这里确定地复现）
    if (!ctx.isPackaged) await ctx.testHook("before-heal");
    const h = await ctx.heal(p.epKey, decideId); // H4（红队 B1/B5）
    if (!h.ok) ctx.diag(`ack 前自愈失败（${p.epKey}），继续由 core 侧 --id 兜底：${h.stderrTail.trim().slice(-400)}`); // 红队 m8
    if (!ctx.isPackaged) await ctx.testHook("after-heal"); // TA-9 变体 3 在此改写产物，单独考 fingerprintsMatch
    const obj = findById(ctx.readStore(ep!), p.approvalId); // 闸 2：对象
    if (!obj || obj.type !== p.stop || !ackable(obj, p.decision)) fail("E_STALE", "审批对象已不在可操作状态（可能已被处理或替换），请以刷新后的卡片为准");
    if (!fingerprintsMatch(ep!.abs, obj!.artifacts, ctx.fs)) fail("E_STALE", "关联产物在你审阅之后已变更（指纹不符），请重新审阅"); // 闸 2：指纹
    if (p.decision === "approve" && p.stop === "05" && obj!.status === "approved" && !gateMatches(ep!.abs, obj!.artifacts, ctx.fs)) {
      fail("E_GATE_MISMATCH", "批准文件对应的不是你审阅的版本"); // 已对齐待确认：先核验再只做确认
    }
    if (!ctx.isPackaged) await ctx.testHook("after-fingerprint-check"); // 全部 host 侧校验之后、第一个 spawn 之前
    for (const s of plan(p, obj!, ep!.abs)) {
      const r = await ctx.run(s.t, s.args, { decide: decideId });
      if (r.timedOut) fail("E_TIMEOUT", "结果未知，请以稍后刷新为准", tailsOf(r));
      if (r.code !== 0) fail("E_CORE", `core 退出码 ${r.code ?? `信号 ${r.signal}`}`, tailsOf(r));
      if (s.t === "REVIEW_APPROVE" && !gateMatches(ep!.abs, obj!.artifacts, ctx.fs)) {
        fail("E_GATE_MISMATCH", "批准文件对应的不是你审阅的版本"); // 闸 3：第 ② 步不 spawn；host 不删除该文件
      }
    }
    if (!verified(findById(ctx.readStore(ep!), p.approvalId), p)) fail("E_UNVERIFIED", "目标对象未到达预期状态（可能已被替换），请以对象库为准"); // 闸 4
    return { approvalId: p.approvalId, decision: p.decision };
  } finally {
    ctx.release(p.epKey);
    await ctx.resnapshot(p.epKey); // 只此一处（红队 m10）
  }
}
