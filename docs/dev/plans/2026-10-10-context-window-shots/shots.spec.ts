// D62 人裁决⑤（2026-10-10）：读数带分母——标称窗口、写成「102k / 128k」——的真实窗口截图（未打包构建 × 临时夹具仓，绝不指向真实 data/）。
// 人已定形式，这里只拍实现给人过目；两档窗口：gpt-4o 128k、gemini-3.8-flash-high 1M。
// 用法（在 desktop/ 下）：PYTHONDONTWRITEBYTECODE=1 npx playwright test -c ../docs/dev/plans/2026-10-10-context-window-shots/shots.config.ts
import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { expect, test } from "../../../../desktop/node_modules/@playwright/test";
import { launchSession, sessionFixture, stubQuit } from "../../../../desktop/e2e/sessionFixtures";
import { sessionKey, sessionScript } from "../../../../desktop/tests/fixtures/session";

const OUT = join(__dirname, "../../../../desktop/out/d62-window-shots");
const EP = "2026-10-10-伪恋-橘万里花的进攻哲学";
const ANSWER = "01-topic.md 已落盘。选题已定，下一步进入 02 脚本创作。";
const STARTED = { t: "turn_started", turn_id: "$turn", rid: "$rid" };

for (const [name, window, expected] of [
  ["gpt-4o-128k", 128_000, "上下文 111.0k / 128k token"],
  ["gemini-1M", 1_048_576, "上下文 111.0k / 1.0M token"],
] as const) {
  test(`D62 读数分母：${name}`, async () => {
    const finished = { t: "turn_finished", turn_id: "$turn", stopped: "done", llm_calls: 5, tool_calls: 4, tool_executions: 4, duplicates_rejected: 0, checkpoints: 0, wrapup: "none", duration_s: 38.151, prompt_chars: 153732, prompt_tokens: 110977, lookups: { subs: 3, presence: 0, notes: 0, web: 0 }, compacted: false, tokens_before: null, tokens_after: null, context_window: window };
    const ready = { op: "emit", frame: { t: "ready", episode: EP, scope: "creative", continue_status: "new", llm: "ok", degrade_reason: null, code_freeze_ok: true, history_count: 0, session_bytes: 0, other_sessions: [], resume_prompt_tokens: null, resume_prompt_chars: null, context_window: window } };
    const fx = sessionFixture([EP]);
    sessionKey(fx.repo, "sk-ava-test-key");
    sessionScript(fx.repo, EP, [
      ready,
      { op: "wait", lines: 1 },
      { op: "reply", frames: [STARTED, { t: "assistant", turn_id: "$turn", kind: "answer", text: ANSWER }, finished, { t: "stop_points", items: [], turn_id: "$turn" }] },
      { op: "loop" },
    ]);
    const L = await launchSession(fx.repo, [], { previewOpen: false });
    try {
      const p = L.page;
      await p.waitForSelector("[data-testid=episode]");
      await p.locator("[data-testid=episode]", { hasText: EP }).first().click();
      await expect(p.getByTestId("center")).toHaveAttribute("data-ep", EP);
      await p.getByTestId("composer-input").fill("开始写01");
      await p.getByTestId("composer-send").click();
      await expect(p.getByTestId("context-readout")).toHaveText(expected);
      mkdirSync(OUT, { recursive: true });
      await p.setViewportSize({ width: 1280, height: 800 });
      await p.waitForTimeout(300);
      await p.screenshot({ path: join(OUT, `${name}.png`) });
    } finally {
      await stubQuit(L, "quit").catch(() => undefined);
      await L.app.close().catch(() => undefined);
      fx.cleanup();
    }
  });
}
