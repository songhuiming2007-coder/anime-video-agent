// VE-0：PR1「零视觉变化」的对拍（Spec 14 §7.1）。只在设置 AVA_VE0_OUT 时运行：
// 把当前构建的渲染结果按元素抓成计算样式快照，写到该路径。协议（门禁 2）：
//   1. 在 PR1 之前的提交上 `npx playwright test e2e/visual.spec.ts` 且 AVA_VE0_OUT=/tmp/ve0-before.json；
//   2. 施工后同一条命令、AVA_VE0_OUT=/tmp/ve0-after.json；
//   3. `node -e` 比对两份 JSON 逐项相等（浅、深两态各一份）。
// 快照排除：outline*（PR1 首次引入全局 :focus-visible，非聚焦态本就不该有环）、color-scheme
// （旧 style.css 的 `light dark` 仍然后加载胜出，但语义上不是渲染差异；红队二轮 🟡-2、E11）、
// 以及 `--*` 自定义属性（Chromium 会把继承下来的 token 值列进计算样式：新增 token 必然改变这些 *输入*，
// 它们不是渲染结果；旧界面如果真吃到了新值，会体现在 background-color 这类渲染属性上——那正是 E11 的证据）。
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { buildFixture, launch, openEpisode, pick } from "./fixtures";

const OUT = process.env.AVA_VE0_OUT;

/** 元素身份：从 documentElement 起的 nth-child 路径，PR1 不动 DOM，所以前后可逐项对齐 */
const SNAPSHOT = () =>
  (() => {
    const EXCLUDE = /^(outline|color-scheme|--)/;
    const path = (el: Element): string => {
      const parts: string[] = [];
      for (let e: Element | null = el; e && e.parentElement; e = e.parentElement) {
        parts.unshift(`${e.tagName}:${[...e.parentElement.children].indexOf(e)}`);
      }
      return parts.join(">");
    };
    const out: Record<string, Record<string, string>> = {};
    for (const el of document.querySelectorAll("*")) {
      const cs = getComputedStyle(el);
      const decl: Record<string, string> = {};
      for (let i = 0; i < cs.length; i++) {
        const p = cs[i];
        if (!EXCLUDE.test(p)) decl[p] = cs.getPropertyValue(p);
      }
      out[path(el)] = decl;
    }
    return out;
  })();

async function snapshot(page: Page): Promise<Record<string, Record<string, string>>> {
  return (await page.evaluate(SNAPSHOT)) as Record<string, Record<string, string>>;
}

/** 钉住两处与本次运行无关的噪声（两次同构构建也会差这几项，已实测）：鼠标悬停态、顶栏里的夹具临时路径原文。 */
async function settle(page: Page): Promise<void> {
  await page.mouse.move(0, 0);
  await page.evaluate(() => {
    const repo = document.querySelector(".repo");
    for (const n of Array.from(repo?.childNodes ?? [])) if (n.nodeType === 3 && n.textContent?.trim()) n.textContent = "/repo";
  });
}

test("VE-0 PR1 前后计算样式快照逐项相等（浅、深各一次）", async () => {
  test.skip(!OUT, "仅在 AVA_VE0_OUT 设置时运行（Spec 14 门禁 2）");
  const fx = buildFixture();
  const L = await launch(fx.repo);
  try {
    await openEpisode(L.page, "E2E-A");
    await pick(L.page, "04-clips.json");
    const result: Record<string, unknown> = {};
    for (const scheme of ["light", "dark"] as const) {
      await L.page.emulateMedia({ colorScheme: scheme });
      await settle(L.page);
      await L.page.waitForTimeout(200);
      const snap = await snapshot(L.page);
      result[scheme] = snap;
      expect(Object.keys(snap).length).toBeGreaterThan(20); // 不许空跑（Spec 14 判据 9）
    }
    mkdirSync(dirname(OUT!), { recursive: true });
    writeFileSync(OUT!, JSON.stringify(result, null, 1));
  } finally {
    await L.app.close();
    fx.cleanup();
  }
});
