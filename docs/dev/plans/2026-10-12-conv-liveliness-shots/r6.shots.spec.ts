// D80 PR-D：R6「工具卡一眼可批」三方案的真实窗口截图（未打包构建 × 临时夹具仓，绝不指向真实 data/）。
// 场景同 P13 取证（shots.spec.ts）：停机点 05 待审 + bash `rm stale.tmp` 弹卡，「批准」已自动聚焦。
// 硬约束（spec R6.1）：聚焦批准后，卡头与 card_text 里「危险标记」到「命令」各行必须在待答区可见范围内。
// card_text 由 core 确定性渲染，H-3 要求按原样显示、不解释标记——三方案都**不拆**卡面文本，只动布局。
//   A 聚焦不滚 + 停机点收成一行：待答区高度不变；聚焦不滚走卡头；有工具卡时停机点卡只留标题行；反馈框藏在「附说明…」后（拒绝仍一键）
//   B 待答区让高：有工具卡时待答区最高 60%（现 40%），对话区相应变矮；停机点卡照旧；「拒绝…」第一次点只展开反馈框（spec 原文）
//   C 卡片进对话流：工具卡挂在对话流末尾（正在跑的回合下面），待答区只剩停机点；反馈框同 A
// 「现状」不改 DOM；A/B/C 是在真实窗口上改 DOM / 样式的原型（constructable stylesheet，渲染端 CSP 拦内联 <style>）。
// 用法（在 desktop/ 下）：
//   PYTHONDONTWRITEBYTECODE=1 npx playwright test -c ../docs/dev/plans/2026-10-12-conv-liveliness-shots/shots.config.ts -g "R6"
// 产物：desktop/out/d80-shots/r6/（index.html 是对照页）
import { mkdirSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test } from "../../../../desktop/node_modules/@playwright/test";
import type { Page } from "../../../../desktop/node_modules/@playwright/test";
import { assistant, startFakeLlm, toolCalls } from "../../../../desktop/e2e/fakeLlm";
import { convSnapshot, launchSession, realCoreFixture, stubQuit } from "../../../../desktop/e2e/sessionFixtures";

const OUT = join(__dirname, "../../../../desktop/out/d80-shots/r6");
const EP = "第12期-命运石之门人物志";
const SCHEMES = ["now", "A", "B", "C"] as const;
type Scheme = (typeof SCHEMES)[number];
const LABEL: Record<Scheme, string> = { now: "现状", A: "A 聚焦不滚 + 停机点收成一行", B: "B 待答区让高", C: "C 卡片进对话流" };

