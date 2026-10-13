// D81 PR-2b：人选方案 B「分组卡片」施工后的实现截图（UI 规则 4 收尾：1280×800 与 1440×900 各至少一张给人看）。
// 未打包构建 × 临时夹具仓（绝不指向真实 data/），夹具内容量同 options.shots.spec.ts（§一 场景）；与 PR-2a 不同，这里侧栏与中栏全是真实渲染，
// 不注入任何原型 DOM / 样式。建企划、建卷都经真实界面点击 → 真实 core。
// 用法（在 desktop/ 下）：
//   PYTHONDONTWRITEBYTECODE=1 npx playwright test -c ../docs/dev/plans/2026-10-12-project-layer-spec/shots.config.ts impl.shots.spec.ts
// 产物：desktop/out/d81-shots/pr-2b/（index.html 是对照页）
import { mkdirSync, rmSync, utimesSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test } from "../../../../desktop/node_modules/@playwright/test";
import type { Page } from "../../../../desktop/node_modules/@playwright/test";
import { launchSession, stubQuit } from "../../../../desktop/e2e/sessionFixtures";
import { cleanup } from "../../../../desktop/tests/helpers";
import { fixtureWrite, sessionRepo } from "../../../../desktop/tests/fixtures/session";
import type { EpisodesList } from "../../../../desktop/src/shared/protocol";

const OUT = join(__dirname, "../../../../desktop/out/d81-shots/pr-2b");

const SUPER = "supercell-编年史";
const SUPER_VOLS = ["00-全一期-合集", "01-黑岩射手与ryo的同人时代", "02-nagi接棒的那几年", "03-こしょう与第三代歌姬", "04-十五年后的告别演出"];
const V2 = "EGOIST-传奇企划志-V2";
const V1 = "EGOIST-传奇企划志";
const EG_VOLS = ["01-借躯降生", "02-皮套囚徒", "03-终局葬礼"];
const PLAIN = [
  "2026-10-10-伪恋-橘万里花的进攻哲学", "2026-09-21-东京喰种-雾岛董香人物志-二", "2026-09-06-你的名字-遗忘的代价",
  "2026-09-03-伪恋-一条乐的算法死局", "2026-08-31-罪恶王冠-校条祭之死", "2026-08-26-天气之子-天野阳菜人物志",
  "2026-08-24-天气之子-电车难题的背叛", "2026-08-20-通往夏天的隧道-逃避的代价", "2026-08-18-春物-折射下的真物",
  "2026-08-15-罪恶王冠-音乐乐评", "2026-08-13-罪恶王冠-函数的终途（void）", "2026-08-12-罪恶王冠-楪祈人物志-二",
  "2026-08-10-罪恶王冠-楪祈人物志-一", "2026-08-09-东京喰种-雾岛董香人物志-一", "2026-08-04-春物-团子不该赢",
  "2026-08-03-春物-谁最适合你", "2026-07-30-春物-雪乃适合大老师", "2026-07-28-春物-自我牺牲",
  "2026-09-2-天气之子-须贺圭介认输版的帆高", "2025-老视频",
];

type Stage = "01" | "02.5" | "03" | "05" | "done";
function writeEp(root: string, key: string, stage: Stage): void {
  const w = (rel: string, text: string) => fixtureWrite(root, `data/episodes/${key}/${rel}`, text);
  w("01-topic.md", "# 选题\n番：示例\n类型：杂谈\n");
  if (stage === "01") return;
  w("02-script.draft.md", "## 段落 1\n配音：示例台词。\n");
  if (stage === "02.5") return;
  w("02-script.md", "## 段落 1\n配音：示例台词。\n");
  w("02-diff.patch", "");
  if (stage === "03") return;
  w("03-audio/manifest.json", JSON.stringify({ segments: [{ index: 1, duration: 4 }] }));
  w("03-audio/seg-01.wav", "");
  w("04-clips.json", '{"segments":[]}\n');
  if (stage === "05") return;
  w("04-clips.approved.json", '{"segments":[]}\n');
  w("05-final.mp4", "");
  w("06-check.log", "PASS\n");
}

const TABLE = (rows: string[]) => ["", "## 卷表", "", "| 卷序 | 目录 | 年份 | 时长预算 | 片尾 |", "|---|---|---|---|---|", ...rows, ""].join("\n");

