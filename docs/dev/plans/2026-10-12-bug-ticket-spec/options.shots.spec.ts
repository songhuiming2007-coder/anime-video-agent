// D82 PR-B：「复制给 coding agent」按钮与「已开工单」工具行的真实窗口截图选型（UI 规则 4）。
// 真实 app（未打包构建）+ 真实 core + 假模型 × 临时夹具仓（绝不指向真实 data/）。开单走 PR-A 的真实链路：
// bash 真失败 → run_pipeline tickets open 弹卡 → 批准 → tool 帧带 handoff、临时仓库 tickets/ 落一份工单。
// 方案差别只在按钮放哪，原型 DOM 只加在那一条工单工具行上（样式经 adoptedStyleSheets 注入，渲染端 CSP 拦内联 <style>），其余全是真实渲染：
//   A 行尾图标：工具行行尾一个常驻的图标按钮（与 D80 的「复制命令」同位，悬停提示「复制给 coding agent」）
//   B 行下一行：工具行下面一行「工单 T-… · 复制给 coding agent」（文字按钮）
//   C 两处：工具行行尾文字按钮 + 输入框上方一条提示条「本会话开了工单 T-…（待修）· 复制给 coding agent」（工具行滚走后仍在）
// 三套方案都把工具行主文本改成「已开工单 T-…」（§一 第 3 步）。「✓ 已复制」帧是把原型按钮的文字换成 CopyButton 的完成态（示意）。
// 用法（在 desktop/ 下）：
//   PYTHONDONTWRITEBYTECODE=1 npx playwright test -c ../docs/dev/plans/2026-10-12-bug-ticket-spec/shots.config.ts
// 产物：desktop/out/d82-shots/pr-b/（index.html 是对照页）
import { execFileSync } from "node:child_process";
import { mkdirSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test } from "../../../../desktop/node_modules/@playwright/test";
import type { Page } from "../../../../desktop/node_modules/@playwright/test";
import { assistant, startFakeLlm, toolCalls } from "../../../../desktop/e2e/fakeLlm";
import { convSnapshot, launchSession, realCoreFixture, stubQuit } from "../../../../desktop/e2e/sessionFixtures";
import { fixtureWrite } from "../../../../desktop/tests/fixtures/session";

const OUT = join(__dirname, "../../../../desktop/out/d82-shots/pr-b");
const SCHEMES = ["A", "B", "C"] as const;
type Scheme = (typeof SCHEMES)[number];
const LABEL: Record<Scheme, string> = { A: "A 行尾图标", B: "B 行下一行", C: "C 工具行 + 输入框上方提示条" };

// 侧栏内容量同 D81 §一（supercell 4 卷 + 合集、EGOIST V2 三卷 + 典藏版、V1 三卷同名、普通期）
const SUPER = "supercell-编年史";
const VOL1 = `${SUPER}/01-黑岩射手与ryo的同人时代`;
const EPS = [
  ...["00-全一期-合集", "01-黑岩射手与ryo的同人时代", "02-nagi接棒的那几年", "03-こしょう与第三代歌姬", "04-十五年后的告别演出"].map((v) => `${SUPER}/${v}`),
  ...["01-借躯降生", "02-皮套囚徒", "03-终局葬礼"].flatMap((v) => [`EGOIST-传奇企划志/${v}`, `EGOIST-传奇企划志-V2/${v}`]),
  "EGOIST-传奇企划志-V2/00-全一期-典藏版",
  ...["2026-10-10-伪恋-橘万里花的进攻哲学", "2026-09-21-东京喰种-雾岛董香人物志-二", "2026-09-06-你的名字-遗忘的代价", "2026-09-03-伪恋-一条乐的算法死局",
    "2026-08-31-罪恶王冠-校条祭之死", "2026-08-26-天气之子-天野阳菜人物志", "2026-08-24-天气之子-电车难题的背叛", "2026-08-20-通往夏天的隧道-逃避的代价",
    "2026-08-18-春物-折射下的真物", "2026-08-15-罪恶王冠-音乐乐评", "2026-08-13-罪恶王冠-函数的终途（void）", "2026-08-12-罪恶王冠-楪祈人物志-二",
    "2026-08-10-罪恶王冠-楪祈人物志-一", "2026-08-09-东京喰种-雾岛董香人物志-一", "2026-08-04-春物-团子不该赢", "2026-08-03-春物-谁最适合你",
    "2026-07-30-春物-雪乃适合大老师", "2026-07-28-春物-自我牺牲", "2025-老视频"],
];
const TABLE = ["企划: supercell 编年史", "合集: 00-全一期-合集", "", "## 卷表", "", "| 卷序 | 目录 | 年份 | 时长预算 | 片尾 |", "|---|---|---|---|---|",
  "| 1 | 01-黑岩射手与ryo的同人时代 | 2007–2010 | 14-16分钟 | 余韵18秒 |", "| 2 | 02-nagi接棒的那几年 | 2011–2014 | 14-16分钟 | 余韵18秒 |",
  "| 3 | 03-こしょう与第三代歌姬 | 2015–2019 | 14-16分钟 | 余韵18秒 |", "| 4 | 04-十五年后的告别演出 | 2020–2024 | 18-20分钟 | 完整保留 |", ""].join("\n");

