// D80 R1（spec 2026-10-12-conv-liveliness §七）：会话标题，真实 core + 假模型。
// 首轮结束 → 后台起名 → 侧栏行从首条消息淡入换成生成名；人改名（回车存、Esc 取消）→ 重启 app 仍在；起名失败 → 主回合照常、保留兜底名。
import { expect, test, type Page } from "@playwright/test";
import { assistant, startFakeLlm } from "./fakeLlm";
import { convSnapshot, launchSession, realCoreFixture, stubQuit } from "./sessionFixtures";

const EP = "SESS-A";
const openEp = async (page: Page, ep: string): Promise<void> => {
  await page.locator("[data-testid=episode]", { hasText: ep }).first().click();
  await expect(page.getByTestId("center")).toHaveAttribute("data-ep", ep);
};
const send = async (page: Page, text: string): Promise<void> => {
  await page.getByTestId("composer-input").fill(text);
  await page.getByTestId("composer-input").press("Enter");
};

test("D80 真实 core：首轮结束后侧栏换成生成名；改名回车存、Esc 取消；重启 app 名字还在", async () => {
  test.setTimeout(120_000);
  const llm = await startFakeLlm();
  llm.titleReply = "「检查稿件字数」";
  llm.titleDelayMs = 1500; // 标题晚于回合结束（及其触发的列表重取）到达：侧栏必须靠 session_title 帧才换名
  const fx = realCoreFixture(llm.url, [EP]);
  let L = await launchSession(fx.repo, [], { previewOpen: false });
  const title = () => L.page.locator("[data-testid=session-row] [data-testid=session-title]");
  try {
    llm.push(assistant("字数正常。"), assistant("好的。"));
    await openEp(L.page, EP);
    await send(L.page, "帮我看一下这期稿件字数是不是超了");
    // 侧栏先是首条消息兜底（回合结束后的列表重取），标题到了再换
    await expect(L.page.locator("[data-testid=conv-row][data-kind=answer]")).toHaveText("字数正常。");
    await expect(title()).toHaveText("帮我看一下这期稿件字数是不是超了");
    await expect(title()).toHaveAttribute("data-source", "first_input");
    await expect(title()).toHaveText("检查稿件字数", { timeout: 20_000 });
    await expect(title()).toHaveAttribute("data-source", "generated");
    expect(llm.titleRequests).toHaveLength(1);
    expect(llm.requests).toHaveLength(1); // 起名请求不进主循环
    const frames = (await convSnapshot(L, `ep:${EP}`)).entries.filter((e) => e.k === "frame" && e.frame?.t === "session_title");
    expect(frames).toHaveLength(1);
    await expect(L.page.getByTestId("conv-stream")).not.toContainText("检查稿件字数"); // 不在聊天流里出任何提示

    // 改名：Esc 取消一字不动；回车保存（会话还活着，不结束它）
    const row = L.page.locator("[data-testid=session-row]").first();
    await row.hover();
    await row.getByTestId("session-rename").click();
    const input = L.page.getByTestId("session-rename-input");
    await expect(input).toHaveValue("检查稿件字数");
    await input.fill("随便改改");
    await input.press("Escape");
    await expect(title()).toHaveText("检查稿件字数");
    await row.hover();
    await row.getByTestId("session-rename").click();
    await L.page.getByTestId("session-rename-input").fill("修稿那次");
    await L.page.getByTestId("session-rename-input").press("Enter");
    await expect(title()).toHaveText("修稿那次");
    await expect(title()).toHaveAttribute("data-source", "custom");
    await expect(L.page.locator("[data-testid=session-row]").first()).toHaveAttribute("data-live", "1");
    // 再聊一轮：已有名字不再起名
    await send(L.page, "那就这样");
    await expect.poll(async () => (await convSnapshot(L, `ep:${EP}`)).entries.filter((e) => e.k === "frame" && e.frame?.t === "turn_finished").length).toBe(2);
    expect(llm.titleRequests).toHaveLength(1);

    await stubQuit(L, "quit");
    await L.app.close();
    L = await launchSession(fx.repo, [], { previewOpen: false });
    await openEp(L.page, EP);
    await expect(title()).toHaveText("修稿那次", { timeout: 20_000 });
  } finally {
    await stubQuit(L, "quit").catch(() => undefined);
    await L.app.close().catch(() => undefined);
    await llm.close();
    fx.cleanup();
  }
});

test("D80 真实 core：起名失败（500）→ 主回合照常结束、聊天流无提示、侧栏保留首条消息", async () => {
  test.setTimeout(60_000);
  const llm = await startFakeLlm(); // titleReply 默认 null = 回 500
  const fx = realCoreFixture(llm.url, [EP]);
  const L = await launchSession(fx.repo, [], { previewOpen: false });
  try {
    llm.push(assistant("在。"));
    await openEp(L.page, EP);
    await send(L.page, "这期到哪了");
    await expect(L.page.locator("[data-testid=conv-row][data-kind=answer]")).toHaveText("在。");
    await expect.poll(() => llm.titleRequests.length, { timeout: 20_000 }).toBe(1);
    await expect(L.page.locator("[data-testid=session-row] [data-testid=session-title]")).toHaveText("这期到哪了");
    await expect(L.page.locator("[data-testid=session-row] [data-testid=session-title]")).toHaveAttribute("data-source", "first_input");
    // 起名失败不出任何提示（夹具仓不是干净 git 树，会照常有一条 code_freeze 提示——它不是本用例的事）
    const codes = await L.page.locator("[data-testid=conv-row][data-kind=notice]").evaluateAll((els) => els.map((e) => e.getAttribute("data-code")));
    expect(codes.filter((c) => c !== "code_freeze")).toEqual([]);
  } finally {
    await stubQuit(L, "quit").catch(() => undefined);
    await L.app.close().catch(() => undefined);
    await llm.close();
    fx.cleanup();
  }
});
