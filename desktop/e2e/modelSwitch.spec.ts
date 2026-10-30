// D91：输入框工具栏的模型 / 推理按钮（照 ZCode），真实 core + 本地假 LLM。
// 断言：按钮跟着 model_config 帧变；切换只影响本会话下一轮请求；回合在跑时置灰；对话框 /model 同一条路；
// 裸 /model、/thinking 展开下拉；长会话名 + 窄窗时按钮不被挤没。
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
    // 会话没起：显示「—」；D147 起按钮可用（存 pending，发第一条消息时生效）
    await expect(page.getByTestId("model-label")).toHaveText("—");
    await expect(page.getByTestId("model-btn")).toBeEnabled();

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
    // D149：running 也可用（core 排队到回合收尾执行，下个回合生效）——不再置灰
    await expect(page.getByTestId("model-btn")).toBeEnabled();
    await expect(page.getByTestId("effort-btn")).toBeEnabled();
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

    // 裸 /model、/thinking：展开对应下拉（不发送、不进模型），点一行才切换
    await send(page, "/model");
    await expect(page.getByTestId("model-pop")).toBeVisible();
    await expect(page.getByTestId("composer-input")).toHaveValue("");
    await expect(page.getByTestId("model-row")).toHaveCount(4);
    await shoot(page, "6-slash-model");
    await page.locator('[data-testid=model-row][data-model="kimi-k2.8"]').click();
    await expect(page.getByTestId("model-label")).toHaveText("kimi-k2.8");
    await send(page, "/thinking");
    await expect(page.getByTestId("effort-pop")).toBeVisible();
    await page.locator('[data-testid=effort-row][data-effort="high"]').click();
    await expect(page.getByTestId("effort-label")).toHaveText("高");
    await idle(page);
    expect(llm.requests.length).toBe(before);

    // D156：换到实测支持集窄的模型（gemini-3.8-flash-high 表里只 low/medium/high/none），
    // effort 选择器只列 4 档（不是全档 7）；切回不在表里的 kimi-k3 恢复全档。
    await page.getByTestId("model-btn").click();
    await page.locator('[data-testid=model-row][data-model="gemini-3.8-flash-high"]').click();
    await expect(page.getByTestId("model-label")).toHaveText("gemini-3.8-flash-high");
    await page.getByTestId("effort-btn").click();
    await expect(page.getByTestId("effort-pop")).toBeVisible();
    await expect(page.getByTestId("effort-row")).toHaveCount(4); // none/minimal/low/medium/high 子集 → 4
    await expect(page.locator('[data-testid=effort-row][data-effort="xhigh"]')).toHaveCount(0);
    // 搜索框过滤
    await page.getByTestId("effort-search").fill("高");
    await expect(page.getByTestId("effort-row")).toHaveCount(1);
    await expect(page.getByTestId("effort-row")).toHaveAttribute("data-effort", "high");
    await page.getByTestId("effort-search").fill("");
    // Shift+Tab 循环：当前 high → Shift+Tab 到 none（绕回）；再按一轮 → minimal
    await page.getByTestId("effort-search").focus();
    await page.keyboard.press("Shift+Tab");
    await expect(page.getByTestId("effort-label")).toHaveText("关");
    // gemini-3.8 支持集 [none,low,medium,high]（无 minimal）：none → low
    await page.keyboard.press("Shift+Tab");
    await expect(page.getByTestId("effort-label")).toHaveText("低");
    await page.keyboard.press("Escape");
    // 换回 kimi-k3（表里没记 → 全档）
    await page.getByTestId("model-btn").click();
    await page.locator('[data-testid=model-row][data-model="kimi-k3"]').click();
    await expect(page.getByTestId("model-label")).toHaveText("kimi-k3");
    await page.getByTestId("effort-btn").click();
    await expect(page.getByTestId("effort-row")).toHaveCount(7);
    await page.keyboard.press("Escape");
  } finally {
    await stubQuit(L, "quit").catch(() => undefined);
    await L.app.close().catch(() => undefined);
    await llm.close();
    fx.cleanup();
  }
});

test("D91 长会话名 + 窄窗：会话名截断，模型 / 推理按钮完整、不重叠", async () => {
  test.setTimeout(120_000);
  const LONG = "2026-10-13-伪恋-鹅诚士郎的束胸力学";
  const llm = await startFakeLlm();
  const fx = realCoreFixture(llm.url, [LONG]);
  const L = await launchSession(fx.repo);
  const page = L.page;
  try {
    await page.setViewportSize({ width: 1000, height: 820 });
    await page.locator("[data-testid=episode]", { hasText: LONG }).first().click();
    llm.push(assistant("好。"));
    await send(page, "你好");
    await expect(page.getByTestId("model-label")).toHaveText("fake");
    await idle(page);
    await shoot(page, "7-long-name");
    const box = async (id: string) => (await page.getByTestId(id).boundingBox())!;
    const [target, model, effort, input] = [await box("composer-target"), await box("model-btn"), await box("effort-btn"), await box("composer-input")];
    // 按钮按内容宽度完整显示（没被压扁）：按钮与标签都不溢出
    for (const id of ["model-btn", "model-label", "effort-btn"]) {
      expect(await page.getByTestId(id).evaluate((el) => el.scrollWidth <= el.clientWidth), id).toBe(true);
    }
    expect(target.x + target.width).toBeLessThanOrEqual(model.x + 0.5);
    expect(model.x + model.width).toBeLessThanOrEqual(effort.x + 0.5);
    expect(effort.x + effort.width).toBeLessThanOrEqual(input.x + input.width + 0.5);
    // 会话名被截断（全名在悬停提示里）
    await expect(page.getByTestId("composer-target")).toHaveAttribute("title", LONG);
    expect(await page.getByTestId("composer-target").evaluate((el) => el.scrollWidth > el.clientWidth)).toBe(true);

    // 真实长模型名 + 更窄的栏：模型名先截断（全名在悬停提示），推理与发送按钮仍完整、都在输入框里
    await page.getByTestId("model-btn").click();
    await page.locator('[data-testid=model-row][data-model="gemini-3.8-flash-high"]').click();
    await expect(page.getByTestId("model-label")).toHaveText("gemini-3.8-flash-high");
    await page.setViewportSize({ width: 900, height: 820 }); // 对话栏约 300px，与人实际窗口相当
    await page.waitForTimeout(200);
    await shoot(page, "8-narrow-long-model");
    await expect(page.getByTestId("model-btn")).toHaveAttribute("title", /^gemini-3\.8-flash-high · /);
    const [m2, e2, send2, input2] = [await box("model-btn"), await box("effort-btn"), await box("composer-send"), await box("composer-input")];
    expect(m2.x + m2.width).toBeLessThanOrEqual(e2.x + 0.5);
    expect(e2.x + e2.width).toBeLessThanOrEqual(send2.x + 0.5);
    expect(send2.x + send2.width).toBeLessThanOrEqual(input2.x + input2.width + 0.5);
    for (const id of ["effort-btn", "composer-send"]) {
      expect(await page.getByTestId(id).evaluate((el) => el.scrollWidth <= el.clientWidth), id).toBe(true);
    }
  } finally {
    await stubQuit(L, "quit").catch(() => undefined);
    await L.app.close().catch(() => undefined);
    await llm.close();
    fx.cleanup();
  }
});
