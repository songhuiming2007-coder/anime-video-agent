// D81 PR-2a：侧栏「企划分组 / 企划会话入口 / 进度一览 / 新建企划与卷」三套方案的真实窗口截图（UI 规则 4）。
// 未打包构建 × 临时夹具仓（绝不指向真实 data/），宿主的期列表是真实扫描（PR-1 的树规则），各期工序是真实 core 的 status。
// 「现状」不改 DOM；A / B / C 只把侧栏期列表区（选题行之下）换成原型 DOM，样式经 adoptedStyleSheets 注入（渲染端 CSP 拦内联 <style>）。
// 侧栏之外（中栏、预览）一律是真实渲染；中栏显示的是脚本点开的那一期。选型用，人选定后按 spec 施工，本脚本不进产品代码。
//   A 缩进树：企划行 = 名称 + 每卷工序缩写串；展开后第一行是「企划会话」，卷缩进挂在下面；按工序视图下企划整组置顶（归组）
//   B 分组卡片：企划是一张浅底卡，卡头右侧「企划会话」按钮，卡头下一条分段进度条（每卷一段）；按工序视图下各卷散进停机点 / 非停机点分组，带企划前缀（散开）
//   C 单行摘要：企划行只写最慢的一卷，点行即进企划会话，右侧小箭头单独展开卷；卷行带「第 N 卷」前缀、左侧细竖线；按工序视图下按最慢一卷归组
// 示意项（截图说明里写明）：「1 张卡待答」「运行中」徽标与「当前在企划会话」的高亮是原型按场景摆的，不来自真实会话。
// 用法（在 desktop/ 下）：
//   PYTHONDONTWRITEBYTECODE=1 npx playwright test -c ../docs/dev/plans/2026-10-12-project-layer-spec/shots.config.ts
// 产物：desktop/out/d81-shots/pr-2a/（index.html 是对照页）
import { mkdirSync, rmSync, utimesSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test } from "../../../../desktop/node_modules/@playwright/test";
import type { Page } from "../../../../desktop/node_modules/@playwright/test";
import { launchSession, stubQuit } from "../../../../desktop/e2e/sessionFixtures";
import { cleanup } from "../../../../desktop/tests/helpers";
import { fixtureWrite, sessionRepo } from "../../../../desktop/tests/fixtures/session";
import type { EpisodeSummary, EpisodesList, ProjectSummary } from "../../../../desktop/src/shared/protocol";

const OUT = join(__dirname, "../../../../desktop/out/d81-shots/pr-2a");
const SCHEMES = ["now", "A", "B", "C"] as const;
type Scheme = (typeof SCHEMES)[number];
const LABEL: Record<Scheme, string> = { now: "现状", A: "A 缩进树", B: "B 分组卡片", C: "C 单行摘要" };

// ---------------- 夹具：§一 场景的内容量（supercell 4 卷 + 合集、EGOIST V2 三卷 + 典藏版、V1 三卷同名、20 个普通期） ----------------

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

function buildFixture(): { root: string; done: () => void } {
  const repo = sessionRepo();
  const root = repo.root;
  const stamp = (rel: string, t: number) => utimesSync(join(root, "data/episodes", rel), t, t);
  let t = 1_790_000_000;
  PLAIN.forEach((k, i) => writeEp(root, k, (["done", "05", "03", "02.5", "01"] as Stage[])[i % 5]));
  PLAIN.forEach((k) => stamp(k, (t += 3600)));
  fixtureWrite(root, `data/episodes/${SUPER}/00-project.md`, "企划: supercell 编年史\n\n## 卷表\n\n| 卷序 | 目录 | 年份 | 时长预算 | 片尾 |\n|---|---|---|---|---|\n");
  const superStages: Stage[] = ["01", "05", "03", "02.5", "01"];
  SUPER_VOLS.forEach((v, i) => writeEp(root, `${SUPER}/${v}`, superStages[i]));
  for (const [p, stages] of [[V2, ["done", "done", "done"]], [V1, ["done", "done", "done"]]] as const) {
    EG_VOLS.forEach((v, i) => writeEp(root, `${p}/${v}`, stages[i] as Stage));
  }
  writeEp(root, `${V2}/00-全一期-典藏版`, "done");
  for (const p of [V1, V2]) [...EG_VOLS, ...(p === V2 ? ["00-全一期-典藏版"] : [])].forEach((v) => stamp(`${p}/${v}`, t - 40 * 86400));
  stamp(V1, t - 60 * 86400);
  stamp(V2, t - 40 * 86400);
  SUPER_VOLS.forEach((v) => stamp(`${SUPER}/${v}`, t + 600));
  stamp(SUPER, t + 600);
  return { root, done: () => cleanup(root) };
}

