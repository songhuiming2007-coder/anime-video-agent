// VE-0：PR1「零视觉变化」的对拍（Spec 14 §7.1）。只在设置 AVA_VE0_OUT 时运行：
// 把当前构建的渲染结果按元素抓成计算样式快照，写到该路径。协议（门禁 2）：
//   1. 在 PR1 之前的提交上 `npx playwright test e2e/visual.spec.ts` 且 AVA_VE0_OUT=/tmp/ve0-before.json；
//   2. 施工后同一条命令、AVA_VE0_OUT=/tmp/ve0-after.json；
//   3. `node -e` 比对两份 JSON 逐项相等（浅、深两态各一份）。
// 快照排除：outline*（PR1 首次引入全局 :focus-visible，非聚焦态本就不该有环）、color-scheme
// （旧 style.css 的 `light dark` 仍然后加载胜出，但语义上不是渲染差异；红队二轮 🟡-2、E11）、
// 以及 `--*` 自定义属性（Chromium 会把继承下来的 token 值列进计算样式：新增 token 必然改变这些 *输入*，
// 它们不是渲染结果；旧界面如果真吃到了新值，会体现在 background-color 这类渲染属性上——那正是 E11 的证据）。
import { execFileSync } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { ava, writeClips, writeManifest } from "./ackFixtures";
import { domAudit } from "./audit";
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

// ---------------- VE-1：真实 DOM 审计（判据本体 = 附件 tools/audit.js 的逐字移植，见 ./audit.ts） ----------------

const THEMES = ["light", "dark", "forced"] as const;
type Theme = (typeof THEMES)[number];

/** 每个状态的 `n` 下限：PR2 首次实跑后填入（判据不许空跑，M18 的形状） */
const N_LOWER: Record<string, number> = {
  list: 51,
  markdown: 56,
  json: 63,
  empty: 39,
  error: 52,
  health: 70,
  stale: 48,
  card: 52,
  "card-reject": 57,
  "card-err": 60,
};

interface AuditResult {
  n: number;
  fails: unknown[];
  small: unknown[];
  nonText: unknown[];
  bgImage: unknown[];
  skippedMedia: number;
}

async function applyTheme(page: Page, theme: Theme): Promise<void> {
  if (theme === "forced") {
    await page.evaluate(() => document.documentElement.setAttribute("data-theme", "dark"));
  } else {
    await page.evaluate(() => document.documentElement.removeAttribute("data-theme"));
    await page.emulateMedia({ colorScheme: theme });
  }
}

async function audit(page: Page, label: string): Promise<void> {
  for (const theme of THEMES) {
    await applyTheme(page, theme);
    await page.mouse.move(0, 0);
    await page.waitForTimeout(120);
    const r = (await page.evaluate(domAudit)) as AuditResult;
    const where = `${label}/${theme}`;
    expect({ where, fails: r.fails }).toEqual({ where, fails: [] });
    expect({ where, nonText: r.nonText }).toEqual({ where, nonText: [] });
    expect({ where, small: r.small }).toEqual({ where, small: [] });
    expect({ where, bgImage: r.bgImage, skippedMedia: r.skippedMedia }).toEqual({ where, bgImage: [], skippedMedia: 0 });
    expect(r.n, `${where} 的审计元素数低于下限 ${N_LOWER[label]}（M18 的形状：选择器失效时 n 会塌到个位数）`).toBeGreaterThanOrEqual(N_LOWER[label]);
  }
}

