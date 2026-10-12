// 期列表（Spec 8 §2.3；D81 §4.1 树规则）：TS 侧唯一一处「复刻 Python 规则」，镜像 pipeline/project.py
// get_episode_tree（cli.get_episodes_list 由它推导）。
// 规则：排除 . / _ 前缀（_ 计入 hiddenUnderscore）；顶层目录有 00-project.md、或有至少一个成员
// （不以 . / _ 开头、含 01-topic.md 的子目录）即企划，否则普通期；企划内 _ 前缀子目录计入隐藏数。
// 判目录用跟随符号链接的 stat（对齐 Python is_dir()）。由 TI-5 跨语言对拍兜住。
// 排序：普通期与企划按 mtime 降序混排（企划 = 企划目录与成员 mtime 的最大值）；企划内成员按目录名**码点序**
// 升序——必须显式按码点比较：默认 sort() 按 UTF-16 码元，含增补平面字符时与 Python sorted() 不一致。
// TS 侧不解析 00-project.md（卷表顺序由宿主经 core 的 /project-meta 取）。
import type { FsProbe } from "./fsio";

export interface EpisodeEntry {
  epKey: string;
  abs: string;
  mtimeMs: number;
}

export interface ProjectEntry {
  /** 企划目录名（相对 episodes 根，一段） */
  projKey: string;
  abs: string;
  /** 成员（卷与合集期），epKey 带企划前缀 `<企划>/<卷>`；按卷目录名码点序 */
  members: EpisodeEntry[];
  hasProjectFile: boolean;
  mtimeMs: number;
}

export type TreeItem = { kind: "episode"; entry: EpisodeEntry } | { kind: "project"; entry: ProjectEntry };

export interface EpisodeTree {
  plain: EpisodeEntry[];
  projects: ProjectEntry[];
  hiddenUnderscore: number;
  /** 普通期与企划按 mtime 降序混排 */
  order: TreeItem[];
}

function isDir(fs: FsProbe, p: string): boolean {
  try {
    return fs.stat(p).isDirectory();
  } catch {
    return false;
  }
}

function isFile(fs: FsProbe, p: string): boolean {
  try {
    return fs.stat(p).isFile();
  } catch {
    return false;
  }
}

function exists(fs: FsProbe, p: string): boolean {
  try {
    fs.stat(p);
    return true;
  } catch {
    return false;
  }
}

/** 码点序比较（对齐 Python 的 str 比较）：逐个码点比，不按 UTF-16 码元。 */
export function compareCodePoints(a: string, b: string): number {
  const ia = a[Symbol.iterator]();
  const ib = b[Symbol.iterator]();
  for (;;) {
    const x = ia.next();
    const y = ib.next();
    if (x.done || y.done) return x.done && y.done ? 0 : x.done ? -1 : 1;
    const cx = x.value.codePointAt(0) as number;
    const cy = y.value.codePointAt(0) as number;
    if (cx !== cy) return cx - cy;
  }
}

function isMember(fs: FsProbe, d: string, s: string): boolean {
  return !s.startsWith(".") && !s.startsWith("_") && isDir(fs, `${d}/${s}`) && exists(fs, `${d}/${s}/01-topic.md`);
}

export function listEpisodeTree(episodesRoot: string, fs: FsProbe): EpisodeTree {
  if (!exists(fs, episodesRoot)) return { plain: [], projects: [], hiddenUnderscore: 0, order: [] };
  const plain: EpisodeEntry[] = [];
  const projects: ProjectEntry[] = [];
  let hiddenUnderscore = 0;
  for (const name of fs.readdir(episodesRoot)) {
    const d = `${episodesRoot}/${name}`;
    if (!isDir(fs, d)) continue;
    if (name.startsWith(".")) continue;
    if (name.startsWith("_")) {
      hiddenUnderscore += 1;
      continue;
    }
    const children = fs.readdir(d);
    const memberNames = children.filter((s) => isMember(fs, d, s)).sort(compareCodePoints);
    const hasProjectFile = isFile(fs, `${d}/00-project.md`);
    if (hasProjectFile || memberNames.length > 0) {
      const members = memberNames.map((s) => ({ epKey: `${name}/${s}`, abs: `${d}/${s}`, mtimeMs: fs.stat(`${d}/${s}`).mtimeMs }));
      hiddenUnderscore += children.filter((s) => isDir(fs, `${d}/${s}`) && s.startsWith("_")).length;
      const mtimeMs = Math.max(fs.stat(d).mtimeMs, ...members.map((m) => m.mtimeMs));
      projects.push({ projKey: name, abs: d, members, hasProjectFile, mtimeMs });
    } else {
      plain.push({ epKey: name, abs: d, mtimeMs: fs.stat(d).mtimeMs });
    }
  }
  const order: TreeItem[] = [
    ...plain.map((entry) => ({ kind: "episode" as const, entry })),
    ...projects.map((entry) => ({ kind: "project" as const, entry })),
  ];
  order.sort((a, b) => b.entry.mtimeMs - a.entry.mtimeMs); // Array.prototype.sort 稳定，与 Python sorted 同
  return { plain, projects, hiddenUnderscore, order };
}

/** 平铺列表（成员与普通期一起按各自 mtime 降序）：由 listEpisodeTree 推导，与 Python get_episodes_list 同形。 */
export function listEpisodes(
  episodesRoot: string,
  fs: FsProbe,
): { visible: EpisodeEntry[]; hiddenUnderscore: number } {
  const tree = listEpisodeTree(episodesRoot, fs);
  const visible: EpisodeEntry[] = [];
  for (const name of exists(fs, episodesRoot) ? fs.readdir(episodesRoot) : []) {
    const plain = tree.plain.find((e) => e.epKey === name);
    if (plain) visible.push(plain);
    const proj = tree.projects.find((p) => p.projKey === name);
    if (proj) visible.push(...proj.members);
  }
  visible.sort((a, b) => b.mtimeMs - a.mtimeMs);
  return { visible, hiddenUnderscore: tree.hiddenUnderscore };
}
