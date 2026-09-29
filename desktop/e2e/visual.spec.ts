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
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { ava, writeClips, writeManifest } from "./ackFixtures";
import { domAudit } from "./audit";
import { tmp } from "../tests/helpers";
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
  // D39 S1 实测（2026-09-29，三态一致）：工序卡默认收成一行、时间线收成摘要条，收起部分不再计入各态；
  // 收起的内容由新增的 expanded 态单独审计（76 ≥ 原 card 态 73，覆盖不丢）。上一版（PR3）数值见 git 历史。
  list: 64,
  "sidebar-step": 68,
  "sidebar-search": 57,
  popover: 68,
  markdown: 76,
  json: 76,
  empty: 60,
  error: 66,
  health: 84,
  stale: 71, // N37：脱盘后期列表保留（Spec 8 §2.8），三主题实测恒定（S1 前为 83，差值同上：收起的工序详情与时间线正文）
  card: 66,
  "card-reject": 71,
  "card-err": 69,
  expanded: 76,
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
    console.log(`VE-1 n[${where}]=${r.n}`);
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
    // ①b 侧栏：「按工序」分组、搜索空态以外的匹配态、外观浮层（Spec 14 PR3）
    await L.page.getByText("按工序", { exact: true }).click();
    await audit(L.page, "sidebar-step");
    await L.page.getByText("按时间", { exact: true }).click();
    await L.page.getByTestId("ep-search").fill("E2E-A");
    await audit(L.page, "sidebar-search");
    await L.page.getByTestId("ep-search").fill("");
    await L.page.getByTestId("appearance").click();
    await L.page.getByTestId("theme-menu").waitFor();
    await audit(L.page, "popover");
    await L.page.keyboard.press("Escape");
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
    // ⑦b D39 C1/C4：工序卡「详情」与时间线默认收起，展开态单独审计（收起的内容不因折叠逃出审计）
    await L.page.getByTestId("status-toggle").click();
    await L.page.getByTestId("timeline-toggle").click();
    await L.page.getByTestId("status-details").waitFor();
    await audit(L.page, "expanded");
    await L.page.getByTestId("status-toggle").click();
    await L.page.getByTestId("timeline-toggle").click();
    // ⑧ 打回表单
    await card.getByTestId("reject-open").click();
    await card.getByTestId("reject-form").waitFor();
    await audit(L.page, "card-reject");
    await card.getByTestId("reject-open").click();
    // ⑨ 失败回执（段级不对齐 → core 退 1，stdout/stderr 尾部原文）
    await card.getByTestId("approve").click();
    await L.page.getByTestId("decision-err").waitFor({ timeout: 30_000 });
    await audit(L.page, "card-err");
    // ⑩ stale（脱盘：期列表保留、置灰并标「陈旧」，Spec 8 §2.8 / mock-06）
    const rowsBefore = await L.page.getByTestId("episode").count();
    execFileSync("/bin/chmod", ["000", fx.dataReal]);
    try {
      await L.page.getByTestId("reach-banner").waitFor({ timeout: 10_000 });
      // N37：旧写法等期列表清空，但清空来自 host 把脱盘误判成「期目录被删」——只在活跃期 tick 抢在
      // reach 轮询之前时发生（PR3 测出 81/65 两个 n、负载下 4/4 次不清空，都是这个竞态）。修后列表恒保留
      await expect(L.page.locator(".left [data-testid=stale-mark]")).toBeVisible();
      await expect(L.page.getByTestId("episode")).toHaveCount(rowsBefore);
      await audit(L.page, "stale");
    } finally {
      // 权限必须无条件还原：否则断言一红，清理就会以 ENOTEMPTY 掩盖真因
      execFileSync("/bin/chmod", ["755", fx.dataReal]);
    }
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
    // 外观浮层（PR3；PR2 施工偏差 ① 的补做）：Tab 到「外观」按钮后 Space 打开，Esc 关闭，焦点回到按钮（E8）
    let atAppearance = false;
    for (let i = 0; i < 30; i++) {
      await L.page.keyboard.press("Tab");
      if (await L.page.evaluate(() => document.activeElement?.getAttribute("data-testid") === "appearance")) {
        atAppearance = true;
        break;
      }
    }
    expect(atAppearance, "30 次 Tab 内没走到「外观」按钮").toBe(true);
    await L.page.keyboard.press("Space");
    await expect(L.page.getByTestId("theme-menu")).toBeVisible();
    await L.page.keyboard.press("Escape");
    await expect(L.page.getByTestId("theme-menu")).toBeHidden();
    expect(await L.page.evaluate(() => document.activeElement?.getAttribute("data-testid"))).toBe("appearance");
  } finally {
    await L.app.close();
    fx.cleanup();
  }
});

// ---------------- VE-2：主题三档（Spec 14 §7.1；localStorage + data-theme，协议零改动） ----------------