function buildFixture(): { root: string; done: () => void } {
  const repo = sessionRepo();
  const root = repo.root;
  const stamp = (rel: string, t: number) => utimesSync(join(root, "data/episodes", rel), t, t);
  let t = 1_790_000_000;
  PLAIN.forEach((k, i) => writeEp(root, k, (["done", "05", "03", "02.5", "01"] as Stage[])[i % 5]));
  PLAIN.forEach((k) => stamp(k, (t += 3600)));
  fixtureWrite(root, `data/episodes/${SUPER}/00-project.md`, "企划: supercell 编年史\n合集: 00-全一期-合集\n" + TABLE([
    "| 1 | 01-黑岩射手与ryo的同人时代 | 2007–2010 | 14-16分钟 | 余韵18秒 |",
    "| 2 | 02-nagi接棒的那几年 | 2011–2014 | 14-16分钟 | 余韵18秒 |",
    "| 3 | 03-こしょう与第三代歌姬 | 2015–2019 | 14-16分钟 | 余韵18秒 |",
    "| 4 | 04-十五年后的告别演出 | 2020–2024 | 18-20分钟 | 完整保留 |",
  ]));
  fixtureWrite(root, `data/episodes/${V2}/00-project.md`, "企划: EGOIST 传奇企划志 V2\n合集: 00-全一期-典藏版\n" + TABLE(EG_VOLS.map((v, i) => `| ${i + 1} | ${v} | 2011–2024 | 14-16分钟 | ${i === EG_VOLS.length - 1 ? "完整保留" : "余韵18秒"} |`)));
  // V1 不立项（没有 00-project.md）：侧栏标「未立项」，卷按目录名码点序
  const superStages: Stage[] = ["01", "05", "03", "02.5", "01"];
  SUPER_VOLS.forEach((v, i) => writeEp(root, `${SUPER}/${v}`, superStages[i]));
  for (const p of [V1, V2]) EG_VOLS.forEach((v) => writeEp(root, `${p}/${v}`, "done"));
  writeEp(root, `${V2}/00-全一期-典藏版`, "done");
  for (const p of [V1, V2]) [...EG_VOLS, ...(p === V2 ? ["00-全一期-典藏版"] : [])].forEach((v) => stamp(`${p}/${v}`, t - 40 * 86400));
  stamp(V1, t - 60 * 86400);
  stamp(V2, t - 40 * 86400);
  SUPER_VOLS.forEach((v) => stamp(`${SUPER}/${v}`, t + 600));
  stamp(SUPER, t + 600);
  return { root, done: () => cleanup(root) };
}

/** UI 规则 5 的底线：侧栏不横向溢出；卡头、徽标行、进度条、行、会话行两两 boundingBox 不相交；中栏不被压破 */
async function assertFloor(page: Page, label: string): Promise<void> {
  const r = await page.evaluate(() => {
    const side = document.querySelector("nav.left") as HTMLElement;
    const sel = ".proj-head, .proj-name, .proj-sess, .proj-sub, .proj-bar, .proj-empty, [data-testid=episodes] .ui-row, [data-testid=episodes] .ep-session-wrap, [data-testid=episodes] .new-episode-form";
    const boxes = [...document.querySelectorAll(sel)].map((e) => e.getBoundingClientRect()).filter((b) => b.height > 0);
    let overlap = 0;
    for (let i = 0; i < boxes.length; i++) for (let j = i + 1; j < boxes.length; j++) {
      const a = boxes[i], b = boxes[j];
      // 嵌套（会话行在 .ep-session-wrap 里、表单在行下）只算真正的交叠：一方完全包含另一方的不算
      const contains = (x: DOMRect, y: DOMRect) => x.left <= y.left + 0.5 && x.right >= y.right - 0.5 && x.top <= y.top + 0.5 && x.bottom >= y.bottom - 0.5;
      if (contains(a, b) || contains(b, a)) continue;
      if (a.left < b.right - 0.5 && b.left < a.right - 0.5 && a.top < b.bottom - 0.5 && b.top < a.bottom - 0.5) overlap++;
    }
    const center = (document.querySelector("[data-testid=center]") as HTMLElement).getBoundingClientRect().width;
    return { overflow: side.scrollWidth > side.clientWidth, overlap, center };
  });
  expect(r.overflow, `${label} 侧栏横向溢出`).toBe(false);
  expect(r.overlap, `${label} 行重叠`).toBe(0);
  expect(r.center, `${label} 中栏被压破`).toBeGreaterThanOrEqual(480);
}

const VOL2 = `${SUPER}/02-nagi接棒的那几年`;
const card = (p: Page, proj: string) => p.locator(`[data-testid=project][data-proj="${proj}"]`);
const SHOTS: { id: string; w: number; h: number; note: string }[] = [
  { id: "i1", w: 1440, h: 900, note: "按时间 · 企划会话（场景第 2 步）：中栏是企划头部（卷表解析结果）" },
  { id: "i2", w: 1280, h: 800, note: "按时间 · 当前在 supercell 第 2 卷（场景第 4、5 步；1280 窄窗）" },
  { id: "i3", w: 1440, h: 900, note: "按时间 · 企划全部折叠、人在普通期：扫一眼各卷进度（场景第 6 步）" },
  { id: "i4", w: 1440, h: 900, note: "按工序 · 各卷散进停机点 / 非停机点（场景第 7 步，方案 B 散开）" },
  { id: "i5", w: 1280, h: 800, note: "按工序 · 1280 窄窗" },
  { id: "i6", w: 1280, h: 800, note: "新建企划后（骨架 00-project.md、还没有卷）+ 在 supercell 下展开「新建卷」表单（场景第 1、3 步）" },
];