// ---------------- 原型渲染（侧栏期列表区） ----------------

type Shot = { view: "time" | "step"; expanded: string[]; active: { kind: "proj" | "ep"; key: string } | null };
const DEMO = { cards: `${SUPER}/02-nagi接棒的那几年`, running: `${SUPER}/03-こしょう与第三代歌姬` };

const CSS = `
.pr2a { display: flex; flex-direction: column; gap: 2px; }
.pr2a .p-chev { flex: none; width: 14px; text-align: center; color: var(--fg-muted); }
.pr2a .p-name { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
.pr2a .p-tag { flex: none; font-size: 11px; color: var(--fg-muted); border: 1px solid var(--border-strong); border-radius: 4px; padding: 0 4px; }
.pr2a .p-codes { font-family: var(--font-mono); font-size: 11px; color: var(--fg-muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.pr2a .p-codes b { color: var(--fg); font-weight: 600; }
.pr2a .p-sess { font-weight: 600; }
.pr2a .p-new { color: var(--fg-muted); font-size: 12px; }
.pr2a .ind { padding-left: 22px !important; }
/* B */
.pr2a.B .card { border: 1px solid var(--border); border-radius: 8px; background: var(--bg-card); margin: 4px 6px; overflow: hidden; }
.pr2a.B .card-head { display: flex; align-items: center; gap: 6px; padding: 6px 8px 0; min-width: 0; }
.pr2a.B .card-head .p-name { flex: 1 1 auto; }
.pr2a.B .card-sub { display: flex; align-items: center; gap: 6px; padding: 2px 8px 0 28px; min-height: 18px; }
.pr2a.B .card-sess { flex: none; font-size: 11px; border: 1px solid var(--border-strong); border-radius: 10px; padding: 1px 8px; background: transparent; color: var(--fg); }
.pr2a.B .card-sess[data-cur="1"] { background: var(--bg-selected); border-color: var(--accent-border); font-weight: 600; }
.pr2a.B .bar { display: flex; gap: 2px; padding: 4px 8px 6px 28px; }
.pr2a.B .bar span { flex: 1; height: 6px; border-radius: 3px; background: var(--border-strong); }
.pr2a.B .bar span.done { background: var(--ok); }
.pr2a.B .bar span.stop { background: var(--warn); }
.pr2a.B .bar span.mid { background: var(--wait); }
.pr2a.B .card .ui-row { border-radius: 0; }
/* C */
.pr2a.C .crow { display: flex; align-items: stretch; }
.pr2a.C .crow > .ui-row { flex: 1; min-width: 0; }
.pr2a.C .ctog { flex: none; width: 26px; border: none; background: transparent; color: var(--fg-muted); cursor: pointer; }
.pr2a.C .cvol { border-left: 2px solid var(--border-strong); margin-left: 14px; }
`;

