// D49-A S4/S5：预览区文件切换器的分组（纯函数，零 DOM）。
// 九步产物的名字是流水线机制、不随番变（AGENTS.md 第六节），所以这张表写在这里；不在表里的顶层条目一律归「其他」。
import type { TreeEntry } from "./protocol";

export interface SwitchItem {
  name: string;
  /** 相对期目录；未生成的产物也给出预期路径 */
  rel: string;
  kind: "dir" | "file" | "other";
  /** 预期产物但磁盘上还没有 */
  missing: boolean;
  /** 可在应用内编辑（只有定稿 02-script.md） */
  editable: boolean;
  /** 有运行中的作业正在产出它 */
  generating: boolean;
  /** D70：定稿还没有、草稿已在——可点开编辑器「从草稿新建」（唯一一个「未生成却可点」的条目） */
  fromDraft: boolean;
}

export interface SwitchGroup {
  title: string;
  items: SwitchItem[];
}

/** 按工序号前缀分组；`expected` 是主线产物——没生成也列出来、灰显，人知道还差什么（可选产物有才列） */
const GROUPS: readonly { title: string; prefix: RegExp; expected: readonly string[] }[] = [
  { title: "稿件", prefix: /^0[12]-/, expected: ["01-topic.md", "02-script.md"] },
  { title: "配音", prefix: /^03-/, expected: ["03-audio"] },
  { title: "排片与成片", prefix: /^0[4-9]-/, expected: ["04-clips.json", "05-final.mp4"] },
];

/** 作业命令首词 → 它产出的顶层产物（只列会「边跑边出文件」的） */
const PRODUCES: Readonly<Record<string, string>> = {
  tts: "03-audio",
  clips: "04-clips.json",
  render: "05-final.mp4",
  cover: "07-cover",
};

/** 运行中作业的命令串（如 `tts --redo 2,4`）→ 正在产出的顶层产物名集合 */
export function generatingOf(runningCommands: readonly (string | null)[]): Set<string> {
  const out = new Set<string>();
  for (const c of runningCommands) {
    const head = (c ?? "").trim().split(/\s+/)[0]?.replace(/^pipeline\./, "") ?? "";
    const p = PRODUCES[head];
    if (p) out.add(p);
  }
  return out;
}

export function groupArtifacts(entries: readonly TreeEntry[], runningCommands: readonly (string | null)[] = []): SwitchGroup[] {
  const byName = new Map(entries.map((e) => [e.name, e]));
  const gen = generatingOf(runningCommands);
  const used = new Set<string>();
  // D70：02-script.md 不存在时编辑器的「从草稿新建」原本无路可达（切换器把它灰掉，02.5 停机点又要它已存在才就绪）
  const hasDraft = byName.get("02-script.draft.md")?.kind === "file";
  const item = (name: string, e: TreeEntry | undefined): SwitchItem => ({
    name,
    rel: e?.rel ?? name,
    kind: e?.kind ?? (name.includes(".") ? "file" : "dir"),
    missing: e === undefined,
    editable: name === "02-script.md" && (e !== undefined || hasDraft),
    generating: gen.has(name),
    fromDraft: name === "02-script.md" && e === undefined && hasDraft,
  });
  const groups: SwitchGroup[] = GROUPS.map((g) => {
    const names = new Set([...g.expected, ...entries.filter((e) => g.prefix.test(e.name)).map((e) => e.name)]);
    const items = [...names].sort().map((n) => {
      used.add(n);
      return item(n, byName.get(n));
    });
    return { title: g.title, items };
  });
  const rest = entries.filter((e) => !used.has(e.name)).map((e) => item(e.name, e));
  if (rest.length > 0) groups.push({ title: "其他", items: rest });
  return groups;
}
