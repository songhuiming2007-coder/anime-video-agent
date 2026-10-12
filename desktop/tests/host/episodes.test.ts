// TI-5：期列表跨语言对拍——TS listEpisodes 与 Python cli.get_episodes_list(root=…) 完全一致。
import { mkdirSync, symlinkSync, utimesSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { afterAll, describe, expect, it } from "vitest";
import { realFs } from "../../src/host/fsio";
import { compareCodePoints, listEpisodeTree, listEpisodes } from "../../src/host/episodes";
import { cleanup, py, tmp } from "../helpers";

const root = tmp("eps");
afterAll(() => cleanup(root));

describe("TI-5 期列表对拍", () => {
  it("含 ./_ 前缀、含与不含 01-topic.md 的子目录、软链期目录", () => {
    const eps = join(root, "data/episodes");
    const outside = join(root, "elsewhere/EXT");
    mkdirSync(outside, { recursive: true });
    const dirs = [
      "PLAIN",
      "PROJ/01-a",
      "PROJ/02-b",
      "PROJ/03-no-topic",
      "PROJ/_c",
      "PROJ/.d",
      "HALF/sub-without-topic",
      "_hidden",
      ".dot",
      "PROJ2/_x",
      "PROJ2/01-y",
    ];
    for (const d of dirs) mkdirSync(join(eps, d), { recursive: true });
    for (const t of ["PROJ/01-a", "PROJ/02-b", "PROJ/_c", "PROJ/.d", "PROJ2/01-y"]) writeFileSync(join(eps, t, "01-topic.md"), "# t\n");
    writeFileSync(join(eps, "notes.txt"), "not a dir");
    symlinkSync(outside, join(eps, "LINKED"));
    mkdirSync(join(root, "elsewhere/SUBEP"));
    writeFileSync(join(root, "elsewhere/SUBEP/01-topic.md"), "# t\n");
    symlinkSync(join(root, "elsewhere/SUBEP"), join(eps, "PROJ2/02-linked"));
    // 各期 mtime 互不相同（相同 mtime 时两侧的 readdir 顺序不同：libuv scandir 排序、Python iterdir 不排序）
    const order = ["PLAIN", "PROJ/01-a", "PROJ/02-b", "HALF", "PROJ2/01-y", "LINKED", "PROJ2/02-linked"];
    order.forEach((d, i) => utimesSync(join(eps, d), 1_700_000_000 + i * 100, 1_700_000_000 + i * 100));

    const ts = listEpisodes(eps, realFs);
    const out = py(
      `import json, sys
from pathlib import Path
from pipeline.agent.cli import get_episodes_list
root = Path(sys.argv[1])
vis, hidden = get_episodes_list(root)
ep_root = root / "data" / "episodes"
print(json.dumps({"visible": [p.relative_to(ep_root).as_posix() for p in vis], "hidden": hidden}))`,
      [root],
    );
    const pyRes = JSON.parse(out) as { visible: string[]; hidden: number };
    expect(ts.visible.map((e) => e.epKey)).toEqual(pyRes.visible);
    expect(ts.hiddenUnderscore).toBe(pyRes.hidden);
    // 期望值先跑后核对：7 个可见期按 mtime 降序；_hidden、PROJ/_c、PROJ2/_x 计入隐藏
    expect(pyRes.visible).toEqual(["PROJ2/02-linked", "LINKED", "PROJ2/01-y", "HALF", "PROJ/02-b", "PROJ/01-a", "PLAIN"]);
    expect(pyRes.hidden).toBe(3);
  });
  it("D81 树形对拍：listEpisodeTree 与 Python get_episode_tree 同形（含码点序与 00-project.md 无成员的企划）", () => {
    const r2 = tmp("eps-tree");
    const eps = join(r2, "data/episodes");
    const outside = join(r2, "elsewhere/EXT");
    mkdirSync(outside, { recursive: true });
    writeFileSync(join(outside, "01-topic.md"), "# t\n");
    const dirs = ["PLAIN", "PROJ/01-a", "PROJ/（终）", "PROJ/😀-x", "PROJ/_c", "NEWP", "SELF/01-x", "HALF/sub", "_hidden"];
    for (const d of dirs) mkdirSync(join(eps, d), { recursive: true });
    for (const t of ["PLAIN", "PROJ/01-a", "PROJ/（终）", "PROJ/😀-x", "PROJ/_c", "SELF", "SELF/01-x"]) writeFileSync(join(eps, t, "01-topic.md"), "# t\n");
    writeFileSync(join(eps, "NEWP/00-project.md"), "企划: 新\n");
    symlinkSync(outside, join(eps, "PROJ/02-linked"));
    const order = ["PLAIN", "HALF", "NEWP", "SELF/01-x", "SELF", "PROJ/01-a", "PROJ/（终）", "PROJ/😀-x", "PROJ"];
    order.forEach((d, i) => utimesSync(join(eps, d), 1_700_000_000 + i * 100, 1_700_000_000 + i * 100));
    utimesSync(outside, 1_600_000_000, 1_600_000_000);

    const ts = listEpisodeTree(eps, realFs);
    const out = py(
      `import json, sys
from pathlib import Path
from pipeline.project import get_episode_tree, ProjectEntry
root = Path(sys.argv[1])
t = get_episode_tree(root)
ep_root = root / "data" / "episodes"
rel = lambda p: p.relative_to(ep_root).as_posix()
print(json.dumps({
  "plain": sorted(rel(p) for p in t.plain),
  "projects": {rel(p.dir): {"members": [rel(m) for m in p.members], "file": p.has_project_file} for p in t.projects},
  "order": [rel(e.dir) if isinstance(e, ProjectEntry) else rel(e) for e in t.order],
  "hidden": t.hidden_underscore}))`,
      [r2],
    );
    const pyRes = JSON.parse(out) as { plain: string[]; projects: Record<string, { members: string[]; file: boolean }>; order: string[]; hidden: number };
    expect(ts.plain.map((e) => e.epKey).sort()).toEqual(pyRes.plain);
    expect(Object.fromEntries(ts.projects.map((p) => [p.projKey, { members: p.members.map((m) => m.epKey), file: p.hasProjectFile }]))).toEqual(pyRes.projects);
    expect(ts.order.map((o) => (o.kind === "project" ? o.entry.projKey : o.entry.epKey))).toEqual(pyRes.order);
    expect(ts.hiddenUnderscore).toBe(pyRes.hidden);
    // 期望值先跑后核对：码点序 （终）(U+FF08) 在 😀(U+1F600) 之前；UTF-16 码元序相反（高代理 0xD83D < 0xFF08）
    expect(pyRes.projects.PROJ.members).toEqual(["PROJ/01-a", "PROJ/02-linked", "PROJ/（终）", "PROJ/😀-x"]);
    expect(["（终）", "😀-x"].sort()).toEqual(["😀-x", "（终）"]);
    expect(["😀-x", "（终）"].sort(compareCodePoints)).toEqual(["（终）", "😀-x"]);
    expect(pyRes.projects.NEWP).toEqual({ members: [], file: true });
    expect(pyRes.plain).toEqual(["HALF", "PLAIN"]);
    cleanup(r2);
  });
  it("episodes 根不存在 → 空列表", () => {
    expect(listEpisodes(join(root, "nope"), realFs)).toEqual({ visible: [], hiddenUnderscore: 0 });
  });
});
