// PR4 ack 链路端到端（Spec 8 §7.1 TA 系列 + TI-3b/6/8/9/10）：UI 按钮 → host spawn → core 对象库。
// 每条用例一个临时 repo 副本（假设 5：.venv 软链后 import 的是副本里的 pipeline）；未打包构建。
import { execFileSync, spawnSync } from "node:child_process";
import { existsSync, readFileSync, realpathSync, statSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { expect, test } from "@playwright/test";
import { cleanup, treeManifest } from "../tests/helpers";
import {
  ACK_TEMPLATES,
  ackRepo,
  arm,
  ava,
  call,
  card,
  clipsJson,
  decisionLines,
  dialogCalls,
  epAt03,
  epAt035,
  epAt05,
  epRow,
  eventsOf,
  fakeCore,
  fpy,
  heals,
  hostPid,
  lastDecideSpawns,
  NS,
  openEp,
  readStore,
  settled,
  release,
  setDialog,
  spawns,
  statusJson,
  storeText,
  waitHit,
  writeClips,
  writeManifest,
  type AckRepo,
} from "./ackFixtures";
import { DESKTOP, ELECTRON, launch, type Launched } from "./fixtures";

const opened: { L?: Launched; R: AckRepo[] }[] = [];
async function start(R: AckRepo, extra: string[] = []): Promise<Launched> {
  const L = await launch(R.repo, extra);
  opened.push({ L, R: [R] });
  return L;
}
test.afterEach(async () => {
  for (const o of opened.splice(0)) {
    await o.L?.app.close().catch(() => undefined);
    for (const r of o.R) {
      cleanup(r.repo);
      if (r.dataReal) cleanup(r.dataReal);
    }
  }
});

const py = (R: AckRepo) => join(R.repo, ".venv/bin/python");
const ackSpawns = (L: Launched) => spawns(L).filter((s) => ACK_TEMPLATES.includes(s.template));
const errOf = (L: Launched) => L.page.getByTestId("decision-err");

test("TA-1 打回（无损）：argv 恰为 REJECT 模板、target/problem 各占一个元素；对象 rejected、feedback 逐字节相等；反馈文件含原文", async () => {
  const R = ackRepo();
  const ep = epAt05(R.repo, R.eps, "TA1");
  const L = await start(R);
  await openEp(L.page, "TA1");
  const c = card(L.page, "05");
  const A = (await c.getAttribute("data-approval-id"))!;
  const target = "04-clips.json s07 1:20";
  const problem = '-画面与台词错位\n第二句 "对白" 对不上\n--force 也别想';
  await c.getByTestId("reject-open").click();
  await c.getByTestId("reject-target").fill(target);
  await c.getByTestId("reject-problem").fill(problem);
  await c.getByTestId("reject-submit").click();
  await expect(L.page.getByTestId("decision-ok")).toContainText("已打回");
  const d = lastDecideSpawns(L);
  expect(d.map((s) => s.template)).toEqual(["HEAL", "REJECT"]);
  // TI-8：合法请求的 spawn argv 中期路径恒等于 host 映射路径
  expect(d[1].argv).toEqual([py(R), "-m", "pipeline.agent.cli", ep, "/reject", "05", "--id", A, target, problem]);
  const obj = readStore(ep).find((o) => o.approval_id === A)!;
  expect(obj.status).toBe("rejected");
  expect(obj.feedback).toEqual({ target, problem });
  const md = readFileSync(join(ep, "_agent/approval_feedback.md"), "utf-8");
  expect(md).toContain(target);
  expect(md).toContain(problem);
});

test("TA-2 05 批准（两步 + 确认路径）：spawn 序列、纳秒 argv 逐字节、解封物字节与指纹、确认路径记账", async () => {
  const R = ackRepo({ base: process.env.AVA_E2E_EXTERNAL_BASE }); // 假设 8：设了该变量就在外置盘上跑
  const ep = epAt05(R.repo, R.eps, "TA2", { nsMtime: true });
  const L = await start(R);
  await openEp(L.page, "TA2");
  const c = card(L.page, "05");
  const A = (await c.getAttribute("data-approval-id"))!;
  const linesBefore = decisionLines(ep).length;
  await c.getByTestId("approve").click();
  await expect(L.page.getByTestId("decision-ok")).toContainText("已批准");
  const d = lastDecideSpawns(L);
  expect(d.map((s) => s.template)).toEqual(["HEAL", "REVIEW_APPROVE", "APPROVE"]);
  expect(d[0].trigger).toBe("H4-pre-ack");
  const raw = /"mtime_ns":\s*(-?\d+)/.exec(storeText(ep))![1];
  expect(raw).toBe(NS);
  const size = statSync(join(ep, "04-clips.json")).size;
  expect(d[1].argv).toEqual([py(R), "-m", "pipeline.agent.cli", ep, "/run", "review", "--approve", `--expect-size=${size}`, `--expect-mtime-ns=${raw}`]);
  expect(d[2].argv).toEqual([py(R), "-m", "pipeline.agent.cli", ep, "/approve", "05", "--id", A]);
  const src = statSync(join(ep, "04-clips.json"), { bigint: true });
  const gate = statSync(join(ep, "04-clips.approved.json"), { bigint: true });
  expect(readFileSync(join(ep, "04-clips.approved.json"))).toEqual(readFileSync(join(ep, "04-clips.json")));
  expect([gate.size, gate.mtimeNs]).toEqual([src.size, src.mtimeNs]);
  expect(String(gate.mtimeNs)).toBe(NS);
  const obj = readStore(ep).find((o) => o.approval_id === A)!;
  expect([obj.status, obj.resolved_by, obj.confirmed_by]).toEqual(["approved", "artifact", "cli"]);
  const lines = decisionLines(ep);
  expect(lines.length).toBe(linesBefore + 1);
  expect(lines.at(-1)!.decision_latency_s).not.toBeNull(); // 对齐发生在同一次 approve 调用中（S3-R11）
  const ev = eventsOf(ep);
  const reviewJob = ev.find((e) => e.type === "job_created" && String(e.payload.command).startsWith("review --approve"))!;
  expect(ev.some((e) => e.type === "job_finished" && e.payload.job_id === reviewJob.payload.job_id && e.payload.status === "succeeded")).toBe(true);
  expect(ev.some((e) => e.type === "approval_resolved" && e.payload.approval_id === A && e.payload.confirms === "artifact")).toBe(true);
});

test("TA-2b 05 已在终端批准后确认：序列恰为 [HEAL, APPROVE]，无 REVIEW_APPROVE；延迟为空", async () => {
  const R = ackRepo();
  const ep = epAt05(R.repo, R.eps, "TA2B", { patch: true });
  // 真实流程：桌面端（或终端 /approvals）先建出 05 pending，人再去终端批准含补丁段的排片；否则工序已到 06，自愈不会补建 05 对象
  expect(ava(R.repo, [ep, "/approvals"]).code).toBe(0);
  const t = spawnSync(py(R), ["-m", "pipeline.review", ep, "--approve"], { cwd: R.repo, input: "y\n", encoding: "utf-8", env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1" } });
  expect(t.status, t.stderr).toBe(0);
  const L = await start(R);
  await openEp(L.page, "TA2B"); // H1 自愈把对象对齐为 APPROVED(artifact)
  const c = card(L.page, "05");
  await expect(c).toContainText("待你确认");
  const A = (await c.getAttribute("data-approval-id"))!;
  const linesBefore = decisionLines(ep).length;
  await c.getByTestId("approve").click();
  await expect(L.page.getByTestId("decision-ok")).toContainText("已确认批准");
  expect(lastDecideSpawns(L).map((s) => s.template)).toEqual(["HEAL", "APPROVE"]);
  const obj = readStore(ep).find((o) => o.approval_id === A)!;
  expect(obj.confirmed_by).toBe("cli");
  const lines = decisionLines(ep);
  expect(lines.length).toBe(linesBefore + 1);
  expect(lines.at(-1)!.decision_latency_s).toBeNull(); // 对齐发生在更早的 heal 中（S3-R11）
});

test("TA-2c 05 已对齐待确认后解封物被改动 → E_GATE_MISMATCH，零 APPROVE spawn，对象仍未确认，host 未删除该文件", async () => {
  const R = ackRepo();
  const ep = epAt05(R.repo, R.eps, "TA2C", { patch: true });
  expect(ava(R.repo, [ep, "/approvals"]).code).toBe(0);
  const t = spawnSync(py(R), ["-m", "pipeline.review", ep, "--approve"], { cwd: R.repo, input: "y\n", encoding: "utf-8", env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1" } });
  expect(t.status, t.stderr).toBe(0);
  const L = await start(R);
  await openEp(L.page, "TA2C");
  const c = card(L.page, "05");
  await expect(c).toContainText("待你确认");
  const A = (await c.getAttribute("data-approval-id"))!;
  // mtime 往后挪 1 s、内容不变：core 的双闸（时序单调 ∧ 内容一致）仍通过，自愈也只动 PENDING 对象——
  // 此时 decide 在确认前的解封物指纹核验是唯一拦截点（§4.3「已对齐、待确认」分支）
  const gate = join(ep, "04-clips.approved.json");
  fpy(R.repo, "import os, sys; st = os.stat(sys.argv[1]); os.utime(sys.argv[1], ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))", [gate]);
  await c.getByTestId("approve").click();
  await expect(errOf(L)).toHaveAttribute("data-code", "E_GATE_MISMATCH");
  await expect(errOf(L)).toContainText("批准文件对应的不是你审阅的版本");
  expect(lastDecideSpawns(L).map((s) => s.template)).toEqual(["HEAL"]);
  const obj = readStore(ep).find((o) => o.approval_id === A)!;
  expect([obj.status, obj.confirmed_by]).toEqual(["approved", null]);
  expect(existsSync(gate)).toBe(true);
});

test("TA-3 05 批准失败原样呈现：段级不对齐与补丁段两种情况 stdout/stderr 尾部原文；无任何重试按钮；对象仍 pending", async () => {
  const R = ackRepo();
  const e1 = epAt05(R.repo, R.eps, "TA3-MIS", { clipDur: 3.0 });
  const e2 = epAt05(R.repo, R.eps, "TA3-PATCH", { patch: true });
  const L = await start(R);
  for (const [name, ep, needle, pane] of [
    ["TA3-MIS", e1, "段级时长不对齐", "err-stderr"],
    ["TA3-PATCH", e2, "补丁段（段号：1）", "err-stdout"],
  ] as const) {
    await openEp(L.page, name);
    const c = card(L.page, "05");
    const A = (await c.getAttribute("data-approval-id"))!;
    await c.getByTestId("approve").click();
    await expect(errOf(L)).toHaveAttribute("data-code", "E_CORE");
    await expect(L.page.getByTestId(pane)).toContainText(needle);
    await expect(L.page.getByTestId("err-stdout").or(L.page.getByTestId("err-stderr")).first()).toBeVisible();
    expect(await L.page.getByRole("button", { name: /重试|confirm-patch|仍然批准/ }).count()).toBe(0);
    expect(readStore(ep).find((o) => o.approval_id === A)!.status).toBe("pending");
    expect(existsSync(join(ep, "04-clips.approved.json"))).toBe(false);
  }
  // 两种情况都在 stdout 与 stderr 各显示了什么：原样记录，供门禁复核
  console.log("TA-3 两种失败的尾部已原样显示（段级不对齐 → stderr；补丁段号 → stdout）");
});

test("TA-4 陈旧卡：UI 显示 pending 后终端先行 /approve → 再点按钮 → E_STALE，零 ack spawn", async () => {
  const R = ackRepo();
  const ep = epAt035(R.eps, "TA4");
  const L = await start(R);
  await openEp(L.page, "TA4");
  const c = card(L.page, "03.5");
  const A = (await c.getAttribute("data-approval-id"))!;
  const before = ackSpawns(L).length;
  // 终端 ack 须先于 H4：UI 每秒重读对象库，卡片会先被刷掉，因此经 before-heal 钩子在 decide 内的同一时刻执行（偏差见施工报告）
  await arm(L, "before-heal");
  await c.getByTestId("approve").click();
  await waitHit(L, "before-heal");
  expect(ava(R.repo, [ep, "/approve", "03.5", "--id", A]).code).toBe(0);
  await release(L, "before-heal");
  await expect(errOf(L)).toHaveAttribute("data-code", "E_STALE");
  expect(ackSpawns(L).length).toBe(before);
});

test("TA-5 无点击零 ack：打开期、预览审片页、播放音频、切换期、重载窗口全程 APPROVE/REJECT/REVIEW_APPROVE 为 0", async () => {
  const R = ackRepo();
  const ep = epAt05(R.repo, R.eps, "TA5");
  fpy(R.repo, "import sys, wave\nfor n in ('seg-01.wav', 'seg-02.wav'):\n    w = wave.open(f'{sys.argv[1]}/03-audio/{n}', 'wb'); w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000); w.writeframes(b'\\0\\0' * 2400); w.close()", [ep]);
  epAt035(R.eps, "TA5-B");
  const L = await start(R);
  await openEp(L.page, "TA5");
  await expect(L.page.getByTestId("html-frame")).toBeVisible(); // 05 停机点自动呼出 04-review.html
  await L.page.locator(`[data-testid=tree-row][data-rel="03-audio"]`).click();
  await L.page.locator("audio").first().waitFor();
  await L.page.locator("audio").first().evaluate((a: HTMLAudioElement) => a.play().catch(() => undefined));
  await L.page.waitForTimeout(800);
  await openEp(L.page, "TA5-B");
  await L.page.reload();
  await L.page.locator("[data-testid=episode]").first().waitFor();
  await openEp(L.page, "TA5");
  await L.page.waitForTimeout(1500);
  expect(ackSpawns(L)).toEqual([]);
});

test("TA-6 能力缺席：repoRoot 指向无 pipeline/approvals.py 的副本 → 决策条只读、HEAL 从未 spawn、期目录无 human_time.json", async () => {
  const R = ackRepo({ withApprovals: false });
  const ep = epAt05(R.repo, R.eps, "TA6");
  const L = await start(R);
  await epRow(L.page, "TA6").click();
  await expect(L.page.getByTestId("decision-readonly")).toContainText("决策条只读");
  expect(await L.page.getByTestId("approve").count()).toBe(0);
  await L.page.waitForTimeout(2500);
  expect(spawns(L).filter((s) => s.template === "HEAL")).toEqual([]);
  expect(existsSync(join(ep, "human_time.json"))).toBe(false);
});

test("TA-7 后置核验：APPROVE 退 0 却什么都不做 → E_UNVERIFIED，UI 不显示成功", async () => {
  const R = ackRepo();
  fakeCore(R.repo, 'if len(_sys.argv) > 2 and _sys.argv[2] == "/approve":\n    raise SystemExit(0)');
  epAt035(R.eps, "TA7");
  const L = await start(R);
  await openEp(L.page, "TA7");
  await card(L.page, "03.5").getByTestId("approve").click();
  await expect(errOf(L)).toHaveAttribute("data-code", "E_UNVERIFIED");
  await expect(errOf(L)).toContainText("目标对象未到达预期状态");
  expect(await L.page.getByTestId("decision-ok").count()).toBe(0);
});

test("TA-8 stdin 恒 ignore：假 core 执行 input() → 立即 EOF 非 0 退出（E_CORE，远早于 30 s 超时）", async () => {
  const R = ackRepo();
  fakeCore(R.repo, 'if len(_sys.argv) > 2 and _sys.argv[2] == "/approve":\n    input()');
  epAt035(R.eps, "TA8");
  const L = await start(R);
  await openEp(L.page, "TA8");
  const t0 = Date.now();
  await card(L.page, "03.5").getByTestId("approve").click();
  await expect(errOf(L)).toHaveAttribute("data-code", "E_CORE", { timeout: 5000 });
  expect(Date.now() - t0).toBeLessThan(5000);
  await expect(L.page.getByTestId("err-stderr")).toContainText("EOFError");
});

test("TA-9 不批准替身（03.5）：spawn 前改写 manifest → core 退 1「已被替换」；变体 2（HEAL 前）经 ackable → E_STALE；变体 3（heal 后）经 fingerprintsMatch → E_STALE", async () => {
  const R = ackRepo();
  const e1 = epAt035(R.eps, "TA9-1");
  const e2 = epAt035(R.eps, "TA9-2");
  const e3 = epAt035(R.eps, "TA9-3");
  const L = await start(R);

  // 主场景：after-fingerprint-check
  await openEp(L.page, "TA9-1");
  let A = (await card(L.page, "03.5").getAttribute("data-approval-id"))!;
  let linesBefore = decisionLines(e1).length;
  await arm(L, "after-fingerprint-check");
  await card(L.page, "03.5").getByTestId("approve").click();
  await waitHit(L, "after-fingerprint-check");
  writeManifest(e1, [4.5]); // 仍合法：只改一段时长
  expect(statusJson(R.repo, e1).current_step.startsWith("03.5")).toBe(true); // 否则自愈不会新建 B（红队 R3 m5）
  await release(L, "after-fingerprint-check");
  await expect(errOf(L)).toHaveAttribute("data-code", "E_CORE");
  await expect(L.page.getByTestId("err-stderr")).toContainText("已被替换");
  expect(lastDecideSpawns(L).map((s) => s.template)).toEqual(["HEAL", "APPROVE"]);
  let store = readStore(e1);
  expect(store.find((o) => o.approval_id === A)!.status).toBe("superseded");
  const B = store.filter((o) => o.type === "03.5" && o.approval_id !== A);
  expect(B.map((o) => o.status)).toEqual(["pending"]);
  expect(decisionLines(e1).length).toBe(linesBefore);

  // 变体 2：改写发生在 HEAL 之前 → heal 把 A 转 SUPERSEDED → ackable → E_STALE，零 ack spawn
  await openEp(L.page, "TA9-2");
  A = (await card(L.page, "03.5").getAttribute("data-approval-id"))!;
  let acks = ackSpawns(L).length;
  await arm(L, "before-heal");
  await card(L.page, "03.5").getByTestId("approve").click();
  await waitHit(L, "before-heal");
  writeManifest(e2, [4.5]);
  await release(L, "before-heal");
  await expect(errOf(L)).toHaveAttribute("data-code", "E_STALE");
  await expect(errOf(L)).toContainText("已不在可操作状态");
  expect(ackSpawns(L).length).toBe(acks);
  expect(readStore(e2).find((o) => o.approval_id === A)!.status).toBe("superseded");

  // 变体 3：heal 之后改写 → A 仍 PENDING、ackable 通过 → 只有 fingerprintsMatch 能返回 E_STALE，零 ack spawn
  await openEp(L.page, "TA9-3");
  A = (await card(L.page, "03.5").getAttribute("data-approval-id"))!;
  acks = ackSpawns(L).length;
  linesBefore = decisionLines(e3).length;
  await arm(L, "after-heal");
  await card(L.page, "03.5").getByTestId("approve").click();
  await waitHit(L, "after-heal");
  writeManifest(e3, [4.5]);
  store = readStore(e3);
  expect(store.find((o) => o.approval_id === A)!.status).toBe("pending");
  await release(L, "after-heal");
  await expect(errOf(L)).toHaveAttribute("data-code", "E_STALE");
  await expect(errOf(L)).toContainText("指纹不符");
  expect(ackSpawns(L).length).toBe(acks);
  expect(decisionLines(e3).length).toBe(linesBefore);
});

test("TA-9c 05 物理闸门竞态：spawn 前把 04-clips.json 改写为段级对齐的 F2 → REVIEW_APPROVE 退 1、解封物不存在、工序仍为 05、第 ② 步未 spawn", async () => {
  const R = ackRepo();
  const ep = epAt05(R.repo, R.eps, "TA9C");
  const L = await start(R);
  await openEp(L.page, "TA9C");
  await arm(L, "after-fingerprint-check");
  await card(L.page, "05").getByTestId("approve").click();
  await waitHit(L, "after-fingerprint-check");
  writeFileSync(join(ep, "04-clips.json"), clipsJson({}, 11.0)); // 段级对齐的 F2（只换起点）
  await release(L, "after-fingerprint-check");
  await expect(errOf(L)).toHaveAttribute("data-code", "E_CORE");
  await expect(L.page.getByTestId("err-stderr")).toContainText("已不是审阅时的版本");
  expect(existsSync(join(ep, "04-clips.approved.json"))).toBe(false);
  expect(statusJson(R.repo, ep).current_step.startsWith("05")).toBe(true);
  expect(lastDecideSpawns(L).map((s) => s.template)).toEqual(["HEAL", "REVIEW_APPROVE"]);
});

test("TA-9b 解封物指纹核验：假 REVIEW_APPROVE 写出与钉住指纹不同的解封物 → E_GATE_MISMATCH，第 ② 步未 spawn，host 未删除该文件", async () => {
  const R = ackRepo();
  fakeCore(
    R.repo,
    'if len(_sys.argv) > 3 and _sys.argv[2] == "/run":\n    _ep = _sys.argv[1]\n    open(_ep + "/04-clips.approved.json", "w").write(open(_ep + "/04-clips.json").read() + "\\n")\n    raise SystemExit(0)',
  );
  const ep = epAt05(R.repo, R.eps, "TA9B");
  const L = await start(R);
  await openEp(L.page, "TA9B");
  await card(L.page, "05").getByTestId("approve").click();
  await expect(errOf(L)).toHaveAttribute("data-code", "E_GATE_MISMATCH");
  await expect(errOf(L)).toContainText("批准文件对应的不是你审阅的版本");
  expect(lastDecideSpawns(L).map((s) => s.template)).toEqual(["HEAL", "REVIEW_APPROVE"]);
  expect(existsSync(join(ep, "04-clips.approved.json"))).toBe(true);
});

test("TA-10 超时杀进程组 + TI-6 进程与端口：孙进程 70 s 后写标记；60 s 超时后 80 s 时标记不存在；子进程 ppid 为 host；全部进程无 LISTEN", async () => {
  test.setTimeout(180_000);
  const R = ackRepo();
  fakeCore(
    R.repo,
    [
      'if len(_sys.argv) > 3 and _sys.argv[2] == "/run":',
      '    open("ta10-pid", "w").write(str(_os.getpid()))',
      '    _sp.Popen([_sys.executable, "-c", "import sys, time; time.sleep(70); open(sys.argv[1], \\"w\\").write(\\"x\\")", _os.path.abspath("ta10-marker")])',
      "    _time.sleep(600)",
    ].join("\n"),
  );
  epAt05(R.repo, R.eps, "TA10");
  const L = await start(R);
  await openEp(L.page, "TA10");
  const t0 = Date.now();
  await card(L.page, "05").getByTestId("approve").click();
  // TI-6：spawn 的 Python 子进程 ppid == host pid；host 是 ava-host 这个 Utility 进程；全部进程无 LISTEN
  await expect.poll(() => existsSync(join(R.repo, "ta10-pid")), { timeout: 10_000 }).toBe(true);
  const childPid = Number(readFileSync(join(R.repo, "ta10-pid"), "utf-8"));
  const hp = await hostPid(L);
  expect(Number(execFileSync("/bin/ps", ["-o", "ppid=", "-p", String(childPid)], { encoding: "utf-8" }).trim())).toBe(hp);
  const metrics = await L.app.evaluate(({ app }) => app.getAppMetrics().map((m) => ({ pid: m.pid, type: m.type, name: m.name ?? null })));
  expect(metrics.filter((m) => m.type === "Utility" && m.name === "ava-host").map((m) => m.pid)).toEqual([hp]);
  // main（type Browser）上的 LISTEN 是 Playwright 自己的 --inspect / --remote-debugging-port（驱动 main 的前提）；
  // 「app 全部进程零 LISTEN」在不经 Playwright 的打包版上断言（e2e-packaged TI-6b）
  const pids = [...metrics.filter((m) => m.type !== "Browser").map((m) => m.pid), childPid].join(",");
  const lsof = spawnSync("/usr/sbin/lsof", ["-a", "-p", pids, "-iTCP", "-sTCP:LISTEN", "-nP"], { encoding: "utf-8" });
  expect(lsof.stdout.trim()).toBe("");
  await expect(errOf(L)).toHaveAttribute("data-code", "E_TIMEOUT", { timeout: 75_000 });
  await expect(errOf(L)).toContainText("结果未知，请以稍后刷新为准");
  const wait = 80_000 - (Date.now() - t0);
  if (wait > 0) await L.page.waitForTimeout(wait);
  expect(existsSync(join(R.repo, "ta10-marker"))).toBe(false);
});

test("TA-11 heal 触发闭集：HEAL 序列恰为 A(H1) B(H1) C(H1) C(H3) C(H2) A(H1) A(H4) A(H5)；后台推进无 heal；返工后新 pending 出现；B 的对象库此后零写入；历史 REJECTED 不引起 H5", async () => {
  test.setTimeout(120_000);
  const R = ackRepo();
  const A = epAt03(R.eps, "A");
  const B = epAt035(R.eps, "B");
  const C = epAt03(R.eps, "C");
  // 前置：A 的对象库带一条已被取代的历史 05 REJECTED（钉住的指纹与磁盘不同）
  fpy(
    R.repo,
    `import json, sys
from pathlib import Path
from pipeline import approvals
ep = Path(sys.argv[1]); (ep / "_agent").mkdir()
a = approvals.Approval(approval_id="appr_1700000000000_old0", episode="A", type="05", status=approvals.ApprovalStatus.REJECTED,
    artifacts=[approvals.ArtifactFingerprint(path="04-clips.json", size=1, mtime_ns=1)], created_at="2026-01-01T00:00:00.000000Z",
    resolved_at="2026-01-01T00:00:01.000000Z", resolved_by="cli", feedback={"target": "s01", "problem": "旧一轮"})
(ep / "_agent" / "approvals_store.json").write_text(json.dumps([a.to_dict()], ensure_ascii=False, indent=2) + "\\n", encoding="utf-8")`,
    [A],
  );
  const L = await start(R);
  await openEp(L.page, "A");
  await openEp(L.page, "B");
  await expect(card(L.page, "03.5")).toBeVisible();
  const bStore = statSync(join(B, "_agent/approvals_store.json")).mtimeMs;
  await openEp(L.page, "C");
  await expect.poll(() => heals(L)).toEqual(["A(H1)", "B(H1)", "C(H1)"]);
  // 终端把后台期 A 推进到 05 停机点：A 在后台，不得 heal
  writeManifest(A);
  writeClips(R.repo, A);
  await L.page.waitForTimeout(6000);
  expect(heals(L)).toEqual(["A(H1)", "B(H1)", "C(H1)"]);
  await L.page.getByTestId("refresh").click();
  await expect.poll(() => heals(L)).toEqual(["A(H1)", "B(H1)", "C(H1)", "C(H3)"]);
  await settled(L.page, "C"); // 刷新（heal + status）跑完再推进 C：否则 H3 的 heal 会直接看到新工序，变化被吸收进 H2 基线
  // 终端把活跃期 C 推进到 03.5 停机点 → 下一次 status 采样边沿触发 H2
  writeManifest(C);
  await expect.poll(() => heals(L), { timeout: 15_000 }).toEqual(["A(H1)", "B(H1)", "C(H1)", "C(H3)", "C(H2)"]);
  await expect(card(L.page, "03.5")).toBeVisible();
  // 切回 A → H1，决策条出现 A 的 05 pending
  await openEp(L.page, "A");
  const c = card(L.page, "05");
  await expect(c).toBeVisible();
  const first = (await c.getAttribute("data-approval-id"))!;
  // 等设为活跃后的第一次 H5 采样（基线）完成：基线里已存在的漂移元组永不触发（§2.12），测试的操作速度远快于真人返工
  await L.page.waitForTimeout(2500);
  // 在 A 上打回 05 → H4
  await c.getByTestId("reject-open").click();
  await c.getByTestId("reject-target").fill("s01");
  await c.getByTestId("reject-problem").fill("换镜头");
  await c.getByTestId("reject-submit").click();
  await expect(L.page.getByTestId("decision-ok")).toContainText("已打回");
  await expect(card(L.page, "05")).toHaveCount(0);
  // 终端返工 04-clips.json → 稳定期后 H5 恰一次，新 pending 出现
  writeClips(R.repo, A, {}, 12.0);
  await expect.poll(() => heals(L), { timeout: 15_000 }).toEqual(["A(H1)", "B(H1)", "C(H1)", "C(H3)", "C(H2)", "A(H1)", "A(H4)", "A(H5)"]);
  await expect(card(L.page, "05")).toBeVisible();
  expect(await card(L.page, "05").getAttribute("data-approval-id")).not.toBe(first);
  await L.page.waitForTimeout(4000);
  expect(heals(L)).toEqual(["A(H1)", "B(H1)", "C(H1)", "C(H3)", "C(H2)", "A(H1)", "A(H4)", "A(H5)"]);
  expect(statSync(join(B, "_agent/approvals_store.json")).mtimeMs).toBe(bStore);
  expect(readStore(A).find((o) => o.approval_id === "appr_1700000000000_old0")!.status).toBe("rejected");
});

test("TA-12 repoRoot 切换与在途命令互斥：decide 在途 → E_BUSY 且不弹框；切换中 decide → E_BUSY；旧代号 STATUS 结果不进任何 snapshot", async () => {
  test.setTimeout(90_000);
  const R = ackRepo();
  const R2 = ackRepo();
  const ep = epAt035(R.eps, "SAME");
  // 新仓库里有同名期：旧仓库的迟到结果若不按代号丢弃，会被新仓库的订阅误收
  const ep2 = epAt035(R2.eps, "SAME");
  const L = await start(R);
  opened[opened.length - 1].R.push(R2);
  await setDialog(L, { repoRoot: R2.repo, confirm: true });
  await openEp(L.page, "SAME");
  // ① decide 停在 spawn 之前 → 切换请求 E_BUSY，对话框从未被调用
  await arm(L, "after-fingerprint-check");
  await card(L.page, "03.5").getByTestId("approve").click();
  await waitHit(L, "after-fingerprint-check");
  const r1 = await call(L.page, "app.requestRepoRootChange");
  expect(r1).toMatchObject({ ok: false, code: "E_BUSY" });
  expect(await dialogCalls(L)).toBe(0);
  // ② 放行：该 decide 正常完成，后置核验读的是原 repoRoot
  await release(L, "after-fingerprint-check");
  await expect(L.page.getByTestId("decision-ok")).toContainText("已批准");
  expect(readStore(ep).filter((o) => o.type === "03.5").map((o) => o.status)).toEqual(["approved"]);
  expect(readStore(ep2)).toEqual([]);
  // ③ 让旧仓库的这一期工序变成 X（新仓库同名期仍是 03.5），暂停一个在途 STATUS，再确认切换
  writeClips(R.repo, ep); // 旧仓库 SAME → 05
  await L.page.evaluate(() => {
    const w = window as unknown as { __steps: string[] };
    w.__steps = [];
    new MutationObserver(() => {
      for (const el of document.querySelectorAll("[data-testid=current-step]")) w.__steps.push(el.textContent ?? "");
    }).observe(document.body, { subtree: true, childList: true, characterData: true });
  });
  await arm(L, "status-result");
  await waitHit(L, "status-result"); // 活跃期每 5 s 一次 status
  const switching = call(L.page, "app.requestRepoRootChange");
  await expect.poll(() => dialogCalls(L)).toBe(1);
  const busy = await call(L.page, "approval.decide", { epKey: "SAME", approvalId: "appr_x", stop: "03.5", decision: "approve" });
  expect(busy).toMatchObject({ ok: false, code: "E_BUSY" });
  await release(L, "status-result");
  const r3 = await switching;
  expect(r3).toMatchObject({ ok: true, result: { changed: true, repoRoot: R2.repo } });
  await L.page.waitForTimeout(2000);
  const steps = await L.page.evaluate(() => (window as unknown as { __steps: string[] }).__steps);
  expect(steps.filter((s) => s.startsWith("05"))).toEqual([]);
  expect(JSON.parse(readFileSync(join(L.userData, "settings.json"), "utf-8"))).toEqual({ version: 1, repoRoot: R2.repo });
});

test("TI-3b 只触发 H1：树清单差异 ⊆ { _agent/、approvals_store.json、approvals_store.lock、events.jsonl }，其他产物零变化", async () => {
  const R = ackRepo();
  const ep = epAt035(R.eps, "TI3B");
  const before = treeManifest(ep);
  const L = await start(R);
  await openEp(L.page, "TI3B");
  await expect(card(L.page, "03.5")).toBeVisible();
  await L.page.waitForTimeout(3000);
  await L.app.close();
  opened[opened.length - 1].L = undefined;
  const after = treeManifest(ep);
  const name = (l: string) => l.split("\t")[0];
  const changed = [...after.filter((l) => !before.includes(l)), ...before.filter((l) => !after.includes(l))].map(name);
  const allowed = new Set(["_agent", "_agent/approvals_store.json", "_agent/approvals_store.lock", "events.jsonl"]);
  expect(changed.filter((n) => !allowed.has(n))).toEqual([]);
  expect(changed).toContain("_agent/approvals_store.json");
});

test("TI-8 renderer 递交路径一律拒绝：requestRepoRootChange 带参数 → E_BAD_REQUEST、不弹框、settings 不变；decide 多一个 path 键 → E_BAD_REQUEST、零 spawn", async () => {
  const R = ackRepo();
  epAt035(R.eps, "TI8");
  const L = await start(R);
  await setDialog(L, { repoRoot: "/tmp/x", confirm: true });
  await openEp(L.page, "TI8");
  const A = (await card(L.page, "03.5").getAttribute("data-approval-id"))!;
  expect(await call(L.page, "app.requestRepoRootChange", { path: "/tmp/x" })).toMatchObject({ ok: false, code: "E_BAD_REQUEST" });
  expect(await dialogCalls(L)).toBe(0);
  expect(existsSync(join(L.userData, "settings.json"))).toBe(false);
  const n = spawns(L).length;
  expect(await call(L.page, "approval.decide", { epKey: "TI8", approvalId: A, stop: "03.5", decision: "approve", path: "/tmp/evil" })).toMatchObject({ ok: false, code: "E_BAD_REQUEST" });
  expect(spawns(L).length).toBe(n);
});

test("TI-9 kill -9 host → 10 s 内看护重启、健康面板显示 host 在线、活跃期重新订阅（新 host 上的 H1）", async () => {
  const R = ackRepo();
  epAt035(R.eps, "TI9");
  const L = await start(R);
  await openEp(L.page, "TI9");
  await L.page.getByTestId("health-toggle").click();
  await expect(L.page.locator("[data-row='host 连接'] td")).toHaveText("在线");
  const pid = (await hostPid(L))!;
  const h1Before = heals(L).length;
  process.kill(pid, "SIGKILL");
  await expect(L.page.locator("[data-row='host 连接'] td")).toHaveText("断开", { timeout: 5000 });
  await expect(L.page.locator("[data-row='host 连接'] td")).toHaveText("在线", { timeout: 10_000 });
  expect(await hostPid(L)).not.toBe(pid);
  await expect.poll(() => heals(L).slice(h1Before), { timeout: 10_000 }).toEqual(["TI9(H1)"]);
  await expect(card(L.page, "03.5")).toBeVisible();
});

test("TI-9b 熔断：60 s 内崩溃 5 次 → 停止重启，renderer 换成致命面板（含 host stderr 尾部段）", async () => {
  test.setTimeout(90_000);
  const R = ackRepo();
  epAt035(R.eps, "TI9B");
  const L = await start(R);
  let last: number | null = null;
  for (let i = 0; i < 5; i++) {
    await expect.poll(async () => {
      const p = await hostPid(L);
      return p !== null && p !== last;
    }, { timeout: 20_000 }).toBe(true);
    last = (await hostPid(L))!;
    process.kill(last, "SIGKILL");
  }
  await expect(L.page.getByTestId("fatal")).toBeVisible({ timeout: 10_000 });
  await expect(L.page.getByTestId("fatal-detail")).toContainText("60 s 内崩溃 5 次");
  await expect(L.page.getByTestId("fatal-detail")).toContainText("host stderr 尾部");
  await L.page.waitForTimeout(3000);
  expect(await hostPid(L)).toBeNull();
});

test("TI-10 已运行一个实例时再启动同一构建 → 第二个进程 5 s 内退出，已有窗口获得焦点；全机仅一个 ava-host", async () => {
  const R = ackRepo();
  epAt035(R.eps, "TI10");
  const L = await start(R);
  await L.app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].blur());
  const t0 = Date.now();
  const second = spawnSync(ELECTRON, [DESKTOP, `--ava-repo-root=${R.repo}`, `--ava-user-data=${L.userData}`], { cwd: DESKTOP, timeout: 10_000, encoding: "utf-8" });
  expect(second.error).toBeUndefined();
  expect(Date.now() - t0).toBeLessThan(5000);
  expect(second.stdout).not.toContain("AVA_BOOT");
  await expect.poll(() => L.app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].isFocused()), { timeout: 3000 }).toBe(true);
  const metrics = await L.app.evaluate(({ app }) => app.getAppMetrics().filter((m) => m.type === "Utility" && m.name === "ava-host").length);
  expect(metrics).toBe(1);
  const ps = execFileSync("/bin/ps", ["-axo", "command"], { encoding: "utf-8" }).split("\n").filter((l) => l.includes(L.userData) && !l.includes("--type=")).length;
  expect(ps).toBe(1);
});

test("TI-4 app 自身状态不放数据盘：userData、crashDumps 不在 realpath(dataRoot) 子树；userData 不是 browser-profile、不在 Chrome 配置子树；crashDumps 在 userData 子树；开发构建不带开关时的 userData（假设 9）", async () => {
  const R = ackRepo();
  epAt035(R.eps, "TI4");
  const L = await start(R);
  const p = await L.app.evaluate(({ app }) => ({ userData: app.getPath("userData"), crashDumps: app.getPath("crashDumps") }));
  const dataReal = realpathSync(join(R.repo, "data"));
  for (const x of [p.userData, p.crashDumps]) expect(`${realpathSync(x)}/`.startsWith(`${dataReal}/`)).toBe(false);
  expect(p.userData).not.toBe(join(R.repo, "data/browser-profile"));
  expect(`${p.userData}/`.startsWith(join(homedir(), "Library/Application Support/Google/Chrome/"))).toBe(false);
  expect(`${realpathSync(p.crashDumps)}/`.startsWith(`${realpathSync(p.userData)}/`)).toBe(true);
  // 假设 9：开发构建不带 --ava-user-data 时的默认 userData（只读取路径，不启动第二个实例）
  const d = await L.app.evaluate(({ app }) => ({ appData: app.getPath("appData"), name: app.getName() }));
  const dflt = join(d.appData, d.name);
  console.log(`假设 9：开发构建默认 userData = ${dflt}（打包版为 ~/Library/Application Support/ava）`);
});

test("门禁 7 ack 在途时退出 app：core 子进程不随 app 退出而被杀，自行跑完；重开后 UI 与磁盘一致", async () => {
  const R = ackRepo();
  // 假 core：/approve 先睡 3 s 再走真实 main（模拟在途的 ack spawn）
  fakeCore(R.repo, 'if len(_sys.argv) > 2 and _sys.argv[2] == "/approve":\n    _time.sleep(3)');
  const ep = epAt035(R.eps, "G7");
  const L = await start(R);
  await openEp(L.page, "G7");
  const A = (await card(L.page, "03.5").getAttribute("data-approval-id"))!;
  await card(L.page, "03.5").getByTestId("approve").click();
  await expect.poll(() => lastDecideSpawns(L).map((s) => s.template), { timeout: 10_000 }).toContain("APPROVE");
  await L.app.close(); // spawn 在途：v1 不向子进程发信号（§2.9）
  opened[opened.length - 1].L = undefined;
  await expect.poll(() => readStore(ep).find((o) => o.approval_id === A)?.status, { timeout: 10_000 }).toBe("approved");
  const L2 = await start(R);
  await openEp(L2.page, "G7");
  await expect(card(L2.page, "03.5")).toHaveCount(0); // 已批准的对象不再出现在决策条上
  expect(readStore(ep).find((o) => o.approval_id === A)).toMatchObject({ status: "approved", resolved_by: "cli" });
});

test("门禁 14 人时数据源缺口常驻可见：03.5/05 决策条「本次审阅不计人时」、03.5 结构化打点提示、人时类 advisory 旁标；截图存档", async () => {
  const R = ackRepo();
  const e35 = epAt035(R.eps, "G14-035");
  const e05 = epAt05(R.repo, R.eps, "G14-05");
  // 人时观测 advisory（status.py 人时检测）：片长 4 s、已记 5 分钟
  writeFileSync(join(e05, "human_time.json"), JSON.stringify([{ stop: "02.5", minutes: 5.0 }]));
  // RF-20：审片页生成时间早于排片文件最后修改时间
  fpy(R.repo, "import os, sys; os.utime(sys.argv[1], (1_700_000_000, 1_700_000_000))", [join(e05, "04-review.html")]);
  const L = await start(R);
  await openEp(L.page, "G14-035");
  const c35 = card(L.page, "03.5");
  await expect(c35.getByTestId("no-human-time")).toHaveText("本次审阅不计人时");
  await expect(c35).toContainText("结构化打点（manifest human_review）须在终端 /voice 完成");
  await L.page.screenshot({ path: join(DESKTOP, "out/gate-evidence/gate14-0305.png") });
  await openEp(L.page, "G14-05");
  const c05 = card(L.page, "05");
  await expect(c05.getByTestId("no-human-time")).toHaveText("本次审阅不计人时");
  await expect(c05.getByTestId("review-page-older")).toHaveText("审片页生成时间早于排片文件最后修改时间");
  await expect(L.page.getByTestId("human-time-incomplete")).toHaveText("（数据源不完整：桌面端审阅不计入）");
  await expect(L.page.locator(".advisories li", { hasText: "人类耗时：本期已记" })).toHaveCount(1);
  await L.page.screenshot({ path: join(DESKTOP, "out/gate-evidence/gate14-05.png") });
  void e35;
});