async function applyScheme(page: Page, scheme: Scheme, shot: Shot, list: EpisodesList): Promise<void> {
  if (scheme === "now") return;
  await page.evaluate(
    ({ scheme, shot, list, css, demo, superKey }) => {
      const esc = (s: string) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c] as string);
      const code = (step: string | null) => { if (!step) return "—"; const m = /^(\d+(?:\.\d+)?)/.exec(step); return m ? m[1] : step.slice(0, 4); };
      const vol = (k: string) => k.split("/").slice(1).join("/");
      const isCol = (k: string) => /^00-/.test(vol(k));
      const stage = (e: EpisodeSummary) => (!e.currentStep ? "none" : e.isBlocked ? "stop" : /^0?9|完成|已交付/.test(e.currentStep) ? "done" : "mid");
      const slow = (p: ProjectSummary) => { let b: EpisodeSummary | null = null; for (const m of p.members.filter((x) => !isCol(x.epKey))) if (!b || parseFloat(code(m.currentStep)) < parseFloat(code(b.currentStep))) b = m; return b; };
      const badges = (e: EpisodeSummary) =>
        `${e.epKey === demo.running ? '<span class="ui-badge ui-badge--wait">运行中</span>' : ""}` +
        `${e.epKey === demo.cards ? '<span class="ui-badge ui-badge--wait">1 张卡待答</span>' : ""}` +
        `${e.isBlocked ? '<span class="ui-badge stop-mark">停机</span>' : ""}`;
      const cur = (kind: "proj" | "ep", key: string) => (shot.active && shot.active.kind === kind && shot.active.key === key ? ' aria-current="true"' : "");
      const epRow = (e: EpisodeSummary, label: string, extraCls = "") =>
        `<button class="ui-row ep ${extraCls}"${cur("ep", e.epKey)} title="${esc(e.epKey)}"><span class="ep-line"><span class="ep-name">${esc(label)}</span></span>` +
        `<span class="ep-step">${badges(e)}<span class="ep-step-text">${esc(e.currentStep ?? "…")}</span></span></button>`;
      const projBadges = (p: ProjectSummary) => {
        const cards = p.members.filter((m) => m.epKey === demo.cards).length;
        const stops = p.members.filter((m) => m.isBlocked).length;
        return `${cards ? `<span class="ui-badge ui-badge--wait">${cards} 张卡待答</span>` : ""}${stops ? `<span class="ui-badge stop-mark">停机 ${stops}</span>` : ""}`;
      };
      const unplanned = (p: ProjectSummary) => (p.hasProjectFile ? "" : '<span class="p-tag">未立项</span>');
      const projName = (p: ProjectSummary) => (p.projKey === superKey ? "supercell 编年史" : p.projKey);
      const volLabel = (p: ProjectSummary, e: EpisodeSummary, withNo: boolean) => {
        const vols = p.members.filter((m) => !isCol(m.epKey));
        const n = vols.indexOf(e);
        return isCol(e.epKey) ? `合集 · ${vol(e.epKey)}` : withNo ? `第 ${n + 1} 卷 · ${vol(e.epKey)}` : vol(e.epKey);
      };
      const open = (p: ProjectSummary) => shot.expanded.includes(p.projKey);

      const A = (p: ProjectSummary) => {
        const codes = p.members.filter((m) => !isCol(m.epKey)).map((m, i) => `${i + 1}·<b>${code(m.currentStep)}</b>`).join(" ");
        let h = `<button class="ui-row ep"${open(p) ? "" : cur("proj", p.projKey)}><span class="ep-line"><span class="p-chev">${open(p) ? "▾" : "▸"}</span><span class="ep-name p-name">${esc(projName(p))}</span>${unplanned(p)}</span>` +
          `<span class="ep-step">${projBadges(p)}<span class="p-codes">${codes || "还没有卷"}</span></span></button>`;
        if (open(p)) {
          h += `<button class="ui-row ep ind"${cur("proj", p.projKey)}><span class="ep-line"><span class="ep-name p-sess">◆ 企划会话</span></span><span class="ep-step"><span class="ep-step-text">总纲 / 卷表 / 跨卷审查 / 总装</span></span></button>`;
          h += p.members.map((m) => epRow(m, vol(m.epKey), "ind")).join("");
          h += `<button class="ui-row ind p-new">＋ 在本企划下新建卷</button>`;
        }
        return h;
      };
      const B = (p: ProjectSummary) => {
        const vols = p.members.filter((m) => !isCol(m.epKey));
        const curP = shot.active && shot.active.kind === "proj" && shot.active.key === p.projKey ? ' data-cur="1"' : "";
        let h = `<div class="card"><div class="card-head"><span class="p-chev">${open(p) ? "▾" : "▸"}</span><span class="p-name">${esc(projName(p))}</span>${unplanned(p)}` +
          `<button class="card-sess"${curP}>企划会话</button></div>` +
          `<div class="card-sub">${projBadges(p)}</div>` +
          `<div class="bar" title="每卷一段：绿=已完成 橙=停在人审 灰蓝=进行中">${vols.map((m) => `<span class="${stage(m)}" title="${esc(vol(m.epKey))}：${esc(m.currentStep ?? "")}"></span>`).join("")}</div>`;
        if (open(p)) {
          h += p.members.map((m) => epRow(m, volLabel(p, m, true))).join("");
          h += `<button class="ui-row p-new">＋ 新建卷</button>`;
        }
        return h + "</div>";
      };
      const C = (p: ProjectSummary) => {
        const s = slow(p);
        const vols = p.members.filter((m) => !isCol(m.epKey));
        const sl = s ? `最慢：第 ${vols.indexOf(s) + 1} 卷 · ${s.currentStep ?? "…"}` : "还没有卷";
        let h = `<div class="crow"><button class="ui-row ep"${cur("proj", p.projKey)} title="点开即进企划会话"><span class="ep-line"><span class="ep-name p-name">${esc(projName(p))}</span>${unplanned(p)}<span class="p-tag">${vols.length} 卷</span></span>` +
          `<span class="ep-step">${projBadges(p)}<span class="ep-step-text">${esc(sl)}</span></span></button><button class="ctog" aria-expanded="${open(p)}">${open(p) ? "▾" : "▸"}</button></div>`;
        if (open(p)) {
          h += `<div class="cvol">${p.members.map((m) => epRow(m, volLabel(p, m, true))).join("")}<button class="ui-row p-new">＋ 新建卷</button></div>`;
        }
        return h;
      };
      const render = scheme === "A" ? A : scheme === "B" ? B : C;

      const root = document.querySelector("[data-testid=episodes]") as HTMLElement;
      const idea = root.querySelector("[data-testid=idea]") as HTMLElement;
      // 移除选题行之后的真实列表（会话子列表、期行、分组头、隐藏提示），换成原型
      let n = idea.nextElementSibling;
      while (n) { const next = n.nextElementSibling; n.remove(); n = next; }
      const box = document.createElement("div");
      box.className = `pr2a ${scheme}`;
      const plainSum = (e: EpisodeSummary) => epRow(e, e.epKey);
      const projects = [...list.projects].sort((a, b) => b.mtimeMs - a.mtimeMs);
      const memberKeys = new Set(list.projects.flatMap((p) => p.members.map((m) => m.epKey)));
      const plain = list.episodes.filter((e) => !memberKeys.has(e.epKey));
      let html = `<button class="ui-row p-new">＋ 新建企划</button>`;
      if (shot.view === "time") {
        const items: { t: number; h: string }[] = [
          ...plain.map((e) => ({ t: e.mtimeMs, h: plainSum(e) })),
          ...projects.map((p) => ({ t: p.mtimeMs, h: render(p) })),
        ];
        items.sort((a, b) => b.t - a.t);
        html += items.map((i) => i.h).join("");
      } else {
        const section = (label: string, count: number) => `<button class="ui-section" aria-expanded="true">▾ ${label}<span class="ui-count">${count}</span></button>`;
        if (scheme === "B") {
          // 散开：各卷按自身状态进停机点 / 非停机点分组，行首带企划名
          const all = [...plain.map((e) => ({ e, label: e.epKey })), ...projects.flatMap((p) => p.members.map((m) => ({ e: m, label: `${projName(p)} · ${volLabel(p, m, true)}` })))];
          const stop = all.filter((x) => x.e.isBlocked), run = all.filter((x) => x.e.isBlocked === false);
          html += section("停机点", stop.length) + stop.map((x) => epRow(x.e, x.label)).join("");
          html += section("非停机点", run.length) + run.map((x) => epRow(x.e, x.label)).join("");
        } else if (scheme === "A") {
          // 归组：企划整组置顶，普通期照旧分组
          html += section("企划", projects.length) + projects.map(render).join("");
          const stop = plain.filter((e) => e.isBlocked), run = plain.filter((e) => e.isBlocked === false);
          html += section("停机点", stop.length) + stop.map(plainSum).join("") + section("非停机点", run.length) + run.map(plainSum).join("");
        } else {
          // C：企划按最慢一卷归入停机点 / 非停机点（整组不拆）
          const pStop = projects.filter((p) => slow(p)?.isBlocked || p.members.some((m) => m.isBlocked));
          const pRun = projects.filter((p) => !pStop.includes(p));
          const stop = plain.filter((e) => e.isBlocked), run = plain.filter((e) => e.isBlocked === false);
          html += section("停机点", stop.length + pStop.length) + pStop.map(render).join("") + stop.map(plainSum).join("");
          html += section("非停机点", run.length + pRun.length) + pRun.map(render).join("") + run.map(plainSum).join("");
        }
      }
      box.innerHTML = html;
      root.appendChild(box);
      const sheet = new CSSStyleSheet();
      sheet.replaceSync(css);
      (sheet as unknown as { pr2a: boolean }).pr2a = true;
      document.adoptedStyleSheets = [...document.adoptedStyleSheets.filter((s) => !(s as unknown as { pr2a?: boolean }).pr2a), sheet];
    },
    { scheme, shot, list, css: CSS, demo: DEMO, superKey: SUPER },
  );
}

