// Spec 10 PR4：真实 core（复制来的真实 `pipeline.agent.protocol`）+ 本地假 LLM 端点重跑 TX。
//
// 纪律（一期 Spec 8 纳秒 mtime 的教训）：期望值不在两侧各写一份对拍。
// - 帧的形状：以真实 core 实际发出的帧为准，逐类与 TS 侧 `convFrames.REQUIRED` 比对键集合（TX-0）；
// - 失败原文：界面文本与真实 `session.jsonl` 里对应 tool 消息的 content 逐字节比对（TX-1）；
// - 配对：假端点对配对不齐的历史回 400，`badPairings == 0` 由真实请求体证明。
import { execFileSync } from "node:child_process";
import { readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { expect, test, type Page } from "@playwright/test";
import type { Launched } from "./fixtures";
import { assistant, startFakeLlm, toolCalls, type FakeLlm } from "./fakeLlm";
import { convSnapshot, fingerprintOf, launchSession, pendingObj, quitStubCalls, realCoreFixture, stubConfirm, stubQuit, writeStore, type SessionFixture } from "./sessionFixtures";
import { ACK_TEMPLATES, spawns } from "./ackFixtures";
import { CONDITIONAL, OUT_TYPES, parseOutFrame, REQUIRED } from "../src/shared/convFrames";
import { parseLossless } from "../src/shared/losslessJson";
import { fixtureWrite } from "../tests/fixtures/session";

type Ctx = SessionFixture & { L: Launched; llm: FakeLlm };

async function withRealCore(fn: (ctx: Ctx) => Promise<void>, eps: string[] = ["SESS-A"], opts: { at035?: boolean } = {}): Promise<void> {
  const llm = await startFakeLlm();
  const fx = realCoreFixture(llm.url, eps, opts);
  const L = await launchSession(fx.repo);
  try {
    await fn({ ...fx, L, llm });
  } catch (e) {
    await dumpScene(L, llm, eps);
    throw e;
  } finally {
    await stubQuit(L, "quit").catch(() => undefined);
    await L.app.close().catch(() => undefined);
    await llm.close();
    fx.cleanup();
  }
}

/** 失败现场：各会话键的 host 缓冲条目（帧只留 t 与关键键）、假端点收到的请求数、app 输出尾部。 */
async function dumpScene(L: Launched, llm: FakeLlm, eps: string[]): Promise<void> {
  for (const key of ["idea", ...eps.map((e) => `ep:${e}`)]) {
    const snap = await convSnapshot(L, key).catch((err: unknown) => ({ error: String(err) }));
    console.log(`[scene] ${key} ${JSON.stringify(snap).slice(0, 6000)}`);
  }
  console.log(`[scene] llm.requests=${llm.requests.length} badPairings=${llm.badPairings}`);
  console.log(`[scene] stdout tail:\n${L.stdout().slice(-3000)}`);
}

const openEp = async (page: Page, ep: string): Promise<void> => {
  await page.locator("[data-testid=episode]", { hasText: ep }).first().click();
  await expect(page.getByTestId("center")).toHaveAttribute("data-ep", ep);
};

const send = async (page: Page, text: string): Promise<void> => {
  await page.getByTestId("composer-input").fill(text);
  await page.getByTestId("composer-input").press("Enter");
};

type Frame = Record<string, unknown> & { t: string };
const framesOf = (snap: Awaited<ReturnType<typeof convSnapshot>>): Frame[] =>
  snap.entries.filter((e) => e.k === "frame").map((e) => e.frame as Frame);

/** 等到该会话收到第 n 个 turn_finished（真实 core 的回合结束信号）。 */
async function waitTurns(L: Launched, convKey: string, n: number, timeout = 30_000): Promise<Frame[]> {
  await expect.poll(async () => framesOf(await convSnapshot(L, convKey)).filter((f) => f.t === "turn_finished").length, { timeout }).toBeGreaterThanOrEqual(n);
  return framesOf(await convSnapshot(L, convKey));
}

/** 读期目录里真实的 session.jsonl（core 写的产物）。 */
function sessionLog(fx: SessionFixture, ep: string): Record<string, unknown>[] {
  return readFileSync(join(fx.repo.eps, ep, "session.jsonl"), "utf-8")
    .split("\n")
    .filter(Boolean)
    .map((l) => JSON.parse(l) as Record<string, unknown>);
}

const ENVELOPE = new Set(["v", "t", "seq", "sid", "rid"]);

test("TX-0 契约：真实 core 的每一帧都过 parseOutFrame、framesLost==0，且逐类键集合与 REQUIRED 完全一致", async () => {
  await withRealCore(async ({ L, llm }) => {
    // 一轮：只读工具 + 失败的 run_pipeline（经工具卡）→ 收尾答复
    llm.push(toolCalls({ name: "read_status", args: {} }, { name: "run_pipeline", args: { command: "check_script" } }), assistant("两步都做完了。"));
    await openEp(L.page, "SESS-A");
    await send(L.page, "看下状态再检查稿件");
    await L.page.getByTestId("request-answer-approve").click();
    const frames = await waitTurns(L, "ep:SESS-A", 1);

    const snap = await convSnapshot(L, "ep:SESS-A");
    expect(snap.framesLost).toBe(0);
    expect(llm.badPairings).toBe(0);

    const seen = new Map<string, Set<string>>();
    for (const f of frames) {
      // host 已校验过一遍；这里对「原样再序列化」再过一次，钉住 host 缓冲里没有被改形的帧
      expect(parseOutFrame(JSON.stringify(f)).ok, `帧未通过 parseOutFrame：${JSON.stringify(f)}`).toBe(true);
      const keys = new Set(Object.keys(f).filter((k) => !ENVELOPE.has(k)));
      const want = new Set([...Object.keys(REQUIRED[f.t as (typeof OUT_TYPES)[number]]), ...Object.keys(CONDITIONAL[f.t as (typeof OUT_TYPES)[number]]?.(f) ?? {})]);
      // 两个方向都查：core 少发（TS 的必需键缺席）已由 parseOutFrame 判 malformed；
      // core 多发（TS 表里没有、会被「向前兼容」静默忽略的键）只有这里查得出
      expect([...keys].filter((k) => !want.has(k)), `core 发出了 TS 表外的键（t=${f.t}）`).toEqual([]);
      expect([...want].filter((k) => !keys.has(k)), `TS 必需键在真实帧里缺席（t=${f.t}）`).toEqual([]);
      if (!seen.has(f.t)) seen.set(f.t, new Set());
      for (const k of keys) seen.get(f.t)!.add(k);
    }
    // 这一轮必须真的覆盖到这些帧类，否则上面的逐类比对是空转
    for (const t of ["ready", "turn_started", "tool", "request", "request_closed", "assistant", "turn_finished", "stop_points"]) {
      expect(seen.has(t), `本轮没有出现 ${t} 帧`).toBe(true);
    }
    // 失败的 run_pipeline 确实判为非成功（S9-R1：顶层 ok:true、result.ok:false）
    const ends = frames.filter((f) => f.t === "tool" && f.phase === "end");
    expect(ends.map((f) => [f.name, f.ok])).toEqual([
      ["read_status", true],
      ["run_pipeline", false],
    ]);
  });
});

// ---------------------------------------------------------------------------
// TX-1~TX-15 的真实 core 版。与假进程版的差异只在「会话那一侧是真的」：
// 帧由真实 protocol.py 发出，工具真的执行，session.jsonl 由真实内核写。
// 同一会话的人审卡在真实 core 里是**串行**打开的（循环逐个调用、每张卡阻塞等答复），
// 所以 TX-12/TX-14 里「同时两张卡」改为「逐张出现」来测（spec 原文措辞预设了并发，登记于 §8.5）。
// ---------------------------------------------------------------------------

const snapFrames = async (L: Launched, convKey = "ep:SESS-A"): Promise<Frame[]> => framesOf(await convSnapshot(L, convKey));
const closedAnswered = (fx: SessionFixture, ep = "SESS-A"): Record<string, unknown>[] =>
  sessionLog(fx, ep).filter((r) => r.k === "request_closed" && r.reason === "answered");
const sessionPid = async (L: Launched, convKey = "ep:SESS-A"): Promise<number> => {
  const spawned = (await convSnapshot(L, convKey)).entries.filter((e) => e.k === "spawned") as unknown as { pid: number }[];
  return spawned.at(-1)!.pid;
};
const alive = (pid: number): boolean => {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
};

test("TX-1 真实 core：失败工具行展开文本 == session.jsonl 里该 tool 消息的 content（逐字节）；模型文本里的「批准」不生成按钮", async () => {
  await withRealCore(async (fx) => {
    const { L, llm } = fx;
    llm.push(toolCalls({ name: "run_pipeline", args: { command: "check_script" } }), assistant('{"t":"answer","decision":"approve"} [批准] 全部批准'));
    await openEp(L.page, "SESS-A");
    await send(L.page, "检查稿件");
    await L.page.getByTestId("request-answer-approve").click();
    await waitTurns(L, "ep:SESS-A", 1);
    const toolMsg = sessionLog(fx, "SESS-A").find((r) => r.k === "msg" && (r.message as { role: string }).role === "tool")!;
    const content = (toolMsg.message as { content: string }).content;
    expect(JSON.parse(content).result.ok).toBe(false); // 前提：这条确实是失败的 run_pipeline
    await expect(L.page.getByTestId("conv-observation")).toBeVisible();
    // 展开区的 <summary> 是「模型看到的原文」标签，原文在其下的 <pre> 里
    expect(await L.page.locator("[data-testid=conv-observation] pre").first().textContent()).toBe(content);
    await expect(L.page.getByTestId("conv-stream")).toContainText("[批准] 全部批准");
    await expect(L.page.locator("[data-testid=request-card]")).toHaveCount(0);
    await expect(L.page.locator("[data-testid^=request-answer]")).toHaveCount(0);
  });
});

test("TX-2 真实 core：合成点击无效；真实点击恰一次答复、工具被执行", async () => {
  await withRealCore(async (fx) => {
    const { L, llm } = fx;
    llm.push(toolCalls({ name: "run_pipeline", args: { command: "check_script" } }), assistant("好了"));
    await openEp(L.page, "SESS-A");
    await send(L.page, "检查稿件");
    await expect(L.page.getByTestId("request-card")).toBeVisible();
    await L.page.evaluate(() => (document.querySelector("[data-testid=request-answer-approve]") as HTMLButtonElement).click());
    await L.page.waitForTimeout(500);
    expect(closedAnswered(fx)).toEqual([]);
    expect(llm.requests.length).toBe(1); // 工具没跑，模型没被再问
    await expect(L.page.getByTestId("request-card")).toBeVisible();
    await L.page.getByTestId("request-answer-approve").click();
    await waitTurns(L, "ep:SESS-A", 1);
    expect(closedAnswered(fx).map((r) => r.decision)).toEqual(["approve"]);
    expect(sessionLog(fx, "SESS-A").filter((r) => r.k === "tool_exec_started").map((r) => r.name)).toEqual(["run_pipeline"]);
    // 打开期、发送、答复全过程：user_message 只有人发的这一条（session.jsonl 的 user 消息计数）
    expect(sessionLog(fx, "SESS-A").filter((r) => r.k === "msg" && r.origin === "user")).toHaveLength(1);
    // spec TX-2 的另一半：停机点卡「批准」同样合成点击 → 零 ack spawn、对象仍 pending
    //（M9：此前两版 TX-2 都只测了工具卡，MUT-22 去掉停机点卡的 isTrusted 首句时只有静态守卫红）
    const approve = L.page.locator("[data-testid=decisions] [data-testid=approve]").first();
    await expect(approve).toBeVisible({ timeout: 15_000 });
    const acksBefore = spawns(L).filter((x) => ACK_TEMPLATES.includes(x.template)).length;
    await L.page.evaluate(() => (document.querySelector("[data-testid=decisions] [data-testid=approve]") as HTMLButtonElement).click());
    await L.page.waitForTimeout(800);
    expect(spawns(L).filter((x) => ACK_TEMPLATES.includes(x.template)).length).toBe(acksBefore);
    await expect(approve).toBeVisible();
  });
});

test("TX-3 真实 core：拒绝附反馈 → 假端点收到的下一次请求里该 tool 消息含原文；流内留痕", async () => {
  await withRealCore(async ({ L, llm }) => {
    llm.push(toolCalls({ name: "run_pipeline", args: { command: "check_script" } }), assistant("收到，改。"));
    await openEp(L.page, "SESS-A");
    await send(L.page, "检查稿件");
    await L.page.getByTestId("request-answer-reject").waitFor();
    await L.page.getByTestId("request-feedback").fill("改成第三人称");
    await L.page.getByTestId("request-answer-reject").click();
    await waitTurns(L, "ep:SESS-A", 1);
    const tools = llm.requests[1].messages.filter((m) => m.role === "tool");
    expect(tools).toHaveLength(1);
    expect(String(tools[0].content)).toContain("改成第三人称");
    await expect(L.page.getByTestId("conv-stream")).toContainText("已拒绝（附反馈：改成第三人称）");
  });
});

test("TX-4 真实 core：慢端点上点「停止」→ 2 s 内出现收尾总结，turn_finished.stopped == interrupted", async () => {
  await withRealCore(async ({ L, llm }) => {
    llm.push(toolCalls({ name: "read_status", args: {} }), { message: { role: "assistant", content: "迟到的回复" }, delayMs: 60_000 }, assistant("已停下，当前确定的是状态已读。"));
    await openEp(L.page, "SESS-A");
    await send(L.page, "慢慢来");
    await expect.poll(() => llm.requests.length, { timeout: 15_000 }).toBe(2); // 第二次请求正挂在慢端点上
    const t0 = Date.now();
    await L.page.getByTestId("composer-stop").click();
    await expect(L.page.getByTestId("conv-stream")).toContainText("收尾总结", { timeout: 2_000 });
    expect(Date.now() - t0).toBeLessThan(2_000);
    const fin = (await waitTurns(L, "ep:SESS-A", 1)).find((f) => f.t === "turn_finished")!;
    expect(fin.stopped).toBe("interrupted");
    expect(fin.wrapup).toBe("ok");
    expect(llm.requests[2].tool_choice).toBe("none"); // 收尾调用不带工具
  });
});

test("TX-6 真实 core：A 挂卡时切到 B 对话 → A 显示运行中；B 的待答区不含 A 的卡；切回 A 卡仍在", async () => {
  await withRealCore(
    async ({ L, llm }) => {
      // A 的第一次回复挂在慢端点上：A 的卡在 B 的会话桶建立之后才到——两个会话的 delta 真的交错
      //（M9：此前 A 的卡先到、之后 A 再无 delta，「A 的更新污染 B 的桶」无从发生，MUT-31 因此存活）
      llm.push({ ...toolCalls({ name: "run_pipeline", args: { command: "check_script" } }), delayMs: 4_000 }, assistant("B 这边好了"));
      await openEp(L.page, "SESS-A");
      await send(L.page, "A 的任务");
      await expect.poll(() => llm.requests.length, { timeout: 10_000 }).toBe(1);
      await openEp(L.page, "SESS-B");
      await send(L.page, "B 的任务");
      await waitTurns(L, "ep:SESS-B", 1);
      await expect(L.page.getByTestId("conv-stream")).toContainText("B 这边好了");
      await expect.poll(async () => (await snapFrames(L, "ep:SESS-A")).filter((f) => f.t === "request").length, { timeout: 15_000 }).toBe(1);
      const aCard = (await snapFrames(L, "ep:SESS-A")).find((f) => f.t === "request")!.request_id as string;
      await L.page.waitForTimeout(500); // 让 A 的 delta 到达 renderer（此时停在 B 的视图）
      await expect(L.page.locator("[data-testid=episode]", { hasText: "SESS-A" }).getByTestId("conv-running")).toBeVisible({ timeout: 10_000 });
      await expect(L.page.locator(`[data-testid=request-card][data-request-id='${aCard}']`)).toHaveCount(0);
      const bFrames = await snapFrames(L, "ep:SESS-B");
      expect(bFrames.some((f) => f.request_id === aCard)).toBe(false);
      await openEp(L.page, "SESS-A");
      await expect(L.page.locator(`[data-testid=request-card][data-request-id='${aCard}']`)).toBeVisible();
    },
    ["SESS-A", "SESS-B"],
  );
});

test("TX-7 真实 core：idea 会话聊一轮 → 建期（core 拒绝显示原文；合法名建成并激活，工序条 01）；idea 会话仍在", async () => {
  await withRealCore(async ({ L, llm }) => {
    llm.push(assistant("先聊聊这期想做什么"));
    await send(L.page, "想做一期杂谈");
    await expect(L.page.getByTestId("conv-stream")).toContainText("先聊聊这期想做什么", { timeout: 15_000 });
    await L.page.locator(".left [data-testid=new-episode-toggle]").click();
    await L.page.locator(".left [data-testid=episode-name]").fill("../x");
    await L.page.locator(".left [data-testid=episode-create]").click();
    await expect(L.page.locator(".left [data-testid=new-episode-error]")).toBeVisible();
    await L.page.locator(".left [data-testid=episode-name]").fill("2026-09-26-e2e-新期");
    await L.page.getByTestId("episode-create").click();
    await expect(L.page.locator("[data-testid=episode]", { hasText: "2026-09-26-e2e-新期" })).toBeVisible({ timeout: 20_000 });
    await expect(L.page.getByTestId("current-step")).toContainText("01");
    // spec：新期对话区只有本地提示行——host 不把 idea 讨论代发给新期（M9：此前没查，MUT-35 因此存活）
    await L.page.waitForTimeout(1_500);
    const fresh = await convSnapshot(L, "ep:2026-09-26-e2e-新期");
    expect(fresh.entries.filter((e) => e.k === "user" || (e.k === "frame" && e.frame?.t === "turn_started"))).toEqual([]);
    expect(llm.requests.length).toBe(1); // 只有 idea 那一轮
    await L.page.getByTestId("idea").click();
    await expect(L.page.getByTestId("conv-stream")).toContainText("先聊聊这期想做什么");
  });
});

test("TX-9 真实 core：卡片打开时 kill -9 会话 → 待答区卡消失、流内「已作废（会话已结束）」", async () => {
  await withRealCore(async ({ L, llm }) => {
    llm.push(toolCalls({ name: "run_pipeline", args: { command: "check_script" } }));
    await openEp(L.page, "SESS-A");
    await send(L.page, "发起来");
    await expect(L.page.getByTestId("request-card")).toBeVisible();
    process.kill(await sessionPid(L), "SIGKILL");
    await expect(L.page.getByTestId("request-card")).toHaveCount(0, { timeout: 10_000 });
    await expect(L.page.getByTestId("conv-stream")).toContainText("已作废（会话已结束）");
  });
});

test("TX-10 真实 core：结束会话后「继续上次会话」→ 历史分隔条与历史条目（history 帧键集合同 TX-0 规则）；随后发消息正常回复", async () => {
  await withRealCore(async ({ L, llm }) => {
    llm.push(assistant("旧回复"), assistant("新回复"));
    await openEp(L.page, "SESS-A");
    await send(L.page, "旧消息");
    await waitTurns(L, "ep:SESS-A", 1);
    await L.page.getByTestId("session-end").click();
    await expect(L.page.getByTestId("session-resume")).toBeVisible({ timeout: 20_000 });
    await L.page.getByTestId("session-resume").click();
    await expect(L.page.getByTestId("conv-stream")).toContainText("以下为恢复的历史", { timeout: 15_000 });
    await expect(L.page.getByTestId("conv-stream")).toContainText("旧消息");
    await expect(L.page.getByTestId("conv-stream")).toContainText("旧回复");
    const hist = (await snapFrames(L)).filter((f) => f.t === "history");
    expect(hist.length).toBeGreaterThan(0);
    for (const f of hist) {
      const keys = Object.keys(f).filter((k) => !ENVELOPE.has(k)).sort();
      const want = [...Object.keys(REQUIRED.history), ...Object.keys(CONDITIONAL.history?.(f) ?? {})].sort();
      expect(keys).toEqual(want);
    }
    await send(L.page, "接着说");
    await expect(L.page.getByTestId("conv-stream")).toContainText("新回复", { timeout: 15_000 });
    expect(llm.badPairings).toBe(0);
  });
});

test("TX-11 真实 core：批准一张工具卡后，时间线该事件显示「命令卡批准」", async () => {
  await withRealCore(async ({ L, llm }) => {
    llm.push(toolCalls({ name: "run_pipeline", args: { command: "check_script" } }), assistant("好"));
    await openEp(L.page, "SESS-A");
    await send(L.page, "检查稿件");
    await L.page.getByTestId("request-answer-approve").click();
    await waitTurns(L, "ep:SESS-A", 1);
    await expect(L.page.getByTestId("timeline")).toContainText("命令卡批准", { timeout: 12_000 });
  });
});

test("TX-12 真实 core：检查点卡恰「继续」「停止」两个按钮（第 50 次回复后）", async () => {
  test.setTimeout(4 * 60_000);
  await withRealCore(async ({ L, llm }) => {
    // 50 次回复各调一次只读工具（参数各不相同，避开判重）→ 第 51 次请求之前问检查点
    for (let i = 0; i < 50; i++) llm.push(toolCalls({ name: "read_artifact", args: { path: `01-topic.md#${i}` } }));
    llm.push(assistant("停在这。"));
    await openEp(L.page, "SESS-A");
    await send(L.page, "一直读");
    const cp = L.page.locator("[data-testid=request-card][data-kind='checkpoint']");
    await expect(cp).toBeVisible({ timeout: 60_000 });
    await expect(cp.locator("button")).toHaveText(["继续", "停止"]);
    await expect(L.page.locator("button", { hasText: /全部批准|以后不再询问/ })).toHaveCount(0);
    await cp.locator("button", { hasText: "停止" }).click();
    const fin = (await waitTurns(L, "ep:SESS-A", 1, 30_000)).find((f) => f.t === "turn_finished")!;
    expect(fin.stopped).toBe("checkpoint_stop");
  });
});

test("TX-12b/TX-13 真实 core：acquire_propose 2 条 → 逐张出 2 张抓取卡（无批量按钮）；批准须过原生确认框，取消不答复", async () => {
  await withRealCore(async (fx) => {
    const { L, llm } = fx;
    const cand = (n: number) => ({ title: `候选${n}`, url: `http://127.0.0.1:9/cand-${n}`, type: "mv", source: "local", why: `第 ${n} 条候选的理由写够二十个字以免触发提示`, expected_dur: 30 });
    llm.push(assistant("在。"), toolCalls({ name: "acquire_propose", args: { candidates: [cand(1), cand(2)] } }), assistant("两条都处理了。"));
    await openEp(L.page, "SESS-A");
    // conv.command 要有活会话：先聊一轮把会话起来，再切到 asset（acquire_propose 只在 asset scope）
    await send(L.page, "在吗");
    await waitTurns(L, "ep:SESS-A", 1);
    await L.page.getByTestId("scope-toggle").click();
    await expect.poll(async () => (await snapFrames(L)).some((f) => f.t === "command_result" && f.name === "scope")).toBe(true);
    await send(L.page, "提两条候选");
    await L.page.getByTestId("request-answer-approve").click(); // 工具卡：acquire_propose 本身
    const fetchCard = L.page.locator("[data-testid=request-card][data-kind='fetch']");
    await expect(fetchCard).toHaveCount(1, { timeout: 15_000 });
    await expect(L.page.locator("button", { hasText: /全部批准|以后不再询问/ })).toHaveCount(0);
    // TX-13：原生确认框取消 → 不答复、卡仍在
    await stubConfirm(L, false);
    const fetch1 = await fetchCard.getAttribute("data-request-id");
    await fetchCard.getByTestId("request-answer-approve").click();
    await L.page.waitForTimeout(500);
    expect(closedAnswered(fx).filter((r) => r.request_id === fetch1)).toEqual([]);
    await expect(fetchCard).toHaveCount(1);
    // 拒第一张 → 第二张出现（真实 core 逐张出卡）
    await fetchCard.getByTestId("request-answer-reject").click();
    await expect.poll(async () => (await snapFrames(L)).filter((f) => f.t === "request" && f.kind === "fetch").length, { timeout: 10_000 }).toBe(2);
    const fetch2 = (await snapFrames(L)).filter((f) => f.t === "request" && f.kind === "fetch")[1].request_id as string;
    expect(fetch2).not.toBe(fetch1);
    // 第二张批准：过确认框（桩返回 true）→ 恰一条答复。按 request_id 定位：第一张关、第二张开几乎同一毫秒，
    // 泛指的 locator 可能落在正在消失的第一张上
    await stubConfirm(L, true);
    const card2 = L.page.locator(`[data-testid=request-card][data-request-id='${fetch2}']`);
    await expect(card2).toBeVisible();
    await card2.getByTestId("request-answer-approve").click();
    await waitTurns(L, "ep:SESS-A", 2, 60_000);
    const fetchCloses = closedAnswered(fx).filter((r) => r.decision !== undefined).map((r) => r.decision);
    expect(fetchCloses).toEqual(["approve", "reject", "approve"]); // 工具卡、抓取 #1 拒、抓取 #2 批
    expect(await L.app.evaluate(() => (globalThis as unknown as { __avaTestConfirm: { calls: number } }).__avaTestConfirm.calls)).toBe(1);
  });
});

test("TX-14 真实 core：Tab+Enter 答复第一张后，第二张（随后才出现）按 Enter 答复不到", async () => {
  await withRealCore(async (fx) => {
    const { L, llm } = fx;
    llm.push(toolCalls({ name: "run_pipeline", args: { command: "check_script" } }, { name: "run_pipeline", args: { command: "status" } }), assistant("好"));
    await openEp(L.page, "SESS-A");
    await send(L.page, "两步");
    const first = L.page.getByTestId("request-card");
    await expect(first).toHaveCount(1);
    const firstId = await first.getAttribute("data-request-id");
    await first.getByTestId("request-answer-approve").focus();
    await L.page.keyboard.press("Enter");
    await expect.poll(async () => (await snapFrames(L)).filter((f) => f.t === "request").length, { timeout: 15_000 }).toBe(2);
    const second = L.page.getByTestId("request-card");
    await expect(second).toHaveCount(1);
    expect(await second.getAttribute("data-request-id")).not.toBe(firstId);
    await L.page.keyboard.press("Enter");
    await L.page.waitForTimeout(500);
    expect(closedAnswered(fx).map((r) => r.request_id)).toEqual([firstId]);
    await expect(second).toBeVisible();
  });
});

test("TX-15 真实 core：有打开卡时 kill -9 host → 原会话键结束、待答区为空；「继续上次会话」恢复历史", async () => {
  test.setTimeout(4 * 60_000);
  await withRealCore(async ({ L, llm }) => {
    llm.push(assistant("第一轮答复"), toolCalls({ name: "run_pipeline", args: { command: "check_script" } }));
    await openEp(L.page, "SESS-A");
    await send(L.page, "第一轮");
    await waitTurns(L, "ep:SESS-A", 1);
    await send(L.page, "第二轮");
    await expect(L.page.getByTestId("request-card")).toBeVisible();
    const hostPid = await L.app.evaluate(() => (globalThis as unknown as { __avaTestHostPid: () => number | null }).__avaTestHostPid());
    process.kill(hostPid!, "SIGKILL");
    await expect(L.page.getByTestId("request-card")).toHaveCount(0, { timeout: 20_000 });
    // 孤儿会话读到 EOF 后按 Spec 9 收尾、释放租约，「继续」才拿得到锁（RF-6）
    await expect(L.page.getByTestId("session-resume")).toBeVisible({ timeout: 20_000 });
    await expect(async () => {
      await L.page.getByTestId("session-resume").click();
      await expect(L.page.getByTestId("conv-stream")).toContainText("以下为恢复的历史", { timeout: 3_000 });
    }).toPass({ timeout: 90_000 });
    await expect(L.page.getByTestId("conv-stream")).toContainText("第一轮答复");
  });
});

test("TX-8 真实 core：回合在跑时退出 → 确认框列出该期；确认 → 会话收 SIGTERM（turn_end.wrapup == skipped），8 s 内进程消失", async () => {
  const llm = await startFakeLlm();
  const fx = realCoreFixture(llm.url);
  const L = await launchSession(fx.repo);
  try {
    llm.push({ message: { role: "assistant", content: "迟到" }, delayMs: 60_000 });
    await openEp(L.page, "SESS-A");
    await send(L.page, "跑着");
    await expect(L.page.getByTestId("session-running")).toBeVisible();
    await expect.poll(() => llm.requests.length, { timeout: 15_000 }).toBe(1);
    const pid = await sessionPid(L);
    // 先「取消」一次：读出确认框桩收到的列表（含该期）；取消后 app 与会话都活着
    await stubQuit(L, "cancel");
    void L.app.evaluate(({ app }) => app.quit()).catch(() => undefined);
    await expect.poll(() => quitStubCalls(L)).toBe(1);
    const lists = await L.app.evaluate(() => (globalThis as unknown as { __avaTestQuit: { lists: string[][] } }).__avaTestQuit.lists);
    expect(lists[0].join(" ")).toContain("SESS-A");
    expect(alive(pid)).toBe(true);
    await stubQuit(L, "quit");
    const t0 = Date.now();
    await L.app.close();
    await expect.poll(() => alive(pid), { timeout: 8_000 }).toBe(false);
    expect(Date.now() - t0).toBeLessThan(8_000 + 2_000);
    const ends = sessionLog(fx, "SESS-A").filter((r) => r.k === "turn_end");
    expect(ends.at(-1)!.wrapup).toBe("skipped");
  } finally {
    await L.app.close().catch(() => undefined);
    await llm.close();
    fx.cleanup();
  }
});

/** 「回合在跑」：让第一次模型请求挂在慢端点上（真实 core 的回合就停在 chat_complete 里）。 */
async function runningTurn(L: Launched, llm: FakeLlm): Promise<void> {
  llm.push({ message: { role: "assistant", content: "迟到" }, delayMs: 60_000 });
  await openEp(L.page, "SESS-A");
  await send(L.page, "跑着");
  await expect(L.page.getByTestId("session-running")).toBeVisible();
  await expect.poll(() => llm.requests.length, { timeout: 15_000 }).toBe(1);
}

async function withRealApp(fn: (ctx: Ctx) => Promise<void>, opts: { at035?: boolean } = {}): Promise<void> {
  const llm = await startFakeLlm();
  const fx = realCoreFixture(llm.url, ["SESS-A"], opts);
  const L = await launchSession(fx.repo);
  try {
    await fn({ ...fx, L, llm });
  } finally {
    await L.app.close().catch(() => undefined);
    await llm.close();
    fx.cleanup();
  }
}

const hostPidOf = (L: Launched) => L.app.evaluate(() => (globalThis as unknown as { __avaTestHostPid: () => number | null }).__avaTestHostPid());

test("TX-5 ①②④ 真实 core：新 pending 自动打开预览；人占用时只出「已就绪」条；焦点不动", async () => {
  await withRealCore(
    async ({ repo, L }) => {
      await openEp(L.page, "SESS-A");
      await expect(L.page.getByTestId("audio-queue")).toBeVisible({ timeout: 15_000 });
      expect(Number(await L.page.locator(".preview").getAttribute("data-auto-open-count"))).toBe(1);
      await L.page.locator("[data-testid=tree-row][data-rel='02-script.md']").click();
      await expect(L.page.getByTestId("script-editor")).toBeVisible();
      const before = await L.page.evaluate(() => document.activeElement?.getAttribute("data-testid") ?? null);
      const fp = fingerprintOf(repo, "SESS-A", "03-audio/manifest.json");
      writeStore(repo, "SESS-A", [pendingObj("a05", "05", "2026-09-25T10:00:05Z", "03-audio/manifest.json", fp)]);
      await expect(L.page.getByTestId("auto-open-strip")).toBeVisible({ timeout: 12_000 });
      await expect(L.page.getByTestId("script-editor")).toBeVisible();
      expect(await L.page.evaluate(() => document.activeElement?.getAttribute("data-testid") ?? null)).toBe(before);
    },
    ["SESS-A"],
    { at035: true },
  );
});

test("TX-5 ③ 真实 core：回合中对象库先后出现两版 → 回合中零呼出，结算后恰呼出第二版一次", async () => {
  await withRealCore(async ({ repo, L, llm }) => {
    llm.push({ message: { role: "assistant", content: "排好了" }, delayMs: 6_000 });
    await openEp(L.page, "SESS-A");
    const fp = fingerprintOf(repo, "SESS-A", "04-clips.json");
    await send(L.page, "重排一下");
    await expect(L.page.getByTestId("session-head")).toHaveAttribute("data-phase", "running");
    const before = Number(await L.page.locator(".preview").getAttribute("data-auto-open-count"));
    writeStore(repo, "SESS-A", [pendingObj("v1", "05", "2026-09-25T10:00:01Z", "04-clips.json", fp)]);
    await L.page.waitForTimeout(250);
    writeStore(repo, "SESS-A", [pendingObj("v2", "05", "2026-09-25T10:00:02Z", "04-clips.json", fp)]);
    // 等过至少两个活跃期轮询周期（ACTIVE_POLL_MS 1 s），确保 host 在回合中**确实读到了**新对象再检查——
    // M9：此前只等 300 ms，host 多半还没读，「回合中零呼出」恒真（MUT-28 因此存活）
    await L.page.waitForTimeout(2_500);
    await expect(L.page.getByTestId("session-head")).toHaveAttribute("data-phase", "running"); // 回合仍在跑
    expect(Number(await L.page.locator(".preview").getAttribute("data-auto-open-count"))).toBe(before);
    await expect.poll(async () => Number(await L.page.locator(".preview").getAttribute("data-auto-open-count")), { timeout: 15_000 }).toBe(before + 1);
    expect(await L.page.locator(".preview").getAttribute("data-auto-open-approval-id")).toBe("v2");
  });
});

test("TX-8b 真实 core：host 熔断后退出 → 5 s 内退出", async () => {
  await withRealApp(async ({ L }) => {
    process.kill((await hostPidOf(L))!, "SIGKILL");
    const t0 = Date.now();
    await L.app.close();
    expect(Date.now() - t0).toBeLessThan(5_000);
  });
});

test("TX-8b② 真实 core：host 就绪但吞掉 quit-query → QUIT_QUERY_TIMEOUT_MS 兜底退出", async () => {
  await withRealApp(async ({ L, llm }) => {
    await runningTurn(L, llm);
    await stubQuit(L, "quit");
    await L.app.evaluate(() => (globalThis as unknown as { __avaTestArm: (n: string) => void }).__avaTestArm("quit-query"));
    const t0 = Date.now();
    await L.app.close();
    const dt = Date.now() - t0;
    expect(dt).toBeGreaterThan(1_800);
    expect(dt).toBeLessThan(8_000);
  });
});

test("TX-8c 真实 core：确认框打开时再触发退出 → 桩仍 1 次、app 未退出", async () => {
  await withRealApp(async ({ L, llm }) => {
    await runningTurn(L, llm);
    await stubQuit(L, "quit", true);
    void L.app.evaluate(({ app }) => app.quit()).catch(() => undefined);
    await expect.poll(() => quitStubCalls(L)).toBe(1);
    void L.app.evaluate(({ app }) => app.quit()).catch(() => undefined);
    await L.page.waitForTimeout(400);
    expect(await quitStubCalls(L)).toBe(1);
    expect(await hostPidOf(L)).toBeGreaterThan(0);
    await L.app.evaluate(() => (globalThis as unknown as { __avaTestQuitRelease: (r: string) => void }).__avaTestQuitRelease("quit"));
  });
});

test("TX-8d 真实 core：关窗后取消 → app 与 host 存活、STATUS spawn 继续；activate 重开窗口", async () => {
  await withRealApp(async ({ L, llm }) => {
    await runningTurn(L, llm);
    await stubQuit(L, "cancel");
    const before = spawns(L).filter((s) => s.template === "STATUS").length;
    await L.page.evaluate(() => window.close());
    await L.page.waitForEvent("close").catch(() => undefined);
    await expect.poll(() => quitStubCalls(L), { timeout: 10_000 }).toBe(1);
    await expect.poll(() => spawns(L).filter((s) => s.template === "STATUS").length, { timeout: 12_000 }).toBeGreaterThan(before);
    await L.app.evaluate(({ app }) => app.emit("activate"));
    await expect.poll(() => L.app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows().length), { timeout: 10_000 }).toBe(1);
    await stubQuit(L, "quit");
  });
});

