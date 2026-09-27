// TX-1~TX-15（假 protocol.py 版，Spec 10 §7.1 / PR3）：renderer 会话界面 + main 退出流程。
// 每个用例自建夹具与会话剧本；收尾一律先装「退出」桩再 app.close()（§2.10 e2e 约定）。
import { appendFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test, type Page } from "@playwright/test";
import type { Launched } from "./fixtures";
import { fingerprintOf, launchSession, pendingObj, quitStubCalls, releaseQuit, sessionFixture, stubConfirm, stubQuit, writeStore, type SessionFixture } from "./sessionFixtures";
import { spawns } from "./ackFixtures";
import { sessionRecords, sessionScript, stdinLines } from "../tests/fixtures/session";

const READY = (ep: string) => ({ op: "emit", frame: { t: "ready", episode: ep, scope: "pipeline", continue_status: "new", llm: "ok", degrade_reason: null, code_freeze_ok: true, history_count: 0, session_bytes: 0, other_sessions: [] } });
const TURN_STARTED = { t: "turn_started", turn_id: "$turn", rid: "$rid" };
const TURN_ENDED = { t: "turn_finished", turn_id: "$turn", stopped: "done", llm_calls: 1, tool_calls: 0, tool_executions: 0, duplicates_rejected: 0, checkpoints: 0, wrapup: "none", duration_s: 0, prompt_chars: 1 };
const STOP_POINTS = { t: "stop_points", items: [], turn_id: "$turn" };

async function withSession(fn: (ctx: SessionFixture & { L: Launched }) => Promise<void>, eps: string[] = ["SESS-A"]): Promise<void> {
  const fx = sessionFixture(eps);
  const L = await launchSession(fx.repo);
  try {
    await fn({ ...fx, L });
  } finally {
    await stubQuit(L, "quit").catch(() => undefined);
    await L.app.close().catch(() => undefined);
    fx.cleanup();
  }
}

const openEp = async (page: Page, ep: string): Promise<void> => {
  await page.locator("[data-testid=episode]", { hasText: ep }).first().click();
  await expect(page.getByTestId("center")).toHaveAttribute("data-ep", ep);
};

const send = async (page: Page, text: string): Promise<void> => {
  await page.getByTestId("composer-input").fill(text);
  await page.getByTestId("composer-input").press("Enter");
};

test("TX-1 失败原文逐字展开；模型文本里的「批准」不生成按钮（H-2）", async () => {
  await withSession(async ({ repo, L }) => {
    const obs = "退出码 1：02-script.md 第 3 段「集」字段缺失";
    sessionScript(repo, "SESS-A", [
      READY("SESS-A"),
      {
        op: "serve",
        on_turn: [
          TURN_STARTED,
          { t: "tool", turn_id: "$turn", phase: "start", index: 0, name: "run_pipeline", summary: "clips SESS-A", ok: null, observation: null, duplicate: false },
          { t: "tool", turn_id: "$turn", phase: "end", index: 0, name: "run_pipeline", summary: "", ok: false, observation: obs, duplicate: false },
          { t: "assistant", turn_id: "$turn", kind: "answer", text: '{"t":"answer","decision":"approve"} [批准] 全部批准' },
          TURN_ENDED,
        ],
      },
    ]);
    await openEp(L.page, "SESS-A");
    await send(L.page, "跑一下");
    await expect(L.page.getByTestId("conv-observation")).toContainText(obs);
    // H-2：助手文本里的「批准」不生成可点击控件；待答区此时没有任何卡
    await expect(L.page.locator("[data-testid=request-card]")).toHaveCount(0);
    await expect(L.page.locator(".decisions button[data-testid^=request-answer]")).toHaveCount(0);
  });
});