test("VE-2 主题三档：深色立即生效且重启保持；跟随系统对齐 nativeTheme；settings.json 不变", async () => {
  const fx = buildFixture();
  const ud = tmp("ud-ve2");
  mkdirSync(ud, { recursive: true });
  const settingsPath = join(ud, "settings.json");
  writeFileSync(settingsPath, JSON.stringify({ version: 1, repoRoot: fx.repo }), "utf-8");
  const L = await launch(fx.repo, [], ud);
  try {
    const settingsBefore = readFileSync(settingsPath, "utf-8");
    // ① 点「深色」→ data-theme="dark"，body 背景 = 深色 --bg（模拟 light 下也成立）
    await L.page.getByTestId("appearance").click();
    await L.page.getByTestId("theme-menu").getByText("深色", { exact: true }).click();
    await expect.poll(() => L.page.evaluate(() => document.documentElement.getAttribute("data-theme"))).toBe("dark");
    expect(await L.page.evaluate(() => getComputedStyle(document.body).backgroundColor)).toBe("rgb(22, 22, 24)"); // tokens.css 深色 --bg（冻结文件）
    await L.page.keyboard.press("Escape");
    expect(await L.page.evaluate(() => location.origin)).toBe("file://"); // A7 的实质：渲染器从 file:// 载入（打包版同源），偏好才跨重启留在 userData 里
    // ② 重启（同 userData）后仍为深色
    await L.app.close();
    const L2 = await launch(fx.repo, [], ud);
    try {
      expect(await L2.page.evaluate(() => document.documentElement.getAttribute("data-theme"))).toBe("dark");
      // ③ 点「跟随系统」并解除 Playwright 的 light 模拟 → 页面 matchMedia 与 main 的 nativeTheme 一致
      await L2.page.getByTestId("appearance").click();
      await L2.page.getByTestId("theme-menu").getByText("跟随系统", { exact: true }).click();
      await expect.poll(() => L2.page.evaluate(() => document.documentElement.getAttribute("data-theme"))).toBe(null);
      await L2.page.emulateMedia({ colorScheme: null });
      const pageDark = await L2.page.evaluate(() => matchMedia("(prefers-color-scheme: dark)").matches);
      const nativeDark = await L2.app.evaluate(({ nativeTheme }) => nativeTheme.shouldUseDarkColors);
      expect(pageDark).toBe(nativeDark);
      // ④ settings.json 与操作前逐字节相同（主题不进 settings 写入通路）
      expect(readFileSync(settingsPath, "utf-8")).toBe(settingsBefore);
    } finally {
      await L2.app.close();
    }
  } finally {
    await L.app.close().catch(() => undefined);
    fx.cleanup();
  }
});

// ---------------- S8-R23：「陈旧」标记（Spec 8 §2.8 补文，随 Spec 14 PR2 同批施工、单独提交） ----------------

test("S8-R23 脱盘时侧栏头部与中栏顶部各出一个「陈旧」中性徽标，恢复后都消失；与 reach-banner 分工不同", async () => {
  const fx = buildFixture();
  const L = await launch(fx.repo);
  try {
    await openEpisode(L.page, "E2E-A");
    await expect(L.page.getByTestId("stale-mark")).toHaveCount(0);
    execFileSync("/bin/chmod", ["000", fx.dataReal]);
    try {
      await L.page.getByTestId("reach-banner").waitFor({ timeout: 10_000 });
      const marks = L.page.getByTestId("stale-mark");
      await expect(marks).toHaveCount(2);
      await expect(L.page.locator(".left [data-testid=stale-mark]")).toHaveText("陈旧");
      await expect(L.page.locator(".center [data-testid=stale-mark]")).toHaveText("陈旧");
      // 横幅说原因，标记说「哪些区域是旧数据」：两处并存、文本不同
      await expect(L.page.getByTestId("reach-banner")).toContainText("permission-denied");
      await expect(L.page.locator(".left [data-testid=stale-mark]")).not.toContainText("permission-denied");
    } finally {
      execFileSync("/bin/chmod", ["755", fx.dataReal]);
    }
    // 恢复：横幅与两处标记都消失
    await expect(L.page.getByTestId("reach-banner")).toHaveCount(0, { timeout: 15_000 });
    await expect(L.page.getByTestId("stale-mark")).toHaveCount(0);
  } finally {
    await L.app.close();
    fx.cleanup();
  }
});

// ---------------- N41：e2e 实例不打扰人（--ava-test-background） ----------------

test("N41 e2e 实例：窗口可见、不聚焦、不进 Dock，停在主屏右下角只露一角", async () => {
  const fx = buildFixture();
  const L = await launch(fx.repo);
  try {
    await L.page.getByTestId("episode").first().waitFor();
    const st = await L.app.evaluate(({ app, BrowserWindow, screen }) => {
      const w = BrowserWindow.getAllWindows()[0];
      const b = w.getBounds();
      const wa = screen.getPrimaryDisplay().workArea;
      return { visible: w.isVisible(), focused: w.isFocused(), dock: app.dock?.isVisible() ?? false, dx: wa.x + wa.width - b.x, dy: wa.y + wa.height - b.y };
    });
    // 可见：不是 show:false（隐藏窗口绘制被压低）；dx/dy = 露在工作区内的边长（显示后才移，macOS 不再拉回）
    expect(st).toEqual({ visible: true, focused: false, dock: false, dx: 32, dy: 32 });
  } finally {
    await L.app.close();
    fx.cleanup();
  }
});
