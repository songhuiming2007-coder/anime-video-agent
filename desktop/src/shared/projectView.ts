// D81 PR-2b：侧栏企划卡片（人选方案 B「分组卡片」）的纯函数——卷的顺序与标签、进度条各段、卡头徽标、搜索与「按时间」混排。
// TG-3：shared 只做纯计算（类型 import 之外零 import、零 DOM 全局）。卷表的解析只在 core（/project-meta），这里只用它的结果。
import type { EpisodeSummary, ProjectSummary } from "./protocol";

/**
 * 进度条一段的状态。只有两种由硬事实给出：走到 09（机器工序全部走完、等人发布）与停在人审（is_blocked）；其余一律「进行中」。
 * 09 先判：status 在 09 恒报 is_blocked=true（pipeline/status.py 第 9 步），blocked 先判的话「完成」永远走不到、已发布的卷全显示成停机。
 */
export type VolumeStage = "done" | "stop" | "mid" | "none";

export function volumeStage(e: Pick<EpisodeSummary, "currentStep" | "isBlocked">): VolumeStage {
  if (e.currentStep === null) return "none";
  if (/^09(\s|$)/.test(e.currentStep)) return "done";
  return e.isBlocked === true ? "stop" : "mid";
}

export interface VolumeRow {
  e: EpisodeSummary;
  /** 侧栏卷行文字：卷表里的卷「第 N 卷 · 目录」，合集「合集 · 目录」，不在卷表的只写目录 */
  label: string;
  /** 卷序（卷表里的卷），合集与不在卷表的为 null */
  seq: number | null;
  collection: boolean;
}

const dirOf = (epKey: string): string => epKey.split("/").slice(1).join("/");
const keyOf = (name: string): string => name.normalize("NFC").toLowerCase();

/** 码点序（与 core 的 sorted() 一致；默认 sort() 按 UTF-16 码元，含增补平面字符时会错） */
function byCodePoint(a: string, b: string): number {
  const x = [...a];
  const y = [...b];
  for (let i = 0; i < Math.min(x.length, y.length); i++) {
    const d = (x[i].codePointAt(0) as number) - (y[i].codePointAt(0) as number);
    if (d !== 0) return d;
  }
  return x.length - y.length;
}

/** 卷行：卷表顺序在前（meta 取不到时按目录名码点序，不报错），合集其次，不在卷表的按码点序殿后 */
export function volumeRows(p: ProjectSummary): VolumeRow[] {
  const seqOf = new Map((p.meta?.volumes ?? []).map((v) => [keyOf(v.dir), v.seq]));
  const coll = p.meta?.collection ? keyOf(p.meta.collection) : null;
  const rows = p.members.map((e): VolumeRow => {
    const dir = dirOf(e.epKey);
    const seq = seqOf.get(keyOf(dir)) ?? null;
    const collection = coll !== null && keyOf(dir) === coll;
    const label = seq !== null ? `第 ${seq} 卷 · ${dir}` : collection ? `合集 · ${dir}` : dir;
    return { e, label, seq, collection };
  });
  const rank = (r: VolumeRow): number => (r.seq !== null ? 0 : r.collection ? 1 : 2);
  return rows.sort((a, b) => rank(a) - rank(b) || (a.seq ?? 0) - (b.seq ?? 0) || byCodePoint(dirOf(a.e.epKey), dirOf(b.e.epKey)));
}

/** 进度条各段：卷表里的卷（合集不进进度条）；meta 取不到时退回「除合集以外的成员」，按 volumeRows 的顺序 */
export function progressSegments(p: ProjectSummary): { label: string; stage: VolumeStage; title: string }[] {
  const rows = volumeRows(p).filter((r) => !r.collection);
  const inTable = rows.filter((r) => r.seq !== null);
  return (inTable.length > 0 ? inTable : rows).map((r) => ({
    label: r.label,
    stage: volumeStage(r.e),
    title: `${r.label}：${r.e.currentStep ?? "未取到"}`,
  }));
}

/** 卡头聚合徽标：待答卡总数（各卷与企划会话）、停在人审的卷数（与进度条同一判据：走到 09 的不算） */
export function projectBadges(p: ProjectSummary): { cards: number; stops: number; running: boolean } {
  const cards = p.members.reduce((n, m) => n + (m.conv?.openRequests ?? 0), 0) + (p.conv?.openRequests ?? 0);
  const stops = p.members.filter((m) => volumeStage(m) === "stop").length;
  const running = p.members.some((m) => m.conv?.running === true) || p.conv?.running === true;
  return { cards, stops, running };
}

/** 卡头显示名：卷表解析出的企划名，取不到用目录名 */
export function projectTitle(p: ProjectSummary): string {
  return p.meta?.name ?? p.projKey;
}

/**
 * 搜索：企划名 / 目录名命中 → 整卡（全部卷）；只有卷名命中 → 企划连带显示、只列命中的卷（§7.6）。
 * 查询为空 → 原样。返回 null = 这个企划整个不显示。
 */
export function filterProject(p: ProjectSummary, q: string): ProjectSummary | null {
  const s = q.trim().toLowerCase();
  if (s === "") return p;
  if (p.projKey.toLowerCase().includes(s) || projectTitle(p).toLowerCase().includes(s)) return p;
  const members = p.members.filter((m) => m.epKey.toLowerCase().includes(s));
  return members.length > 0 ? { ...p, members } : null;
}

export type SidebarItem = { kind: "episode"; e: EpisodeSummary } | { kind: "project"; p: ProjectSummary };

/** 「按时间」：普通期与企划按 mtime 降序混排（企划的 mtime 由宿主给出 = 目录与成员的最大值）；同 mtime 保序 */
export function timeline(plain: readonly EpisodeSummary[], projects: readonly ProjectSummary[]): SidebarItem[] {
  const items: SidebarItem[] = [
    ...plain.map((e) => ({ kind: "episode" as const, e })),
    ...projects.map((p) => ({ kind: "project" as const, p })),
  ];
  const t = (i: SidebarItem): number => (i.kind === "episode" ? i.e.mtimeMs : i.p.mtimeMs);
  return items.sort((a, b) => t(b) - t(a));
}

/** 普通期 = 平铺列表里不属于任何企划的期 */
export function plainEpisodes(episodes: readonly EpisodeSummary[], projects: readonly ProjectSummary[]): EpisodeSummary[] {
  const members = new Set(projects.flatMap((p) => p.members.map((m) => m.epKey)));
  return episodes.filter((e) => !members.has(e.epKey));
}