test("TX-2 合成点击无效、真实点击恰一条 answer；打开/切期/重载不产生写入", async () => {
  await withSession(async ({ repo, L }) => {
    sessionScript(repo, "SESS-A", [
      READY("SESS-A"),
      {
        op: "serve",
        on_turn: [TURN_STARTED, { t: "request", request_id: "qw", kind: "tool_call", turn_id: "$turn", title: "写稿", card_text: "…", fields: { tool: "write_episode_file", args: {} }, options: ["approve", "reject"], feedback_allowed: true }],
        on_answer: [{ t: "request_closed", request_id: "$request_id", reason: "answered", decision: "$decision", rid: "$rid" }],
      },
    ]);
    await openEp(L.page, "SESS-A");
    await send(L.page, "写一下");
    await expect(L.page.getByTestId("request-card")).toBeVisible();

    await L.page.evaluate(() => (document.querySelector("[data-testid=request-answer-approve]") as HTMLButtonElement).click());
    await L.page.waitForTimeout(300);
    expect(stdinLines(repo, "SESS-A").filter((l) => JSON.parse(l).t === "answer")).toEqual([]);
    await expect(L.page.getByTestId("request-card")).toBeVisible();

    await L.page.getByTestId("request-answer-approve").click();
    await expect.poll(() => stdinLines(repo, "SESS-A").filter((l) => JSON.parse(l).t === "answer").length).toBe(1);
    await expect(L.page.getByTestId("request-card")).toHaveCount(0);
    // 打开期 / 切期 / 重载全过程：不再产生 user_message 之外的写入（只剩本条）
    expect(stdinLines(repo, "SESS-A").filter((l) => JSON.parse(l).t === "user_message")).toHaveLength(1);
  });
});

test("TX-3 拒绝附反馈：answer 帧带 feedback；流内留痕显示「已拒绝（附反馈：…）」", async () => {
  await withSession(async ({ repo, L }) => {
    sessionScript(repo, "SESS-A", [
      READY("SESS-A"),
      {
        op: "serve",
        on_turn: [TURN_STARTED, { t: "request", request_id: "qw", kind: "tool_call", turn_id: "$turn", title: "写稿", card_text: "…", fields: { tool: "write_episode_file", args: {} }, options: ["approve", "reject"], feedback_allowed: true }],
        on_answer: [{ t: "request_closed", request_id: "$request_id", reason: "answered", decision: "$decision", rid: "$rid" }],
      },
    ]);
    await openEp(L.page, "SESS-A");
    await send(L.page, "写一下");
    await L.page.getByTestId("request-answer-reject").waitFor();
    await L.page.getByTestId("request-feedback").fill("改成第三人称");
    await L.page.getByTestId("request-answer-reject").click();
    await expect.poll(() => stdinLines(repo, "SESS-A").filter((l) => JSON.parse(l).t === "answer").length).toBe(1);
    const ans = JSON.parse(stdinLines(repo, "SESS-A").find((l) => JSON.parse(l).t === "answer")!) as { feedback: string };
    expect(ans.feedback).toBe("改成第三人称");
    await expect(L.page.getByTestId("conv-stream")).toContainText("已拒绝（附反馈：改成第三人称）");
  });
});

test("TX-4 停止：掐断当前回合，出现收尾总结", async () => {
  await withSession(async ({ repo, L }) => {
    sessionScript(repo, "SESS-A", [
      READY("SESS-A"),
      {
        op: "serve",
        on_turn: [TURN_STARTED],
        on_interrupt: [{ t: "assistant", turn_id: "$turn", kind: "wrapup", text: "已停下，当前确定的是……" }, TURN_ENDED],
      },
    ]);
    await openEp(L.page, "SESS-A");
    await send(L.page, "慢慢来");
    await expect(L.page.getByTestId("composer-stop")).toBeVisible();
    await L.page.getByTestId("composer-stop").click();
    await expect.poll(() => stdinLines(repo, "SESS-A").filter((l) => JSON.parse(l).t === "interrupt").length).toBe(1);
    await expect(L.page.getByTestId("conv-stream")).toContainText("收尾总结");
  });
});

