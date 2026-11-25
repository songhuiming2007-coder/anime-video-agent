// Spec 10 §3.6：渲染层会话状态（纯 reducer，零 DOM）。与 Spec 8 的 store.ts 同构：
// snapshot 整体替换；delta 只在 generation 相同且 seq == last + 1 时追加；否则丢弃或请求 snapshot。
// `reset-convs`（onConnect）：清空全部会话桶——新 host 的 generation 带新 bootId，旧会话桶不许被拼上（🟡-9）。
import { foldConv, type FoldedConv } from "../shared/convFold";
import type { OutFrame } from "../shared/convFrames";
import type { ConvDelta, ConvEntry, ConvKey, ConvPhase, ConvSnapshot } from "../shared/protocol";

export interface ConvState {
  convKey: ConvKey;
  /** "<hostBootId>:<n>" */
  generation: string;
  seq: number;
  phase: ConvPhase;
  turnId: string | null;
  entries: ConvEntry[];
  open: OutFrame[];
  framesLost: number;
  truncatedHead: boolean;
  settlePending: string | null;
  keyProblem: string | null;
  /** D147：会话启动前预选的模型 / 推理强度 / 模式；没有为 undefined */
  pending?: { model?: string; effort?: string; scope?: "asset" | "auto" };
}

export interface ConvStore {
  convs: Readonly<Record<string, ConvState>>;
}

export type ConvAction = { type: "snapshot"; snap: ConvSnapshot } | { type: "delta"; delta: ConvDelta } | { type: "reset-convs" };

export interface ConvReduceResult {
  store: ConvStore;
  /** 需要向 host 请求 conv.snapshot 的会话键（跳号时） */
  resnapshot: ConvKey | null;
}

export const emptyConvStore: ConvStore = { convs: {} };

export function fromConvSnapshot(snap: ConvSnapshot): ConvState {
  return {
    convKey: snap.convKey,
    generation: snap.generation,
    seq: snap.seq,
    phase: snap.phase,
    turnId: snap.turnId,
    entries: snap.entries,
    open: snap.open,
    framesLost: snap.framesLost,
    truncatedHead: snap.truncatedHead,
    settlePending: snap.settlePending,
    keyProblem: snap.keyProblem,
    pending: snap.pending,
  };
}

function mergeEntries(cur: readonly ConvEntry[], incoming: readonly ConvEntry[]): ConvEntry[] {
  if (incoming.length === 0) return [...cur];
  const next = [...cur];
  for (const e of incoming) {
    if (e.k === "frame" && e.frame.t === "assistant_delta") {
      const turnId = String(e.frame.turn_id ?? "");
      const prev = next[next.length - 1];
      if (prev && prev.k === "frame" && prev.frame.t === "assistant_delta" && String(prev.frame.turn_id ?? "") === turnId) {
        // 保留首段到达时刻 prev.at，避免流式输出时活动指示器的秒表每块都被归零
        next[next.length - 1] = {
          ...prev,
          frame: { ...prev.frame, seq: e.frame.seq, text: `${String(prev.frame.text ?? "")}${String(e.frame.text ?? "")}` },
        };
        continue;
      }
    } else if (e.k === "frame" && e.frame.t === "assistant" && String(e.frame.kind ?? "") !== "local_note") {
      const turnId = String(e.frame.turn_id ?? "");
      let replaceAt = -1;
      for (let i = next.length - 1; i >= 0; i -= 1) {
        const item = next[i];
        if (item.k !== "frame") break;
        if (item.frame.t === "assistant_delta" && String(item.frame.turn_id ?? "") === turnId) {
          if (replaceAt !== -1) next.splice(replaceAt, 1);
          replaceAt = i;
          continue;
        }
        if (item.frame.t === "notice" || item.frame.t === "log") continue;
        break;
      }
      if (replaceAt !== -1) {
        next[replaceAt] = e;
        continue;
      }
    }
    next.push(e);
  }
  return next;
}

export function reduceConvs(store: ConvStore, action: ConvAction): ConvReduceResult {
  switch (action.type) {
    case "snapshot":
      return { store: { convs: { ...store.convs, [action.snap.convKey]: fromConvSnapshot(action.snap) } }, resnapshot: null };
    case "reset-convs":
      return { store: emptyConvStore, resnapshot: null };
    case "delta": {
      const d = action.delta;
      const cur = store.convs[d.convKey];
      if (!cur) return { store, resnapshot: null }; // 不属于任何已知会话键：丢弃
      if (d.generation !== cur.generation) return { store, resnapshot: null }; // 旧 generation 的 delta 不许污染新桶
      if (d.seq !== cur.seq + 1) return { store, resnapshot: d.convKey }; // 跳号：本地不可信，请求 snapshot
      const next: ConvState = {
        ...cur,
        seq: d.seq,
        phase: d.phase,
        turnId: d.turnId,
        entries: mergeEntries(cur.entries, d.entries),
        open: d.open,
        keyProblem: d.keyProblem,
        pending: d.pending,
      };
      return { store: { convs: { ...store.convs, [d.convKey]: next } }, resnapshot: null };
    }
  }
}

const EMPTY_FOLDED: FoldedConv = foldConv([]);
const foldCache = new WeakMap<ConvState, FoldedConv>();

export function foldOf(state: ConvState | undefined): FoldedConv {
  if (!state) return EMPTY_FOLDED;
  let cached = foldCache.get(state);
  if (!cached) {
    cached = foldConv(state.entries);
    foldCache.set(state, cached);
  }
  return cached;
}