const CSS = `
.d82 .copy-btn { flex: none; }
.d82-a { color: var(--fg-muted); }
.d82-b-line { display: flex; align-items: center; gap: var(--space-2); padding: 2px var(--space-3) var(--space-1) 34px; font-size: var(--text-sm); color: var(--fg-muted); min-width: 0; }
.d82-b-line .d82-id { font-family: var(--font-mono); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
.d82-c-strip { display: flex; align-items: center; gap: var(--space-2); margin: 0 0 var(--space-2); padding: var(--space-1) var(--space-3); border: 1px solid var(--border); border-radius: var(--radius-lg); background: var(--bg-subtle); font-size: var(--text-sm); color: var(--fg-muted); min-width: 0; }
.d82-c-strip .d82-id { font-family: var(--font-mono); color: var(--fg); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
.d82-c-strip .spacer { flex: 1; }
`;

async function applyScheme(p: Page, scheme: Scheme, ticketId: string, copied: boolean): Promise<void> {
  await p.evaluate(({ scheme, ticketId, copied, css }) => {
    document.querySelectorAll(".d82-proto").forEach((e) => e.remove());
    const rows = [...document.querySelectorAll<HTMLElement>("[data-testid=conv-row][data-kind=tool]")].filter((r) => r.querySelector(".conv-tool")?.getAttribute("title") === "run_pipeline");
    // 回合结束后默认折叠（toolView.turnDefaultOpen：outcome==="done" 收起），折叠时工具行不在 DOM 里：A / B 无处可放，C 只有提示条
    const row = rows[rows.length - 1] as HTMLElement | undefined;
    const tool = row?.querySelector<HTMLElement>(".conv-tool") ?? null;
    if (row && tool) {
      row.classList.add("d82");
      (row.querySelector(".conv-tool-main") as HTMLElement).textContent = `已开工单 ${ticketId}`;
      const sub = row.querySelector<HTMLElement>(".conv-tool-sub");
      if (sub) sub.textContent = "";
    }
    const copyIcon = document.querySelector(".conv-tool-copy svg, [data-testid=tool-copy] svg")?.outerHTML ?? "⧉";
    const btn = (text: string, cls: string) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = `ui-btn ui-btn--ghost ui-btn--sm copy-btn d82-proto ${cls}${copied ? " copy-btn--done" : ""}`;
      b.title = "复制给 coding agent";
      b.dataset.testid = "d82-handoff";
      b.innerHTML = copied ? "✓ 已复制" : text;
      return b;
    };
    const status = tool?.querySelector(".conv-tool-dur") ?? tool?.querySelector(".conv-tool-status") ?? null;
    if (scheme === "A" && tool) tool.insertBefore(btn(copyIcon, "d82-a"), status);
    if (scheme === "B" && row && tool) {
      const line = document.createElement("div");
      line.className = "d82-b-line d82-proto";
      line.innerHTML = `<span>工单</span><span class="d82-id">${ticketId}</span><span>·</span>`;
      line.appendChild(btn("复制给 coding agent", "d82-b"));
      row.insertBefore(line, tool.nextSibling);
    }
    if (scheme === "C") {
      if (tool) tool.insertBefore(btn("复制给 coding agent", "d82-c"), status);
      const composer = document.querySelector<HTMLElement>("[data-testid=composer-input]")!.closest(".composer") as HTMLElement;
      const strip = document.createElement("div");
      strip.className = "d82-c-strip d82-proto";
      strip.innerHTML = `<span>本会话开了工单</span><span class="d82-id">${ticketId}</span><span>（待修）</span><span class="spacer"></span>`;
      const b2 = btn("复制给 coding agent", "d82-c2");
      b2.classList.remove("copy-btn--done");
      b2.innerHTML = "复制给 coding agent";
      strip.appendChild(b2);
      composer.parentElement!.insertBefore(strip, composer);
      // 与输入框同一列（输入框居中限宽）：按输入框相对父容器的左右边距对齐
      const pr = composer.parentElement!.getBoundingClientRect(), cr = composer.getBoundingClientRect();
      strip.style.marginLeft = `${Math.round(cr.left - pr.left)}px`;
      strip.style.marginRight = `${Math.round(pr.right - cr.right)}px`;
    }
    const sheet = new CSSStyleSheet();
    sheet.replaceSync(css);
    (sheet as unknown as { d82: boolean }).d82 = true;
    document.adoptedStyleSheets = [...document.adoptedStyleSheets.filter((s) => !(s as unknown as { d82?: boolean }).d82), sheet];
  }, { scheme, ticketId, copied, css: CSS });
}