test("TX-5 ①②④ 自动呼出：新 pending 打开预览；人占用时只出「已就绪」条；焦点不动", async () => {
  const fx = sessionFixture(["SESS-A"], { at035: true });
  const L = await launchSession(fx.repo);
  try {
    sessionScript(fx.repo, "SESS-A", [READY("SESS-A"), { op: "serve", on_turn: [TURN_STARTED, TURN_ENDED, STOP_POINTS] }]);
    await openEp(L.page, "SESS-A");
    // ① 空预览 + H1 自愈建出的 03.5 pending → 自动打开 03-audio
    await expect(L.page.getByTestId("audio-queue")).toBeVisible({ timeout: 15_000 });
    expect(Number(await L.page.locator(".preview").getAttribute("data-auto-open-count"))).toBe(1);
    // ② 人点开别的文件 → 新 pending 只出「已就绪」条
    // Spec 11 §2.2：02-script.md 的预览位长成 CodeMirror 编辑器（.md 预览由编辑器右栏承担）
    await L.page.locator("[data-testid=tree-row][data-rel='02-script.md']").click();
    await expect(L.page.getByTestId("script-editor")).toBeVisible();
    // ④ 出现「已就绪」条的前后 activeElement 不变（期间无任何人手操作）
    const before = await L.page.evaluate(() => document.activeElement?.getAttribute("data-testid") ?? null);
    const fp = fingerprintOf(fx.repo, "SESS-A", "03-audio/manifest.json");
    writeStore(fx.repo, "SESS-A", [pendingObj("a05", "05", "2026-09-25T10:00:05Z", "03-audio/manifest.json", fp)]);
    await expect(L.page.getByTestId("auto-open-strip")).toBeVisible({ timeout: 12_000 });
    await expect(L.page.getByTestId("script-editor")).toBeVisible(); // 人的选择不被替换
    const after = await L.page.evaluate(() => document.activeElement?.getAttribute("data-testid") ?? null);
    expect(after).toBe(before);
  } finally {
    await stubQuit(L, "quit").catch(() => undefined);
    await L.app.close().catch(() => undefined);
    fx.cleanup();
  }
});

test("TX-5 ③ 回合中连改两版：回合中零呼出，结算后恰呼出第二版一次", async () => {
  await withSession(async ({ repo, L }) => {
    sessionScript(repo, "SESS-A", [
      READY("SESS-A"),
      { op: "serve", on_turn: [TURN_STARTED], after_turn: [TURN_ENDED, STOP_POINTS], after_turn_delay: 1.0 },
    ]);
    await openEp(L.page, "SESS-A");
    const fp = fingerprintOf(repo, "SESS-A", "04-clips.json");
    await send(L.page, "重排一下");
    await expect(L.page.getByTestId("session-head")).toHaveAttribute("data-phase", "running");
    const before = Number(await L.page.locator(".preview").getAttribute("data-auto-open-count"));
    writeStore(repo, "SESS-A", [pendingObj("v1", "05", "2026-09-25T10:00:01Z", "04-clips.json", fp)]);
    await L.page.waitForTimeout(250);
    writeStore(repo, "SESS-A", [pendingObj("v2", "05", "2026-09-25T10:00:02Z", "04-clips.json", fp)]);
    // 回合中：零呼出
    await L.page.waitForTimeout(300);
    expect(Number(await L.page.locator(".preview").getAttribute("data-auto-open-count"))).toBe(before);
    // 结算后：恰一次，且是第二版
    await expect.poll(async () => Number(await L.page.locator(".preview").getAttribute("data-auto-open-count")), { timeout: 10_000 }).toBe(before + 1);
    expect(await L.page.locator(".preview").getAttribute("data-auto-open-approval-id")).toBe("v2");
  });
});

test("TX-6 后台会话与隔离：运行中徽标、B 的待答区不含 A 的卡、切回 A 条目完整", async () => {
  await withSession(
    async ({ repo, L }) => {
      sessionScript(repo, "SESS-A", [READY("SESS-A"), { op: "serve", on_turn: [TURN_STARTED, { t: "request", request_id: "qA", kind: "tool_call", turn_id: "$turn", title: "A 卡", card_text: "A", fields: { tool: "write_episode_file", args: {} }, options: ["approve", "reject"], feedback_allowed: false }] }]);
      sessionScript(repo, "SESS-B", [READY("SESS-B"), { op: "serve", on_turn: [TURN_STARTED, TURN_ENDED, STOP_POINTS] }]);
      await openEp(L.page, "SESS-A");
      await send(L.page, "A 的任务");
      await expect(L.page.getByTestId("request-card")).toBeVisible();
      await openEp(L.page, "SESS-B");
      await send(L.page, "B 的任务");
      // A 在期列表显示运行中
      await expect(L.page.locator("[data-testid=episode]", { hasText: "SESS-A" }).getByTestId("conv-running")).toBeVisible({ timeout: 10_000 });
      // B 的待答区不含 A 的卡
      await expect(L.page.locator("[data-testid=request-card][data-request-id='qA']")).toHaveCount(0);
      // 切回 A：条目还在
      await openEp(L.page, "SESS-A");
      await expect(L.page.getByTestId("request-card")).toBeVisible();
    },
    ["SESS-A", "SESS-B"],
  );
});

