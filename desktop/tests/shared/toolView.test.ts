// D80（R2 / R4）：工具行显示规则与回合切段。期望值先在实现上跑通，再逐条核对 spec 的规则后写死。
import { describe, expect, it } from "vitest";
import type { ConvRow, ToolRow } from "../../src/shared/convFold";
import {
  durText,
  firstLine,
  foldLabel,
  groupDefaultOpen,
  groupRuns,
  mainText,
  spanText,
  subText,
  TOOL_CATEGORY,
  toolCategory,
  toolStatus,
  turnDefaultOpen,
  turnSegments,
  type Segment,
} from "../../src/shared/toolView";

const tool = (name: string, ok: boolean | null, o: Partial<ToolRow> = {}): ToolRow => ({
  k: "tool", at: 0, id: `${name}-${String(Math.random())}`, name, summary: "", ok, duplicate: false, durationMs: null, observation: null, logs: [], command: null, description: null, ...o,
});
const user = (text: string, at = 0): ConvRow => ({ k: "user", at, text });
const answer = (text: string, at = 0): ConvRow => ({ k: "assistant", at, kind: "answer", text, label: null });
const footer = (stopped = "done", at = 0): ConvRow => ({ k: "footer", at, text: "…", stopped });
const turns = (segs: Segment[]) => segs.filter((s): s is Extract<Segment, { k: "turn" }> => s.k === "turn");

describe("R2 ② 类别词", () => {
  it("15 个工具全部有类别词（与 config/agent/tools.json 一致），未登记的原样", () => {
    expect(Object.keys(TOOL_CATEGORY).sort()).toEqual(
      ["acquire_propose", "bash", "browser", "cover_edit", "crawl", "list_episodes", "read_artifact", "read_status", "run_pipeline", "search_notes", "web_fetch", "web_search", "write_episode_file", "write_memory", "write_note"].sort(),
    );
    expect(toolCategory("bash")).toBe("终端");
    expect(toolCategory("read_status")).toBe("读取");
    expect(toolCategory("cover_edit")).toBe("写入");
    expect(toolCategory("run_pipeline")).toBe("流水线");
    expect(toolCategory("search_notes")).toBe("搜索");
    expect(toolCategory("browser")).toBe("网页");
    expect(toolCategory("acquire_propose")).toBe("素材");
    expect(toolCategory("future_tool")).toBe("future_tool");
  });
});

describe("R2 ② 状态词", () => {
  it("未结束：所属回合在跑 = 执行中，否则 = 已停止；结束看 ok", () => {
    expect(toolStatus(tool("bash", null), true)).toBe("run");
    expect(toolStatus(tool("bash", null), false)).toBe("stopped");
    expect(toolStatus(tool("bash", true), true)).toBe("ok");
    expect(toolStatus(tool("bash", false), false)).toBe("fail");
  });
});

describe("R2 ① 主文本 / 次文本 / 耗时 / 错误首行", () => {
  it("bash 有用途：用途是主文本、命令摘要退到次文本；没用途：命令摘要是主文本、无次文本", () => {
    const withDesc = tool("bash", true, { summary: "wc -m 02-script.md", description: "统计稿件字数" });
    expect([mainText(withDesc), subText(withDesc)]).toEqual(["统计稿件字数", "wc -m 02-script.md"]);
    const bare = tool("bash", true, { summary: "ls -la" });
    expect([mainText(bare), subText(bare)]).toEqual(["ls -la", ""]);
  });
  it("其余工具用摘要；read_status / list_episodes 摘要为空时兜底", () => {
    expect(mainText(tool("read_artifact", true, { summary: "01-topic.md" }))).toBe("01-topic.md");
    expect(mainText(tool("read_status", true))).toBe("期状态");
    expect(mainText(tool("list_episodes", true))).toBe("期列表");
    expect(mainText(tool("web_search", true))).toBe("");
  });
  it("耗时 < 1s 不显示，≥ 1s 一位小数", () => {
    expect([durText(null), durText(0), durText(999), durText(1000), durText(3240)]).toEqual(["", "", "", "1.0s", "3.2s"]);
  });
  it("错误首行取第一处非空行", () => {
    expect(firstLine("\n  \ncat: x: No such file\nTraceback")).toBe("cat: x: No such file");
    expect(firstLine(null)).toBe("");
  });
  it("回合用时 <60 秒写秒，否则分秒", () => {
    expect([spanText(0), spanText(59_999), spanText(72_000), spanText(-5)]).toEqual(["0 秒", "59 秒", "1分12秒", "0 秒"]);
  });
});

describe("R2 ⑦ 连续 bash 折成终端组", () => {
  const rows: ConvRow[] = [tool("bash", true), tool("bash", false), tool("read_artifact", true), tool("bash", true), { k: "log", at: 0, lines: ["x"] }, tool("bash", true), tool("bash", true)];
  it("≥2 条连续才成组；被别的行隔开就断开", () => {
    expect(groupRuns(rows, [0, 1, 2, 3, 4, 5, 6])).toEqual([
      { k: "group", key: "g:0", indices: [0, 1] },
      { k: "row", index: 2 },
      { k: "row", index: 3 },
      { k: "row", index: 4 },
      { k: "group", key: "g:5", indices: [5, 6] },
    ]);
  });
  it("组默认开合：有失败或有正在跑的就展开", () => {
    expect(groupDefaultOpen(rows, [0, 1], false)).toBe(true);
    expect(groupDefaultOpen(rows, [5, 6], false)).toBe(false);
    const live: ConvRow[] = [tool("bash", true), tool("bash", null)];
    expect(groupDefaultOpen(live, [0, 1], true)).toBe(true);
    expect(groupDefaultOpen(live, [0, 1], false)).toBe(false);
  });
});

