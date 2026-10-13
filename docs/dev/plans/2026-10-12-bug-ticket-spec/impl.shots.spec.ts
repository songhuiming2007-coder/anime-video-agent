// D82 PR-C：人选方案（按钮 C 工具行 + 输入框上方提示条、卡面 K1 摘要在前）施工后的实现截图。不注入任何原型 DOM / 样式。
// 以下为 PR-B 选型脚本的原注释（场景与夹具相同）：
// D82 PR-B：「复制给 coding agent」按钮与「已开工单」工具行的真实窗口截图选型（UI 规则 4）。
// 真实 app（未打包构建）+ 真实 core + 假模型 × 临时夹具仓（绝不指向真实 data/）。开单走 PR-A 的真实链路：
// bash 真失败 → run_pipeline tickets open 弹卡 → 批准 → tool 帧带 handoff、临时仓库 tickets/ 落一份工单。
// 方案差别只在按钮放哪，原型 DOM 只加在那一条工单工具行上（样式经 adoptedStyleSheets 注入，渲染端 CSP 拦内联 <style>），其余全是真实渲染：
//   A 行尾图标：工具行行尾一个常驻的图标按钮（与 D80 的「复制命令」同位，悬停提示「复制给 coding agent」）
//   B 行下一行：工具行下面一行「工单 T-… · 复制给 coding agent」（文字按钮）
//   C 两处：工具行行尾文字按钮 + 输入框上方一条提示条「本会话开了工单 T-…（待修）· 复制给 coding agent」（工具行滚走后仍在）
// 三套方案都把工具行主文本改成「已开工单 T-…」（§一 第 3 步）。「✓ 已复制」帧是把原型按钮的文字换成 CopyButton 的完成态（示意）。
// 用法（在 desktop/ 下）：
//   PYTHONDONTWRITEBYTECODE=1 npx playwright test -c ../docs/dev/plans/2026-10-12-bug-ticket-spec/shots.config.ts impl.shots.spec.ts
// 产物：desktop/out/d82-shots/pr-c/
import { execFileSync } from "node:child_process";
import { mkdirSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test } from "../../../../desktop/node_modules/@playwright/test";
import type { Page } from "../../../../desktop/node_modules/@playwright/test";
import { assistant, startFakeLlm, toolCalls } from "../../../../desktop/e2e/fakeLlm";
import { convSnapshot, launchSession, realCoreFixture, stubQuit } from "../../../../desktop/e2e/sessionFixtures";
import { fixtureWrite } from "../../../../desktop/tests/fixtures/session";

const OUT = join(__dirname, "../../../../desktop/out/d82-shots/pr-c");

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


