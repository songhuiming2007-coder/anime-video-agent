// D45 会话管理界面原型（附件，不属常规 e2e；只在人要复现选型证据时手动跑）。
// 未打包构建 × 临时夹具仓（绝不指向真实 data/）× 1440×900 / 1280×800。原型是运行时注入的 DOM + 样式，
// 只用现有设计系统的类（ui-row / ui-popover / ui-btn / ui-badge）与 token，不是实现。
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test } from "../../../../desktop/node_modules/@playwright/test";
import type { Page } from "../../../../desktop/node_modules/@playwright/test";
import { launchSession, sessionFixture, stubQuit } from "../../../../desktop/e2e/sessionFixtures";
import { sessionKey, sessionScript } from "../../../../desktop/tests/fixtures/session";

const OUT = join(__dirname, "../../../../desktop/out/d45-shots"); // desktop/out 已被 .gitignore 排除
const SIZES: [number, number][] = [
  [1440, 900],
  [1280, 800],
];

const ANSWER =
  "S01E09 里董香的戏按字幕查到这几处：08:27 雏实搬来同住、09:24「董香时不时会发呆」、17:31 去看鹦鹉后闪回父亲（19:11「董香你是姐姐 要好好教导弟弟」）、20:00 雏实说头发是董香剪的。依子的便当在 S01E04 03:52，不在本期范围。";
const READY = (ep: string) => ({
  op: "emit",
  frame: { t: "ready", episode: ep, scope: "creative", continue_status: "new", llm: "ok", degrade_reason: null, code_freeze_ok: true, history_count: 0, session_bytes: 0, other_sessions: [] },
});
const TURN = [
  { t: "turn_started", turn_id: "$turn", rid: "$rid" },
  { t: "tool", turn_id: "$turn", phase: "start", index: 0, name: "search_notes", summary: "[字幕] S01E09", ok: null, observation: null, duplicate: false },
  { t: "tool", turn_id: "$turn", phase: "end", index: 0, name: "search_notes", summary: "", ok: true, observation: null, duplicate: false },
  { t: "assistant", turn_id: "$turn", kind: "answer", text: ANSWER },
  { t: "turn_finished", turn_id: "$turn", stopped: "done", llm_calls: 2, tool_calls: 1, tool_executions: 1, duplicates_rejected: 0, checkpoints: 0, wrapup: "none", duration_s: 3, prompt_chars: 1 },
  { t: "stop_points", items: [], turn_id: "$turn" },
];

/** 原型里展示的会话（与真实董香二期同量级：一个很长、一个没有 assistant 消息）。 */
const ROWS = [
  { title: "S01E09 董香有哪些戏", meta: "2 条 · 刚刚", live: true },
  { title: "直接调整02-script.draft.md和02-script.md", meta: "161 条 · 今天 16:07", live: false },
  { title: "帮我看看 05 为什么排片不对，顺便把第 3 段的集字段补上，还有第 7 段的查询也改一下", meta: "7 条 · 9月26日", live: false },
  { title: "（没有用户消息）", meta: "0 条 · 9月26日", live: false, empty: true },
];

type Variant = "A" | "A-open" | "B" | "C";