test("TX-7 建期：core 拒绝显示原文；成功后激活新期且工序条为 01；idea 会话仍在", async () => {
  await withSession(async ({ repo, L }) => {
    sessionScript(repo, "idea", [READY("idea"), { op: "serve", on_turn: [TURN_STARTED, { t: "assistant", turn_id: "$turn", kind: "answer", text: "先聊聊这期想做什么" }, TURN_ENDED, STOP_POINTS] }]);
    await send(L.page, "想做一期杂谈");
    await expect(L.page.getByTestId("conv-stream")).toContainText("先聊聊这期想做什么");
    await L.page.locator(".left [data-testid=new-episode-toggle]").click();
    await L.page.locator(".left [data-testid=episode-name]").fill("../x");
    await L.page.locator(".left [data-testid=episode-create]").click();
    await expect(L.page.locator(".left [data-testid=new-episode-error]")).toBeVisible();
    await L.page.locator(".left [data-testid=episode-name]").fill("2026-09-26-e2e-新期");
    await L.page.getByTestId("episode-create").click();
    await expect(L.page.locator("[data-testid=episode]", { hasText: "2026-09-26-e2e-新期" })).toBeVisible({ timeout: 20_000 });
    await expect(L.page.getByTestId("current-step")).toContainText("01");
    // idea 会话仍在（切回可见原对话）
    await L.page.getByTestId("idea").click();
    await expect(L.page.getByTestId("conv-stream")).toContainText("先聊聊这期想做什么");
  });
});

test("TX-9 会话崩溃作废：待答区卡消失、流内显示已作废", async () => {
  await withSession(async ({ repo, L }) => {
    sessionScript(repo, "SESS-A", [READY("SESS-A"), { op: "serve", on_turn: [TURN_STARTED, { t: "request", request_id: "qc", kind: "tool_call", turn_id: "$turn", title: "卡", card_text: "c", fields: { tool: "write_episode_file", args: {} }, options: ["approve", "reject"], feedback_allowed: false }] }]);
    await openEp(L.page, "SESS-A");
    await send(L.page, "发起来");
    await expect(L.page.getByTestId("request-card")).toBeVisible();
    const pid = sessionRecords(repo, "SESS-A").find((r) => r.kind === "pid")!.pid!;
    process.kill(pid, "SIGKILL");
    await expect(L.page.getByTestId("request-card")).toHaveCount(0, { timeout: 10_000 });
    await expect(L.page.getByTestId("conv-stream")).toContainText("已作废（会话已结束）");
  });
});

test("TX-10 继续上次会话：出现历史分隔条与历史条目", async () => {
  await withSession(async ({ repo, L }) => {
    sessionScript(repo, "SESS-A", [READY("SESS-A"), { op: "emit", frame: { t: "history", index: 0, role: "user", text: "旧消息", name: null } }, { op: "emit", frame: { t: "history", index: 1, role: "assistant", text: "旧回复", name: null } }, { op: "serve", on_turn: [TURN_STARTED, TURN_ENDED, STOP_POINTS], on_shutdown: "exit" }]);
    await openEp(L.page, "SESS-A");
    await expect(L.page.getByTestId("session-resume")).toBeVisible();
    await L.page.getByTestId("session-resume").click();
    await expect(L.page.getByTestId("conv-stream")).toContainText("以下为恢复的历史");
    await expect(L.page.getByTestId("conv-stream")).toContainText("旧消息");
  });
});

test("TX-12 卡片按钮：检查点卡恰两个；两张抓取卡、无批量按钮", async () => {
  await withSession(async ({ repo, L }) => {
    const fetch = (id: string, no: number) => ({ t: "request", request_id: id, kind: "fetch", turn_id: "$turn", title: `候选 #${no}`, card_text: "…", fields: { no, title: "t", url: `http://127.0.0.1:9/${no}`, type: "image", source: "web", why: "w", expected_dur: "1" }, options: ["approve", "reject"], feedback_allowed: false });
    sessionScript(repo, "SESS-A", [READY("SESS-A"), { op: "serve", on_turn: [TURN_STARTED, fetch("f1", 1), fetch("f2", 2), { t: "request", request_id: "cp", kind: "checkpoint", turn_id: "$turn", title: "检查点", card_text: "…", fields: {}, options: ["continue", "stop"], feedback_allowed: false }] }]);
    await openEp(L.page, "SESS-A");
    await send(L.page, "抓起");
    await expect(L.page.locator("[data-testid=request-card]")).toHaveCount(3);
    await expect(L.page.locator("[data-testid=request-card][data-kind='fetch']")).toHaveCount(2);
    const cp = L.page.locator("[data-testid=request-card][data-kind='checkpoint']");
    await expect(cp.locator("button")).toHaveCount(2);
    await expect(cp.locator("button")).toHaveText(["继续", "停止"]);
    await expect(L.page.locator("button", { hasText: /全部批准|以后不再询问/ })).toHaveCount(0);
  });
});