/** 在页面里执行：撤掉上一套原型，按 scheme 改样式 / DOM，再聚焦「批准」（自包含） */
function apply(doc: Document, scheme: string): void {
  doc.adoptedStyleSheets = doc.adoptedStyleSheets.filter((sh) => !(sh as unknown as { r6?: boolean }).r6);
  const card = doc.querySelector<HTMLElement>("[data-testid=request-card]")!;
  const dock = doc.querySelector<HTMLElement>(".dock")!;
  const home = (card as unknown as { __home?: HTMLElement }).__home;
  if (home && card.parentElement !== home) home.prepend(card); // C 挪走过：放回待答区
  (card as unknown as { __home?: HTMLElement }).__home = dock;
  doc.querySelectorAll(".r6-feedback-toggle").forEach((n) => n.remove());
  const reject = card.querySelector<HTMLElement>("[data-testid=request-answer-reject]");
  if (reject) reject.textContent = "拒绝";
  if (scheme === "now") {
    card.querySelector<HTMLButtonElement>("[data-testid=request-answer-approve]")!.focus();
    return;
  }
  const css: Record<string, string> = {
    A: `
      .decisions.dock .decision:not(.request-card) > :not(.ui-card-head) { display: none; }
      .decisions.dock .decision:not(.request-card) { padding-block: var(--space-2); }
      .request-card .ui-field { display: none; }
      .request-card .request-card-fields { display: none; }`,
    B: `
      .conv-shell .dock { max-height: 60%; }
      .request-card .ui-field { display: none; }`,
    C: `
      .request-card .ui-field { display: none; }
      .request-card .request-card-fields { display: none; }
      [data-testid=conv-stream] > .request-card { flex: none; }`,
  };
  const sheet = new CSSStyleSheet();
  sheet.replaceSync(css[scheme]);
  (sheet as unknown as { r6?: boolean }).r6 = true;
  doc.adoptedStyleSheets = [...doc.adoptedStyleSheets, sheet];
  const actions = card.querySelector<HTMLElement>(".ui-card-actions")!;
  if (scheme === "A" || scheme === "C") {
    actions.insertAdjacentHTML("beforeend", '<button type="button" class="ui-btn ui-btn--ghost ui-btn--sm r6-feedback-toggle">附说明…</button>');
  } else if (reject) {
    reject.textContent = "拒绝…";
  }
  if (scheme === "C") {
    const stream = doc.querySelector<HTMLElement>("[data-testid=conv-stream]")!;
    stream.appendChild(card);
  }
  const approve = card.querySelector<HTMLButtonElement>("[data-testid=request-answer-approve]")!;
  if (scheme === "B") approve.focus();
  else approve.focus({ preventScroll: true });
  if (scheme === "A") dock.scrollTop = 0;
  if (scheme === "C") {
    const stream = doc.querySelector<HTMLElement>("[data-testid=conv-stream]")!;
    stream.scrollTop = stream.scrollHeight;
  }
}

type Vis = { text: boolean; approve: boolean };
/** 硬约束的量法同 P13：card_text 整段在其滚动容器的可见范围内（C 的容器是对话流）；另查聚焦的「批准」按钮也在视野内——
 *  回车照样能批，但人看不见按钮就不知道焦点在哪（首轮截图 A@1280 文本可见、按钮被挤出，量法据此补上） */
async function visible(p: Page): Promise<Vis> {
  return p.evaluate(() => {
    const inside = (el: HTMLElement) => {
      const box = (el.closest(".dock") ?? el.closest("[data-testid=conv-stream]")) as HTMLElement;
      const r = el.getBoundingClientRect();
      const b = box.getBoundingClientRect();
      return r.top >= b.top - 0.5 && r.bottom <= b.bottom + 0.5;
    };
    return {
      text: inside(document.querySelector<HTMLElement>("[data-testid=request-card-text]")!),
      approve: inside(document.querySelector<HTMLElement>("[data-testid=request-answer-approve]")!),
    };
  });
}

test("R6 方案截图：现状 / A / B / C", async () => {
  test.setTimeout(300_000);
  rmSync(OUT, { recursive: true, force: true });
  mkdirSync(OUT, { recursive: true });
  const llm = await startFakeLlm();
  const fx = realCoreFixture(llm.url, [EP]);
  const L = await launchSession(fx.repo, [], { previewOpen: false });
  const p = L.page;
  const send = async (t: string) => { await p.getByTestId("composer-input").fill(t); await p.getByTestId("composer-input").press("Enter"); };
  const results: Record<string, Vis> = {};
  try {
    await p.setViewportSize({ width: 1440, height: 900 });
    await p.locator("[data-testid=episode]").first().click();
    await p.evaluate(() => document.querySelectorAll<HTMLElement>(".banner").forEach((b) => { if (b.textContent?.includes("Code Freeze")) b.hidden = true; }));
    llm.push(toolCalls({ name: "read_status", args: {} }), assistant("停在 05。"));
    await send("这期走到哪了");
    await expect.poll(async () => (await convSnapshot(L, `ep:${EP}`)).entries.filter((e) => e.k === "frame" && e.frame?.t === "turn_finished").length, { timeout: 30_000 }).toBe(1);
    llm.push(toolCalls({ name: "bash", args: { command: "rm stale.tmp", description: "清理临时文件" } }), { ...assistant("清理完了。"), delayMs: 600_000 });
    await send("把临时文件清掉");
    await p.getByTestId("request-answer-approve").waitFor({ timeout: 20_000 });
    for (const [w, h] of [[1440, 900], [1280, 800]] as const) {
      await p.setViewportSize({ width: w, height: h });
      for (const s of SCHEMES) {
        await p.evaluate(`(${apply.toString()})(document, ${JSON.stringify(s)})`);
        await p.waitForTimeout(300);
        results[`${s}-${w}`] = await visible(p);
        await p.getByTestId("center").screenshot({ path: join(OUT, `${s}-${w}.png`) });
      }
    }
    writeFileSync(join(OUT, "visible.json"), JSON.stringify(results, null, 1));
    writeFileSync(join(OUT, "index.html"), indexHtml(results));
  } finally {
    await stubQuit(L, "quit").catch(() => undefined);
    await L.app.close().catch(() => undefined);
    await llm.close();
    fx.cleanup();
  }
});

