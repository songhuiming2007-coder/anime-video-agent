// TI-5：期列表跨语言对拍——TS listEpisodes 与 Python cli.get_episodes_list(root=…) 完全一致。
import { mkdirSync, symlinkSync, utimesSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { afterAll, describe, expect, it } from "vitest";
import { realFs } from "../../src/host/fsio";
import { listEpisodes } from "../../src/host/episodes";
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
  it("episodes 根不存在 → 空列表", () => {
    expect(listEpisodes(join(root, "nope"), realFs)).toEqual({ visible: [], hiddenUnderscore: 0 });
  });
});
