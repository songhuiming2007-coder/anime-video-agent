// 停机点自动呼出（Spec 10 §2.7 的 renderer 侧规则 R1–R6）。纯状态机、零 DOM：
// 「有媒体在播放」由调用方每次作为 env 传入，「回合是否在跑」由 turn_started / settled 事件驱动。
//
// 为什么要有这个模块：现状是「活跃期出现任何新 pending 就无条件替换预览」（Spec 8 §2.5），
// agent 一个回合连改几版 04-clips.json 就闪几次半成品（RF-24）。这里的规则是：
// 回合进行中只登记不呼出；回合结算后每类停机点只呼出最新的那一个；人正在看别的东西就不抢。
import type { ApprovalJson } from "../shared/contracts";
import { compareIso } from "../shared/isoTime";

export type PreviewOwner = "none" | "auto" | "human";

export interface AutoOpenState {
  /** 当前预览内容的来源：auto = 本模块呼出的，human = 人自己点开的 */
  owner: PreviewOwner;
  /** 见过的对象（跨切期、跨 host 重连保留）：同一个 approval_id 不会二次呼出 */
  seen: ReadonlySet<string>;
  /** 回合进行中攒下的候选，按停机点类型去重（同类型保留最新者） */
  deferred: ReadonlyMap<string, ApprovalJson>;
  /** 已见 turn_started、尚未见对应 settled 的回合 */
  awaiting: string | null;
  /** 因人不空闲（owner=human 或媒体在播）而挂起的候选 */
  strip: ApprovalJson | null;
}

export const initialAutoOpen: AutoOpenState = {
  owner: "none",
  seen: new Set(),
  deferred: new Map(),
  awaiting: null,
  strip: null,
};

export interface AutoOpenEnv {
  /** 预览区里有媒体元素正在播放（!paused && !ended） */
  mediaPlaying: boolean;
}

export type AutoOpenEvent =
  | { kind: "pendings"; items: ApprovalJson[] } // R1：活跃期对象库出现未见过的 pending
  | { kind: "turn_started"; turnId: string } // R2a
  | { kind: "settled"; turnId: string; items: ApprovalJson[] } // R2b：宿主侧结算
  | { kind: "human" } // R4：人从产物树 / 画廊点开文件
  | { kind: "strip" } // R5：人点「已就绪 · 查看」
  | { kind: "reset"; phase: "none" | "idle" | "running" | "exited"; turnId: string | null; settlePending: string | null }; // R6

export interface AutoOpenResult {
  state: AutoOpenState;
  /** 需要换预览的候选；null = 保持现状 */
  open: ApprovalJson | null;
}

/** 「最新」一律按 created_at 比，相等取数组中靠后者（§2.7）。 */
function latest(items: ApprovalJson[]): ApprovalJson {
  let best = items[0];
  for (const o of items) if (compareIso(o.created_at, best.created_at) >= 0) best = o;
  return best;
}

function applyCandidate(s: AutoOpenState, cand: ApprovalJson, env: AutoOpenEnv): AutoOpenResult {
  // R3：人不空闲就不替换预览，只出「已就绪 · 查看」条
  if (s.owner === "human" || env.mediaPlaying) return { state: { ...s, strip: cand }, open: null };
  return { state: { ...s, owner: "auto", strip: null }, open: cand };
}

export function step(s: AutoOpenState, ev: AutoOpenEvent, env: AutoOpenEnv): AutoOpenResult {
  switch (ev.kind) {
    case "pendings": {
      const fresh = ev.items.filter((a) => !s.seen.has(a.approval_id));
      if (fresh.length === 0) return { state: s, open: null };
      const seen = new Set(s.seen);
      for (const a of fresh) seen.add(a.approval_id);
      // R1 后半：回合进行中只登记（同类型保留最新者），不呼出
      if (s.awaiting !== null) {
        const deferred = new Map(s.deferred);
        for (const a of fresh) {
          const cur = deferred.get(a.type);
          if (!cur || compareIso(a.created_at, cur.created_at) >= 0) deferred.set(a.type, a);
        }
        return { state: { ...s, seen, deferred }, open: null };
      }
      return applyCandidate({ ...s, seen }, latest(fresh), env);
    }
    case "turn_started":
      return { state: { ...s, awaiting: ev.turnId }, open: null };
    case "settled": {
      if (ev.turnId !== s.awaiting) return { state: s, open: null }; // 迟到 / 不符的结算不算
      const live = new Set(ev.items.filter((a) => a.status === "pending").map((a) => a.approval_id));
      const cands = [...s.deferred.values()].filter((a) => live.has(a.approval_id));
      const cleared: AutoOpenState = { ...s, awaiting: null, deferred: new Map() };
      if (cands.length === 0) return { state: cleared, open: null }; // 本回合零停机点：只清状态
      return applyCandidate(cleared, latest(cands), env);
    }
    case "human":
      return { state: { ...s, owner: "human" }, open: null };
    case "strip":
      if (s.strip === null) return { state: s, open: null };
      return { state: { ...s, owner: "auto", strip: null }, open: s.strip };
    case "reset": {
      // R6：owner / deferred / strip 清空，`seen` 保留；awaiting 从 snapshot 的顶层字段重建
      //（不扫条目缓冲：长回合的 log 可能让缓冲截头，turn_started 条目随之丢失）
      const awaiting = ev.phase === "running" ? ev.turnId : ev.settlePending;
      return { state: { owner: "none", seen: s.seen, deferred: new Map(), awaiting, strip: null }, open: null };
    }
  }
}
