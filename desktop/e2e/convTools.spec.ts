// D80（spec 2026-10-12-conv-liveliness §七）：工具行 / 终端组 / 回合折叠 / 运行动感，真实 core + 假模型。
// 机检只做底线（plans/README UI 规则 5）：数据显示对、不重叠不溢出、减弱动效真停、恢复的行不播入场。「好不好用」归人验收。
import { expect, test, type Page } from "@playwright/test";
import { assistant, startFakeLlm, toolCalls } from "./fakeLlm";
import { convSnapshot, launchSession, realCoreFixture, stubQuit } from "./sessionFixtures";

const EP = "SESS-A";
const OTHER = "SESS-B";

const openEp = async (page: Page, ep: string): Promise<void> => {
  await page.locator("[data-testid=episode]", { hasText: ep }).first().click();
  await expect(page.getByTestId("center")).toHaveAttribute("data-ep", ep);
};
const send = async (page: Page, text: string): Promise<void> => {
  await page.getByTestId("composer-input").fill(text);
  await page.getByTestId("composer-input").press("Enter");
};
const bash = (command: string, description: string) => ({ name: "bash", args: { command, description } });

/** 两两 boundingBox 不相交（面积为 0 的跳过）；返回相交的对子，便于失败时看是谁 */
async function overlaps(page: Page, selector: string): Promise<string[]> {
  return page.evaluate((sel) => {
    const boxes = Array.from(document.querySelectorAll<HTMLElement>(sel))
      .map((el) => ({ el, r: el.getBoundingClientRect() }))
      .filter(({ r }) => r.width > 0 && r.height > 0);
    const bad: string[] = [];
    for (let i = 0; i < boxes.length; i += 1)
      for (let j = i + 1; j < boxes.length; j += 1) {
        const a = boxes[i].r;
        const b = boxes[j].r;
        if (a.left < b.right - 0.5 && b.left < a.right - 0.5 && a.top < b.bottom - 0.5 && b.top < a.bottom - 0.5)
          bad.push(`${boxes[i].el.textContent?.slice(0, 30) ?? ""} ✕ ${boxes[j].el.textContent?.slice(0, 30) ?? ""}`);
      }
    return bad;
  }, selector);
}