function indexHtml(results: Record<string, Vis>): string {
  const mark = (ok: boolean, yes: string, no: string) => `<span class="${ok ? "ok" : "bad"}">${ok ? `✓ ${yes}` : `✗ ${no}`}</span>`;
  const row = (w: number) => `<h2>${w === 1440 ? "1440×900" : "1280×800"}</h2><div class="grid">${SCHEMES.map((s) => {
    const v = results[`${s}-${w}`];
    return `<figure><figcaption>${LABEL[s]}<br>${mark(v.text, "命令与危险标记在视野内", "命令被挤出视野")}<br>${mark(v.approve, "「批准」按钮可见", "「批准」按钮被挤出")}</figcaption><a href="${s}-${w}.png"><img src="${s}-${w}.png"></a></figure>`;
  }).join("")}</div>`;
  return `<!doctype html><meta charset="utf-8"><title>D80 R6 方案对照</title>
<style>
  body { font: 14px/1.6 -apple-system, "PingFang SC", sans-serif; margin: 16px 24px; background: #f6f6f4; color: #222; }
  h1 { font-size: 20px; } h2 { font-size: 16px; margin-top: 28px; }
  p, li { color: #444; max-width: 1100px; }
  .grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }
  figure { margin: 0; } figcaption { font-weight: 600; margin-bottom: 4px; }
  img { width: 100%; border: 1px solid #ddd; border-radius: 6px; background: #fff; }
  .ok { color: #1a7f37; font-weight: 400; } .bad { color: #c62828; font-weight: 400; }
</style>
<h1>D80 R6：工具卡一眼可批 · 方案对照</h1>
<p>场景：停机点 05 待审时，ava 要跑 <code>rm stale.tmp</code> 弹卡，「批准」已自动聚焦（回车即批）。硬约束：不点开任何东西，命令与「危险标记」就在眼前。卡面文本是 core 原样渲染的（H-3 不许拆解），三方案只动布局。</p>
<ul>
  <li><b>A 聚焦不滚 + 停机点收成一行</b>：待答区高度不变；聚焦不把卡头滚走；有工具卡时停机点卡只留标题行（处理完卡片再展开）；「告诉它怎么改」藏到「附说明…」后，<b>「拒绝」仍一键</b>。参数原文也收起。</li>
  <li><b>B 待答区让高</b>：有工具卡时待答区最高 60%（现 40%），对话区变矮；停机点卡照旧；<b>「拒绝…」第一次点只展开反馈框</b>，再点才拒（spec 原写法，只想拒的人要点两次）。</li>
  <li><b>C 卡片进对话流</b>：工具卡出现在对话流末尾、正在跑的回合下面（像聊天里的一条消息）；待答区只剩停机点；反馈同 A。</li>
</ul>
<p>标题下的 ✓ / ✗ 是脚本实测：聚焦后卡面文本是否整段落在可见区内、聚焦的「批准」按钮是否可见。点图看原尺寸。</p>
${row(1440)}
${row(1280)}
`;
}