test("D81 PR-2b 实现截图：方案 B 分组卡片 × 六个场景帧", async () => {
  rmSync(OUT, { recursive: true, force: true });
  mkdirSync(OUT, { recursive: true });
  const fx = buildFixture();
  const L = await launchSession({ root: fx.root, baseDir: join(fx.root, ".ava-session"), keychainPath: join(fx.root, ".ava-session/security"), eps: join(fx.root, "data/episodes") });
  const p = L.page;
  const shoot = async (id: string, w: number, h: number) => {
    await p.setViewportSize({ width: w, height: h });
    // 期激活（heal + status）结束后再拍：否则工序卡还没出来
    await expect(p.getByTestId("center")).toHaveAttribute("data-loading", "0", { timeout: 30_000 });
    if ((await p.getByTestId("center").getAttribute("data-ep")) !== "") await expect(p.getByTestId("status")).toBeVisible({ timeout: 30_000 });
    await assertFloor(p, id);
    await p.screenshot({ path: join(OUT, `${id}.png`) });
  };
  try {
    await expect(p.locator("[data-testid=episode]").first()).toBeVisible({ timeout: 30_000 });
    await expect.poll(async () => {
      const l = (await p.evaluate(() => (window as unknown as { __avaTestCall: (m: string) => Promise<unknown> }).__avaTestCall("episodes.list"))) as EpisodesList;
      return l.episodes.filter((e) => e.currentStep !== null).length + (l.projects.find((x) => x.projKey === SUPER)?.meta?.volumes.length ?? 0);
    }, { timeout: 60_000 }).toBeGreaterThanOrEqual(34);
    await p.getByRole("button", { name: "按时间" }).click();

    await card(p, SUPER).getByTestId("project-session").click();
    await expect(p.getByTestId("center")).toHaveAttribute("data-proj", SUPER);
    await expect(p.getByTestId("project-head")).toContainText("第 4 卷 · 04-十五年后的告别演出");
    await expect(card(p, V2)).toContainText("EGOIST 传奇企划志 V2"); // 卷表可解析（解析失败会退回目录名）
    await shoot("i1", 1440, 900);

    await p.locator(`[data-testid=episode][data-ep="${VOL2}"]`).click();
    await expect(p.getByTestId("center")).toHaveAttribute("data-ep", VOL2);
    await shoot("i2", 1280, 800);

    await p.locator(`[data-testid=episode][data-ep="${PLAIN[0]}"]`).click();
    for (const proj of [SUPER, V2, V1]) {
      const t = card(p, proj).getByTestId("project-toggle");
      if ((await t.getAttribute("aria-expanded")) === "true") await t.click();
    }
    await p.locator("nav.left").evaluate((e) => e.scrollTo(0, 0));
    await shoot("i3", 1440, 900);

    await p.getByRole("button", { name: "按工序" }).click();
    await p.locator(`[data-testid=episode][data-ep="${VOL2}"]`).first().click();
    await expect(p.getByTestId("center")).toHaveAttribute("data-ep", VOL2);
    await shoot("i4", 1440, 900);
    await shoot("i5", 1280, 800);

    await p.getByRole("button", { name: "按时间" }).click();
    await p.getByTestId("new-project-toggle").click();
    await p.getByTestId("project-name").fill("ClariS-十五年");
    await p.getByTestId("project-create").click();
    // ava new-project 写骨架 00-project.md（§6.3 ⑱），所以新企划不标「未立项」、显示「还没有卷」；「未立项」只出现在有卷无 00-project.md 的旧目录（V1）
    await expect(card(p, "ClariS-十五年")).toContainText("还没有卷", { timeout: 20_000 });
    await expect(card(p, V1).getByTestId("project-unplanned")).toBeVisible();
    await expect(p.getByTestId("center")).toHaveAttribute("data-proj", "ClariS-十五年");
    const sup = card(p, SUPER).getByTestId("project-toggle");
    if ((await sup.getAttribute("aria-expanded")) !== "true") await sup.click();
    await card(p, SUPER).getByTestId("new-volume-toggle").click();
    await shoot("i6", 1280, 800);
    writeFileSync(join(OUT, "index.html"), indexHtml());
  } finally {
    await stubQuit(L, "quit").catch(() => undefined);
    await L.app.close().catch(() => undefined);
    fx.done();
  }
});

function indexHtml(): string {
  const rows = SHOTS.map((s) => `<tr><td class="lab"><b>${s.id}</b><br>${s.w}×${s.h}<br>${s.note}</td><td><a href="${s.id}.png"><img src="${s.id}.png"></a></td></tr>`).join("\n");
  return `<!doctype html><meta charset="utf-8"><title>D81 PR-2b 实现截图</title>
<style>body{font:14px -apple-system,sans-serif;margin:16px}table{border-collapse:collapse}td{border:1px solid #ccc;padding:6px;vertical-align:top}img{width:900px}.lab{width:220px}</style>
<h1>D81 PR-2b：方案 B 分组卡片 · 实现截图</h1>
<p>真实窗口（未打包构建 × 临时夹具仓），侧栏与中栏全部是真实渲染；各期工序是真实 core 的 status，卷表是真实 core 的 /project-meta。V1 没有 00-project.md（「未立项」，卷按目录名排）。</p>
<table>
${rows}
</table>`;
}
