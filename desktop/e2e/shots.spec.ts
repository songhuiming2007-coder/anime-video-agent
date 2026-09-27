// 门禁 7 的取证工具：对真实 app 按 Spec 14 §2.7 的口径截三态图（light / dark / forced-dark），交人做审美判定。
// 只在设置 AVA_SHOTS=<输出目录> 时运行（同 VE-0 的纪律：常规套件不碰它）：
//   cd desktop && AVA_SHOTS=out/pr3-shots npx playwright test e2e/shots.spec.ts
// 用 e2e 的临时夹具仓（绝不指向真实 data/）；PNG 不进 git（out/ 已排除）。
import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { expect, test } from "@playwright/test";
import { buildFixture, launch, openEpisode, pick } from "./fixtures";

const OUT = process.env.AVA_SHOTS;

test("门禁 7：真实 app 三态截图（工作台 + 按工序分组 + 外观浮层）", async () => {
  test.skip(!OUT, "仅在 AVA_SHOTS 设置时运行（Spec 14 门禁 7）");
  const fx = buildFixture({ withApprovals: true });
  const L = await launch(fx.repo);
  const themes = [
    ["light", null],
    ["dark", "dark"],
    ["forced-dark", "dark"],
  ] as const;
  try {
    mkdirSync(OUT!, { recursive: true });
    await openEpisode(L.page, "E2E-A");
    await pick(L.page, "02-script.md");
    await L.page.getByTestId("markdown").waitFor();
    await L.page.setViewportSize({ width: 1280, height: 820 });
    for (const [name, scheme] of themes) {
      // light/dark 走系统外观（media 块）；forced-dark 走手动块（§2.7 的三态口径）
      await L.page.evaluate((s) => {
        if (s === null) document.documentElement.removeAttribute("data-theme");
        else document.documentElement.setAttribute("data-theme", s);
      }, name === "forced-dark" ? "dark" : null);
      await L.page.emulateMedia({ colorScheme: scheme });
      await L.page.waitForTimeout(150);
      await L.page.screenshot({ path: join(OUT!, `workbench-${name}.png`) });
      // 外观浮层打开态 + 「按工序」分组（侧栏头部两种视图都进图）
      await L.page.getByTestId("appearance").click();
      await L.page.getByTestId("theme-menu").waitFor();
      await L.page.screenshot({ path: join(OUT!, `popover-${name}.png`) });
      await L.page.keyboard.press("Escape");
      await L.page.evaluate((s) => {
        if (s === null) document.documentElement.removeAttribute("data-theme");
        else document.documentElement.setAttribute("data-theme", s);
      }, name === "forced-dark" ? "dark" : null);
      await L.page.emulateMedia({ colorScheme: scheme });
      await L.page.getByText("按工序", { exact: true }).click();
      await L.page.waitForTimeout(100);
      await L.page.screenshot({ path: join(OUT!, `grouped-${name}.png`) });
      await L.page.getByText("按时间", { exact: true }).click();
    }
    expect(true).toBe(true);
  } finally {
    await L.app.close();
    fx.cleanup();
  }
});
