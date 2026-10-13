// D81 PR-2b：侧栏企划卡片（方案 B）的纯函数——进度条各段、卷行顺序与标签、徽标、搜索连带显示、「按时间」混排。
import { describe, expect, it } from "vitest";
import type { EpisodeSummary, ProjectMeta, ProjectSummary } from "../../src/shared/protocol";
import { filterProject, plainEpisodes, progressSegments, projectBadges, projectTitle, timeline, volumeRows, volumeStage } from "../../src/shared/projectView";

const ep = (epKey: string, currentStep: string | null, isBlocked: boolean | null = false, mtimeMs = 0, conv: EpisodeSummary["conv"] = null): EpisodeSummary =>
  ({ epKey, mtimeMs, currentStep, isBlocked, conv }) as EpisodeSummary;
const meta = (volumes: [number, string][], collection: string | null = null): ProjectMeta => ({
  hasProjectFile: true, name: "测试编年史", collection, volumes: volumes.map(([seq, dir]) => ({ seq, dir })), error: null, hints: [],
});
const proj = (members: EpisodeSummary[], m: ProjectMeta | null = null, projKey = "P", mtimeMs = 0): ProjectSummary => ({ projKey, mtimeMs, hasProjectFile: m !== null, members, conv: null, meta: m });

describe("volumeStage：只有两种硬事实（停在人审、走到 09），其余一律进行中", () => {
  it.each([
    [null, null, "none"],
    ["05 审时间码", true, "stop"],
    ["09 人工发布", true, "done"], // status 在 09 恒报 is_blocked=true：仍是「完成」，不是停在人审
    ["09", false, "done"],
    ["08 封面与标题候选", false, "mid"],
    ["090 别的", false, "mid"], // 09 后必须是空白或结尾，不靠前缀撞上
  ] as const)("%j blocked=%j → %s", (step, blocked, want) => {
    expect(volumeStage({ currentStep: step, isBlocked: blocked })).toBe(want);
  });
});

describe("volumeRows：卷表顺序在前、合集其次、不在卷表的按码点序殿后", () => {
  it("卷表顺序（不是目录名序）、合集写「合集 ·」、名字比较按 NFC + 小写", () => {
    const p = proj([ep("P/00-合集", "01"), ep("P/01-A", "02"), ep("P/02-b", "03"), ep("P/zz", "01"), ep("P/aa", "01")], meta([[1, "02-b"], [2, "01-a"]], "00-合集"));
    expect(volumeRows(p).map((r) => [r.label, r.seq, r.collection])).toEqual([
      ["第 1 卷 · 02-b", 1, false],
      ["第 2 卷 · 01-A", 2, false],
      ["合集 · 00-合集", null, true],
      ["aa", null, false],
      ["zz", null, false],
    ]);
  });
  it("没有卷表（meta 取不到）→ 按目录名码点序，标签只写目录名；增补平面字符按码点而不是 UTF-16 码元", () => {
    // "\u{1F600}"（码点 0x1F600）在 UTF-16 码元序里排在 "！"（0xFF01）之前，码点序里在之后
    const p = proj([ep("P/\u{1F600}", "01"), ep("P/！", "01"), ep("P/01-a", "01")]);
    expect(volumeRows(p).map((r) => r.label)).toEqual(["01-a", "！", "\u{1F600}"]);
  });
});

describe("progressSegments：每卷一段（卷表里的卷；合集不进）", () => {
  it("有卷表 → 只取卷表里的卷，按卷序；段的 title 含工序原文", () => {
    const p = proj([ep("P/00-合集", "09 发布"), ep("P/01-a", "05 审时间码", true), ep("P/02-b", "09 发布"), ep("P/草稿", "01")], meta([[1, "02-b"], [2, "01-a"]], "00-合集"));
    expect(progressSegments(p).map((s) => [s.stage, s.title])).toEqual([
      ["done", "第 1 卷 · 02-b：09 发布"],
      ["stop", "第 2 卷 · 01-a：05 审时间码"],
    ]);
  });
  it("没有卷表 → 退回除合集外的全部成员；工序未取到写「未取到」", () => {
    const p = proj([ep("P/01-a", null, null), ep("P/02-b", "03 语音合成")]);
    expect(progressSegments(p).map((s) => [s.label, s.stage, s.title])).toEqual([
      ["01-a", "none", "01-a：未取到"],
      ["02-b", "mid", "02-b：03 语音合成"],
    ]);
  });
});

describe("projectBadges / projectTitle", () => {
  it("待答卡 = 各卷 + 企划会话之和；停机 = 停在人审的卷数；运行中任一即真", () => {
    const conv = (openRequests: number, running = false) => ({ live: true, running, openRequests }) as EpisodeSummary["conv"];
    const p = { ...proj([ep("P/01-a", "05", true, 0, conv(2)), ep("P/02-b", "03", false, 0, conv(0, true)), ep("P/03-c", "05", true), ep("P/04-d", "09 人工发布", true)]), conv: conv(1) };
    expect(projectBadges(p)).toEqual({ cards: 3, stops: 2, running: true }); // 走到 09 的卷不计停机
    expect(projectBadges(proj([ep("P/01-a", "03")]))).toEqual({ cards: 0, stops: 0, running: false });
  });
  it("卷表解析出企划名用它，取不到用目录名", () => {
    expect(projectTitle(proj([], meta([])))).toBe("测试编年史");
    expect(projectTitle(proj([], null, "EGOIST-传奇企划志"))).toBe("EGOIST-传奇企划志");
  });
});

describe("filterProject：搜索命中卷名时企划连带显示、只列命中的卷", () => {
  const p = proj([ep("P/01-借躯降生", "09"), ep("P/02-皮套囚徒", "09")], meta([]), "EGOIST-V2");
  it("空查询原样；企划名或目录名命中 → 整卡；只有卷名命中 → 只列命中的卷；都不命中 → null", () => {
    expect(filterProject(p, "  ")).toBe(p);
    expect(filterProject(p, "egoist")!.members.length).toBe(2);
    expect(filterProject(p, "编年史")!.members.length).toBe(2);
    expect(filterProject(p, "借躯")!.members.map((m) => m.epKey)).toEqual(["P/01-借躯降生"]);
    expect(filterProject(p, "伪恋")).toBeNull();
  });
});

describe("timeline / plainEpisodes：「按时间」普通期与企划按 mtime 降序混排，企划成员不进平铺", () => {
  it("混排与去成员", () => {
    const vol = ep("P/01-a", "01", false, 50);
    const all = [ep("老", "01", false, 10), vol, ep("新", "01", false, 90)];
    const projects = [proj([vol], null, "P", 50)];
    const plain = plainEpisodes(all, projects);
    expect(plain.map((e) => e.epKey)).toEqual(["老", "新"]);
    expect(timeline(plain, projects).map((i) => (i.kind === "episode" ? i.e.epKey : `proj:${i.p.projKey}`))).toEqual(["新", "proj:P", "老"]);
  });
});
