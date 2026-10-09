// D64 余项②：会话头上下文读数「不显眼」的三个方案，真实窗口截图（未打包构建 × 临时夹具仓，绝不指向真实 data/）。
// A 是现状；B / C 是在真实窗口上改 DOM 的原型，只为让人比较位置与样式，不是实现。
// 用法（在 desktop/ 下）：PYTHONDONTWRITEBYTECODE=1 npx playwright test -c ../docs/dev/plans/2026-10-09-context-readout-shots/shots.config.ts
import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { expect, test } from "../../../../desktop/node_modules/@playwright/test";
import type { Page } from "../../../../desktop/node_modules/@playwright/test";
import { launchSession, sessionFixture, stubQuit } from "../../../../desktop/e2e/sessionFixtures";
import { sessionKey, sessionScript } from "../../../../desktop/tests/fixtures/session";

const OUT = join(__dirname, "../../../../desktop/out/d64-shots");
const EP = "2026-10-10-伪恋-橘万里花的进攻哲学";

const ANSWER = `01-topic.md 已落盘。

- **张力**：全员被动内耗的死局 vs 橘万里花绝对主动的进攻哲学
- **模式**：立论（杂谈）
- **时长目标**：2.5-3.5 分钟

选题已定，下一步进入 02 脚本创作。`;

const STARTED = { t: "turn_started", turn_id: "$turn", rid: "$rid" };
const FINISHED = { t: "turn_finished", turn_id: "$turn", stopped: "done", llm_calls: 5, tool_calls: 4, tool_executions: 4, duplicates_rejected: 0, checkpoints: 0, wrapup: "none", duration_s: 38.151, prompt_chars: 153732, prompt_tokens: 110977, lookups: { subs: 3, presence: 0, notes: 0, web: 0 } };
const READY = { op: "emit", frame: { t: "ready", episode: EP, scope: "creative", continue_status: "new", llm: "ok", degrade_reason: null, code_freeze_ok: true, history_count: 0, session_bytes: 0, other_sessions: [], resume_prompt_tokens: null, resume_prompt_chars: null } };

/** 三个方案：A 现状；B 头部徽章（正文色、等宽数字，挪到模式徽章旁）；C 输入框状态行（打字时视线所在处）。 */
const VARIANTS: Record<string, (doc: Document) => void> = {
  "A-现状": () => undefined,
  "B-头部徽章": (doc) => {
    const r = doc.querySelector<HTMLElement>("[data-testid=context-readout]")!;
    r.className = "ui-badge";
    r.style.color = "var(--fg)";
    r.style.fontVariantNumeric = "tabular-nums";
    doc.querySelector("[data-testid=session-scope]")!.after(r);
  },
  "C-输入框状态行": (doc) => {
    const r = doc.querySelector<HTMLElement>("[data-testid=context-readout]")!;
    const c = r.cloneNode(true) as HTMLElement;
    r.style.display = "none";
    c.className = "";
    c.style.color = "var(--fg-muted)";
    c.style.fontVariantNumeric = "tabular-nums";
    doc.querySelector("[data-testid=composer-target]")!.after(c);
    const dot = doc.createElement("span");
    dot.className = "muted";
    dot.textContent = "·";
    c.before(dot);
  },
};

async function shoot(page: Page, name: string): Promise<void> {
  mkdirSync(OUT, { recursive: true });
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.waitForTimeout(300);
  await page.screenshot({ path: join(OUT, `${name}.png`) });
}

test("D64 余项②：上下文读数显眼度三方案", async () => {
  const fx = sessionFixture([EP]);
  const repo = fx.repo;
  sessionKey(repo, "sk-ava-test-key");
  sessionScript(repo, EP, [
    READY,
    { op: "wait", lines: 1 },
    { op: "reply", frames: [STARTED, { t: "assistant", turn_id: "$turn", kind: "answer", text: ANSWER }, FINISHED, { t: "stop_points", items: [], turn_id: "$turn" }] },
    { op: "loop" },
  ]);
  const L = await launchSession(repo, [], { previewOpen: false });
  try {
    const p = L.page;
    await p.waitForSelector("[data-testid=episode]");
    await p.locator("[data-testid=episode]", { hasText: EP }).first().click();
    await expect(p.getByTestId("center")).toHaveAttribute("data-ep", EP);
    await p.getByTestId("composer-input").fill("把句号去了，开始写01");
    await p.getByTestId("composer-send").click();
    await expect(p.getByTestId("context-readout")).toHaveText("上下文 111.0k token");
    for (const [name, apply] of Object.entries(VARIANTS)) {
      await p.reload();
      await p.locator("[data-testid=episode]", { hasText: EP }).first().click();
      await expect(p.getByTestId("context-readout")).toHaveText("上下文 111.0k token");
      await p.evaluate(`(${apply.toString()})(document)`);
      await shoot(p, name);
    }
  } finally {
    await stubQuit(L, "quit").catch(() => undefined);
    await L.app.close().catch(() => undefined);
    fx.cleanup();
  }
});