test("TX-8e 真实 core：stopping 期间再按退出被拦下，等 sessions-down 才退", async () => {
  await withRealApp(async ({ L, llm }) => {
    await runningTurn(L, llm);
    await stubQuit(L, "quit");
    await L.app.evaluate(() => (globalThis as unknown as { __avaTestArm: (n: string) => void }).__avaTestArm("quit-proceed"));
    void L.app.evaluate(({ app }) => app.quit()).catch(() => undefined);
    await expect.poll(() => quitStubCalls(L)).toBe(1);
    await expect.poll(() => L.app.evaluate(() => (globalThis as unknown as { __avaTestHookHits: string[] }).__avaTestHookHits.includes("quit-proceed"))).toBe(true);
    void L.app.evaluate(({ app }) => app.quit()).catch(() => undefined);
    await L.page.waitForTimeout(800);
    expect(await hostPidOf(L)).toBeGreaterThan(0);
    await L.app.evaluate(() => (globalThis as unknown as { __avaTestRelease: (n: string) => void }).__avaTestRelease("quit-proceed"));
    await L.app.close();
  });
});

test("TX-8f 真实 core：stopping 期间 host 崩溃 → 5 s 内退出", async () => {
  await withRealApp(async ({ L, llm }) => {
    await runningTurn(L, llm);
    await stubQuit(L, "quit");
    await L.app.evaluate(() => (globalThis as unknown as { __avaTestArm: (n: string) => void }).__avaTestArm("quit-proceed"));
    void L.app.evaluate(({ app }) => app.quit()).catch(() => undefined);
    await expect.poll(() => L.app.evaluate(() => (globalThis as unknown as { __avaTestHookHits: string[] }).__avaTestHookHits.includes("quit-proceed"))).toBe(true);
    const pid = await hostPidOf(L);
    const t0 = Date.now();
    process.kill(pid!, "SIGKILL");
    await L.app.waitForEvent("close", { timeout: 8_000 });
    expect(Date.now() - t0).toBeLessThan(5_000);
  });
});

