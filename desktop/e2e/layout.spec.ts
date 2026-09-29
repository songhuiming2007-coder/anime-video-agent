// D39 S3：分栏布局跨重启记忆（同一 userData 重启），以及存储被写坏时回落默认。
// 临时夹具仓，绝不指向真实 data/；全程只用点击，不做键盘自动化（D37）。
import { expect, test } from "@playwright/test";
import { tmp } from "../tests/helpers";
import { buildFixture, launch, openEpisode } from "./fixtures";

test("L-1 收起侧栏、展开预览、收起本期文件 → 同一 userData 重启后原样恢复", async () => {
  const fx = buildFixture();
  const ud = tmp("ud");
  try {
    let L = await launch(fx.repo, [], ud, { previewOpen: false });
    // 首次启动是默认态：侧栏在、预览收成窄条
    await expect(L.page.locator("#ava-left")).toBeVisible();
    await expect(L.page.getByTestId("preview-rail")).toBeVisible();
    await openEpisode(L.page, "E2E-A");
    await L.page.getByTestId("files-toggle").click();
    await expect(L.page.getByTestId("files-toggle")).toHaveAttribute("aria-expanded", "false");
    await L.page.getByTestId("toggle-left").click();
    await L.page.getByTestId("toggle-preview").click();
    await expect(L.page.locator("#ava-left")).toBeHidden();
    await expect(L.page.locator("#ava-preview")).toBeVisible();
    await L.app.close();

    L = await launch(fx.repo, [], ud, { previewOpen: false });
    try {
      await expect(L.page.locator("#ava-left")).toBeHidden();
      await expect(L.page.getByTestId("toggle-left")).toHaveAttribute("aria-pressed", "false");
      await expect(L.page.locator("#ava-preview")).toBeVisible();
      await expect(L.page.getByTestId("preview-rail")).toHaveCount(0);
      await L.page.getByTestId("toggle-left").click();
      // 本期文件是收起的，树行不可见，所以不用 openEpisode（它等树行出现）；等中栏切到该期即可
      await L.page.locator("[data-testid=episode]", { hasText: "E2E-A" }).first().click();
      await expect(L.page.getByTestId("center")).toHaveAttribute("data-ep", "E2E-A");
      await expect(L.page.getByTestId("files-toggle")).toHaveAttribute("aria-expanded", "false");
    } finally {
      await L.app.close();
    }
  } finally {
    fx.cleanup();
  }
});

test("L-2 存储里的布局被写坏 → 重启后回落默认（侧栏在、预览收起），界面照常可用", async () => {
  const fx = buildFixture();
  const ud = tmp("ud");
  try {
    let L = await launch(fx.repo, [], ud, { previewOpen: false });
    await L.page.getByTestId("toggle-left").click(); // 先存一个非默认值，证明回落不是「本来就是默认」
    await expect(L.page.locator("#ava-left")).toBeHidden();
    await L.page.evaluate(() => localStorage.setItem("ava.layout", '{"v":1,"leftOpen":fa'));
    await L.app.close();

    L = await launch(fx.repo, [], ud, { previewOpen: false });
    try {
      await expect(L.page.locator("#ava-left")).toBeVisible();
      await expect(L.page.getByTestId("preview-rail")).toBeVisible();
      await expect(L.page.locator("#ava-preview")).toBeHidden();
      await openEpisode(L.page, "E2E-A");
      await expect(L.page.getByTestId("current-step")).toBeVisible();
    } finally {
      await L.app.close();
    }
  } finally {
    fx.cleanup();
  }
});