test("TX-13 原生确认框：抓取卡批准要过桩；取消不写 answer，批准恰一条", async () => {
  await withSession(async ({ repo, L }) => {
    sessionScript(repo, "SESS-A", [
      READY("SESS-A"),
      { op: "serve", on_turn: [TURN_STARTED, { t: "request", request_id: "f1", kind: "fetch", turn_id: "$turn", title: "候选", card_text: "…", fields: { no: 1, title: "t", url: "http://127.0.0.1:9/a", type: "image", source: "web", why: "w", expected_dur: "1" }, options: ["approve", "reject"], feedback_allowed: false }], on_answer: [{ t: "request_closed", request_id: "$request_id", reason: "answered", decision: "$decision", rid: "$rid" }] },
    ]);
    await openEp(L.page, "SESS-A");
    await send(L.page, "抓素材");
    await expect(L.page.getByTestId("request-card")).toBeVisible();
    await stubConfirm(L, false);
    await L.page.getByTestId("request-answer-approve").click();
    await L.page.waitForTimeout(400);
    expect(stdinLines(repo, "SESS-A").filter((l) => JSON.parse(l).t === "answer")).toEqual([]);
    await expect(L.page.getByTestId("request-card")).toBeVisible();
    await stubConfirm(L, true);
    await L.page.getByTestId("request-answer-approve").click();
    await expect.poll(() => stdinLines(repo, "SESS-A").filter((l) => JSON.parse(l).t === "answer").length).toBe(1);
  });
});

test("TX-14 Tab + Enter 答复第一张后，再按 Enter 答复不到第二张", async () => {
  await withSession(async ({ repo, L }) => {
    const card = (id: string) => ({ t: "request", request_id: id, kind: "tool_call", turn_id: "$turn", title: id, card_text: "…", fields: { tool: "write_episode_file", args: {} }, options: ["approve", "reject"], feedback_allowed: false });
    sessionScript(repo, "SESS-A", [READY("SESS-A"), { op: "serve", on_turn: [TURN_STARTED, card("q1"), card("q2")], on_answer: [{ t: "request_closed", request_id: "$request_id", reason: "answered", decision: "$decision", rid: "$rid" }] }]);
    await openEp(L.page, "SESS-A");
    await send(L.page, "两张卡");
    await expect(L.page.locator("[data-testid=request-card]")).toHaveCount(2);
    await L.page.locator("[data-testid=request-card][data-request-id='q1'] [data-testid=request-answer-approve]").focus();
    await L.page.keyboard.press("Enter");
    await expect.poll(() => stdinLines(repo, "SESS-A").filter((l) => JSON.parse(l).t === "answer").length).toBe(1);
    await L.page.keyboard.press("Enter");
    await L.page.waitForTimeout(300);
    const answers = stdinLines(repo, "SESS-A").filter((l) => JSON.parse(l).t === "answer").map((l) => JSON.parse(l).request_id);
    expect(answers).toEqual(["q1"]);
    await expect(L.page.locator("[data-testid=request-card][data-request-id='q2']")).toBeVisible();
  });
});

test("TX-8 退出确认：确认后会话收 SIGTERM（不是 shutdown），app 退出", async () => {
  const fx = sessionFixture();
  const L = await launchSession(fx.repo);
  try {
    sessionScript(fx.repo, "SESS-A", [READY("SESS-A"), { op: "serve", on_turn: [TURN_STARTED] }]);
    await openEp(L.page, "SESS-A");
    await send(L.page, "跑着");
    await expect(L.page.getByTestId("session-running")).toBeVisible();
    await stubQuit(L, "quit");
    await L.app.close();
    const recs = sessionRecords(fx.repo, "SESS-A");
    expect(recs.some((r) => r.kind === "stdin" && JSON.parse(r.line!).t === "shutdown")).toBe(false);
    expect(recs.some((r) => r.kind === "signal" && r.signal === "SIGTERM")).toBe(true);
  } finally {
    await L.app.close().catch(() => undefined);
    fx.cleanup();
  }
});