// ---------------------------------------------------------------------------
// TX-5 ⑤（Spec 10 PR4，真实 heal 时序）：会话、协议、run_pipeline、H5 heal、回合末 ensure_pending 全部真实；
// 只有 pipeline/clips.py 在副本里换成桩（每跑一次写一版 04-clips.json 与带版本标记的 04-review.html）。
// ---------------------------------------------------------------------------

const CLIPS_STUB = `# TX-5 ⑤ 桩：每次执行写一版确定内容的 04-clips.json（版本号递增）与带版本标记的 04-review.html
import json, pathlib, sys
ep = pathlib.Path(sys.argv[1])
marker = ep / ".clips_version"
n = int(marker.read_text()) + 1 if marker.exists() else 1
marker.write_text(str(n))
seg = {"index": 1, "status": "ok", "duration": 4.0, "clips": [{"season": 1, "episode": 1, "start": 10.0 + n, "dur": 4.0}], "note": "v" * n}
(ep / "04-clips.json").write_text(json.dumps({"anime": "TestAnime", "total_duration": 4.0, "segments": [seg]}, indent=2))
(ep / "04-review.html").write_text("<!doctype html><meta charset=utf-8><title>审片 v%d</title><p data-version=%d>v%d</p>" % (n, n, n))
print("clips v%d" % n)
`;

