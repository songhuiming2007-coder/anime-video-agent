// 期列表的搜索与「按工序」分组（Spec 14 §3.5，纯函数；TG-3：shared 零 import、零 DOM 全局）。
// 与企划进度条同一口径：把 09 发布步单独拆出为「待发布」，避免和真正的待审停机点混为一谈。

export function filterEpisodes<T extends { epKey: string }>(list: readonly T[], q: string): T[] {
  const s = q.trim().toLowerCase();
  if (s === "") return [...list];
  return list.filter((e) => e.epKey.toLowerCase().includes(s));
}

export interface ViewGroup<T> {
  key: "stopped" | "running" | "ready" | "unknown";
  label: "待你处理" | "进行中" | "待发布" | "状态未取到";
  items: T[];
}

function isReadyToPublish(step: string | null | undefined): boolean {
  return typeof step === "string" && /^09(\s|$)/.test(step.trim());
}

/** 组序固定（待你处理 → 进行中 → 待发布 → 状态未取到）、组内保序、空组省略 */
export function groupByStep<T extends { isBlocked: boolean | null; currentStep?: string | null }>(list: readonly T[]): ViewGroup<T>[] {
  const stopped: T[] = [];
  const running: T[] = [];
  const ready: T[] = [];
  const unknown: T[] = [];
  for (const e of list) {
    if (isReadyToPublish(e.currentStep)) ready.push(e);
    else if (e.isBlocked === true) stopped.push(e);
    else if (e.isBlocked === false) running.push(e);
    else unknown.push(e);
  }
  const out: ViewGroup<T>[] = [];
  if (stopped.length > 0) out.push({ key: "stopped", label: "待你处理", items: stopped });
  if (running.length > 0) out.push({ key: "running", label: "进行中", items: running });
  if (ready.length > 0) out.push({ key: "ready", label: "待发布", items: ready });
  if (unknown.length > 0) out.push({ key: "unknown", label: "状态未取到", items: unknown });
  return out;
}
