// D45 施工步骤截图（方案 B，真实实现，不是注入原型）：未打包构建 × 临时夹具仓（绝不指向真实 data/）。
// 夹具仓复制的是工作树里的真 pipeline/（cli.py 的 /list-sessions、/delete-session 真跑），只有 protocol.py 是剧本驱动的假进程。
// 只跑本文件：PYTHONDONTWRITEBYTECODE=1 npx playwright test -c ../docs/dev/plans/2026-10-08-session-management-options/options.config.ts steps
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test } from "../../../../desktop/node_modules/@playwright/test";
import type { Page } from "../../../../desktop/node_modules/@playwright/test";
import { launchSession, sessionFixture, stubQuit } from "../../../../desktop/e2e/sessionFixtures";
import { fixtureWrite, sessionKey, sessionScript } from "../../../../desktop/tests/fixtures/session";

const OUT = join(__dirname, "../../../../desktop/out/d45-shots");
const SIZES: [number, number][] = [
  [1440, 900],
  [1280, 800],
];
const EP = "2026-09-21-东京喰种-雾岛董香人物志-二";
const SID_NOW = "aaaaaaaaaaaaaaa1";
const SID_OLD = "bbbbbbbbbbbbbbb2";
const SID_LONG = "ccccccccccccccc3";
const SID_EMPTY = "ddddddddddddddd4";

/** 会话日志夹具：与真实董香二期同形（一个很长、一个没有用户消息）。 */
function sessionLog(): string {
  const rec = (o: Record<string, unknown>) => JSON.stringify(o) + "\n";
  const start = (sid: string, ts: string) => rec({ k: "session_start", sid, seq: 0, ts, schema: 1, episode: EP, scope_mode: "creative", pid: 1, resident_sha256: "x" });
  const msg = (sid: string, seq: number, role: string, content: string, ts: string) => rec({ k: "msg", sid, seq, ts, origin: role, message: { role, content } });
  return [
    start(SID_EMPTY, "2026-09-26T10:54:07Z"),
    start(SID_OLD, "2026-09-26T10:55:44Z"),
    msg(SID_OLD, 1, "user", "直接调整02-script.draft.md和02-script.md", "2026-09-26T10:55:45Z"),
    msg(SID_OLD, 2, "assistant", "好", "2026-10-08T08:07:01Z"),
    start(SID_LONG, "2026-09-26T11:02:22Z"),
    msg(SID_LONG, 1, "user", "帮我看看 05 为什么排片不对，顺便把第 3 段的集字段补上，还有第 7 段的查询也改一下", "2026-09-26T11:02:23Z"),
    msg(SID_LONG, 2, "assistant", "好", "2026-09-26T11:03:00Z"),
    start(SID_NOW, "2026-10-08T09:00:00Z"),
    msg(SID_NOW, 1, "user", "S01E09 董香有哪些戏", "2026-10-08T09:00:01Z"),
    msg(SID_NOW, 2, "assistant", "08:27 雏实搬来同住……", "2026-10-08T09:00:05Z"),
  ].join("");
}

const ANSWER = "S01E09 里董香的戏按字幕查到这几处：08:27 雏实搬来同住、09:24「董香时不时会发呆」、17:31 去看鹦鹉后闪回父亲、20:00 雏实说头发是董香剪的。";
function script(sid: string): unknown[] {
  return [
    { op: "emit", frame: { t: "ready", sid, episode: EP, scope: "creative", continue_status: "new", llm: "ok", degrade_reason: null, code_freeze_ok: true, history_count: 0, session_bytes: 0, other_sessions: [] } },
    {
      op: "serve",
      on_shutdown: "exit",
      on_turn: [
        { t: "turn_started", sid, turn_id: "$turn", rid: "$rid" },
        { t: "assistant", sid, turn_id: "$turn", kind: "answer", text: ANSWER },
        { t: "turn_finished", sid, turn_id: "$turn", stopped: "done", llm_calls: 1, tool_calls: 0, tool_executions: 0, duplicates_rejected: 0, checkpoints: 0, wrapup: "none", duration_s: 2, prompt_chars: 1 },
        { t: "stop_points", sid, items: [], turn_id: "$turn" },
      ],
    },
  ];
}

async function shoot(page: Page, name: string): Promise<void> {
  mkdirSync(OUT, { recursive: true });
  for (const [w, h] of SIZES) {
    await page.setViewportSize({ width: w, height: h });
    await page.waitForTimeout(300);
    await page.screenshot({ path: join(OUT, `${name}-${w}x${h}.png`) });
  }
}

test("S1：侧栏会话子列表 / 悬停出删除 / 新会话 / 进入旧会话", async () => {
  const fx = sessionFixture([EP, "2026-08-09-东京喰种-雾岛董香人物志-一"]);
  const repo = fx.repo;
  sessionKey(repo, "sk-ava-test-key");
  fixtureWrite(repo.root, `data/episodes/${EP}/session.jsonl`, sessionLog());
  sessionScript(repo, EP, script(SID_NOW));
  const L = await launchSession(repo, [], { previewOpen: false });
  try {
    const p = L.page;
    await p.waitForSelector("[data-testid=episode]");
    await p.locator("[data-testid=episode]", { hasText: EP }).first().click();
    await expect(p.getByTestId("center")).toHaveAttribute("data-ep", EP);
    await p.getByTestId("session-list").waitFor();
    await expect(p.getByTestId("session-row")).toHaveCount(4);
    // 刚打开：没有活会话 →「＋ 新会话」是当前
    await expect(p.getByTestId("session-new")).toHaveAttribute("aria-current", "true");
    await p.mouse.move(0, 0);
    await shoot(p, "s1a-list-idle");

    // 发一条消息：假进程自报 sid = SID_NOW → 列表标出它
    await p.getByTestId("composer-input").fill("S01E09 董香有哪些戏");
    await p.getByTestId("composer-send").click();
    await p.locator("[data-testid=conv-row][data-kind=footer]").first().waitFor();
    await expect(p.locator(`[data-testid=session-row][data-sid=${SID_NOW}]`)).toHaveAttribute("data-live", "1");
    await p.locator(`[data-testid=session-row][data-sid=${SID_LONG}]`).hover();
    await shoot(p, "s1b-live-hover-delete");

    // 进入旧会话（剧本换成该会话自报的 sid，假进程启动时才读剧本）
    sessionScript(repo, EP, script(SID_OLD));
    await p.locator(`[data-testid=session-row][data-sid=${SID_OLD}] button.ep-session`).click();
    await expect(p.locator(`[data-testid=session-row][data-sid=${SID_OLD}]`)).toHaveAttribute("data-live", "1");
    await p.mouse.move(0, 0);
    await shoot(p, "s1c-entered-old");

    // 新会话：对话区清空，「＋ 新会话」成为当前
    await p.getByTestId("session-new").click();
    await expect(p.getByTestId("session-new")).toHaveAttribute("aria-current", "true");
    await expect(p.locator("[data-testid=conv-row][data-kind=answer]")).toHaveCount(0);
    await shoot(p, "s1d-fresh");
    writeFileSync(join(OUT, "s1-ok.txt"), "S1 全部断言通过\n");
  } finally {
    await stubQuit(L, "quit").catch(() => undefined);
    await L.app.close().catch(() => undefined);
    fx.cleanup();
  }
});