test("VE-1 真实 DOM 审计：全部夹具状态 × 三种主题态，对比度/非文本/字号/背景图零失败", async () => {
  const fx = buildFixture();
  // 05 pending：manifest + 形状正确的 04-clips.json，再让 core 自愈建出对象（与 TA-2b 同法）
  writeManifest(fx.epA);
  writeClips(fx.repo, fx.epA, { clipDur: 3.0 }); // 段级不对齐：批准时 core 必失败，用于渲出失败回执
  expect(ava(fx.repo, [fx.epA, "/approvals"]).code).toBe(0);
  const L = await launch(fx.repo);
  try {
    // ① 期列表（含 05 停机行）+ hover
    await openEpisode(L.page, "E2E-A");
    await L.page.locator("[data-testid=episode]").first().hover();
    await audit(L.page, "list");
    // ② markdown 预览
    await pick(L.page, "02-script.md");
    await L.page.getByTestId("markdown").waitFor();
    await audit(L.page, "markdown");
    // ③ JSON 预览（含折叠三角）
    await pick(L.page, "04-clips.json");
    await audit(L.page, "json");
    // ④ 空态（切期清预览）
    await openEpisode(L.page, "E2E-B");
    await audit(L.page, "empty");
    // ⑤ 错态（跨根符号链接被读闸拒绝）
    await openEpisode(L.page, "E2E-A");
    await pick(L.page, "link-out.json");
    await L.page.getByText("读取失败").waitFor({ timeout: 10_000 });
    await audit(L.page, "error");
    // ⑥ 健康面板
    await L.page.getByTestId("health-toggle").click();
    await L.page.getByTestId("health-panel").waitFor();
    await audit(L.page, "health");
    await L.page.getByTestId("health-toggle").click();
    // ⑦ 05 决策卡
    const card = L.page.locator('[data-testid=decision][data-stop="05"]');
    await card.waitFor({ timeout: 15_000 });
    await audit(L.page, "card");
    // ⑧ 打回表单
    await card.getByTestId("reject-open").click();
    await card.getByTestId("reject-form").waitFor();
    await audit(L.page, "card-reject");
    await card.getByTestId("reject-open").click();
    // ⑨ 失败回执（段级不对齐 → core 退 1，stdout/stderr 尾部原文）
    await card.getByTestId("approve").click();
    await L.page.getByTestId("decision-err").waitFor({ timeout: 30_000 });
    await audit(L.page, "card-err");
    // ⑩ stale（脱盘：期行变「未取到」+ 未知圆环，预览/中栏置灰）
    execFileSync("/bin/chmod", ["000", fx.dataReal]);
    await L.page.getByTestId("reach-banner").waitFor({ timeout: 10_000 });
    await audit(L.page, "stale");
    execFileSync("/bin/chmod", ["755", fx.dataReal]);
  } finally {
    await L.app.close();
    fx.cleanup();
  }
});

// ---------------- VE-3：键盘可达与焦点环 ----------------

test("VE-3 键盘：Tab 到 [data-testid=episode] 时焦点环为 --focus-ring，Enter 打开该期", async () => {
  const fx = buildFixture();
  const L = await launch(fx.repo);
  try {
    await L.page.evaluate(() => document.body.focus());
    let landed = false;
    for (let i = 0; i < 30; i++) {
      await L.page.keyboard.press("Tab");
      const isEp = await L.page.evaluate(() => document.activeElement?.getAttribute("data-testid") === "episode");
      if (isEp) {
        landed = true;
        break;
      }
    }
    expect(landed, "30 次 Tab 内没走到期行").toBe(true);
    const ring = await L.page.evaluate(() => {
      const cs = getComputedStyle(document.activeElement as Element);
      const token = getComputedStyle(document.documentElement).getPropertyValue("--focus-ring").trim();
      const probe = document.createElement("span");
      probe.style.color = token;
      document.body.appendChild(probe);
      const want = getComputedStyle(probe).color;
      probe.remove();
      return { w: cs.outlineWidth, off: cs.outlineOffset, color: cs.outlineColor, want };
    });
    expect({ w: ring.w, off: ring.off }).toEqual({ w: "2px", off: "2px" });
    expect(ring.color).toBe(ring.want);
    const before = await L.page.getByTestId("center").getAttribute("data-ep");
    await L.page.keyboard.press("Enter");
    await expect
      .poll(async () => await L.page.getByTestId("center").getAttribute("data-ep"), { timeout: 10_000 })
      .not.toBe(before);
  } finally {
    await L.app.close();
    fx.cleanup();
  }
});
