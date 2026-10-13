// D91：输入框工具栏的模型 / 推理按钮（照 ZCode），真实 core + 本地假 LLM。
// 断言：按钮跟着 model_config 帧变；切换只影响本会话下一轮请求；回合在跑时置灰；对话框 /model 同一条路。
// 设 AVA_SHOTS=<目录> 时另截真实窗口图（浅色 / 深色：空闲、模型下拉、推理下拉、回合进行中）给人看样子。
import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { assistant, startFakeLlm } from "./fakeLlm";
import { launchSession, realCoreFixture, stubQuit } from "./sessionFixtures";

const SHOTS = process.env.AVA_SHOTS;

const send = async (page: Page, text: string): Promise<void> => {
  await page.getByTestId("composer-input").fill(text);
  await page.getByTestId("composer-input").press("Enter");
};

const idle = async (page: Page): Promise<void> => {
  await expect(page.getByTestId("composer-stop")).toHaveCount(0, { timeout: 30_000 });
};

async function setTheme(page: Page, dark: boolean): Promise<void> {
  await page.evaluate((d) => {
    if (d) document.documentElement.setAttribute("data-theme", "dark");
    else document.documentElement.removeAttribute("data-theme");
  }, dark);
  await page.emulateMedia({ colorScheme: dark ? "dark" : "light" });
  await page.waitForTimeout(150);
}

async function shoot(page: Page, name: string): Promise<void> {
  if (!SHOTS) return;
  mkdirSync(SHOTS, { recursive: true });
  for (const dark of [false, true]) {
    await setTheme(page, dark);
    await page.screenshot({ path: join(SHOTS, `${name}-${dark ? "dark" : "light"}.png`) });
  }
  await setTheme(page, false);
}

test("D91 模型 / 推理按钮：显示、切换、只影响本会话、回合中置灰", async () => {
  test.setTimeout(180_000);
  const llm = await startFakeLlm();
  const fx = realCoreFixture(llm.url, ["SESS-A"]);
  const L = await launchSession(fx.repo);
  const page = L.page;
  try {
    await page.setViewportSize({ width: 1280, height: 820 });
    await page.locator("[data-testid=episode]", { hasText: "SESS-A" }).first().click();
    // 会话没起：显示「—」、按钮不可点
    await expect(page.getByTestId("model-label")).toHaveText("—");
    await expect(page.getByTestId("model-btn")).toBeDisabled();

    llm.push(assistant("好的，先看看稿件。"));
    await send(page, "你好");
    await expect(page.getByTestId("model-label")).toHaveText("fake");
    await expect(page.getByTestId("effort-label")).toHaveText("默认");
    await expect(page.getByTestId("model-btn")).toHaveAttribute("data-source", "default");
    await idle(page);
    await shoot(page, "1-idle");

    // 模型下拉：列表来自代理 /models，当前项打勾
    await page.getByTestId("model-btn").click();
    await expect(page.getByTestId("model-row")).toHaveCount(4);
    await expect(page.locator('[data-testid=model-row][data-model="fake"]')).toHaveAttribute("aria-current", "true");
    await shoot(page, "2-model-open");
    await page.locator('[data-testid=model-row][data-model="kimi-k3"]').click();
    await expect(page.getByTestId("model-label")).toHaveText("kimi-k3");
    await expect(page.getByTestId("model-btn")).toHaveAttribute("data-source", "session");

    // 推理下拉
    await page.getByTestId("effort-btn").click();
    await expect(page.getByTestId("effort-row")).toHaveCount(7);
    await shoot(page, "3-effort-open");
    await page.locator('[data-testid=effort-row][data-effort="low"]').click();
    await expect(page.getByTestId("effort-label")).toHaveText("低");

    // 下一轮请求用新设置；回合在跑时两个按钮置灰
    llm.push({ message: { role: "assistant", content: "换了模型之后的回答。" }, delayMs: 2500 });
    await send(page, "再说一次");
    await expect(page.getByTestId("composer-stop")).toBeVisible();
    await expect(page.getByTestId("model-btn")).toBeDisabled();
    await expect(page.getByTestId("effort-btn")).toBeDisabled();
    await shoot(page, "4-running");
    await idle(page);
    const last = llm.requests[llm.requests.length - 1];
    expect([last.model, last.reasoning_effort]).toEqual(["kimi-k3", "low"]);
    expect(llm.requests[0].model).toBe("fake");
    expect("reasoning_effort" in llm.requests[0]).toBe(false);

    // 对话框文字命令走同一条路：/model default → 回到默认，按钮跟着变；不进模型
    const before = llm.requests.length;
    await send(page, "/model default");
    await expect(page.getByTestId("model-label")).toHaveText("fake");
    await expect(page.getByTestId("model-btn")).toHaveAttribute("data-source", "default");
    await expect(page.getByTestId("effort-label")).toHaveText("低");
    await idle(page);
    expect(llm.requests.length).toBe(before);
    await shoot(page, "5-after-slash");
  } finally {
    await stubQuit(L, "quit").catch(() => undefined);
    await L.app.close().catch(() => undefined);
    await llm.close();
    fx.cleanup();
  }
});