/** UI 规则 5 的底线：侧栏不横向溢出；原型行两两 boundingBox 不相交 */
async function assertFloor(page: Page, label: string): Promise<void> {
  const r = await page.evaluate(() => {
    const side = document.querySelector("[data-testid=episodes]") as HTMLElement;
    const rows = [...document.querySelectorAll(".pr2a .ui-row, .pr2a .card-head, .pr2a .card-sub, .pr2a .bar")].map((e) => e.getBoundingClientRect()).filter((b) => b.height > 0);
    let overlap = 0;
    for (let i = 0; i < rows.length; i++) for (let j = i + 1; j < rows.length; j++) {
      const a = rows[i], b = rows[j];
      if (a.left < b.right - 0.5 && b.left < a.right - 0.5 && a.top < b.bottom - 0.5 && b.top < a.bottom - 0.5) overlap++;
    }
    return { overflow: side.scrollWidth > side.clientWidth, overlap };
  });
  expect(r.overflow, `${label} 侧栏横向溢出`).toBe(false);
  expect(r.overlap, `${label} 原型行重叠`).toBe(0);
}

const VOL2 = `${SUPER}/02-nagi接棒的那几年`;
const SHOTS: { id: string; w: number; h: number; shot: Shot; note: string }[] = [
  { id: "s1", w: 1440, h: 900, shot: { view: "time", expanded: [SUPER], active: { kind: "proj", key: SUPER } }, note: "按时间 · supercell 展开 · 当前在企划会话（场景第 2 步）" },
  { id: "s2", w: 1280, h: 800, shot: { view: "time", expanded: [SUPER], active: { kind: "ep", key: VOL2 } }, note: "按时间 · 当前在第 2 卷（场景第 4、5 步；1280 窄窗）" },
  { id: "s3", w: 1440, h: 900, shot: { view: "time", expanded: [], active: { kind: "ep", key: VOL2 } }, note: "按时间 · 企划全部折叠：扫一眼各卷进度（场景第 6 步；人在第 2 卷）" },
  { id: "s4", w: 1440, h: 900, shot: { view: "step", expanded: [SUPER, V2], active: { kind: "ep", key: VOL2 } }, note: "按工序 · 企划内各卷怎么出现（场景第 7 步）" },
  { id: "s5", w: 1280, h: 800, shot: { view: "step", expanded: [SUPER], active: { kind: "ep", key: VOL2 } }, note: "按工序 · 1280 窄窗" },
];

