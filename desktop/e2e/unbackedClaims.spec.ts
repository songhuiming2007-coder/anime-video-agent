// D105（人选 B）：回合 0 次工具调用、回复却声称核查过 → 回复下方一条警示；调过工具的回合不出。
// 真实 core + 本地假 LLM。设 AVA_SHOTS=<目录> 时另截真实窗口图（浅色 / 深色）。
import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { assistant, startFakeLlm } from "./fakeLlm";
import { launchSession, realCoreFixture } from "./sessionFixtures";

const SHOTS = process.env.AVA_SHOTS;

async function shoot(page: Page, name: string): Promise<void> {
  if (!SHOTS) return;
  mkdirSync(SHOTS, { recursive: true });
  for (const dark of [false, true]) {
    await page.evaluate((d) => {
      if (d) document.documentElement.setAttribute("data-theme", "dark");
      else document.documentElement.removeAttribute("data-theme");
    }, dark);
    await page.emulateMedia({ colorScheme: dark ? "dark" : "light" });
    await page.waitForTimeout(150);
    await page.screenshot({ path: join(SHOTS, `${name}-${dark ? "dark" : "light"}.png`) });
  }
}

const send = async (page: Page, text: string): Promise<void> => {
  await page.getByTestId("composer-input").fill(text);
  await page.getByTestId("composer-input").press("Enter");
  await expect(page.getByTestId("composer-stop")).toHaveCount(0, { timeout: 30_000 });
};

test("D105 没调工具却声称核查过：回复下方警示", async () => {
  test.setTimeout(120_000);
  const llm = await startFakeLlm();
  const fx = realCoreFixture(llm.url, ["EP-D105"]);
  const L = await launchSession(fx.repo);
  const page = L.page;
  try {
    await page.setViewportSize({ width: 1280, height: 820 });
    await page.locator("[data-testid=episode]", { hasText: "EP-D105" }).first().click();

    llm.push(assistant("好的，先看看稿件。"));
    await send(page, "你好");
    await expect(page.getByTestId("unbacked-claims")).toHaveCount(0);

    // 10-15 supercell 会话 seq 604–615 的原话
    llm.push(assistant("我重新调取了音频工程和乐谱档案，戴上监听耳机反复复核了前奏，按一手物证时间轴查实，段 6 不用改。"));
    await send(page, "段 6 说开头是钢琴滑音，你再核一下");
    const warn = page.getByTestId("unbacked-claims");
    await expect(warn).toHaveCount(1);
    await expect(warn).toContainText("本回合没有调用任何工具");
    await expect(warn).toContainText("「调取」「复核」「一手物证」「查实」");
    await shoot(page, "d105-unbacked-claims");
  } finally {
    await L.app.close().catch(() => undefined);
    await llm.close();
    fx.cleanup();
  }
});
