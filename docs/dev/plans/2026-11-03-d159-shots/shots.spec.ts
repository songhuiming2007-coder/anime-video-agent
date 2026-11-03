// D159：「对话流里系统信息比人写的更显眼」施工后的真实窗口截图（未打包构建 × 临时夹具仓，绝不指向真实 data/）。
// 场景复原自 2026-11-03 人报的 supercell 期现场：启动期三条系统信息（该期还有 N 个会话 / ticket_fixed / 停机点待审）
// 全堆在对话流首部，人的一句话缩成右上角最小气泡。人选方案①：三条全部移出对话流。
// 用法（在 desktop/ 下）：PYTHONDONTWRITEBYTECODE=1 npx playwright test -c ../docs/dev/plans/2026-11-03-d159-shots/shots.config.ts
import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { expect, test } from "../../../../desktop/node_modules/@playwright/test";
import { launchSession, sessionFixture, stubQuit } from "../../../../desktop/e2e/sessionFixtures";
import { sessionScript } from "../../../../desktop/tests/fixtures/session";

const OUT = join(__dirname, "../../../../desktop/out/d159-shots");
const EP = "SESS-A";

const READY = {
  op: "emit",
  frame: {
    t: "ready", episode: EP, scope: "pipeline", continue_status: "new", llm: "ok", degrade_reason: null,
    code_freeze_ok: true, history_count: 0, session_bytes: 0,
    // 现场：该期还有 3 个更早的会话
    other_sessions: [{ sid: "e9c25c52aaaaaaaa" }, { sid: "b1b1b1b1aaaaaaaa" }, { sid: "c2c2c2c2aaaaaaaa" }],
    resume_prompt_tokens: null, resume_prompt_chars: null, context_window: 262000, session_kind: "episode", project: null,
    model: "devin/swe-2", reasoning_effort: null, model_source: "default", effort_source: "default",
  },
};
// 现场：工单提醒（含「另有 N 份工单解析失败」）
const TICKET = { op: "emit", frame: { t: "notice", level: "warn", code: "ticket_fixed", text: "另有 2 份工单解析失败（缺状态行等），没有参与续跑提醒" } };
// 现场：停机点 02.5 待审
const STOPS = { op: "emit", frame: { t: "stop_points", items: [{ type: "02.5" }], turn_id: null } };
const TURN_STARTED = { t: "turn_started", turn_id: "$turn", rid: "$rid" };
const TURN_ENDED = {
  t: "turn_finished", turn_id: "$turn", stopped: "done", llm_calls: 1, tool_calls: 0, tool_executions: 0,
  duplicates_rejected: 0, checkpoints: 0, wrapup: "none", duration_s: 77, prompt_chars: 1, lookups: null,
  prompt_tokens: null, compacted: false, tokens_before: null, tokens_after: null, context_window: null,
};
const ANSWER = {
  t: "assistant", turn_id: "$turn", kind: "answer",
  text: "看完 17-21，「乱」的根子是一个：时间顺序倒了。现在五段走的是 2014（Tia 系列开始）→ 2017（Deal with the devil）→ 2015 年 4 月（Bravely Second）→ 2015 年 11 月（小林武史）——听众刚进入 2017 又被拉回 2015。\n\n最小手术方案——不动各段内容，只换顺序加两处接缝：\n\n- 17、18 整段后移到 21 之后，顺序变为：19 → 20 → 21 → 17 → 18。\n- 17 开头「与此同时」删掉，改为「ryo 的幕后制作仍未停止」。",
};

test("D159 施工后：三条系统信息各归其位，对话流只剩人说的与 agent 做的", async () => {
  mkdirSync(OUT, { recursive: true });
  const fx = sessionFixture([EP]);
  sessionScript(fx.repo, EP, [
    READY,
    TICKET,
    STOPS,
    { op: "serve", on_turn: [TURN_STARTED, ANSWER, TURN_ENDED], on_shutdown: "exit" },
  ]);
  const L = await launchSession(fx.repo, [], { previewOpen: false });
  try {
    await L.page.locator("[data-testid=episode]", { hasText: EP }).first().click();
    // 会话懒启动：第一条消息才 spawn，ready / ticket_fixed / stop_points 随之到达
    await L.page.getByTestId("composer-input").fill("感觉17-21文案写得好乱");
    await L.page.getByTestId("composer-input").press("Enter");
    await expect(L.page.getByTestId("conv-stream")).toContainText("时间顺序倒了", { timeout: 20_000 });
    for (const [name, size] of [["1280x800", { width: 1280, height: 800 }], ["1440x900", { width: 1440, height: 900 }]] as const) {
      await L.page.setViewportSize(size);
      await L.page.waitForTimeout(300);
      await L.page.screenshot({ path: join(OUT, `after-${name}.png`), fullPage: false });
    }
    // 机检：对话流里零系统行，三条信息各在自家位置
    await expect(L.page.locator("[data-testid=conv-row][data-kind=stop]")).toHaveCount(0);
    await expect(L.page.getByTestId("conv-stream").locator("[data-testid=conv-row][data-kind=notice]")).toHaveCount(0);
    await expect(L.page.getByTestId("session-head").getByTestId("other-sessions")).toBeVisible();
    await expect(L.page.getByTestId("handoff-strip").getByTestId("handoff-note")).toHaveCount(1);
  } finally {
    await stubQuit(L, "quit").catch(() => undefined);
    await L.app.close().catch(() => undefined);
    fx.cleanup();
  }
});