/** UI 规则 5 底线：工单工具行里的元素（含原型按钮、B 的下一行、C 的提示条）两两不相交、对话区不横向溢出 */
async function assertFloor(p: Page, label: string): Promise<void> {
  const r = await p.evaluate(() => {
    const row = document.querySelector(".d82");
    const els = [...(row?.querySelectorAll(".conv-tool > *, .d82-b-line > *") ?? []), ...document.querySelectorAll(".d82-c-strip > *, .d82-c-strip, .composer")];
    const boxes = els.map((e) => e.getBoundingClientRect()).filter((b) => b.width > 0 && b.height > 0);
    const contains = (x: DOMRect, y: DOMRect) => x.left <= y.left + 0.5 && x.right >= y.right - 0.5 && x.top <= y.top + 0.5 && x.bottom >= y.bottom - 0.5;
    let overlap = 0;
    for (let i = 0; i < boxes.length; i++) for (let j = i + 1; j < boxes.length; j++) {
      const a = boxes[i], b = boxes[j];
      if (contains(a, b) || contains(b, a)) continue;
      if (a.left < b.right - 0.5 && b.left < a.right - 0.5 && a.top < b.bottom - 0.5 && b.top < a.bottom - 0.5) overlap++;
    }
    const stream = document.querySelector<HTMLElement>("[data-testid=conv-stream]")!;
    return { overlap, xOverflow: stream.scrollWidth > stream.clientWidth };
  });
  expect(r.overlap, `${label} 重叠`).toBe(0);
  expect(r.xOverflow, `${label} 横向溢出`).toBe(false);
}

/** 开单卡：卡头（标题行）与批准按钮是否同时在对话流可见区内（D80 附件的 textFullyVisible 量法：元素框整个落在容器框内） */
async function measureCard(p: Page): Promise<Record<string, unknown>> {
  return p.evaluate(() => {
    const card = document.querySelector<HTMLElement>("[data-testid=request-card]")!;
    const head = card.firstElementChild as HTMLElement;
    const approve = card.querySelector<HTMLElement>("[data-testid=request-answer-approve]")!;
    const text = card.querySelector<HTMLElement>("[data-testid=request-card-text]")!;
    let box: HTMLElement | null = card.parentElement;
    while (box && !(box.scrollHeight > box.clientHeight && getComputedStyle(box).overflowY !== "visible")) box = box.parentElement;
    const c = (box ?? document.documentElement).getBoundingClientRect();
    const vis = (e: HTMLElement) => { const r = e.getBoundingClientRect(); return r.top >= c.top - 0.5 && r.bottom <= c.bottom + 0.5; };
    const t = text.textContent ?? "";
    return {
      headVisible: vis(head), approveVisible: vis(approve), both: vis(head) && vis(approve),
      cardHeight: Math.round(card.getBoundingClientRect().height), containerHeight: Math.round(c.height),
      textLines: t.split("\n").length,
      hasCommand: t.includes("echo 'KeyError: BGM'"), hasExit: t.includes("退出码: 1"), hasWhy: t.includes("为什么是代码 bug 而不是用法错误"),
      approveTop: Math.round(approve.getBoundingClientRect().top - c.top),
    };
  });
}