test("D81 PR-2a 侧栏企划分组：三套方案 × 五个场景帧", async () => {
  rmSync(OUT, { recursive: true, force: true });
  mkdirSync(OUT, { recursive: true });
  const fx = buildFixture();
  const L = await launchSession({ root: fx.root, baseDir: join(fx.root, ".ava-session"), keychainPath: join(fx.root, ".ava-session/security"), eps: join(fx.root, "data/episodes") });
  const p = L.page;
  try {
    await expect(p.locator("[data-testid=episode]").first()).toBeVisible({ timeout: 30_000 });
    // 等宿主把各期真实工序取回（status --json）
    await expect.poll(async () => {
      const l = (await p.evaluate(() => (window as unknown as { __avaTestCall: (m: string) => Promise<unknown> }).__avaTestCall("episodes.list"))) as EpisodesList;
      return l.episodes.filter((e) => e.currentStep !== null).length;
    }, { timeout: 60_000 }).toBeGreaterThanOrEqual(30);
    const list = (await p.evaluate(() => (window as unknown as { __avaTestCall: (m: string) => Promise<unknown> }).__avaTestCall("episodes.list"))) as EpisodesList;
    expect(list.projects.map((x) => x.projKey).sort()).toEqual([V1, V2, SUPER].sort());
    for (const s of SHOTS) {
      await p.setViewportSize({ width: s.w, height: s.h });
      for (const scheme of SCHEMES) {
        await p.reload();
        await expect(p.locator("[data-testid=episode]").first()).toBeVisible({ timeout: 30_000 });
        // 中栏一律真实打开 supercell 第 2 卷（重载后默认停在「选题」，不打开的话选题行的高亮会跟原型打架）
        await p.getByRole("button", { name: "按时间" }).click();
        await p.locator("[data-testid=episode]", { hasText: VOL2 }).first().click();
        await expect(p.getByTestId("center")).toHaveAttribute("data-ep", VOL2);
        if (s.shot.view === "step") await p.getByRole("button", { name: "按工序" }).click();
        await applyScheme(p, scheme, s.shot, list);
        if (scheme !== "now") await assertFloor(p, `${scheme}-${s.id}`);
        await p.screenshot({ path: join(OUT, `${s.id}-${scheme}.png`) });
      }
    }
    writeFileSync(join(OUT, "index.html"), indexHtml());
  } finally {
    await stubQuit(L, "quit").catch(() => undefined);
    await L.app.close().catch(() => undefined);
    fx.done();
  }
});

