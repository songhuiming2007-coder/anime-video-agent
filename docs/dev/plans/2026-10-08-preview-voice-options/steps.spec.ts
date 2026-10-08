// D49-A / D50-A 施工逐步截图：同一夹具，不注入任何原型 DOM——截的是真实实现。
// 用法（在 desktop/ 下）：STEP=s1 PYTHONDONTWRITEBYTECODE=1 npx playwright test -c ../docs/dev/plans/2026-10-08-preview-voice-options/options.config.ts steps
import { test } from "../../../../desktop/node_modules/@playwright/test";
import { launch } from "../../../../desktop/e2e/fixtures";
import { cleanup } from "../../../../desktop/tests/helpers";
import { EP025, EP035, fixture, openEp, shoot } from "./shared";

const STEP = process.env.STEP ?? "step";

test(`施工截图 ${STEP}`, async () => {
  const repo = fixture();
  const L = await launch(repo);
  try {
    await L.page.waitForSelector("[data-testid=episode]");
    await openEp(L.page, EP025);
    await L.page.getByTestId("script-editor").waitFor({ timeout: 20_000 }).catch(() => undefined);
    await shoot(L.page, `${STEP}-025`);
    if ((await L.page.getByTestId("editor-pane-switch").count()) > 0) {
      await L.page.locator("[data-testid=editor-pane-switch] button[data-pane=render]").click();
      await shoot(L.page, `${STEP}-025-render`);
      await L.page.locator("[data-testid=editor-pane-switch] button[data-pane=src]").click();
    }
    if ((await L.page.getByTestId("preview-switch-btn").count()) > 0) {
      await L.page.getByTestId("toggle-left").click();
      await L.page.getByTestId("preview-switch-btn").click();
      await shoot(L.page, `${STEP}-025-switch-left-collapsed`);
      await L.page.keyboard.press("Escape");
      await L.page.getByTestId("toggle-left").click();
    }
    await L.page.getByTestId("toggle-left").click();
    await shoot(L.page, `${STEP}-025-left-collapsed`);
    await L.page.getByTestId("toggle-left").click();
    await openEp(L.page, EP035);
    await L.page.getByTestId("voice-panel").waitFor({ timeout: 20_000 }).catch(() => undefined);
    await shoot(L.page, `${STEP}-035`);
    const seg = L.page.locator("[data-testid=seg-row][data-label='2'] [data-testid=seg-text]");
    if ((await seg.count()) > 0) {
      await L.page.setViewportSize({ width: 1440, height: 900 });
      await seg.evaluate((el) => {
        const node = el.firstChild as Text;
        const at = node.data.indexOf("姐弟");
        const r = document.createRange();
        r.setStart(node, at);
        r.setEnd(node, at + 2);
        window.getSelection()!.removeAllRanges();
        window.getSelection()!.addRange(r);
        el.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
      });
      await L.page.getByTestId("fix-pinyin").fill("jie3 di4");
      await L.page.getByTestId("fix-check").filter({ hasText: "通过" }).waitFor({ timeout: 15_000 }).catch(() => undefined);
      await L.page.getByTestId("toggle-left").click();
      await shoot(L.page, `${STEP}-035-fix-left-collapsed`);
    }
  } finally {
    await L.app.close().catch(() => undefined);
    cleanup(repo);
  }
});