test("D80 真实 core：工具行说人话、失败回合不折 / 成功回合折、终端组、两档窗口不重叠不溢出、减弱动效真停、恢复的行不播入场", async () => {
  test.setTimeout(120_000);
  const llm = await startFakeLlm();
  const fx = realCoreFixture(llm.url, [EP, OTHER]);
  const L = await launchSession(fx.repo, [], { previewOpen: false });
  const p = L.page;
  const turnsDone = (n: number) =>
    expect.poll(async () => (await convSnapshot(L, `ep:${EP}`)).entries.filter((e) => e.k === "frame" && e.frame?.t === "turn_finished").length, { timeout: 30_000 }).toBeGreaterThanOrEqual(n);
  try {
    await p.setViewportSize({ width: 1440, height: 900 });
    await openEp(p, EP);

    // 回合 1：全部成功 → 结束时自动收起成一行
    llm.push(toolCalls({ name: "read_status", args: {} }), toolCalls(bash("ls -la", "列出期目录"), bash("pwd", "看当前目录")), assistant("停在 05。"));
    await send(p, "这期走到哪了");
    await turnsDone(1);
    const t1 = p.getByTestId("conv-turn").nth(0);
    await expect(t1).toHaveAttribute("data-outcome", "done");
    await expect(t1).toHaveAttribute("data-open", "0");
    await expect(t1.getByTestId("turn-fold")).toContainText("已工作");
    await expect(t1.getByTestId("turn-fold")).toContainText("3 个工具");
    await expect(t1.getByTestId("tool-toggle")).toHaveCount(0); // 收起后过程不挂载
    // 实时到达的用户消息与答复挂入场标记
    await expect(p.locator("[data-testid=conv-row][data-kind=user][data-enter='1']")).toHaveCount(1);
    await expect(p.locator("[data-testid=conv-row][data-kind=answer][data-enter='1']")).toHaveCount(1);

    // R5：回合末脚注默认短摘要，ⓘ 展开完整遥测
    const foot = p.locator("[data-testid=conv-row][data-kind=footer]").first();
    await expect(foot.getByTestId("footer-brief")).toHaveText(/^\d+ 次模型调用 · 上下文/);
    await expect(foot.getByTestId("footer-full")).toHaveCount(0);
    await foot.getByTestId("footer-more").click();
    await expect(foot.getByTestId("footer-full")).toContainText("检查点");
    // R7：会话行右侧相对时间
    await expect(p.getByTestId("session-when").first()).toHaveText("刚刚");

    // 回合 2：有失败 → 不折；三条 bash 折成一组，组内有失败 → 组默认展开
    llm.push(toolCalls(bash("ls _agent", "列出留底目录"), bash("cat 旧稿-v1.md", "读取旧稿"), bash("head -3 01-topic.md", "看选题开头")), assistant("旧稿不在。"));
    await send(p, "把旧稿找出来");
    await turnsDone(2);
    const t2 = p.getByTestId("conv-turn").nth(1);
    await expect(t2).toHaveAttribute("data-outcome", "fail");
    await expect(t2).toHaveAttribute("data-open", "1");
    await expect(t2.getByTestId("turn-fold")).toContainText("1 个失败");
    const group = t2.getByTestId("conv-group");
    await expect(group).toHaveAttribute("data-open", "1");
    await expect(group.getByTestId("group-toggle")).toContainText("3 个命令");
    const rows = group.locator("[data-testid=conv-row][data-kind=tool]");
    await expect(rows).toHaveCount(3);
    // R2 ①：用途是主文本、命令退到次文本；组内子行不重复类别词；只有状态词带失败色
    const failRow = rows.nth(1);
    await expect(failRow).toHaveAttribute("data-status", "fail");
    await expect(failRow.locator(".conv-tool-main")).toHaveText("读取旧稿");
    await expect(failRow.locator(".conv-tool-sub")).toHaveText("cat 旧稿-v1.md");
    await expect(failRow.locator(".conv-tool-cat")).toHaveCount(0);
    await expect(failRow.getByTestId("tool-status")).toHaveText("执行失败");
    await expect(failRow.getByTestId("tool-status")).toHaveAttribute("title", /旧稿-v1\.md/);
    const colors = await failRow.evaluate((el) => {
      const c = (q: string) => getComputedStyle(el.querySelector(q)!).color;
      return { main: c(".conv-tool-main"), status: c("[data-testid=tool-status]"), sub: c(".conv-tool-sub") };
    });
    expect(colors.status).not.toBe(colors.main);
    expect(colors.sub).not.toBe(colors.status);
    // 展开：命令全文 + 模型看到的原文（逐字与会话记录一致的断言在 sessionReal TX-1）
    await failRow.getByTestId("tool-toggle").click();
    await expect(failRow.locator(".conv-tool-cmd code")).toHaveText("cat 旧稿-v1.md");
    await expect(failRow.getByTestId("conv-observation").locator("pre")).toContainText("旧稿-v1.md");
    await expect(failRow.getByTestId("tool-copy")).toHaveCount(0); // 展开后复制在命令块里，行内不重复

    // 底线：两档窗口下工具行、组头、折叠行、消息两两不相交，对话流不横向溢出
    const LINES = "[data-testid=turn-fold], .conv-tool-row > .conv-tool, .conv-group > .conv-tool, [data-testid=conv-row][data-kind=user], [data-testid=conv-row][data-kind=answer]";
    for (const [w, h] of [[1440, 900], [1280, 800]] as const) {
      await p.setViewportSize({ width: w, height: h });
      await t1.getByTestId("turn-fold").click(); // 回合 1 也展开，行越多越容易撞
      await expect(t1).toHaveAttribute("data-open", "1");
      await p.waitForTimeout(400); // 等高度过渡走完（--dur-slow 300ms）
      expect(await overlaps(p, LINES), `${w}×${h} 重叠`).toEqual([]);
      const sw = await p.getByTestId("conv-stream").evaluate((el) => [el.scrollWidth, el.clientWidth]);
      expect(sw[0], `${w}×${h} 横向溢出`).toBeLessThanOrEqual(sw[1]);
      await t1.getByTestId("turn-fold").click();
      await expect(t1).toHaveAttribute("data-open", "0");
    }

    // 回合 3：运行中——类别词扫光、末尾「正在执行」；减弱动效下扫光与入场的 animation-name 为 none
    llm.push(toolCalls(bash("sleep 4", "等待机检作业")), assistant("跑完了。"));
    await send(p, "跑一下机检");
    const shimmer = p.getByTestId("conv-tool-shimmer");
    await expect(shimmer).toHaveCount(1);
    // R7.2：正在跑的会话，侧栏标题扫光
    await expect(p.locator("[data-testid=session-row][data-live='1'] [data-testid=session-title]")).toHaveAttribute("data-running", "1");
    await expect(p.getByTestId("conv-activity")).toContainText("正在执行");
    await expect(p.getByTestId("conv-turn").nth(2)).toHaveAttribute("data-outcome", "running");
    await expect(p.getByTestId("conv-turn").nth(2).getByTestId("turn-fold")).toContainText("工作中");
    expect(await shimmer.evaluate((el) => getComputedStyle(el).animationName)).toBe("conv-shimmer");
    await p.emulateMedia({ reducedMotion: "reduce" });
    expect(await shimmer.evaluate((el) => getComputedStyle(el).animationName)).toBe("none");
    expect(await p.locator(".conv-enter").first().evaluate((el) => getComputedStyle(el).animationName)).toBe("none");
    await p.emulateMedia({ reducedMotion: "no-preference" });
    await turnsDone(3);
    await expect(shimmer).toHaveCount(0);
    // R5：三轮下来停机点没变，「停机点 … 待审」只有一行（此前每轮一行）
    await expect(p.locator("[data-testid=conv-row][data-kind=stop]")).toHaveCount(1);
    await expect(p.locator("[data-testid=session-title][data-running]")).toHaveCount(0);
    await expect(p.getByTestId("conv-turn").nth(2)).toHaveAttribute("data-open", "0"); // 运行 → 结束的边沿自动收起

    // R6.3 / R7.2：要批的卡——时间线卡片行「等待批准」、侧栏活会话挂「待答」；批准后结局写回
    llm.push(toolCalls(bash("rm stale.tmp", "清理临时文件")), assistant("清理完了。"));
    await send(p, "把临时文件清掉");
    const cardRow = p.locator("[data-testid=conv-row][data-kind=card]");
    await expect(cardRow.locator(".conv-card-phrase")).toHaveText("等待批准", { timeout: 20_000 });
    await expect(cardRow.locator(".conv-tool-cat")).toHaveText("工具卡");
    await expect(p.getByTestId("session-open")).toHaveText("待答");
    await p.getByTestId("request-answer-approve").click();
    await turnsDone(4);
    await expect(p.getByTestId("session-open")).toHaveCount(0);
    await p.getByTestId("conv-turn").nth(3).getByTestId("turn-fold").click(); // 正常结束已收起，展开看卡片结局
    await expect(p.getByTestId("conv-turn").nth(3).locator("[data-kind=card] .conv-card-phrase")).toHaveText("已批准");

    // 切走再切回（App 以会话键重挂对话流）：已在的行一律不带入场标记
    await openEp(p, OTHER);
    await openEp(p, EP);
    await expect(p.getByTestId("conv-turn")).toHaveCount(4);
    await expect(p.locator("[data-testid=conv-stream] [data-enter]")).toHaveCount(0);
    await expect(p.locator("[data-testid=conv-stream] .conv-enter")).toHaveCount(0);
  } finally {
    await stubQuit(L, "quit").catch(() => undefined);
    await L.app.close().catch(() => undefined);
    await llm.close();
    fx.cleanup();
  }
});