test("TX-8g 全部会话空闲时退出：不弹确认框直接退，空闲会话收 shutdown（D38）", async () => {
  const fx = sessionFixture();
  const L = await launchSession(fx.repo);
  try {
    sessionScript(fx.repo, "SESS-A", [READY("SESS-A"), { op: "serve", on_turn: [TURN_STARTED, TURN_ENDED, STOP_POINTS], on_shutdown: "exit" }]);
    await openEp(L.page, "SESS-A");
    await send(L.page, "说完就停");
    await expect(L.page.getByTestId("session-head")).toHaveAttribute("data-phase", "idle");
    // 桩答「取消」：一旦弹框，app 就退不出——能退出本身即证明确认框没弹
    await stubQuit(L, "cancel");
    const closed = L.app.waitForEvent("close", { timeout: 8_000 });
    void L.app.evaluate(({ app }) => app.quit()).catch(() => undefined);
    await closed;
    const recs = sessionRecords(fx.repo, "SESS-A");
    expect(recs.some((r) => r.kind === "stdin" && JSON.parse(r.line!).t === "shutdown")).toBe(true);
    expect(recs.some((r) => r.kind === "signal")).toBe(false);
  } finally {
    await stubQuit(L, "quit").catch(() => undefined); // 修复失效时别让 app.close() 挂在「取消」桩上
    await L.app.close().catch(() => undefined);
    fx.cleanup();
  }
});

test("TX-8h 有回合在跑时退出：弹确认框（列出该期）、取消不退出、会话零写入零信号（D38 对照组）", async () => {
  const fx = sessionFixture();
  const L = await launchSession(fx.repo);
  try {
    sessionScript(fx.repo, "SESS-A", [READY("SESS-A"), { op: "serve", on_turn: [TURN_STARTED] }]);
    await openEp(L.page, "SESS-A");
    await send(L.page, "跑着");
    await expect(L.page.getByTestId("session-running")).toBeVisible();
    await stubQuit(L, "cancel");
    void L.app.evaluate(({ app }) => app.quit()).catch(() => undefined);
    await expect.poll(() => quitStubCalls(L)).toBe(1);
    const lists = await L.app.evaluate(() => (globalThis as unknown as { __avaTestQuit: { lists: string[][] } }).__avaTestQuit.lists);
    expect(lists).toEqual([["SESS-A · 运行中"]]);
    await L.page.waitForTimeout(400);
    expect(await L.app.evaluate(() => (globalThis as unknown as { __avaTestHostPid: () => number | null }).__avaTestHostPid())).toBeGreaterThan(0);
    await expect(L.page.getByTestId("session-running")).toBeVisible();
    const recs = sessionRecords(fx.repo, "SESS-A");
    expect(recs.some((r) => r.kind === "stdin" && JSON.parse(r.line!).t === "shutdown")).toBe(false);
    expect(recs.some((r) => r.kind === "signal")).toBe(false);
  } finally {
    await stubQuit(L, "quit").catch(() => undefined);
    await L.app.close().catch(() => undefined);
    fx.cleanup();
  }
});

test("TX-15 host 重启：原会话键显示已结束、待答区为空、可继续上次会话", async () => {
  await withSession(async ({ repo, L }) => {
    sessionScript(repo, "SESS-A", [READY("SESS-A"), { op: "serve", on_turn: [TURN_STARTED, { t: "request", request_id: "qh", kind: "tool_call", turn_id: "$turn", title: "卡", card_text: "c", fields: { tool: "write_episode_file", args: {} }, options: ["approve", "reject"], feedback_allowed: false }] }]);
    await openEp(L.page, "SESS-A");
    await send(L.page, "起来");
    await expect(L.page.getByTestId("request-card")).toBeVisible();
    const hostPid = await L.app.evaluate(() => (globalThis as unknown as { __avaTestHostPid: () => number | null }).__avaTestHostPid());
    process.kill(hostPid!, "SIGKILL");
    // 新 host 起来：桶清空 → 该会话键回到「无会话」，待答区为空
    await expect(L.page.getByTestId("request-card")).toHaveCount(0, { timeout: 20_000 });
    await expect(L.page.getByTestId("session-resume")).toBeVisible({ timeout: 20_000 });
  });
});

