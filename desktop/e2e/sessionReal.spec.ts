// Spec 10 PR4：真实 core（复制来的真实 `pipeline.agent.protocol`）+ 本地假 LLM 端点重跑 TX。
//
// 纪律（一期 Spec 8 纳秒 mtime 的教训）：期望值不在两侧各写一份对拍。
// - 帧的形状：以真实 core 实际发出的帧为准，逐类与 TS 侧 `convFrames.REQUIRED` 比对键集合（TX-0）；
// - 失败原文：界面文本与真实 `session.jsonl` 里对应 tool 消息的 content 逐字节比对（TX-1）；
// - 配对：假端点对配对不齐的历史回 400，`badPairings == 0` 由真实请求体证明。
import { execFileSync } from "node:child_process";
import { copyFileSync, existsSync, readdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
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

/** 逐帧对拍 TS 表（REQUIRED + CONDITIONAL）：返回每类帧实际出现过的键。 */
function assertFrameContract(frames: Frame[]): Map<string, Set<string>> {
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
  return seen;
}

// ADR-0030（2026-10-12）：run_pipeline 白名单内免卡，不再能当「弹卡的工具」用。测卡片机制的用例改用 bash 硬危险
// `rm stale.tmp`：仍逐次弹卡；夹具里没有这个文件，执行必失败（result.ok=false），零副作用。
const CARD_RM = { name: "bash", args: { command: "rm stale.tmp", description: "清理临时文件" } };
test("TX-0 契约：真实 core 的每一帧都过 parseOutFrame、framesLost==0，且逐类键集合与 REQUIRED 完全一致", async () => {
  await withRealCore(async ({ L, llm }) => {
    // 一轮：只读工具 + 失败的 bash rm（经工具卡）→ 收尾答复
    llm.push(toolCalls({ name: "read_status", args: {} }, CARD_RM), assistant("两步都做完了。"));
    await openEp(L.page, "SESS-A");
    await send(L.page, "看下状态再检查稿件");
    await L.page.getByTestId("request-answer-approve").click();
    const frames = await waitTurns(L, "ep:SESS-A", 1);

    const snap = await convSnapshot(L, "ep:SESS-A");
    expect(snap.framesLost).toBe(0);
    expect(llm.badPairings).toBe(0);

    const seen = assertFrameContract(frames);
    // 这一轮必须真的覆盖到这些帧类，否则上面的逐类比对是空转
    for (const t of ["ready", "turn_started", "tool", "request", "request_closed", "assistant", "turn_finished", "stop_points"]) {
      expect(seen.has(t), `本轮没有出现 ${t} 帧`).toBe(true);
    }
    // 失败的 bash 确实判为非成功（S9-R1：顶层 ok:true、result.ok:false）
    const ends = frames.filter((f) => f.t === "tool" && f.phase === "end");
    expect(ends.map((f) => [f.name, f.ok])).toEqual([
      ["read_status", true],
      ["bash", false],
    ]);
  });
});

test("TX-0c 契约（D81）：卷会话与企划会话的帧过契约；企划会话 read_artifact 读到两卷定稿；卷会话首轮请求含卷表", async () => {
  await withRealCore(async ({ L, llm, repo }) => {
    fixtureWrite(repo.root, "data/episodes/P/00-project.md", [
      "# 00 企划", "", "企划: 测试编年史", "", "## 卷表", "",
      "| 卷序 | 目录 | 年份 | 时长预算 | 片尾 |", "|---|---|---|---|---|",
      "| 1 | 01-a | 2011–2014 | 14-16分钟 | 余韵18秒 |", "| 2 | 02-b | 2015–2019 | 18-20分钟 | 完整保留 |", "",
    ].join("\n"));
    const call = (m: string, p: unknown) =>
      L.page.evaluate(([mm, pp]) => (window as unknown as { __avaTestCall: (m: string, p: unknown) => Promise<unknown> }).__avaTestCall(mm as string, pp), [m, p] as const);

    // 企划会话：上位需求 A 验收口径的机检版——不切会话就能读两卷的 02-script.md
    llm.push(toolCalls({ name: "read_artifact", args: { path: "01-a/02-script.md" } }, { name: "read_artifact", args: { path: "02-b/02-script.md", area: "project" } }), assistant("两卷都读到了。"));
    await call("conv.send", { convKey: "proj:P", text: "读两卷定稿" });
    const pf = await waitTurns(L, "proj:P", 1);
    assertFrameContract(pf);
    const pReady = pf.find((f) => f.t === "ready")!;
    expect([pReady.session_kind, pReady.project]).toEqual(["project", "P"]);
    expect(pf.filter((f) => f.t === "tool" && f.phase === "end").map((f) => [f.name, f.ok])).toEqual([["read_artifact", true], ["read_artifact", true]]);
    expect(pf.filter((f) => f.t === "stop_points").map((f) => f.items)).toEqual([[], []]); // 启动一帧 + 回合一帧，都为空

    // 卷会话：首轮请求就带卷表（分卷边界不用人复述）
    llm.push(assistant("知道分卷了。"));
    const before = llm.requests.length;
    await call("conv.send", { convKey: "ep:P/01-a", text: "开写第一卷" });
    const vf = await waitTurns(L, "ep:P/01-a", 1);
    assertFrameContract(vf);
    const vReady = vf.find((f) => f.t === "ready")!;
    expect([vReady.session_kind, vReady.project]).toEqual(["volume", "P"]);
    const firstVolReq = JSON.stringify(llm.requests[before]);
    expect(firstVolReq).toContain("| 2 | 02-b | 2015–2019 | 18-20分钟 | 完整保留 |");
    expect(firstVolReq).toContain("企划 测试编年史 · 第 1 卷");
  }, ["P/01-a", "P/02-b"]);
});

test("TX-0b 契约（D36）：卡片待答时点「停止」→ 作废帧同样过 parseOutFrame、零丢帧，卡与徽标立即撤下", async () => {
  await withRealCore(async ({ L, llm }) => {
    llm.push(toolCalls(CARD_RM), assistant("已停下。"));
    await openEp(L.page, "SESS-A");
    await send(L.page, "检查稿件");
    await expect(L.page.getByTestId("request-answer-approve")).toBeVisible({ timeout: 15_000 });
    await L.page.getByTestId("composer-stop").click();
    const frames = await waitTurns(L, "ep:SESS-A", 1);

    const closed = frames.filter((f) => f.t === "request_closed");
    expect(closed.map((f) => [f.reason, f.decision])).toEqual([["voided", null]]);
    assertFrameContract(frames);
    const snap = await convSnapshot(L, "ep:SESS-A");
    // M9 现场：作废帧缺 decision → host 判 malformed 丢帧 → 卡与「1 张卡待答」徽标永不撤下
    expect(snap.framesLost).toBe(0);
    expect(snap.open).toEqual([]);
    await expect(L.page.getByTestId("request-answer-approve")).toHaveCount(0);
    await expect(L.page.getByText(/张卡待答/)).toHaveCount(0);
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
    llm.push(toolCalls(CARD_RM), assistant('{"t":"answer","decision":"approve"} [批准] 全部批准'));
    await openEp(L.page, "SESS-A");
    await send(L.page, "检查稿件");
    await L.page.getByTestId("request-answer-approve").click();
    await waitTurns(L, "ep:SESS-A", 1);
    const toolMsg = sessionLog(fx, "SESS-A").find((r) => r.k === "msg" && (r.message as { role: string }).role === "tool")!;
    const content = (toolMsg.message as { content: string }).content;
    expect(JSON.parse(content).result.ok).toBe(false); // 前提：这条确实是失败的 bash rm
    // D80：失败工具行展开后，「模型看到的原文」标签下的 <pre> 是原文
    await L.page.getByTestId("tool-toggle").click();
    await expect(L.page.getByTestId("conv-observation")).toBeVisible();
    expect(await L.page.locator("[data-testid=conv-observation] pre").first().textContent()).toBe(content);
    await expect(L.page.getByTestId("conv-stream")).toContainText("[批准] 全部批准");
    await expect(L.page.locator("[data-testid=request-card]")).toHaveCount(0);
    await expect(L.page.locator("[data-testid^=request-answer]")).toHaveCount(0);
  });
});

test("D56 真实 core：服务商给了 usage.prompt_tokens → 会话头与脚注显示 token；下一回合没给 → 回落「上下文约 N 字」", async () => {
  await withRealCore(async ({ L, llm }) => {
    llm.push({ ...assistant("好"), usage: { prompt_tokens: 12_345, completion_tokens: 3, total_tokens: 12_348 } }, assistant("又好"));
    await openEp(L.page, "SESS-A");
    await send(L.page, "第一句");
    await waitTurns(L, "ep:SESS-A", 1);
    await expect(L.page.getByTestId("context-readout")).toHaveText("上下文 12.3k token");
    // D80 R5：脚注默认只显示短摘要，完整遥测点 ⓘ 展开
    await expect(L.page.getByTestId("footer-brief")).toHaveText("1 次模型调用 · 上下文 12.3k token");
    await L.page.getByTestId("footer-more").click();
    await expect(L.page.getByText(/· 查证 字幕 0 · 在场 0 · 笔记 0 · 网页 0 · 上下文 12\.3k token$/)).toBeVisible();
    await send(L.page, "第二句");
    await waitTurns(L, "ep:SESS-A", 2);
    await expect(L.page.getByTestId("context-readout")).toHaveText(/^上下文约 [\d,]+ 字$/);
  });
});

test("TX-2 真实 core：合成点击无效；真实点击恰一次答复、工具被执行", async () => {
  await withRealCore(async (fx) => {
    const { L, llm } = fx;
    llm.push(toolCalls(CARD_RM), assistant("好了"));
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
    expect(sessionLog(fx, "SESS-A").filter((r) => r.k === "tool_exec_started").map((r) => r.name)).toEqual(["bash"]);
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
    llm.push(toolCalls(CARD_RM), assistant("收到，改。"));
    await openEp(L.page, "SESS-A");
    await send(L.page, "检查稿件");
    await L.page.getByTestId("request-answer-reject").waitFor();
    await L.page.getByTestId("request-feedback-toggle").click(); // D80 R6.2：反馈框默认收起
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
      llm.push({ ...toolCalls(CARD_RM), delayMs: 4_000 }, assistant("B 这边好了"));
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

test("TX-7 真实 core：idea 会话聊一轮 → 建期（core 拒绝显示原文；合法名建成并激活，工序条 01）→ 选题记录带入新期（Spec 18）", async () => {
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
    // Spec 18 §3.3 ①：idea-note 两态——带入了就说带入了几条
    await expect(L.page.getByTestId("idea-note")).toHaveAttribute("data-migrated", "1");
    await expect(L.page.getByTestId("idea-note")).toContainText(/已带入选题会话记录（\d+ 条消息）/);
    // 带入 = 复制记录，不是代发消息（H-8；MUT-35 的杀手）：新期没有任何回合，LLM 只收到 idea 那一轮
    await L.page.waitForTimeout(1_500);
    const fresh = await convSnapshot(L, "ep:2026-09-26-e2e-新期");
    expect(fresh.entries.filter((e) => e.k === "user" || (e.k === "frame" && e.frame?.t === "turn_started"))).toEqual([]);
    expect(llm.requests.length).toBe(1);
    // 新期首发不重述：模型直接看得到选题讨论（SESSION_CONTINUE 接着迁入段聊），对话区回放出 assistant 原文
    llm.push(assistant("好，按刚才的草案写"));
    await send(L.page, "把刚才的草案写进 01-topic.md");
    await waitTurns(L, "ep:2026-09-26-e2e-新期", 1);
    const contents = (llm.requests.at(-1)!.messages as { content?: unknown }[]).map((m) => String(m.content ?? ""));
    expect(contents).toContain("想做一期杂谈");
    expect(contents).toContain("先聊聊这期想做什么");
    await expect(L.page.getByTestId("conv-stream")).toContainText("先聊聊这期想做什么");
    expect((await snapFrames(L, "ep:2026-09-26-e2e-新期")).find((f) => f.t === "ready")?.continue_status).toBe("resumed");
    // 再点「选题」：旧讨论已带走，显示「已带入 <期名>」，是全新空会话
    await L.page.getByTestId("idea").click();
    await expect(L.page.getByTestId("idea-carried")).toContainText("已带入 2026-09-26-e2e-新期");
    await expect(L.page.getByTestId("conv-stream")).not.toContainText("先聊聊这期想做什么");
  });
});

test("TX-9 真实 core：卡片打开时 kill -9 会话 → 待答区卡消失、流内「已作废（会话已结束）」", async () => {
  await withRealCore(async ({ L, llm }) => {
    llm.push(toolCalls(CARD_RM));
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
    await expect(L.page.getByTestId("session-head")).toHaveAttribute("data-phase", "exited", { timeout: 20_000 });
    // D45：从侧栏会话列表进入（真 core 的 /list-sessions 列出刚才那个会话）
    await L.page.locator("[data-testid=session-row] button.ep-session").first().click();
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

test("D57 / D58 真实 core：选题会话列表——两段对话 → 退出 app 再打开 → 列表两条 → 点旧段接上旧历史（注入收起）→ 删另一段进回收站", async () => {
  const llm = await startFakeLlm();
  const fx = realCoreFixture(llm.url, ["SESS-A"], {});
  let L = await launchSession(fx.repo);
  const rows = () => L.page.locator("[data-testid=session-list] [data-testid=session-row]");
  try {
    llm.push(assistant("甲的回复"), assistant("乙的回复"), assistant("甲又回复"));
    await L.page.getByTestId("idea").click();
    await expect(L.page.getByTestId("session-list")).toBeVisible();
    await expect(rows()).toHaveCount(0); // 还没聊过
    await send(L.page, "选题甲旧消息");
    await waitTurns(L, "idea", 1);
    await expect(rows()).toHaveCount(1);
    // ＋ 新会话：结束当前段，下一条消息开新段（不恢复甲）
    await L.page.getByTestId("session-new").click();
    await expect(L.page.getByTestId("session-head")).toHaveAttribute("data-phase", "none");
    await send(L.page, "选题乙消息");
    await waitTurns(L, "idea", 1);
    await expect(L.page.getByTestId("conv-stream")).not.toContainText("选题甲旧消息");
    await expect(rows()).toHaveCount(2);
    await stubQuit(L, "quit");
    await L.app.close();

    L = await launchSession(fx.repo);
    await L.page.getByTestId("idea").click();
    await expect(rows()).toHaveCount(2, { timeout: 20_000 });
    await rows().filter({ hasText: "选题甲旧消息" }).locator("button.ep-session").click();
    await expect(L.page.getByTestId("conv-stream")).toContainText("选题甲旧消息", { timeout: 15_000 });
    await expect(L.page.getByTestId("conv-stream")).toContainText("甲的回复");
    await expect(L.page.getByTestId("conv-stream")).not.toContainText("选题乙消息");
    // 恢复出的系统注入折叠成一行，正文默认不展开（Spec 10 §2.3；D57 前是整段平铺）
    const inj = L.page.locator("[data-testid=conv-row][data-kind=injection]").first();
    await expect(inj).toContainText(/^系统注入（\d+ 字/);
    await expect(inj.locator("pre")).toBeHidden();
    await send(L.page, "接着聊甲");
    await expect(L.page.getByTestId("conv-stream")).toContainText("甲又回复", { timeout: 15_000 });
    // 删乙：原生确认框桩答「确认」→ 乙进 data/_idea/_agent/session-trash/，列表只剩甲，当前对话不受影响
    await stubConfirm(L, true);
    const rowB = rows().filter({ hasText: "选题乙消息" });
    await rowB.hover(); // 删除按钮悬停才显示（D45 同款）
    await rowB.getByTestId("session-delete").click();
    await expect(rows()).toHaveCount(1, { timeout: 15_000 });
    await expect(rows().first()).toContainText("选题甲旧消息");
    await expect(L.page.getByTestId("conv-stream")).toContainText("甲又回复");
    const trash = join(fx.repo.root, "data/_idea/_agent/session-trash");
    expect(existsSync(trash) && readdirSync(trash).length).toBe(1);
    expect(llm.badPairings).toBe(0);
  } catch (e) {
    await dumpScene(L, llm, ["SESS-A"]);
    throw e;
  } finally {
    await stubQuit(L, "quit").catch(() => undefined);
    await L.app.close().catch(() => undefined);
    await llm.close();
    fx.cleanup();
  }
});

test("TX-11 真实 core：批准一张工具卡后，时间线该事件显示「命令卡批准」", async () => {
  await withRealCore(async ({ L, llm }) => {
    llm.push(toolCalls(CARD_RM), assistant("好"));
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

// TX-12b/TX-13（抓取卡逐张出、批准须过原生确认框）随 ADR-0030 删除：抓取不再出卡、会话卡的原生确认框已撤。
// 抓取本身的语义（只抓本次输入、去重、序号错位不抓、中断）由 tests/test_agent_session.py TK-2/TK-3 守。

test("TX-14 / ADR-0030 回车批准：卡出现即聚焦「批准」、回车即批；第二张同样；输入框里有字时不抢焦点，回车发的是消息", async () => {
  await withRealCore(async (fx) => {
    const { L, llm } = fx;
    const rmA = { name: "bash", args: { command: "rm a.tmp", description: "清 a" } };
    const rmB = { name: "bash", args: { command: "rm b.tmp", description: "清 b" } };
    const rmC = { name: "bash", args: { command: "rm c.tmp", description: "清 c" } };
    llm.push(toolCalls(rmA), toolCalls(rmB), toolCalls(rmC), assistant("好"));
    await openEp(L.page, "SESS-A");
    await send(L.page, "三步");
    const card = L.page.getByTestId("request-card");
    await expect(card).toHaveCount(1);
    const firstId = await card.getAttribute("data-request-id");
    await expect(card.getByTestId("request-answer-approve")).toBeFocused();
    await L.page.waitForTimeout(500); // 过了防连按窗口
    await L.page.keyboard.press("Enter"); // 真实按键：原生按钮派发 isTrusted 的 click
    await expect.poll(() => closedAnswered(fx).map((r) => r.request_id), { timeout: 15_000 }).toEqual([firstId]);

    // 第二张：随后才出现，同样聚焦、回车即批
    await expect.poll(async () => (await snapFrames(L)).filter((f) => f.t === "request").length, { timeout: 15_000 }).toBe(2);
    const secondId = await card.getAttribute("data-request-id");
    expect(secondId).not.toBe(firstId);
    await expect(card.getByTestId("request-answer-approve")).toBeFocused();
    await L.page.waitForTimeout(500); // 过了防连按窗口
    await L.page.keyboard.press("Enter");
    await expect.poll(() => closedAnswered(fx).length, { timeout: 15_000 }).toBe(2);

    // 第三张出现前人已在输入框里打字：不抢焦点，回车发出的是排队消息，卡不被批
    const input = L.page.getByTestId("composer-input");
    await input.click();
    await input.fill("先别删 c");
    await expect.poll(async () => (await snapFrames(L)).filter((f) => f.t === "request").length, { timeout: 15_000 }).toBe(3);
    await expect(input).toBeFocused();
    await input.press("Enter");
    await expect(L.page.getByTestId("composer-queued")).toContainText("先别删 c");
    await L.page.waitForTimeout(300);
    expect(closedAnswered(fx)).toHaveLength(2);
    await expect(card).toBeVisible();
  });
});

test("TX-15 真实 core：有打开卡时 kill -9 host → 原会话键结束、待答区为空；「继续上次会话」恢复历史", async () => {
  test.setTimeout(4 * 60_000);
  await withRealCore(async ({ L, llm }) => {
    llm.push(assistant("第一轮答复"), toolCalls(CARD_RM));
    await openEp(L.page, "SESS-A");
    await send(L.page, "第一轮");
    await waitTurns(L, "ep:SESS-A", 1);
    await send(L.page, "第二轮");
    await expect(L.page.getByTestId("request-card")).toBeVisible();
    const hostPid = await L.app.evaluate(() => (globalThis as unknown as { __avaTestHostPid: () => number | null }).__avaTestHostPid());
    process.kill(hostPid!, "SIGKILL");
    await expect(L.page.getByTestId("request-card")).toHaveCount(0, { timeout: 20_000 });
    // 孤儿会话读到 EOF 后按 Spec 9 收尾、释放租约，「继续」才拿得到锁（RF-6）
    await expect(L.page.getByTestId("session-head")).toHaveAttribute("data-phase", "none", { timeout: 20_000 });
    await expect(async () => {
      await L.page.locator("[data-testid=session-row] button.ep-session").first().click();
      await expect(L.page.getByTestId("conv-stream")).toContainText("以下为恢复的历史", { timeout: 3_000 });
    }).toPass({ timeout: 90_000 });
    await expect(L.page.getByTestId("conv-stream")).toContainText("第一轮答复");
  });
});

test("D45 真实 core：侧栏会话列表——新会话开出第二个会话；删除取消则一字不动，确认则移进回收站、另一个会话照旧在", async () => {
  test.setTimeout(3 * 60_000);
  await withRealCore(async (fx) => {
    const { L, llm } = fx;
    llm.push(assistant("第一个会话的答复"), assistant("第二个会话的答复"));
    await openEp(L.page, "SESS-A");
    await send(L.page, "第一个会话");
    await waitTurns(L, "ep:SESS-A", 1);
    await L.page.getByTestId("session-new").click();
    await expect(L.page.getByTestId("session-head")).toHaveAttribute("data-phase", "none", { timeout: 20_000 });
    await expect(L.page.locator("[data-testid=conv-row][data-kind=answer]")).toHaveCount(0);
    await send(L.page, "第二个会话");
    await expect(L.page.getByTestId("conv-stream")).toContainText("第二个会话的答复", { timeout: 20_000 });
    await expect(L.page.getByTestId("session-row")).toHaveCount(2);
    const sids = [...new Set(sessionLog(fx, "SESS-A").map((r) => String(r.sid)))];
    expect(sids).toHaveLength(2);
    const [first, second] = sids;
    // 新会话的帧带上了自己的 sid（D45 修的协议缺陷），列表据此标出活会话
    await expect(L.page.locator(`[data-testid=session-row][data-sid="${second}"]`)).toHaveAttribute("data-live", "1");
    const logBefore = readFileSync(join(fx.repo.eps, "SESS-A", "session.jsonl"), "utf-8");

    await stubConfirm(L, false);
    await L.page.locator(`[data-testid=session-row][data-sid="${first}"]`).hover();
    await L.page.locator(`[data-testid=session-row][data-sid="${first}"] [data-testid=session-delete]`).click();
    await expect.poll(() => L.app.evaluate(() => (globalThis as unknown as { __avaTestConfirm: { calls: number } }).__avaTestConfirm.calls)).toBe(1);
    expect(readFileSync(join(fx.repo.eps, "SESS-A", "session.jsonl"), "utf-8")).toBe(logBefore);
    await expect(L.page.getByTestId("session-row")).toHaveCount(2);

    await stubConfirm(L, true);
    await L.page.locator(`[data-testid=session-row][data-sid="${first}"]`).hover();
    await L.page.locator(`[data-testid=session-row][data-sid="${first}"] [data-testid=session-delete]`).click();
    await expect(L.page.getByTestId("session-row")).toHaveCount(1, { timeout: 20_000 });
    expect(existsSync(join(fx.repo.eps, "SESS-A", "_agent", "session-trash", `${first}.jsonl`))).toBe(true);
    expect(sessionLog(fx, "SESS-A").every((r) => r.sid === second)).toBe(true);
    // 删的是另一个会话：为释放租约结束的当前会话被接回来，对话照旧
    await expect(L.page.locator(`[data-testid=session-row][data-sid="${second}"]`)).toHaveAttribute("data-live", "1", { timeout: 20_000 });
    await expect(L.page.getByTestId("conv-stream")).toContainText("第二个会话的答复");
  });
});

// N49：会话 spawn 的 PATH 白名单里没有 /opt/homebrew/bin，会话里 run_pipeline 起的作业（jobs.py 原样继承
// 会话 env）按名字调 ffmpeg/ffprobe 就 FileNotFoundError。既有真实 core 用例只跑 check_script（不碰 ffmpeg），
// 所以一直没暴露。这里用 qc：它先 ffprobe 两条流的时长，再用 ffmpeg 测响度，两个程序都走到。
// qc 在 5 s 夹具上本身判不合格（片长、响度），作业退出码必非 0——断言落在「ffmpeg/ffprobe 真的跑了」，不在作业成败。
const HOMEBREW_FFMPEG = "/opt/homebrew/bin/ffmpeg";

test("N49 真实 core：会话里跑 run_pipeline qc → 作业能按名字找到 ffprobe/ffmpeg（06-check.log 有实测的音画时长与响度）", async () => {
  // 修法假定 ffmpeg 在 /opt/homebrew/bin（Apple Silicon Homebrew）；不在就不是这条用例能证明的环境
  expect(existsSync(HOMEBREW_FFMPEG), `${HOMEBREW_FFMPEG} 不存在`).toBe(true);
  await withRealCore(async (fx) => {
    const { L, llm } = fx;
    const epDir = join(fx.repo.eps, "SESS-A");
    execFileSync(HOMEBREW_FFMPEG, ["-nostdin", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", "5", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", join(epDir, "05-final.mp4")]);
    llm.push(toolCalls({ name: "run_pipeline", args: { command: "qc" } }), assistant("质检跑完了。"));
    await openEp(L.page, "SESS-A");
    await send(L.page, "跑一下质检"); // ADR-0030：qc 免卡，直接跑
    await waitTurns(L, "ep:SESS-A", 1);

    const toolMsg = sessionLog(fx, "SESS-A").find((r) => r.k === "msg" && (r.message as { role: string }).role === "tool")!;
    const content = (toolMsg.message as { content: string }).content;
    const result = JSON.parse(content).result as { argv: string[] };
    expect(result.argv.slice(1, 3)).toEqual(["-m", "pipeline.qc"]); // 前提：跑的确实是 qc 作业
    expect(content, "作业按名字找不到 ffmpeg/ffprobe").not.toMatch(/No such file or directory: '(ffprobe|ffmpeg)'/);
    // 两条都只能由真实跑过的探测写出：音画时长对齐 = ffprobe 读到两条流；响度读数 = ffmpeg ebur128 跑完
    const log = readFileSync(join(epDir, "06-check.log"), "utf-8");
    expect(log).toMatch(/PASS\s+音画时长对齐\s+画面 5\.000s \/ 音频 5\.000s/);
    expect(log).toMatch(/响度 .*-\d+\.\d+ LUFS/);
  });
});

// D77 / ADR-0029：bash 工具的原病例——人给一张期目录之外的图，ava 自己拷进封面目录并改名，人只批卡。
// 第二条只读命令（ls）免卡直接跑；会话注入的 LLM 密钥变量（AVA_TEST_KEY）不传给 bash 子进程。
// D76（人选方案 C）：同一场景里三处「复制」各点一次，系统剪贴板读回的必须是原文（不带卡面「│」前缀、不是截断的摘要）。
// 剪贴板是人的：测试前存、测试后还原。
const clipboardText = (L: Launched): Promise<string> => L.app.evaluate(({ clipboard }) => clipboard.readText());
/** 每次点复制前先写哨兵：否则上一处复制留下的同一段原文会让「这一处根本没写进去」的情况照样通过 */
const armClipboard = (L: Launched): Promise<void> => L.app.evaluate(({ clipboard }) => clipboard.writeText("<D76 哨兵>"));

test("D77 / ADR-0030 真实 core：bash 清旧图并拷图进期目录 → 硬危险命令卡（危险标记在命令前）→ 批准后文件落地；只读 ls 免卡；子进程无密钥变量；D76 三处复制得原文", async () => {
  await withRealCore(async (fx) => {
    const { L, llm } = fx;
    const savedClipboard = await clipboardText(L);
    const src = join(fx.repo.root, "Weixin Image 1.jpg");
    writeFileSync(src, Buffer.from([0xff, 0xd8, 0x66, 0x61, 0x6b, 0x65]));
    // ADR-0030：mkdir / cp 已免卡；前面带一个 rm -f（清旧图）使它仍是硬危险命令卡，D76 ①卡上复制照测
    const cmd = `rm -f 07-cover/import/old.jpg; mkdir -p 07-cover/import && cp '${src}' 07-cover/import/marika-h.jpg && echo "[$AVA_TEST_KEY]"`;
    llm.push(
      toolCalls({ name: "bash", args: { command: cmd, description: "把人给的图拷进封面目录并改名" } }),
      toolCalls({ name: "bash", args: { command: "ls 07-cover/import", description: "确认文件在" } }),
      assistant(`放好了。要再看一眼可以跑：\n\n\`\`\`bash\nls -la 07-cover/import && ffprobe -v error 07-cover/import/marika-h.jpg\n\`\`\``),
    );
    await openEp(L.page, "SESS-A");
    await send(L.page, "把这张图放进本期封面目录");
    const cardText = L.page.getByTestId("request-card-text");
    await expect(cardText).toContainText("命令审批", { timeout: 15_000 });
    await expect(cardText).toContainText("把人给的图拷进封面目录并改名");
    await expect(cardText).toContainText("07-cover/import/marika-h.jpg");
    const text = (await cardText.textContent()) ?? "";
    expect(text.indexOf("危险标记:")).toBeLessThan(text.indexOf("命令:"));
    expect(text).toContain("[删除]");
    try {
      // D76 ①审批卡：复制的是请求参数里的命令原文
      await armClipboard(L);
      await L.page.getByTestId("card-copy").click();
      await expect(L.page.getByTestId("card-copy")).toHaveText("✓ 已复制");
      expect(await clipboardText(L)).toBe(cmd);
      await L.page.getByTestId("request-answer-approve").click();
      await waitTurns(L, "ep:SESS-A", 1);
      // 帧契约：bash 的 tool 帧带 command，键集合与 TS 表（REQUIRED + CONDITIONAL）两个方向都一致
      assertFrameContract(framesOf(await convSnapshot(L, "ep:SESS-A")));
      // D76 ②工具行：两条 bash 行各有「复制命令」，第一条复制出来是完整命令（摘要截在 80 字）。
      // D80：这一轮全成功 → 回合已收起、两条 bash 折成终端组也收起，先依次展开
      await L.page.getByTestId("turn-fold").click();
      await L.page.getByTestId("group-toggle").click();
      const toolCopies = L.page.getByTestId("tool-copy");
      await expect(toolCopies).toHaveCount(2);
      await armClipboard(L);
      await toolCopies.first().click();
      await expect(toolCopies.first()).toHaveText("✓ 已复制");
      expect(await clipboardText(L)).toBe(cmd);
      // D76 ③助手回复的代码块：复制出来是块内原文，不带末尾换行
      await armClipboard(L);
      await L.page.getByTestId("code-copy").click();
      await expect(L.page.getByTestId("code-copy")).toHaveText("✓ 已复制");
      expect(await clipboardText(L)).toBe("ls -la 07-cover/import && ffprobe -v error 07-cover/import/marika-h.jpg");
    } finally {
      await L.app.evaluate(({ clipboard }, t) => clipboard.writeText(t), savedClipboard);
    }

    const epDir = join(fx.repo.eps, "SESS-A");
    expect([...readFileSync(join(epDir, "07-cover/import/marika-h.jpg"))]).toEqual([0xff, 0xd8, 0x66, 0x61, 0x6b, 0x65]);
    const results = sessionLog(fx, "SESS-A")
      .filter((r) => r.k === "msg" && (r.message as { role: string }).role === "tool")
      .map((r) => JSON.parse((r.message as { content: string }).content) as { ok: boolean; result: { returncode: number; stdout_tail: string } });
    expect(results).toHaveLength(2);
    expect(results[0].result.returncode).toBe(0);
    expect(results[0].result.stdout_tail.trim()).toBe("[]"); // 密钥变量没传给子进程
    expect(results[1].result.stdout_tail.trim()).toBe("marika-h.jpg"); // ls 跑了，且没再弹卡（只批过一张）
    await expect(L.page.getByTestId("request-answer-approve")).toHaveCount(0);
  });
});

test("D76 真实 core：继续上次会话 → 恢复出来的 bash 旧工具行显示工具名与命令摘要，「复制命令」得完整原文", async () => {
  await withRealCore(async ({ L, llm }) => {
    const cmd = `ls -la . && echo "${"一段很长的说明文字".repeat(12)}"`; // 超 80 字：摘要截断、复制得原文（ADR-0030 起免卡）
    llm.push(toolCalls({ name: "bash", args: { command: cmd, description: "看一眼目录" } }), assistant("看完了。"));
    await openEp(L.page, "SESS-A");
    await send(L.page, "看看目录");
    await waitTurns(L, "ep:SESS-A", 1);
    await L.page.getByTestId("session-end").click();
    await expect(L.page.getByTestId("session-head")).toHaveAttribute("data-phase", "exited", { timeout: 20_000 });
    await L.page.locator("[data-testid=session-row] button.ep-session").first().click();
    await expect(L.page.getByTestId("conv-stream")).toContainText("以下为恢复的历史", { timeout: 15_000 });
    // history 帧键集合两个方向都与 TS 表一致（bash 条目带 command）
    assertFrameContract(framesOf(await convSnapshot(L, "ep:SESS-A")).filter((f) => f.t === "history"));
    // D80：恢复出来的一轮视为已结束 → 收起；展开后工具行显示类别词、用途（history 帧带 description）与命令摘要
    await L.page.getByTestId("turn-fold").last().click();
    const row = L.page.locator("[data-testid=conv-row][data-kind=tool]").filter({ has: L.page.getByTestId("tool-copy") });
    await expect(row).toHaveCount(1);
    await expect(row).toContainText("终端");
    await expect(row).toContainText("看一眼目录");
    await expect(row).toContainText("ls -la . && echo");
    const saved = await clipboardText(L);
    try {
      await armClipboard(L);
      await row.getByTestId("tool-copy").click();
      await expect(row.getByTestId("tool-copy")).toHaveText("✓ 已复制");
      expect(await clipboardText(L)).toBe(cmd);
    } finally {
      await L.app.evaluate(({ clipboard }, t) => clipboard.writeText(t), saved);
    }
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
    // ADR-0030：clips 免卡；两版之间插一张仍弹卡的 CARD_RM 当「回合还在进行」的同步闸门（原来由第二张 clips 卡承担）
    llm.push(toolCalls({ name: "run_pipeline", args: { command: "clips" } }), toolCalls(CARD_RM), toolCalls({ name: "run_pipeline", args: { command: "clips" } }), assistant("两版都排好了"));
    await openEp(L.page, "SESS-A");
    await expect(L.page.getByTestId("current-step")).toContainText("04");
    const count0 = Number(await L.page.locator(".preview").getAttribute("data-auto-open-count"));
    await send(L.page, "排两版");
    await expect(L.page.getByTestId("request-card")).toBeVisible({ timeout: 20_000 });
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
    const t0 = Date.now();
    await send(L.page, "跑质检"); // ADR-0030：qc 免卡，发出即跑
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

test("D66 真实 core：删除进回收站 → 分组出现（折叠/标记文案/同 sid 两文件）→ 逐段彻底清空、主日志其余段还在", async () => {
  test.setTimeout(3 * 60_000);
  await withRealCore(async (fx) => {
    const { L, llm } = fx;
    const confirmCalls = () => L.app.evaluate(() => (globalThis as unknown as { __avaTestConfirm: { calls: number } }).__avaTestConfirm.calls);
    llm.push(assistant("第一个会话的答复"), assistant("第二个会话的答复"));
    await openEp(L.page, "SESS-A");
    await send(L.page, "第一个会话");
    await waitTurns(L, "ep:SESS-A", 1);
    await L.page.getByTestId("session-new").click();
    await expect(L.page.getByTestId("session-head")).toHaveAttribute("data-phase", "none", { timeout: 20_000 });
    await send(L.page, "第二个会话");
    await expect(L.page.getByTestId("conv-stream")).toContainText("第二个会话的答复", { timeout: 20_000 });
    const sids = [...new Set(sessionLog(fx, "SESS-A").map((r) => String(r.sid)))];
    expect(sids).toHaveLength(2);
    const [first, second] = sids;
    const trashDir = join(fx.repo.eps, "SESS-A", "_agent", "session-trash");

    // 空回收站不渲染分组
    await expect(L.page.getByTestId("trash-group")).toHaveCount(0);

    // 删第一个会话 → 分组出现；默认折叠；展开看到该行（sid 前缀 + 大小）
    await stubConfirm(L, true);
    const rowA = L.page.locator(`[data-testid=session-row][data-sid="${first}"]`);
    // 悬停才显示的按钮 + 列表重取会重渲（D66 多了一次 trashList 往返）：hover+click 整体重试
    await expect(async () => {
      await rowA.hover();
      await rowA.getByTestId("session-delete").click({ timeout: 2_000 });
    }).toPass({ timeout: 30_000 });
    await expect(L.page.getByTestId("session-row")).toHaveCount(1, { timeout: 20_000 });
    await expect(L.page.getByTestId("trash-group")).toHaveCount(1, { timeout: 20_000 });
    await expect(L.page.getByTestId("trash-row")).toHaveCount(0);
    await L.page.getByTestId("trash-toggle").click();
    await expect(L.page.getByTestId("trash-row")).toHaveCount(1);
    await expect(L.page.getByTestId("trash-row").first()).toContainText(first.slice(0, 8));
    expect(await L.page.getByTestId("trash-row").first().locator(".ep-trash-row").getAttribute("title")).toContain("KB");

    // 手造四种形态（同 sid 时间戳副本 = 手动搬回再删；空文件；无法解析；末尾残行）
    copyFileSync(join(trashDir, `${first}.jsonl`), join(trashDir, `${first}-20261010T010203000000.jsonl`));
    writeFileSync(join(trashDir, "eeeeeeeeeeeeeee5.jsonl"), "");
    writeFileSync(join(trashDir, "fffffffffffffff6.jsonl"), "这不是会话记录\n");
    writeFileSync(join(trashDir, "9999999999999997.jsonl"), readFileSync(join(trashDir, `${first}.jsonl`)) + '{"k": "msg", "sid": "999');

    // 确认取消 → 一字不动（确认框在宿主层）；act 结束照常重取，手造文件随这次重取进列表
    await stubConfirm(L, false);
    const before = await confirmCalls();
    await expect(async () => {
      await L.page.getByTestId("trash-row").first().hover();
      await L.page.getByTestId("trash-row").first().getByTestId("trash-purge").click({ timeout: 2_000 });
    }).toPass({ timeout: 30_000 });
    await expect.poll(confirmCalls, { timeout: 10_000 }).toBe(before + 1);
    await expect(L.page.getByTestId("trash-row")).toHaveCount(5, { timeout: 20_000 });
    expect(readdirSync(trashDir).filter((f) => f.endsWith(".jsonl"))).toHaveLength(5);
    expect(existsSync(join(trashDir, `${first}.jsonl`))).toBe(true);

    // 标记文案与同 sid 区分
    await expect(L.page.locator('[data-testid=trash-row][data-file="eeeeeeeeeeeeeee5.jsonl"]')).toContainText("空文件");
    await expect(L.page.locator('[data-testid=trash-row][data-file="fffffffffffffff6.jsonl"]')).toContainText("无法解析");
    await expect(L.page.locator('[data-testid=trash-row][data-file="9999999999999997.jsonl"]')).toContainText("记录不全");
    await expect(L.page.locator(`[data-testid=trash-row][data-file="${first}-20261010T010203000000.jsonl"]`)).toContainText("20261010T010203000000");

    // 逐段彻底清空：每段一次确认；删完分组消失；主日志只剩第二段；文件系统上 .jsonl 不存在
    await stubConfirm(L, true);
    for (const f of [`${first}.jsonl`, `${first}-20261010T010203000000.jsonl`, "eeeeeeeeeeeeeee5.jsonl", "fffffffffffffff6.jsonl", "9999999999999997.jsonl"]) {
      const row = L.page.locator(`[data-testid=trash-row][data-file="${f}"]`);
      await expect(async () => {
        await row.hover();
        await row.getByTestId("trash-purge").click({ timeout: 2_000 });
      }).toPass({ timeout: 30_000 });
      await expect(L.page.locator(`[data-testid=trash-row][data-file="${f}"]`)).toHaveCount(0, { timeout: 20_000 });
      expect(existsSync(join(trashDir, f))).toBe(false);
    }
    await expect(L.page.getByTestId("trash-group")).toHaveCount(0, { timeout: 20_000 });
    expect(sessionLog(fx, "SESS-A").every((r) => r.sid === second)).toBe(true);
    await expect(L.page.getByTestId("session-row")).toHaveCount(1);
    expect(llm.badPairings).toBe(0);
  });
});