describe("R4 ① 回合切段", () => {
  it("正常一轮：工具进过程，终稿答复 / 页脚 / 停机点留在外面；第一条 user 之前的是 loose；默认收起", () => {
    const rows: ConvRow[] = [
      { k: "notice", at: 0, level: "warn", code: "code_freeze", text: "…" },
      user("看下进度", 1000),
      tool("read_status", true),
      tool("bash", true),
      tool("bash", true),
      answer("停在 05", 5000),
      footer("done", 73_000),
      { k: "stop", at: 0, text: "停机点 05 待审（在待答区处理）" },
    ];
    const segs = turnSegments(rows, false);
    expect(segs[0]).toEqual({ k: "loose", index: 0 });
    const [t] = turns(segs);
    expect(t.process).toEqual([{ k: "row", index: 2 }, { k: "group", key: "g:3", indices: [3, 4] }]);
    expect(t.tail).toEqual([5, 6, 7]);
    expect([t.outcome, t.tools, t.fails, t.running]).toEqual(["done", 3, 0, false]);
    expect(turnDefaultOpen(t)).toBe(false);
    expect(foldLabel(t, 0)).toBe("已工作 1分12秒");
  });
  it("有失败 / 被拒卡 / 页脚非 done / 进程退出 → 不自动收起", () => {
    const fail = turns(turnSegments([user("x"), tool("bash", false), answer("y"), footer()], false))[0];
    expect([fail.outcome, fail.fails, turnDefaultOpen(fail)]).toEqual(["fail", 1, true]);
    const rej = turns(turnSegments([user("x"), { k: "card", at: 0, requestId: "q", label: "[工具卡] bash", text: "…", status: "rejected" }, answer("y"), footer()], false))[0];
    expect([rej.outcome, turnDefaultOpen(rej)]).toEqual(["rejected", true]);
    const intr = turns(turnSegments([user("x", 0), tool("bash", true), footer("interrupted", 9000)], false))[0];
    expect([intr.outcome, turnDefaultOpen(intr), foldLabel(intr, 0)]).toEqual(["stopped", true, "已停止 · 用时 9 秒"]);
    const exited = turns(turnSegments([user("x"), tool("bash", null), { k: "exited", at: 0, code: 1, signal: null, stderrTail: "" }], false))[0];
    expect(exited.outcome).toBe("stopped");
  });
  it("最后一轮没页脚且会话在跑 = 运行中（展开、「工作中 N 秒」）；不在跑 = 已停止", () => {
    const rows: ConvRow[] = [user("a", 0), tool("bash", true), answer("b"), footer(), user("c", 10_000), tool("bash", null)];
    const live = turns(turnSegments(rows, true));
    expect(live.map((t) => t.running)).toEqual([false, true]);
    expect([live[1].outcome, turnDefaultOpen(live[1]), foldLabel(live[1], 33_000)]).toEqual(["running", true, "工作中 23 秒"]);
    expect(turns(turnSegments(rows, false))[1].outcome).toBe("stopped");
  });
  it("D79 插话：user 紧跟工具行、回合未结束 → 留在过程里，不另起一段", () => {
    const rows: ConvRow[] = [user("改第 3 段"), tool("bash", true), user("顺便看下第 4 段"), tool("bash", true), answer("都改了"), footer()];
    const segs = turns(turnSegments(rows, false));
    expect(segs).toHaveLength(1);
    expect(segs[0].process).toEqual([{ k: "row", index: 1 }, { k: "row", index: 2 }, { k: "row", index: 3 }]);
    expect(segs[0].tail).toEqual([4, 5]);
  });
  it("中途答复（D79 续轮）之后还有工具：它进过程，最后那条才是终稿", () => {
    const rows: ConvRow[] = [user("x"), answer("先这样"), tool("read_artifact", true), answer("终稿"), footer()];
    const [t] = turns(turnSegments(rows, false));
    expect(t.process).toEqual([{ k: "row", index: 1 }, { k: "row", index: 2 }]);
    expect(t.tail).toEqual([3, 4]);
  });
  it("历史重建（无页脚）：user 跟在答复之后即新一轮；不知用时，标签「工作过程」", () => {
    const rows: ConvRow[] = [{ k: "historySeparator", at: 0 }, user("a"), tool("bash", true), answer("b"), user("c"), answer("d")];
    const segs = turnSegments(rows, false);
    expect(segs[0]).toEqual({ k: "loose", index: 0 });
    const ts = turns(segs);
    expect(ts.map((t) => [t.user, t.process.length, t.tail])).toEqual([[1, 1, [3]], [4, 0, [5]]]);
    expect([ts[0].outcome, ts[0].endAt, foldLabel(ts[0], 0)]).toEqual(["done", null, "工作过程"]);
  });
  it("纯聊天一轮：过程为空", () => {
    const [t] = turns(turnSegments([user("你好"), answer("在"), footer()], false));
    expect([t.process, t.tail, t.tools]).toEqual([[], [1, 2], 0]);
  });
});