test("TX-8c 退出确认框打开时再触发退出 → 桩仍 1 次、app 未退出", async () => {
  const fx = sessionFixture();
  const L = await launchSession(fx.repo);
  try {
    sessionScript(fx.repo, "SESS-A", [READY("SESS-A"), { op: "serve", on_turn: [TURN_STARTED] }]);
    await openEp(L.page, "SESS-A");
    await send(L.page, "跑着");
    await expect(L.page.getByTestId("session-running")).toBeVisible();
    await stubQuit(L, "quit", true); // 挂住：模拟确认框已打开
    void L.app.evaluate(({ app }) => app.quit()).catch(() => undefined);
    await expect.poll(() => quitStubCalls(L)).toBe(1);
    void L.app.evaluate(({ app }) => app.quit()).catch(() => undefined);
    await L.page.waitForTimeout(400);
    expect(await quitStubCalls(L)).toBe(1); // 重入被忽略，确认框没有被弹第二次
    expect(await L.app.evaluate(() => (globalThis as unknown as { __avaTestHostPid: () => number | null }).__avaTestHostPid())).toBeGreaterThan(0);
    await releaseQuit(L, "quit");
  } finally {
    await L.app.close().catch(() => undefined);
    fx.cleanup();
  }
});

test("TX-8b host 不在时退出：不等 quit-state，直接退出", async () => {
  const fx = sessionFixture();
  const L = await launchSession(fx.repo);
  try {
    const hostPid = await L.app.evaluate(() => (globalThis as unknown as { __avaTestHostPid: () => number | null }).__avaTestHostPid());
    process.kill(hostPid!, "SIGKILL");
    const t0 = Date.now();
    await L.app.close();
    expect(Date.now() - t0).toBeLessThan(5000);
  } finally {
    fx.cleanup();
  }
});

test("TX-8d 关窗后取消：app 与 host 存活，活跃期 STATUS spawn 继续；activate 重开窗口", async () => {
  const fx = sessionFixture();
  const L = await launchSession(fx.repo);
  try {
    sessionScript(fx.repo, "SESS-A", [READY("SESS-A"), { op: "serve", on_turn: [TURN_STARTED] }]);
    await openEp(L.page, "SESS-A");
    await send(L.page, "跑着");
    await expect(L.page.getByTestId("session-running")).toBeVisible();
    await stubQuit(L, "cancel");
    const before = spawns(L).filter((s) => s.template === "STATUS").length;
    await L.page.evaluate(() => window.close());
    await L.page.waitForEvent("close").catch(() => undefined);
    await expect.poll(() => quitStubCalls(L), { timeout: 10_000 }).toBe(1);
    // 取消后 host 定时器未停：STATUS 仍按周期出现
    await expect.poll(() => spawns(L).filter((s) => s.template === "STATUS").length, { timeout: 12_000 }).toBeGreaterThan(before);
    // activate 重开窗口
    await L.app.evaluate(({ app }) => app.emit("activate"));
    await expect.poll(() => L.app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows().length), { timeout: 10_000 }).toBe(1);
  } finally {
    await stubQuit(L, "quit").catch(() => undefined);
    await L.app.close().catch(() => undefined);
    fx.cleanup();
  }
});

test("TX-11 H-10 时间线标签：无 approval_id 的 approval_resolved 按 decision/source 显示「命令卡批准」", async () => {
  await withSession(async ({ repo, L }) => {
    await openEp(L.page, "SESS-A");
    // 追加一条无 approval_id 的 approval_resolved（真实路径：status_card.py 对批准与拒绝都发该事件）
    appendFileSync(
      join(repo.root, "data/episodes/SESS-A/events.jsonl"),
      JSON.stringify({ event_id: "evt_h10_0001", timestamp: "2026-09-25T10:00:30.000000Z", episode: "SESS-A", type: "approval_resolved", payload: { decision: "approved", source: "write_episode_file", command: "write_episode_file" } }) + "\n",
    );
    await expect(L.page.getByTestId("timeline")).toContainText("命令卡批准（write_episode_file）", { timeout: 12_000 });
  });
});