function applyProto(arg: { v: Variant; rows: typeof ROWS }) {
  const { v, rows } = arg;
  const q = (s: string) => document.querySelector(s) as HTMLElement;
  const mk = (tag: string, cls: string, html = "") => {
    const e = document.createElement(tag);
    e.className = cls;
    e.innerHTML = html;
    return e;
  };
  const esc = (s: string) => s.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" })[c] as string);
  const css = `
  .d45-pop { position: absolute; top: calc(100% + 4px); left: var(--space-4); width: 380px; display: flex; flex-direction: column; gap: 2px; }
  .d45-anchor { position: relative; }
  .d45-row { min-height: 44px; align-items: center; }
  .d45-row .d45-text { min-width: 0; flex: 1; display: flex; flex-direction: column; gap: 2px; padding-block: var(--space-1); }
  .d45-row .d45-meta { color: var(--fg-muted); font-size: var(--text-sm); }
  .d45-row .d45-del { visibility: hidden; color: var(--fg-muted); }
  .d45-row:hover .d45-del, .d45-row.d45-hover .d45-del { visibility: visible; }
  .d45-row.d45-empty .ui-row-title { color: var(--fg-muted); }
  .d45-new { color: var(--fg-muted); }
  .d45-sep { height: 1px; margin: var(--space-1) var(--space-2); background: var(--border); }
  .d45-sub { display: flex; flex-direction: column; gap: 1px; margin: 2px 0 var(--space-2) var(--space-4); padding-left: var(--space-2); border-left: 1px solid var(--border); }
  .d45-sub .ui-row { min-height: 30px; font-size: var(--text-sm); }
  .d45-tabs { flex: none; display: flex; align-items: stretch; gap: 2px; padding: var(--space-1) max(var(--space-4), calc((100% - 880px) / 2)) 0; border-bottom: 1px solid var(--border); overflow-x: auto; }
  .d45-tab { display: flex; align-items: center; gap: var(--space-1); max-width: 200px; height: 32px; padding: 0 var(--space-1) 0 var(--space-3); border: 1px solid transparent; border-bottom: 0; border-radius: var(--radius-lg) var(--radius-lg) 0 0; background: transparent; color: var(--fg-muted); font-size: var(--text-sm); cursor: pointer; flex: none; }
  .d45-tab[aria-current="true"] { background: var(--bg-panel); border-color: var(--border); color: var(--fg); margin-bottom: -1px; }
  .d45-tab .d45-tt { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  `;
  const sheet = new CSSStyleSheet();
  sheet.replaceSync(css);
  document.adoptedStyleSheets = [...document.adoptedStyleSheets, sheet];

  const head = q("[data-testid=session-head]");
  const delBtn = `<button class="ui-btn ui-btn--ghost ui-btn--sm d45-del" aria-label="删除会话">删除</button>`;
  const rowHtml = (r: (typeof rows)[number], i: number) =>
    `<div class="ui-row d45-row${r.empty ? " d45-empty" : ""}${i === 2 ? " d45-hover" : ""}" ${r.live ? 'aria-current="true"' : ""}>` +
    `<span class="d45-text"><span class="ui-row-title">${esc(r.title)}</span><span class="d45-meta">${r.live ? "当前 · " : ""}${esc(r.meta)}</span></span>${delBtn}</div>`;

  if (v === "A" || v === "A-open") {
    // A：会话头最左侧「会话 ▾」，点开是浮层列表；「＋ 新会话」在列表顶部
    const wrap = mk("span", "d45-anchor");
    wrap.appendChild(mk("button", "ui-btn ui-btn--ghost ui-btn--sm", `会话 · ${rows.length} ▾`));
    head.insertBefore(wrap, head.firstChild);
    head.style.overflow = "visible";
    if (v === "A-open") {
      const pop = mk("div", "ui-popover d45-pop");
      pop.innerHTML =
        `<div class="ui-popover-title">本期会话</div>` +
        `<button class="ui-row d45-new">＋ 新会话</button><div class="d45-sep"></div>` +
        rows.map(rowHtml).join("");
      head.parentElement!.style.position = "relative";
      wrap.appendChild(pop);
    }
  } else if (v === "B") {
    // B：左侧期列表里，当前期下面展开会话子列表
    const cur = q("[data-testid=episode][aria-current=true]");
    const sub = mk("div", "d45-sub");
    sub.innerHTML =
      `<button class="ui-row d45-new">＋ 新会话</button>` +
      rows
        .map(
          (r, i) =>
            `<div class="ui-row d45-row${r.empty ? " d45-empty" : ""}${i === 2 ? " d45-hover" : ""}" ${r.live ? 'aria-current="true"' : ""}>` +
            `<span class="ui-row-title">${esc(r.title)}</span>${delBtn}</div>`,
        )
        .join("");
    cur.insertAdjacentElement("afterend", sub);
  } else {
    // C：对话区顶部标签条；「＋」开新会话，每个标签带 ×
    const tabs = mk("div", "d45-tabs");
    tabs.innerHTML =
      rows
        .map(
          (r) =>
            `<div class="d45-tab" ${r.live ? 'aria-current="true"' : ""} title="${esc(r.title)}"><span class="d45-tt">${esc(r.title)}</span>` +
            `<button class="ui-btn ui-btn--ghost ui-btn--sm ui-btn--icon" aria-label="删除会话">×</button></div>`,
        )
        .join("") + `<button class="ui-btn ui-btn--ghost ui-btn--sm ui-btn--icon" aria-label="新会话">＋</button>`;
    head.insertAdjacentElement("afterend", tabs);
  }
}

/** 底线量测：注入的原型元素有没有压住别的文本、有没有被裁掉一部分（不用来证明好用）。 */
function overlapCheck() {
  const proto = [...document.querySelectorAll(".d45-pop, .d45-sub, .d45-tabs, .d45-anchor > button")] as HTMLElement[];
  const out: unknown[] = [];
  for (const p of proto) {
    const r = p.getBoundingClientRect();
    if (r.right > window.innerWidth + 0.5 || r.bottom > window.innerHeight + 0.5 || r.left < -0.5) out.push({ kind: "出视口", cls: p.className, r: [r.left, r.top, r.right, r.bottom].map(Math.round) });
  }
  return out;
}

async function openEp(page: Page, ep: string): Promise<void> {
  await page.locator("[data-testid=episode]", { hasText: ep }).first().click();
  await expect(page.getByTestId("center")).toHaveAttribute("data-ep", ep);
}

test("会话管理三个原型：同一状态（董香二期，当前会话刚答完一轮）", async () => {
  const ep = "2026-09-21-东京喰种-雾岛董香人物志-二";
  const fx = sessionFixture([ep, "2026-08-09-东京喰种-雾岛董香人物志-一"]);
  const repo = fx.repo;
  sessionKey(repo, "sk-ava-test-key");
  sessionScript(repo, ep, [READY(ep), { op: "serve", on_turn: TURN }]);
  const report: Record<string, unknown> = {};
  for (const v of ["A", "A-open", "B", "C"] as Variant[]) {
    const L = await launchSession(repo, [], { previewOpen: false });
    try {
      await L.page.waitForSelector("[data-testid=episode]");
      await openEp(L.page, ep);
      await L.page.getByTestId("composer-input").fill("S01E09 董香有哪些戏");
      await L.page.getByTestId("composer-send").click();
      await L.page.locator("[data-testid=conv-row][data-kind=footer]").first().waitFor();
      await L.page.evaluate(applyProto, { v, rows: ROWS });
      mkdirSync(OUT, { recursive: true });
      for (const [w, h] of SIZES) {
        await L.page.setViewportSize({ width: w, height: h });
        await L.page.mouse.move(0, 0);
        await L.page.waitForTimeout(400);
        const name = `proto-${v}-${w}x${h}`;
        await L.page.screenshot({ path: join(OUT, `${name}.png`) });
        report[name] = await L.page.evaluate(overlapCheck);
      }
    } finally {
      await stubQuit(L, "quit").catch(() => undefined);
      await L.app.close().catch(() => undefined);
    }
  }
  writeFileSync(join(OUT, "report.json"), JSON.stringify(report, null, 1));
  fx.cleanup();
});
