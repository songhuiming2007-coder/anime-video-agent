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
  const { epKey, approvalId, stop, decision, feedback, cover, title } = raw;
  if (typeof epKey !== "string" || typeof approvalId !== "string" || !approvalId.trim()) fail("E_BAD_REQUEST", "epKey / approvalId 不合法");
  if (!isStopType(stop)) fail("E_BAD_REQUEST", `未知停机点 ${String(stop)}`);
  if (decision === "approve") {
    if (feedback !== undefined) fail("E_BAD_REQUEST", "approve 不带 feedback");
    // 09 定稿：cover 与 title 必须同时给出、trim 后非空（Spec 12 S8-R19）；其余停机点不许带
    const hasCover = typeof cover === "string" && cover.trim() !== "";
    const hasTitle = typeof title === "string" && title.trim() !== "";
    if (stop === "09") {
      if (!hasCover || !hasTitle) fail("E_BAD_REQUEST", "09 定稿必须同时给出封面与标题");
      return { epKey: epKey as string, approvalId: approvalId as string, stop: stop as StopType, decision: "approve", finalize: { cover: cover as string, title: title as string } };
    }
    if (cover !== undefined || title !== undefined) fail("E_BAD_REQUEST", `停机点 ${stop} 不接受 cover / title`);
    return { epKey: epKey as string, approvalId: approvalId as string, stop: stop as StopType, decision: "approve", finalize: null };
  }
  if (decision !== "reject") fail("E_BAD_REQUEST", `未知决定 ${String(decision)}`);
  if (cover !== undefined || title !== undefined) fail("E_BAD_REQUEST", "reject 不带 cover / title");
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
export function plan(p: DecideParams, obj: ApprovalRecord, ep: string, confirmPatch = false): Step[] {
  if (p.decision === "reject") {
    return [{ t: "REJECT", args: { ep, stop: p.stop, approvalId: p.approvalId, target: p.feedback.target, problem: p.feedback.problem } }];
  }
  const approve: Step = {
    t: "APPROVE",
    args: { ep, stop: p.stop, approvalId: p.approvalId, ...(p.finalize ? { cover: p.finalize.cover, title: p.finalize.title } : {}) },
  };
  if (p.stop === "05" && obj.status === "pending") {
    const clips = obj.artifacts.find((a) => a.path === "04-clips.json");
    if (!clips) fail("E_STALE", "对象未钉住 04-clips.json 的指纹，无法核验审阅版本，请刷新");
    const review: Step = { t: "REVIEW_APPROVE", args: { ep, size: clips!.size, mtimeNs: clips!.mtime_ns, ...(confirmPatch ? { confirmPatch: true } : {}) } };
    return [review, approve];
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
  /** D89：原生确认框（同 apply-patch 那套）；补丁段二次确认用 */
  confirm: (title: string, detail: string) => Promise<boolean>;
  resnapshot: (epKey: string) => Promise<void>;
}

const tailsOf = (r: CoreResult) => ({ stdoutTail: r.stdoutTail, stderrTail: r.stderrTail });

function findById(r: StoreRead, id: string): ApprovalRecord | undefined {
  return r.state === "ok" ? r.items.find((o) => o.approval_id === id) : undefined;
}

/** `review --patch-segments` 的 stdout → 段号列表；形状不对返回 null（当 core 出错处理，不猜） */
export function parsePatchSegs(stdout: string): number[] | null {
  try {
    const v: unknown = JSON.parse(stdout.trim().split("\n").pop() ?? "");
    if (typeof v !== "object" || v === null || !Array.isArray((v as { patch_segments?: unknown }).patch_segments)) return null;
    const segs = (v as { patch_segments: unknown[] }).patch_segments;
    return segs.every((n) => Number.isInteger(n)) ? (segs as number[]) : null;
  } catch {
    return null;
  }
}

export async function decide(
  ctx: DecideCtx,
  p: DecideParams,
  decideId: number,
): Promise<{ approvalId: string; decision: DecideParams["decision"]; cancelled?: true }> {
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
    // D89：05 补丁段二次确认（Spec §4.4 闸 3）。此前按钮不带 --confirm-patch、core 去读终端 → EOFError。
    // 补丁段只可能来自本期补丁池（04-patch/）：没有这个目录就不查，普通期 spawn 序列与 D89 前一致。
    let confirmPatch = false;
    if (p.decision === "approve" && p.stop === "05" && obj!.status === "pending" && ctx.existsDir(`${ep!.abs}/04-patch`)) {
      const q = await ctx.run("REVIEW_PATCH_SEGS", { ep: ep!.abs }, { decide: decideId });
      if (q.timedOut) fail("E_TIMEOUT", "补丁段查询超时，未批准", tailsOf(q));
      if (q.code !== 0) fail("E_CORE", `补丁段查询失败：core 退出码 ${q.code ?? `信号 ${q.signal}`}`, tailsOf(q));
      const segs = parsePatchSegs(q.stdoutTail);
      if (segs === null) fail("E_CORE", "补丁段查询输出无法解析，未批准", tailsOf(q));
      if (segs!.length > 0) {
        const ok = await ctx.confirm(
          `本期有 ${segs!.length} 个补丁段，确认已逐段看过？`,
          `段 ${segs!.join("、")} 用的是本期补丁池里的外部素材 / 补丁画面，不是正片检索出来的。确认每一段画面都核对过再批准。`,
        );
        if (!ok) return { approvalId: p.approvalId, decision: p.decision, cancelled: true }; // 人取消：不 spawn、不写
        confirmPatch = true;
      }
    }
    for (const s of plan(p, obj!, ep!.abs, confirmPatch)) {
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
