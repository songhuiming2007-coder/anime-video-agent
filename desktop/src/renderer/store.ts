// renderer 状态：按 epKey 分桶（§2.5 消息级隔离）。纯函数 reducer，零 DOM，便于单测（TE-10、TE-12）。
// delta 只在 generation 相同且 seq == last + 1 时应用；generation 不同 → 丢弃；seq 跳号 → 丢弃并请求 resnapshot。
import type { ApprovalJson, EventJson } from "../shared/contracts";
import type { DegradedNotice, EpisodeDelta, EpisodeSnapshot, JobView, SnapshotApprovals, SnapshotStatus } from "../shared/protocol";

export interface EpisodeState {
  epKey: string;
  generation: number;
  seq: number;
  status: SnapshotStatus;
  approvals: SnapshotApprovals;
  events: EventJson[];
  eventsMeta: Omit<EpisodeSnapshot["events"], "items">;
  jobs: JobView[];
  degradedNotices: DegradedNotice[];
}

export interface Store {
  episodes: Readonly<Record<string, EpisodeState>>;
}

export type Action =
  | { type: "snapshot"; snap: EpisodeSnapshot }
  | { type: "delta"; delta: EpisodeDelta }
  | { type: "forget"; epKey: string }
  | { type: "reset" };

export interface ReduceResult {
  store: Store;
  /** 需要向 host 请求 episode.resnapshot 的期 */
  resnapshot: string | null;
}

export const emptyStore: Store = { episodes: {} };

export function fromSnapshot(snap: EpisodeSnapshot): EpisodeState {
  const { items, ...meta } = snap.events;
  return {
    epKey: snap.epKey,
    generation: snap.generation,
    seq: 0,
    status: snap.status,
    approvals: snap.approvals,
    events: items,
    eventsMeta: meta,
    jobs: snap.jobs,
    degradedNotices: snap.degradedNotices,
  };
}

export function reduce(store: Store, action: Action): ReduceResult {
  switch (action.type) {
    case "snapshot":
      return { store: { episodes: { ...store.episodes, [action.snap.epKey]: fromSnapshot(action.snap) } }, resnapshot: null };
    case "reset":
      return { store: emptyStore, resnapshot: null };
    case "forget": {
      const next = { ...store.episodes };
      delete next[action.epKey];
      return { store: { episodes: next }, resnapshot: null };
    }
    case "delta": {
      const d = action.delta;
      const cur = store.episodes[d.epKey];
      if (!cur) return { store, resnapshot: null }; // 不属于任何订阅：丢弃
      if (d.generation !== cur.generation) return { store, resnapshot: null }; // 旧 generation 的 delta 不许污染新 snapshot
      if (d.seq !== cur.seq + 1) return { store, resnapshot: d.epKey }; // 跳号：本地状态不可信，请求 snapshot
      const next: EpisodeState = {
        ...cur,
        seq: d.seq,
        events: d.newEvents.length ? [...cur.events, ...d.newEvents] : cur.events,
        status: d.status ?? cur.status,
        approvals: d.approvals ?? cur.approvals,
        jobs: d.jobs ?? cur.jobs,
        degradedNotices: d.degradedNotices ?? cur.degradedNotices,
      };
      return { store: { episodes: { ...store.episodes, [d.epKey]: next } }, resnapshot: null };
    }
  }
}

export function select(store: Store, epKey: string | null): EpisodeState | undefined {
  return epKey === null ? undefined : store.episodes[epKey];
}

/** 决策条用的对象集合；只取本期桶 */
export function approvalsOf(ep: EpisodeState | undefined): ApprovalJson[] {
  if (!ep) return [];
  const a = ep.approvals;
  return a.state === "ok" ? a.items : a.state === "error" && a.lastGood ? a.lastGood : [];
}