/** 用真实 core 把 03.5 批掉（不手写对象库）：ensure_pending 建出对象 → approve。 */
function approve035(fx: SessionFixture, ep: string): void {
  const code = [
    "import sys",
    "from pipeline import approvals",
    "obj = approvals.ensure_pending(sys.argv[1])",
    "assert obj is not None and obj.type == '03.5', obj",
    "approvals.approve(sys.argv[1], '03.5', approval_id=obj.approval_id, source='test')",
  ].join("\n");
  execFileSync(join(fx.repo.root, ".venv/bin/python"), ["-c", code, join(fx.repo.eps, ep)], { cwd: fx.repo.root, env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1" } });
}

/** 对象库里钉住「当前 04-clips.json 指纹」的 05 pending 的 approval_id（没有则 null）。 */
function pending05For(fx: SessionFixture, ep: string): string | null {
  const dir = join(fx.repo.eps, ep);
  let items: { approval_id: string; type: string; status: string; artifacts: { path: string; size: number; mtime_ns: bigint | number }[] }[];
  try {
    items = parseLossless(readFileSync(join(dir, "_agent/approvals_store.json"), "utf-8")) as typeof items;
  } catch {
    return null;
  }
  let st;
  try {
    st = statSync(join(dir, "04-clips.json"), { bigint: true });
  } catch {
    return null; // 第一版还没写出来
  }
  const hit = items.find(
    (o) => o.type === "05" && o.status === "pending" && o.artifacts.some((a) => a.path === "04-clips.json" && BigInt(a.size) === st.size && BigInt(a.mtime_ns) === st.mtimeNs),
  );
  return hit?.approval_id ?? null;
}

async function tx5v(round: number): Promise<void> {
  const llm = await startFakeLlm();
  const fx = realCoreFixture(llm.url, ["SESS-A"], { at035: true });
  fixtureWrite(fx.repo.root, "pipeline/clips.py", CLIPS_STUB);
  approve035(fx, "SESS-A");
  const L = await launchSession(fx.repo);
  try {
    llm.push(toolCalls({ name: "run_pipeline", args: { command: "clips" } }), toolCalls({ name: "run_pipeline", args: { command: "clips" } }), assistant("两版都排好了"));
    await openEp(L.page, "SESS-A");
    await expect(L.page.getByTestId("current-step")).toContainText("04");
    const count0 = Number(await L.page.locator(".preview").getAttribute("data-auto-open-count"));
    await send(L.page, "排两版");
    await L.page.getByTestId("request-answer-approve").click();
    // 同步点（四轮 🔵-4）：第一版的 05 pending 确实在回合中被建出，且未被呼出
    let v1: string | null = null;
    await expect.poll(() => (v1 = pending05For(fx, "SESS-A")), { timeout: 20_000, message: `round ${round}: 第一版 pending 未建出` }).not.toBeNull();
    expect(Number(await L.page.locator(".preview").getAttribute("data-auto-open-count"))).toBe(count0);
    expect(await L.page.locator(".preview").getAttribute("data-auto-open-approval-id")).not.toBe(v1);
    await L.page.getByTestId("request-answer-approve").click();
    await waitTurns(L, "ep:SESS-A", 1);
    await expect.poll(async () => Number(await L.page.locator(".preview").getAttribute("data-auto-open-count")), { timeout: 15_000 }).toBe(count0 + 1);
    const v2 = pending05For(fx, "SESS-A");
    expect(v2).not.toBeNull();
    expect(v2).not.toBe(v1);
    expect(await L.page.locator(".preview").getAttribute("data-auto-open-approval-id")).toBe(v2);
    const settled = (await convSnapshot(L, "ep:SESS-A")).entries.filter((e) => e.k === "settled") as unknown as { timedOut: boolean }[];
    expect(settled.map((s) => s.timedOut)).toEqual([false]);
    const health = (await L.page.evaluate(() => (window as unknown as { __avaTestHealth: () => Promise<{ diagnostics: string[] }> }).__avaTestHealth())) as { diagnostics: string[] };
    expect(health.diagnostics.filter((d) => d.includes("回合结算超时"))).toEqual([]);
  } finally {
    await stubQuit(L, "quit").catch(() => undefined);
    await L.app.close().catch(() => undefined);
    await llm.close();
    fx.cleanup();
  }
}

test("TX-5 ⑤ 真实 heal 时序：回合内连改两版 → 结算后恰呼出第二版一次（连续 10 次一致）", async () => {
  test.setTimeout(15 * 60_000);
  for (let round = 1; round <= 10; round++) await tx5v(round);
});

// ---------------------------------------------------------------------------
// A8 实测（Spec 10 §1 假设表）：render 等 job 刷 log 帧时 renderer 是否卡顿。
// 测量用例，不设阈值（阈值得由这次的数据来定，不能先拍）：把读数打进测试输出，回填 spec。
// 唯一的断言是「洪峰真的发生了」——否则读数没有意义。
// ---------------------------------------------------------------------------

const QC_FLOOD = `# A8 桩：一口气打 LINES 行，模拟 render 的日志洪峰
import sys
LINES = 20000
for i in range(LINES):
    sys.stdout.write("[qc] line %05d %s\\n" % (i, "x" * 60))
sys.stdout.flush()
sys.exit(1)
`;

test("A8 实测：2 万行 job 日志经真实 core → log 帧洪峰期间 renderer 的最大帧间隔", async () => {
  test.setTimeout(5 * 60_000);
  const llm = await startFakeLlm();
  const fx = realCoreFixture(llm.url);
  fixtureWrite(fx.repo.root, "pipeline/qc.py", QC_FLOOD);
  const L = await launchSession(fx.repo);
  try {
    llm.push(toolCalls({ name: "run_pipeline", args: { command: "qc" } }), assistant("质检跑完了"));
    await openEp(L.page, "SESS-A");
    await L.page.evaluate(() => {
      const w = window as unknown as { __a8: { t: number[]; on: boolean } };
      w.__a8 = { t: [], on: true };
      const tick = (ts: number) => {
        if (!w.__a8.on) return;
        w.__a8.t.push(ts);
        requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
    });
    await send(L.page, "跑质检");
    await L.page.getByTestId("request-answer-approve").click();
    const t0 = Date.now();
    await waitTurns(L, "ep:SESS-A", 1, 120_000);
    const wall = Date.now() - t0;
    const ts = await L.page.evaluate(() => {
      const w = window as unknown as { __a8: { t: number[]; on: boolean } };
      w.__a8.on = false;
      return w.__a8.t;
    });
    const gaps = ts.slice(1).map((v, i) => v - ts[i]);
    const snap = await convSnapshot(L, "ep:SESS-A");
    const logs = snap.entries.filter((e) => e.k === "frame" && e.frame?.t === "log").length;
    const report = {
      logFrames: logs,
      framesLost: snap.framesLost,
      turnWallMs: wall,
      rafSamples: ts.length,
      maxGapMs: Math.round(Math.max(...gaps)),
      over100ms: gaps.filter((g) => g > 100).length,
      over250ms: gaps.filter((g) => g > 250).length,
    };
    console.log(`A8 ${JSON.stringify(report)}`);
    expect(logs).toBeGreaterThan(1000); // 洪峰真的发生了
  } finally {
    await stubQuit(L, "quit").catch(() => undefined);
    await L.app.close().catch(() => undefined);
    await llm.close();
    fx.cleanup();
  }
});

test("A5 实测：SESSION_NEW spawn → ready 的延迟（真实 core，空会话记录）", async () => {
  await withRealCore(async ({ L, llm }) => {
    llm.push(assistant("好"));
    await openEp(L.page, "SESS-A");
    await send(L.page, "在吗");
    await waitTurns(L, "ep:SESS-A", 1);
    const snap = await convSnapshot(L, "ep:SESS-A");
    const spawned = snap.entries.find((e) => e.k === "spawned") as unknown as { at: number };
    const ready = snap.entries.find((e) => e.k === "frame" && e.frame?.t === "ready") as unknown as { at: number };
    const started = snap.entries.find((e) => e.k === "frame" && e.frame?.t === "turn_started") as unknown as { at: number };
    const user = snap.entries.find((e) => e.k === "user") as unknown as { at: number } | undefined;
    console.log(`A5 ${JSON.stringify({ spawnToReadyMs: ready.at - spawned.at, readyToTurnStartedMs: started.at - ready.at, userAt: user ? user.at - ready.at : null })}`);
    expect(ready.at).toBeGreaterThanOrEqual(spawned.at);
  });
});
