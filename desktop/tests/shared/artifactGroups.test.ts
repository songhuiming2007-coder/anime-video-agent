// D49-A S4/S5：预览区文件切换器的分组纯函数。
import { describe, expect, it } from "vitest";
import { generatingOf, groupArtifacts } from "../../src/shared/artifactGroups";
import type { TreeEntry } from "../../src/shared/protocol";

const e = (name: string, kind: TreeEntry["kind"] = name.includes(".") ? "file" : "dir"): TreeEntry => ({ name, rel: name, kind, size: 1, mtimeMs: 1 });

describe("groupArtifacts", () => {
  it("按稿件 / 配音 / 排片与成片 / 其他分组；主线产物缺了也列出并标 missing，可选产物缺了不列", () => {
    const g = groupArtifacts([e("01-topic.md"), e("02-script.md"), e("03-audio"), e("events.jsonl"), e("_agent")]);
    expect(g.map((x) => x.title)).toEqual(["稿件", "配音", "排片与成片", "其他"]);
    expect(g[0].items.map((i) => i.name)).toEqual(["01-topic.md", "02-script.md"]);
    const clips = g[2].items.find((i) => i.name === "04-clips.json");
    expect(clips?.missing).toBe(true);
    expect(g[2].items.map((i) => i.name)).toEqual(["04-clips.json", "05-final.mp4"]);
    expect(g[3].items.map((i) => i.name)).toEqual(["events.jsonl", "_agent"]);
  });

  it("只有存在的定稿 02-script.md 标可编辑", () => {
    const g = groupArtifacts([e("02-script.md"), e("02-script.draft.md"), e("04-review.html")]);
    expect(g[2].items.map((i) => i.name)).toEqual(["04-clips.json", "04-review.html", "05-final.mp4"]);
    const names = Object.fromEntries(g[0].items.map((i) => [i.name, i.editable]));
    expect(names).toEqual({ "01-topic.md": false, "02-script.draft.md": false, "02-script.md": true });
  });

  it("D70：定稿缺、草稿在 → 02-script.md 仍 missing 但 fromDraft 可点；草稿也没有就照旧灰掉", () => {
    const withDraft = groupArtifacts([e("01-topic.md"), e("02-script.draft.md")]);
    const script = withDraft[0].items.find((i) => i.name === "02-script.md");
    expect(script).toMatchObject({ missing: true, fromDraft: true, editable: true, kind: "file" });
    const noDraft = groupArtifacts([e("01-topic.md")]).at(0)?.items.find((i) => i.name === "02-script.md");
    expect(noDraft).toMatchObject({ missing: true, fromDraft: false, editable: false });
    const both = groupArtifacts([e("02-script.md"), e("02-script.draft.md")]).at(0)?.items.find((i) => i.name === "02-script.md");
    expect(both).toMatchObject({ missing: false, fromDraft: false, editable: true });
  });

  it("运行中作业标出正在产出的产物（命令可带参数、可带 pipeline. 前缀）", () => {
    expect([...generatingOf(["tts --redo 2,4", null, "pipeline.clips", "check_script"])].sort()).toEqual(["03-audio", "04-clips.json"]);
    const g = groupArtifacts([e("03-audio")], ["tts"]);
    expect(g[1].items[0].generating).toBe(true);
  });
});