// 开单卡的两种卡面原型（§一 第 2 步：不点开就看到命令 / 退出码 / 报错行与「为什么不是用法错误」，且卡头与批准按钮同时可见）
//   K1 摘要在前：卡面先列 是否追加 / 命令 / 退出码 / 报错末行 / 为什么不是用法错误，工单全文收进「工单全文」折叠
//   K2 全文限高：卡面仍是全文，文本区限高、内部滚动，批准按钮常驻可见
const CARD_CSS = `
.d82-k2 .request-card-text { max-height: 200px; overflow: auto; }
.d82-k1-sum { margin: 0; white-space: pre-wrap; font-family: var(--font-mono); font-size: var(--text-sm); }
.d82-k1-full > summary { cursor: pointer; color: var(--fg-muted); font-size: var(--text-sm); }
.d82-k1-full pre { max-height: 240px; overflow: auto; }
`;
async function applyCard(p: Page, k: "now" | "K1" | "K2"): Promise<void> {
  await p.evaluate(({ k, css }) => {
    const card = document.querySelector<HTMLElement>("[data-testid=request-card]")!;
    const pre = card.querySelector<HTMLElement>("[data-testid=request-card-text]")!;
    const orig = pre.dataset.orig ?? pre.textContent ?? "";
    pre.dataset.orig = orig;
    card.classList.remove("d82-k2");
    card.querySelectorAll(".d82-k1").forEach((e) => e.remove());
    pre.hidden = false;
    if (k === "K2") card.classList.add("d82-k2");
    if (k === "K1") {
      const lines = orig.split("\n").map((l) => l.replace(/^│ ?/, ""));
      const at = (re: RegExp) => lines.findIndex((l) => re.test(l));
      const block = (start: number) => { const out: string[] = []; for (let i = start + 1; i < lines.length && /^ {2}/.test(lines[i]); i++) if (!/^ {2}```/.test(lines[i])) out.push(lines[i].trim()); return out; };
      const cmd = block(at(/^- 命令:/));
      const err = block(at(/^- stderr 尾部:/)).filter((l) => l !== "");
      const whyAt = at(/^### 为什么是代码 bug/);
      const why: string[] = [];
      for (let i = whyAt + 1; i < lines.length && !/^#/.test(lines[i]); i++) if (lines[i].trim() !== "") why.push(lines[i].trim());
      const head = lines.find((l) => /^将新开一张工单|^将追加到/.test(l)) ?? "";
      const sum = [head.replace(/：$/, ""), `命令: ${cmd.join(" ")}`, lines[at(/^- 退出码:/)].replace(/^- /, ""), `报错末行: ${err[err.length - 1] ?? ""}`, "为什么是代码 bug 而不是用法错误:", ...why.map((l) => `  ${l}`)].join("\n");
      const s = document.createElement("pre");
      s.className = "d82-k1 d82-k1-sum";
      s.textContent = sum;
      const d = document.createElement("details");
      d.className = "d82-k1 d82-k1-full";
      d.innerHTML = `<summary>工单全文（${lines.length} 行）</summary>`;
      const full = document.createElement("pre");
      full.className = "request-card-text";
      full.textContent = orig;
      d.appendChild(full);
      pre.hidden = true;
      pre.after(s, d);
    }
    const sheet = new CSSStyleSheet();
    sheet.replaceSync(css);
    (sheet as unknown as { d82card: boolean }).d82card = true;
    document.adoptedStyleSheets = [...document.adoptedStyleSheets.filter((x) => !(x as unknown as { d82card?: boolean }).d82card), sheet];
  }, { k, css: CARD_CSS });
}

const settle = async (p: Page) => {
  await p.getByTestId("conv-stream").evaluate((el) => { el.scrollTop = el.scrollHeight; });
  await p.waitForTimeout(450);
};

test("D82 PR-B「复制给 coding agent」三套方案 × §一 第 1–5 步", async () => {
  rmSync(OUT, { recursive: true, force: true });
  mkdirSync(OUT, { recursive: true });
  const llm = await startFakeLlm();
  const fx = realCoreFixture(llm.url, EPS);
  fixtureWrite(fx.repo.root, `data/episodes/${SUPER}/00-project.md`, TABLE);
  // 场景：第一卷 02.5 已批、停在 03 配音（夹具默认给每期配了 03-audio 与 04-clips.json，会停在 05；
  // 工单阻塞 03 的提醒只在还没走过 03 时出现）。02.5 的封板产物是 02-diff.patch
  for (const rel of ["03-audio", "04-clips.json"]) rmSync(join(fx.repo.eps, VOL1, rel), { recursive: true, force: true });
  fixtureWrite(fx.repo.root, `data/episodes/${VOL1}/02-diff.patch`, "");
  execFileSync("git", ["init", "-q", fx.repo.root]);
  fixtureWrite(fx.repo.root, ".gitignore", "/tickets/\n");
  const L = await launchSession(fx.repo, [], { previewOpen: false });
  const p = L.page;
  const send = async (t: string) => { await p.getByTestId("composer-input").fill(t); await p.getByTestId("composer-input").press("Enter"); };
  const turns = (n: number) => expect.poll(async () => (await convSnapshot(L, `ep:${VOL1}`)).entries.filter((e) => e.k === "frame" && e.frame?.t === "turn_finished").length, { timeout: 30_000 }).toBeGreaterThanOrEqual(n);
  const hideFreeze = () => p.evaluate(() => document.querySelectorAll<HTMLElement>(".banner").forEach((b) => { if (b.textContent?.includes("Code Freeze")) b.hidden = true; }));
  const measures: Record<string, unknown> = {};
  try {
    await p.setViewportSize({ width: 1440, height: 900 });
    const toggle = p.locator(`[data-testid=project][data-proj="${SUPER}"] [data-testid=project-toggle]`);
    if ((await toggle.getAttribute("aria-expanded")) !== "true") await toggle.click();
    await p.locator(`[data-testid=episode][data-ep="${VOL1}"]`).click();
    await expect(p.getByTestId("center")).toHaveAttribute("data-ep", VOL1);
    await hideFreeze();

    // §一 第 1–2 步：工序真失败（bash，D80 方案 A 的失败行）→ ava 提议开单，弹卡
    const why = "同一组参数在 EGOIST V2 第三卷能跑通，这一卷报 KeyError: BGM；01-topic.md 的 BGM 字段与曲库登记已逐项核对，曲名在表里";
    const fail = { message: { role: "assistant" as const, content: null, tool_calls: [{ id: "call_tts1", type: "function" as const, function: { name: "bash", arguments: JSON.stringify({ command: "echo 'Traceback (most recent call last):' >&2; echo '  File \"pipeline/tts.py\", line 812, in mix_bgm' >&2; echo 'KeyError: BGM' >&2; exit 1", description: "重配第 5 段并混 BGM" }) } }] } };
    const open = `tickets open --call call_tts1 --blocked 03 --symptom '重配第 5 段时混 BGM 报 KeyError: BGM，整段失败' --expected '第 5 段重配成功并混入 BGM' --repro 'run_pipeline tts --redo 5' --why '${why}'`;
    llm.push(fail as never, assistant("配音在混 BGM 时报 KeyError: BGM。我核对过 01-topic.md 与曲库登记都没问题，判断是引擎缺陷，提议开一张工单。"));
    await send("重配第 5 段");
    await turns(1);
    await settle(p);
    await p.getByTestId("center").screenshot({ path: join(OUT, "f1-fail-1440.png") });
    llm.push(toolCalls({ name: "run_pipeline", args: { command: open } }), assistant("工单已开，可以把交接文本复制给 coding agent。"));
    await send("开单吧");
    await expect(p.getByTestId("request-card")).toBeVisible({ timeout: 20_000 });
    await p.waitForTimeout(500);
    for (const k of ["now", "K1", "K2"] as const) {
      await applyCard(p, k);
      for (const [w, h] of [[1280, 800], [1440, 900]] as const) {
        await p.setViewportSize({ width: w, height: h });
        await p.waitForTimeout(400);
        await p.locator("[data-testid=request-card]").evaluate((el) => el.scrollIntoView({ block: "nearest" }));
        await p.waitForTimeout(200);
        measures[`card-${k}-${w}`] = await measureCard(p);
        await p.screenshot({ path: join(OUT, `f2-card-${k}-${w}.png`) });
      }
    }
    await applyCard(p, "now");

    // §一 第 3 步：人批准 → 工具行带 handoff（真实帧）
    await p.getByTestId("request-answer-approve").click();
    await turns(2);
    const frames = (await convSnapshot(L, `ep:${VOL1}`)).entries.filter((e) => e.k === "frame").map((e) => e.frame as Record<string, unknown>);
    const end = frames.filter((f) => f.t === "tool" && f.phase === "end" && f.name === "run_pipeline").at(-1)!;
    const handoff = String(end.handoff);
    const ticketId = /T-\d{8}T\d{6}Z-[0-9a-f]{6}/.exec(handoff)![0];
    measures.handoff = handoff.replace(fx.repo.root, "<夹具仓>");
    const fold = p.getByTestId("turn-fold").last();
    for (const scheme of SCHEMES) {
      // 默认状态：回合结束即折叠（人批准、回合跑完后的样子）
      await p.setViewportSize({ width: 1440, height: 900 });
      if ((await fold.getAttribute("aria-expanded")) === "true") await fold.click();
      await settle(p);
      await applyScheme(p, scheme, ticketId, false);
      await p.screenshot({ path: join(OUT, `f3-${scheme}-folded-1440.png`) });
      // 展开这一回合后
      await fold.click();
      for (const [w, h] of [[1440, 900], [1280, 800]] as const) {
        await p.setViewportSize({ width: w, height: h });
        await settle(p);
        await applyScheme(p, scheme, ticketId, false);
        await assertFloor(p, `${scheme}-${w}`);
        await p.screenshot({ path: join(OUT, `f3-${scheme}-${w}.png`) });
      }
      // §一 第 4 步：复制后的反馈（CopyButton 完成态，示意）
      await p.setViewportSize({ width: 1440, height: 900 });
      await applyScheme(p, scheme, ticketId, true);
      await p.getByTestId("center").screenshot({ path: join(OUT, `f4-${scheme}-1440.png`) });
    }
    await p.evaluate(() => document.querySelectorAll(".d82-proto").forEach((e) => e.remove()));

    // §一 第 5 步：coding agent 修完（这里直接改夹具工单的「处理结果」）→ 重开会话 → 对话区顶部 ticket_fixed 提醒（真实 notice 帧）
    const dir = join(fx.repo.root, "tickets");
    const file = join(dir, readdirSync(dir).find((n) => n.endsWith(".md"))!);
    writeFileSync(file, readFileSync(file, "utf-8").replace("状态: 已修 | 不是bug | 不修\n提交:\n续跑:", "状态: 已修\n提交: 3f9c2ab\n续跑: run_pipeline tts --redo 5"));
    await p.getByTestId("session-end").click();
    await expect(p.getByTestId("session-head")).toHaveAttribute("data-phase", /^(none|exited)$/, { timeout: 15_000 });
    llm.push(assistant("好，从 03 续跑。"));
    await send("继续");
    await expect(p.locator("[data-testid=conv-row][data-kind=notice][data-code=ticket_fixed]")).toBeVisible({ timeout: 20_000 });
    for (const [w, h] of [[1440, 900], [1280, 800]] as const) {
      await p.setViewportSize({ width: w, height: h });
      await p.getByTestId("conv-stream").evaluate((el) => { el.scrollTop = 0; });
      await p.waitForTimeout(300);
      await p.getByTestId("center").screenshot({ path: join(OUT, `f5-notice-${w}.png`) });
    }
    writeFileSync(join(OUT, "measures.json"), JSON.stringify(measures, null, 2));
    writeFileSync(join(OUT, "index.html"), indexHtml(measures));
  } finally {
    await stubQuit(L, "quit").catch(() => undefined);
    await L.app.close().catch(() => undefined);
    await llm.close();
    fx.cleanup();
  }
});

function indexHtml(m: Record<string, unknown>): string {
  const img = (f: string, w = 560) => `<a href="${f}"><img src="${f}" style="width:${w}px"></a>`;
  const card = (k: string, w: number) => {
    const c = m[`card-${k}-${w}`] as Record<string, unknown>;
    return `${w}：卡头 ${c.headVisible ? "可见" : "<b>不可见</b>"}、批准按钮 ${c.approveVisible ? "可见" : "<b>不可见</b>"}（卡高 ${c.cardHeight}px / 可见区 ${c.containerHeight}px）`;
  };
  const K: Record<string, string> = { now: "现状（PR-A：卡面是工单全文）", K1: "K1 摘要在前、全文收起", K2: "K2 全文、文本区限高内部滚动" };
  const cardRows = ["now", "K1", "K2"].map((k) => `<tr><td class="lab"><b>${K[k]}</b><br>${card(k, 1280)}<br>${card(k, 1440)}</td><td>${img(`f2-card-${k}-1280.png`, 480)}</td><td>${img(`f2-card-${k}-1440.png`, 480)}</td></tr>`).join("\n");
  const rows = SCHEMES.map((s) => `<tr><td class="lab"><b>${LABEL[s]}</b></td><td>${img(`f3-${s}-folded-1440.png`, 420)}</td><td>${img(`f3-${s}-1440.png`)}</td><td>${img(`f3-${s}-1280.png`)}</td><td>${img(`f4-${s}-1440.png`, 420)}</td></tr>`).join("\n");
  return `<!doctype html><meta charset="utf-8"><title>D82 PR-B 复制给 coding agent</title>
<style>body{font:14px -apple-system,sans-serif;margin:16px}table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:6px;vertical-align:top}.lab{width:160px}</style>
<h1>D82 PR-B：「复制给 coding agent」按钮 · 截图选型</h1>
<p>真实窗口（未打包构建 × 临时夹具仓 × 真实 core × 假模型）。开单是 PR-A 的真实链路，handoff 是真实帧；只有工单那一条工具行上的按钮 / 下一行 / 提示条是原型 DOM，「✓ 已复制」是把原型按钮换成 CopyButton 的完成态（示意）。</p>
<ul>
<li><b>A 行尾图标</b>：工具行行尾一个常驻图标按钮（与 D80 的「复制命令」同位），悬停提示「复制给 coding agent」。</li>
<li><b>B 行下一行</b>：工具行下面一行「工单 T-… · 复制给 coding agent」。</li>
<li><b>C 两处</b>：工具行行尾文字按钮 + 输入框上方提示条「本会话开了工单 T-…（待修）· 复制给 coding agent」（工具行滚出视野后仍可复制）。</li>
</ul>
<h2>与方案无关的帧</h2>
<p>第 1 步 失败工具行 ${img("f1-fail-1440.png", 420)}</p>
<h2>第 2 步：开单卡（§一 要求 1280×800 下卡头与批准按钮同时可见，且不点开就看到命令 / 退出码 / 报错行与「为什么不是用法错误」）</h2>
<p>PR-A 的卡面是整份工单（约 50 行），现状两档都看不到批准按钮。K1 / K2 是两种改法（原型）：</p>
<table><tr><th>卡面</th><th>1280×800</th><th>1440×900</th></tr>
${cardRows}
</table>
<p>第 5 步 重开会话的提醒（真实 ticket_fixed notice）${img("f5-notice-1440.png", 480)} ${img("f5-notice-1280.png", 480)}</p>
<h2>第 3–4 步：三套方案</h2>
<p><b>注意</b>：回合跑完默认折叠成「已工作 … · N 个工具」（D80 R4，<code>turnDefaultOpen</code>），工单那条工具行随之收进折叠里——A / B 的按钮在默认状态下看不见，要先点开回合；C 的提示条在折叠之外。若选 A 或 B，PR-C 需要同时让「含工单的回合」默认展开（改 <code>turnDefaultOpen</code>）。</p>
<table><tr><th>方案</th><th>回合跑完（默认折叠）1440</th><th>展开后 1440×900</th><th>展开后 1280×800</th><th>复制后 1440</th></tr>
${rows}
</table>`;
}
