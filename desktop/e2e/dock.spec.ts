// D98（人选方案 C）：AVA 窗口不在前台时，等人的事件让 Dock 角标 +1 并持续弹跳；窗口回到前台即清零、停跳。
// Dock 本身截不到：未打包构建里 main 把每个 Dock 动作记进 globalThis.__avaTestDock，这里断言动作序列。
// 系统焦点是否授予不归 app 控制（N44），所以「回到前台」直接发窗口的 focus 事件，不依赖真实抢焦点。
import { expect, test, type Page } from "@playwright/test";
import { launchSession, sessionFixture, stubQuit } from "./sessionFixtures";
import { sessionScript } from "../tests/fixtures/session";

const READY = (ep: string) => ({ op: "emit", frame: { t: "ready", episode: ep, scope: "pipeline", continue_status: "new", llm: "ok", degrade_reason: null, code_freeze_ok: true, history_count: 0, session_bytes: 0, other_sessions: [], resume_prompt_tokens: null, resume_prompt_chars: null, context_window: null, session_kind: "episode", project: null, model: "fake", reasoning_effort: null, model_source: "default", effort_source: "default" } });

const openEp = async (page: Page, ep: string): Promise<void> => {
  await page.locator("[data-testid=episode]", { hasText: ep }).first().click();
  await expect(page.getByTestId("center")).toHaveAttribute("data-ep", ep);
};

test("D98 窗口不在前台：审批卡 → 角标 1 + 弹跳；回到前台 → 清角标 + 停跳", async () => {
  const fx = sessionFixture(["DOCK-A"]);
  const L = await launchSession(fx.repo);
  try {
    sessionScript(fx.repo, "DOCK-A", [
      READY("DOCK-A"),
      {
        op: "serve",
        on_turn: [{ t: "turn_started", turn_id: "$turn", rid: "$rid" }, { t: "request", request_id: "qd", kind: "tool_call", turn_id: "$turn", title: "bash", card_text: "…", fields: { tool: "bash", args: {} }, options: ["approve", "reject"], feedback_allowed: true }],
        on_answer: [{ t: "request_closed", request_id: "$request_id", reason: "answered", decision: "$decision", rid: "$rid" }],
      },
    ]);
    await openEp(L.page, "DOCK-A");
    await L.app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].blur());
    await expect.poll(() => L.app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].isFocused())).toBe(false);
    const dock = () => L.app.evaluate(() => (globalThis as unknown as { __avaTestDock?: unknown[] }).__avaTestDock ?? []);
    expect(await dock()).toEqual([]);

    await L.page.getByTestId("composer-input").fill("裁一下图");
    await L.page.getByTestId("composer-input").press("Enter");
    await expect(L.page.getByTestId("request-card")).toBeVisible();
    await expect.poll(dock).toEqual([{ do: "badge", text: "1" }, { do: "bounce" }]);

    await L.app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].emit("focus"));
    await expect.poll(dock).toEqual([{ do: "badge", text: "1" }, { do: "bounce" }, { do: "badge", text: "" }, { do: "cancelBounce" }]);
  } finally {
    await stubQuit(L, "quit").catch(() => undefined);
    await L.app.close().catch(() => undefined);
    fx.cleanup();
  }
});