test("D82 PR-C 实现截图：按钮 C + 卡面 K1", async () => {
  rmSync(OUT, { recursive: true, force: true });
  mkdirSync(OUT, { recursive: true });
  const llm = await startFakeLlm();
  const fx = realCoreFixture(llm.url, EPS);
  fixtureWrite(fx.repo.root, `data/episodes/${SUPER}/00-project.md`, TABLE);
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
  const saved = await L.app.evaluate(({ clipboard }) => clipboard.readText());
  try {
    await p.setViewportSize({ width: 1440, height: 900 });
    const toggle = p.locator(`[data-testid=project][data-proj="${SUPER}"] [data-testid=project-toggle]`);
    if ((await toggle.getAttribute("aria-expanded")) !== "true") await toggle.click();
    await p.locator(`[data-testid=episode][data-ep="${VOL1}"]`).click();
    await expect(p.getByTestId("center")).toHaveAttribute("data-ep", VOL1);
    await hideFreeze();
    const why = "同一组参数在 EGOIST V2 第三卷能跑通，这一卷报 KeyError: BGM；01-topic.md 的 BGM 字段与曲库登记已逐项核对，曲名在表里";
    const fail = { message: { role: "assistant" as const, content: null, tool_calls: [{ id: "call_tts1", type: "function" as const, function: { name: "bash", arguments: JSON.stringify({ command: "echo 'Traceback (most recent call last):' >&2; echo '  File \"pipeline/tts.py\", line 812, in mix_bgm' >&2; echo 'KeyError: BGM' >&2; exit 1", description: "重配第 5 段并混 BGM" }) } }] } };
    const open = `tickets open --call call_tts1 --blocked 03 --symptom '重配第 5 段时混 BGM 报 KeyError: BGM，整段失败' --expected '第 5 段重配成功并混入 BGM' --repro 'run_pipeline tts --redo 5' --why '${why}'`;
    llm.push(fail as never, assistant("配音在混 BGM 时报 KeyError: BGM。我核对过 01-topic.md 与曲库登记都没问题，判断是引擎缺陷，提议开一张工单。"));
    await send("重配第 5 段");
    await turns(1);
    llm.push(toolCalls({ name: "run_pipeline", args: { command: open } }), assistant("工单已开，可以把交接文本复制给 coding agent。"));
    await send("开单吧");
    await expect(p.getByTestId("request-card")).toBeVisible({ timeout: 20_000 });
    for (const [w, h] of [[1280, 800], [1440, 900]] as const) {
      await p.setViewportSize({ width: w, height: h });
      await p.waitForTimeout(400);
      await p.locator("[data-testid=request-card]").evaluate((el) => el.scrollIntoView({ block: "nearest" }));
      await p.waitForTimeout(200);
      measures[`card-${w}`] = await measureCard(p);
      await p.screenshot({ path: join(OUT, `c2-card-${w}.png`) });
    }
    await p.getByTestId("request-answer-approve").click();
    await turns(2);
    for (const [w, h] of [[1440, 900], [1280, 800]] as const) {
      await p.setViewportSize({ width: w, height: h });
      await settle(p);
      await p.screenshot({ path: join(OUT, `c3-folded-${w}.png`) });
    }
    const fold = p.getByTestId("turn-fold").last();
    if ((await fold.getAttribute("aria-expanded")) !== "true") await fold.click();
    for (const [w, h] of [[1440, 900], [1280, 800]] as const) {
      await p.setViewportSize({ width: w, height: h });
      await settle(p);
      await p.screenshot({ path: join(OUT, `c3-open-${w}.png`) });
    }
    await p.setViewportSize({ width: 1440, height: 900 });
    await p.getByTestId("handoff-strip-copy").click();
    await expect(p.getByTestId("handoff-strip-copy")).toHaveText("✓ 已复制");
    await p.getByTestId("center").screenshot({ path: join(OUT, "c4-copied-1440.png") });
    const dir = join(fx.repo.root, "tickets");
    const file = join(dir, readdirSync(dir).find((n) => n.endsWith(".md"))!);
    writeFileSync(file, readFileSync(file, "utf-8").replace("状态: 已修 | 不是bug | 不修\n提交:\n续跑:", "状态: 已修\n提交: 3f9c2ab\n续跑: run_pipeline tts --redo 5"));
    await p.getByTestId("session-end").click();
    await expect(p.getByTestId("session-head")).toHaveAttribute("data-phase", /^(none|exited)$/, { timeout: 15_000 });
    llm.push(assistant("好，从 03 续跑。"));
    await send("继续");
    await expect(p.locator("[data-testid=conv-row][data-kind=notice][data-code=ticket_fixed]")).toBeVisible({ timeout: 20_000 });
    await p.getByTestId("conv-stream").evaluate((el) => { el.scrollTop = 0; });
    await p.waitForTimeout(300);
    await p.getByTestId("center").screenshot({ path: join(OUT, "c5-notice-1440.png") });
    writeFileSync(join(OUT, "measures.json"), JSON.stringify(measures, null, 2));
  } finally {
    await L.app.evaluate(({ clipboard }, t) => clipboard.writeText(t), saved).catch(() => undefined);
    await stubQuit(L, "quit").catch(() => undefined);
    await L.app.close().catch(() => undefined);
    await llm.close();
    fx.cleanup();
  }
});
