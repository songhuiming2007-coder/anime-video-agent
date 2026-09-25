// 期列表（Spec 8 §2.3）：TS 侧唯一一处「复刻 Python 规则」，镜像 pipeline/agent/cli.py get_episodes_list。
// 规则：排除 . / _ 前缀（_ 计入 hidden_underscore）；含 01-topic.md 子期的目录展平（子目录中 _ 前缀计数）；
// 判目录用跟随符号链接的 stat（对齐 Python is_dir()）；mtime 降序、稳定排序。由 TI-5 跨语言对拍兜住。
import type { FsProbe } from "./fsio";

export interface EpisodeEntry {
  epKey: string;
  abs: string;
  mtimeMs: number;
}

function isDir(fs: FsProbe, p: string): boolean {
  try {
    return fs.stat(p).isDirectory();
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

export function listEpisodes(
  episodesRoot: string,
  fs: FsProbe,
): { visible: EpisodeEntry[]; hiddenUnderscore: number } {
  if (!exists(fs, episodesRoot)) return { visible: [], hiddenUnderscore: 0 };
  const visible: { epKey: string; abs: string }[] = [];
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
    const subEps = children.filter(
      (s) => isDir(fs, `${d}/${s}`) && !s.startsWith(".") && !s.startsWith("_") && exists(fs, `${d}/${s}/01-topic.md`),
    );
    if (subEps.length > 0) {
      for (const s of subEps) visible.push({ epKey: `${name}/${s}`, abs: `${d}/${s}` });
      hiddenUnderscore += children.filter((s) => isDir(fs, `${d}/${s}`) && s.startsWith("_")).length;
    } else {
      visible.push({ epKey: name, abs: d });
    }
  }
  const withTime = visible.map((e) => ({ ...e, mtimeMs: fs.stat(e.abs).mtimeMs }));
  withTime.sort((a, b) => b.mtimeMs - a.mtimeMs); // Array.prototype.sort 稳定，与 Python sorted 同
  return { visible: withTime, hiddenUnderscore };
}