function indexHtml(): string {
  const cols = SCHEMES.map((s) => `<th>${LABEL[s]}</th>`).join("");
  const rows = SHOTS.map((s) => `<tr><td class="lab"><b>${s.id}</b><br>${s.w}×${s.h}<br>${s.note}</td>${SCHEMES.map((sc) => `<td><a href="${s.id}-${sc}.png"><img src="${s.id}-${sc}.png"></a></td>`).join("")}</tr>`).join("\n");
  return `<!doctype html><meta charset="utf-8"><title>D81 PR-2a 侧栏企划分组</title>
<style>body{font:14px -apple-system,sans-serif;margin:16px}table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:6px;vertical-align:top}img{width:420px}.lab{width:200px}</style>
<h1>D81 PR-2a：侧栏企划分组 · 截图选型</h1>
<p>真实窗口（未打包构建 × 临时夹具仓）。侧栏期列表区是原型 DOM，其余是真实渲染；中栏一律是真实打开的 supercell 第 2 卷（企划会话的中栏界面在 PR-2b 才有，s1 只比较侧栏）。<b>示意</b>：「1 张卡待答」「运行中」徽标与「当前在企划会话」的高亮按场景摆放，不来自真实会话；各期工序是真实 core 的 status。</p>
<ul>
<li><b>A 缩进树</b>：企划行 = 名称 + 每卷工序缩写串（1·05 2·03 …）；展开后第一行「◆ 企划会话」，卷缩进挂在下面，末行「＋ 在本企划下新建卷」；按工序视图下企划整组置顶（归组）。</li>
<li><b>B 分组卡片</b>：企划是一张浅底卡，卡头右侧「企划会话」按钮，卡头下一条分段进度条（每卷一段：绿=完成、橙=停在人审、灰蓝=进行中）；卷行带「第 N 卷」前缀；按工序视图下各卷散进停机点 / 非停机点分组（散开）。</li>
<li><b>C 单行摘要</b>：企划行只写最慢的一卷，点行即进企划会话，右侧小箭头单独展开卷；卷行带「第 N 卷」、左侧细竖线；按工序视图下企划按最慢一卷整组归入。</li>
</ul>
<table><tr><th>场景</th>${cols}</tr>
${rows}
</table>`;
}