test("TX-8b② host 就绪但吞掉 quit-query：QUIT_QUERY_TIMEOUT_MS 兜底退出，不弹确认框", async () => {
  const fx = sessionFixture();
  const L = await launchSession(fx.repo);
  try {
    sessionScript(fx.repo, "SESS-A", [READY("SESS-A"), { op: "serve", on_turn: [TURN_STARTED] }]);
    await openEp(L.page, "SESS-A");
    await send(L.page, "跑着");
    await expect(L.page.getByTestId("session-running")).toBeVisible();
    await stubQuit(L, "quit"); // 若真走到确认框就会 <1 s 退出；用它区分「兜底」与「应答」
    await L.app.evaluate(() => (globalThis as unknown as { __avaTestArm: (n: string) => void }).__avaTestArm("quit-query"));
    const t0 = Date.now();
    await L.app.close();
    const dt = Date.now() - t0;
    expect(dt).toBeGreaterThan(1_800); // host 应答的路径最快 <1 s，只有 2 s 兜底能到这个时长
    expect(dt).toBeLessThan(8_000);
  } finally {
    await L.app.close().catch(() => undefined);
    fx.cleanup();
  }
});

test("TX-8e stopping 期间再按退出被拦下：等 sessions-down 才退（M7 F-1）", async () => {
  const fx = sessionFixture();
  const L = await launchSession(fx.repo);
  const hostPidNow = () =>
    L.app.evaluate(() => (globalThis as unknown as { __avaTestHostPid: () => number | null }).__avaTestHostPid());
  try {
    sessionScript(fx.repo, "SESS-A", [READY("SESS-A"), { op: "serve", on_turn: [TURN_STARTED] }]);
    await openEp(L.page, "SESS-A");
    await send(L.page, "跑着");
    await expect(L.page.getByTestId("session-running")).toBeVisible();
    await stubQuit(L, "quit");
    await L.app.evaluate(() => (globalThis as unknown as { __avaTestArm: (n: string) => void }).__avaTestArm("quit-proceed"));
    void L.app.evaluate(({ app }) => app.quit()).catch(() => undefined); // #1：确认框 → proceedQuit → stopping（host 停住）
    await expect.poll(() => quitStubCalls(L)).toBe(1);
    await expect
      .poll(() => L.app.evaluate(() => (globalThis as unknown as { __avaTestHookHits: string[] }).__avaTestHookHits.includes("quit-proceed")))
      .toBe(true);
    void L.app.evaluate(({ app }) => app.quit()).catch(() => undefined); // #2：stopping 重入，必须被 preventDefault
    await L.page.waitForTimeout(800);
    expect(await hostPidNow()).toBeGreaterThan(0); // app 与 host 都还活着
    await L.app.evaluate(() => (globalThis as unknown as { __avaTestRelease: (n: string) => void }).__avaTestRelease("quit-proceed"));
    await L.app.close();
  } finally {
    await L.app.close().catch(() => undefined);
    fx.cleanup();
  }
});

test("TX-8f stopping 期间 host 崩溃：立即退出，不重启、不等 10 s 兜底（M7 F-4）", async () => {
  const fx = sessionFixture();
  const L = await launchSession(fx.repo);
  try {
    sessionScript(fx.repo, "SESS-A", [READY("SESS-A"), { op: "serve", on_turn: [TURN_STARTED] }]);
    await openEp(L.page, "SESS-A");
    await send(L.page, "跑着");
    await expect(L.page.getByTestId("session-running")).toBeVisible();
    await stubQuit(L, "quit");
    await L.app.evaluate(() => (globalThis as unknown as { __avaTestArm: (n: string) => void }).__avaTestArm("quit-proceed"));
    void L.app.evaluate(({ app }) => app.quit()).catch(() => undefined);
    await expect
      .poll(() => L.app.evaluate(() => (globalThis as unknown as { __avaTestHookHits: string[] }).__avaTestHookHits.includes("quit-proceed")))
      .toBe(true);
    const pid = await L.app.evaluate(() => (globalThis as unknown as { __avaTestHostPid: () => number | null }).__avaTestHostPid());
    const t0 = Date.now();
    process.kill(pid!, "SIGKILL");
    await L.app.waitForEvent("close", { timeout: 8_000 });
    expect(Date.now() - t0).toBeLessThan(5_000); // 看护重启 + 10 s 兜底会明显超过 5 s
  } finally {
    await L.app.close().catch(() => undefined);
    fx.cleanup();
  }
});
