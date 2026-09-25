// heal 触发闭集 H1–H5 的唯一入口（Spec 8 §2.12）。heal = spawn `ava <期> /approvals` 让 core 自愈落盘。
// 明令禁止：后台期 heal；周期 heal；由对象库文件本身或事件行触发 heal。
// HEAL spawn 模板属 PR4；在它接入之前 executor 为 null，调度照常记录、不 spawn。
import type { ApprovalRecord } from "../shared/contracts";
import { relPathProblem } from "../shared/mediaUrl";

export type HealTrigger = "H1-activated" | "H2-step-changed" | "H3-user-refresh" | "H4-pre-ack" | "H5-artifact-drift";

export interface HealResult {
  ok: boolean;
  stderrTail: string;
}

export type HealExecutor = (epKey: string, trigger: HealTrigger) => Promise<HealResult>;

export interface HealLogEntry {
  epKey: string;
  trigger: HealTrigger;
  at: number;
  spawned: boolean;
}

/** 同一期同一时刻至多一个 heal 在途；在途期间的新触发合并为「完成后再跑一次」。 */
export class HealScheduler {
  readonly log: HealLogEntry[] = [];
  private inflight = new Map<string, Promise<HealResult>>();
  private queued = new Map<string, { trigger: HealTrigger; promise: Promise<HealResult> }>();

  constructor(
    private executor: HealExecutor | null,
    private readonly onFailure: (epKey: string, r: HealResult) => void = () => {},
  ) {}

  setExecutor(executor: HealExecutor | null): void {
    this.executor = executor;
  }

  busy(): boolean {
    return this.inflight.size > 0;
  }

  requestHeal(epKey: string, trigger: HealTrigger): Promise<HealResult> {
    const q = this.queued.get(epKey);
    if (q) return q.promise; // 已有排队的「再跑一次」，合并
    const cur = this.inflight.get(epKey);
    if (cur) {
      const promise = cur.then(() => {
        this.queued.delete(epKey);
        return this.start(epKey, trigger);
      });
      this.queued.set(epKey, { trigger, promise });
      return promise;
    }
    return this.start(epKey, trigger);
  }

  private start(epKey: string, trigger: HealTrigger): Promise<HealResult> {
    const exec = this.executor;
    this.log.push({ epKey, trigger, at: Date.now(), spawned: exec !== null });
    if (!exec) return Promise.resolve({ ok: false, stderrTail: "HEAL 未接入（能力缺席或 PR4 未施工）" });
    const p = exec(epKey, trigger)
      .catch((e: unknown) => ({ ok: false, stderrTail: e instanceof Error ? e.message : String(e) }))
      .then((r) => {
        if (!r.ok) this.onFailure(epKey, r); // 红队 m8：失败只记诊断，不重试
        return r;
      })
      .finally(() => this.inflight.delete(epKey));
    this.inflight.set(epKey, p);
    return p;
  }
}

// ---- H5：活跃期关联产物漂移（§2.12 表 H5 行；TE-14）----

export interface H5State {
  baselineTaken: boolean;
  /** approvalId → 上一次采样的指纹元组规范串 */
  lastSample: Map<string, string>;
  /** `${approvalId}|${元组}`：已触发过（或基线时已存在）的元组 */
  fired: Set<string>;
}

export function newH5State(): H5State {
  return { baselineTaken: false, lastSample: new Map(), fired: new Set() };
}

export type H5Stat = (rel: string) => { size: bigint; mtimeNs: bigint } | "missing";

/** 每种停机点最新的那个对象（按 created_at，相同取数组中靠后者）。 */
export function latestPerStop(objs: ApprovalRecord[]): ApprovalRecord[] {
  const best = new Map<string, ApprovalRecord>();
  for (const o of objs) {
    const cur = best.get(o.type);
    if (!cur || o.created_at >= cur.created_at) best.set(o.type, o);
  }
  return [...best.values()];
}

export function h5Step(
  state: H5State,
  objs: ApprovalRecord[],
  stat: H5Stat,
): { state: H5State; fire: boolean; rejectedPaths: string[] } {
  const next: H5State = { baselineTaken: true, lastSample: new Map(), fired: new Set(state.fired) };
  const rejectedPaths: string[] = [];
  let fire = false;
  for (const o of latestPerStop(objs)) {
    if (o.status !== "pending" && o.status !== "rejected") continue;
    const pinnedParts: string[] = [];
    const nowParts: string[] = [];
    for (const a of o.artifacts) {
      if (relPathProblem(a.path) !== null) {
        rejectedPaths.push(a.path); // 段检查不过：只记诊断、不 stat
        continue;
      }
      const s = stat(a.path);
      const size = s === "missing" ? -1n : s.size;
      const mtime = s === "missing" ? -1n : s.mtimeNs;
      pinnedParts.push(`${a.path}:${a.size}:${a.mtime_ns}`);
      nowParts.push(`${a.path}:${size}:${mtime}`);
    }
    const now = nowParts.join("|");
    if (now === pinnedParts.join("|")) continue; // 与钉住值一致：无漂移
    const key = `${o.approval_id}|${now}`;
    next.lastSample.set(o.approval_id, now);
    if (!state.baselineTaken) {
      next.fired.add(key); // 基线：设为活跃期后的第一次采样不触发，且该元组此后也不再触发
      continue;
    }
    // 稳定期：同一元组连续两次采样都出现才触发；每个对象的每个新元组至多触发一次
    if (state.lastSample.get(o.approval_id) === now && !next.fired.has(key)) {
      next.fired.add(key);
      fire = true;
    }
  }
  return { state: next, fire, rejectedPaths };
}
