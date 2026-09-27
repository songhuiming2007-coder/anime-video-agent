// 期列表的搜索与「按工序」分组（Spec 14 §3.5，纯函数冻结；TG-3：shared 零 import、零 DOM 全局）。
// 组名是对 is_blocked 布尔值的直译（停机点 / 非停机点 / 未取到），不推断「进行中 / 已完成」（RF-8）。

export function filterEpisodes<T extends { epKey: string }>(list: readonly T[], q: string): T[] {
  const s = q.trim().toLowerCase();
  if (s === "") return [...list];
  return list.filter((e) => e.epKey.toLowerCase().includes(s));
}

export interface ViewGroup<T> {
  key: "stopped" | "running" | "unknown";
  label: "停机点" | "非停机点" | "未取到";
  items: T[];
}

/** 组序固定、组内保序、空组省略 */
export function groupByStep<T extends { isBlocked: boolean | null }>(list: readonly T[]): ViewGroup<T>[] {
  const stopped: T[] = [];
  const running: T[] = [];
  const unknown: T[] = [];
  for (const e of list) (e.isBlocked === true ? stopped : e.isBlocked === false ? running : unknown).push(e);
  const out: ViewGroup<T>[] = [];
  if (stopped.length > 0) out.push({ key: "stopped", label: "停机点", items: stopped });
  if (running.length > 0) out.push({ key: "running", label: "非停机点", items: running });
  if (unknown.length > 0) out.push({ key: "unknown", label: "未取到", items: unknown });
  return out;
}
