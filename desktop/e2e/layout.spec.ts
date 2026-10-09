// D39 S3：分栏布局跨重启记忆（同一 userData 重启），以及存储被写坏时回落默认。
// 临时夹具仓，绝不指向真实 data/；全程只用点击，不做键盘自动化（D37）。
import { expect, test } from "@playwright/test";
import { tmp } from "../tests/helpers";
import { buildFixture, launch, openEpisode } from "./fixtures";
import { fingerprintOf, launchSession, pendingObj, sessionFixture, stubQuit, writeStore } from "./sessionFixtures";
import { fixtureWrite, sessionScript } from "../tests/fixtures/session";

test("L-1 收起侧栏、展开预览、收起本期文件 → 同一 userData 重启后原样恢复", async () => {
  const fx = buildFixture();
  const ud = tmp("ud");
  try {
    let L = await launch(fx.repo, [], ud, { previewOpen: false });
    // 首次启动是默认态：侧栏在、预览收成窄条
    await expect(L.page.locator("#ava-left")).toBeVisible();
    await expect(L.page.getByTestId("preview-rail")).toBeVisible();
    await openEpisode(L.page, "E2E-A");
    await L.page.getByTestId("files-toggle").click();
    await expect(L.page.getByTestId("files-toggle")).toHaveAttribute("aria-expanded", "false");
    await L.page.getByTestId("toggle-left").click();
    await L.page.getByTestId("toggle-preview").click();
    await expect(L.page.locator("#ava-left")).toBeHidden();
    await expect(L.page.locator("#ava-preview")).toBeVisible();
    await L.app.close();

    L = await launch(fx.repo, [], ud, { previewOpen: false });
    try {
      await expect(L.page.locator("#ava-left")).toBeHidden();
      await expect(L.page.getByTestId("toggle-left")).toHaveAttribute("aria-pressed", "false");
      await expect(L.page.locator("#ava-preview")).toBeVisible();
      await expect(L.page.getByTestId("preview-rail")).toHaveCount(0);
      await L.page.getByTestId("toggle-left").click();
      // 本期文件是收起的，树行不可见，所以不用 openEpisode（它等树行出现）；等中栏切到该期即可
      await L.page.locator("[data-testid=episode]", { hasText: "E2E-A" }).first().click();
      await expect(L.page.getByTestId("center")).toHaveAttribute("data-ep", "E2E-A");
      await expect(L.page.getByTestId("files-toggle")).toHaveAttribute("aria-expanded", "false");
    } finally {
      await L.app.close();
    }
  } finally {
    fx.cleanup();
  }
});

test("L-2 存储里的布局被写坏 → 重启后回落默认（侧栏在、预览收起），界面照常可用", async () => {
  const fx = buildFixture();
  const ud = tmp("ud");
  try {
    let L = await launch(fx.repo, [], ud, { previewOpen: false });
    await L.page.getByTestId("toggle-left").click(); // 先存一个非默认值，证明回落不是「本来就是默认」
    await expect(L.page.locator("#ava-left")).toBeHidden();
    await L.page.evaluate(() => localStorage.setItem("ava.layout", '{"v":1,"leftOpen":fa'));
    await L.app.close();

    L = await launch(fx.repo, [], ud, { previewOpen: false });
    try {
      await expect(L.page.locator("#ava-left")).toBeVisible();
      await expect(L.page.getByTestId("preview-rail")).toBeVisible();
      await expect(L.page.locator("#ava-preview")).toBeHidden();
      await openEpisode(L.page, "E2E-A");
      await expect(L.page.getByTestId("current-step")).toBeVisible();
    } finally {
      await L.app.close();
    }
  } finally {
    fx.cleanup();
  }
});

// ---------------- D39 S4：两条底线机检（防回退，不证明「好用」；plans/README UI 规则 5） ----------------
// 状态取最挤的一种：有会话 + 05 停机点卡 + 人时 + 钥匙串降级 + 带失败 job 的时间线；两种窗口 × 预览收起 / 展开。
// ① 关键元素（当前工序、批准、打回、输入框、发送）两两 boundingBox 不相交，且各自 100% 露在可见区（不被任何滚动/裁剪祖先藏掉）；
// ② 对话流可见高度 ≥ 160px，中栏无横向溢出（scrollWidth ≤ clientWidth）。
const KEY_ELEMENTS = ["current-step", "approve", "reject-open", "composer-input", "composer-send"] as const;
const STREAM_MIN_PX = 160; // 与 style.css 的 .conv-shell .conv-stream 保底一致

function floorProbe(ids: readonly string[]) {
  const vis = (el: Element) => {
    const r = el.getBoundingClientRect();
    let v = { l: r.left, t: r.top, r: r.right, b: r.bottom };
    for (let a = el.parentElement; a; a = a.parentElement) {
      const cs = getComputedStyle(a);
      if (cs.overflowX === "visible" && cs.overflowY === "visible") continue;
      const ab = a.getBoundingClientRect();
      v = { l: Math.max(v.l, ab.left + a.clientLeft), t: Math.max(v.t, ab.top + a.clientTop), r: Math.min(v.r, ab.left + a.clientLeft + a.clientWidth), b: Math.min(v.b, ab.top + a.clientTop + a.clientHeight) };
    }
    v = { l: Math.max(v.l, 0), t: Math.max(v.t, 0), r: Math.min(v.r, innerWidth), b: Math.min(v.b, innerHeight) };
    const area = Math.max(0, v.r - v.l) * Math.max(0, v.b - v.t);
    return { rect: { x: r.left, y: r.top, w: r.width, h: r.height }, frac: r.width * r.height > 0 ? area / (r.width * r.height) : 0 };
  };
  const els = ids.map((id) => {
    const el = document.querySelector(`[data-testid="${id}"]`);
    return { id, found: !!el, ...(el ? vis(el) : { rect: { x: 0, y: 0, w: 0, h: 0 }, frac: 0 }) };
  });
  const overlaps: string[] = [];
  for (let i = 0; i < els.length; i++)
    for (let j = i + 1; j < els.length; j++) {
      const a = els[i].rect, b = els[j].rect;
      if (Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x) > 0.5 && Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y) > 0.5) overlaps.push(`${els[i].id} × ${els[j].id}`);
    }
  const stream = document.querySelector("[data-testid=conv-stream]") as HTMLElement;
  const center = document.querySelector("[data-testid=center]") as HTMLElement;
  return {
    missing: els.filter((e) => !e.found).map((e) => e.id),
    notFullyVisible: els.filter((e) => e.found && e.frac < 0.999).map((e) => `${e.id}@${e.frac.toFixed(2)}`),
    overlaps,
    streamH: stream.clientHeight,
    centerOverflowX: center.scrollWidth - center.clientWidth,
  };
}

test("L-3 底线：1280×800 / 1440×900 × 预览收起 / 展开，关键元素不相交且完整可见、对话流 ≥ 160、中栏无横向溢出", async () => {
  const fx = sessionFixture(["SESS-A"]);
  const repo = fx.repo;
  fixtureWrite(repo.root, "data/episodes/SESS-A/human_time.json", JSON.stringify([{ stop: "02.5", minutes: 12.4, source: "desktop" }, { stop: "03.5", minutes: 31.07, source: "terminal" }]));
  writeStore(repo, "SESS-A", [pendingObj("appr_05", "05", "2026-09-25T10:00:02Z", "04-clips.json", fingerprintOf(repo, "SESS-A", "04-clips.json"))]);
  const reason = "钥匙串里没有该密钥；配置指名的环境变量 AVA_TEST_KEY 在本次 spawn 中缺失，已按 Spec 9 如实降级（无模型可用）";
  const obs = "退出码 1：python -m pipeline.clips /private/var/folders/8f/_04djfns4_l70r2r7pgy4ghm0000gn/T/ava-desktop-repo-Hw3ak8/data/episodes/SESS-A/02-script.md\nKeyError: '集' 字段缺失（第 3 段）";
  sessionScript(repo, "SESS-A", [
    { op: "emit", frame: { t: "ready", episode: "SESS-A", scope: "pipeline", continue_status: "new", llm: "degraded", degrade_reason: reason, code_freeze_ok: true, history_count: 0, session_bytes: 0, other_sessions: [] } },
    {
      op: "serve",
      on_turn: [
        { t: "turn_started", turn_id: "$turn", rid: "$rid" },
        { t: "tool", turn_id: "$turn", phase: "start", index: 0, name: "run_pipeline", summary: "clips SESS-A", ok: null, observation: null, duplicate: false },
        { t: "tool", turn_id: "$turn", phase: "end", index: 0, name: "run_pipeline", summary: "", ok: false, observation: obs, duplicate: false },
        { t: "assistant", turn_id: "$turn", kind: "answer", text: "第 3 段的「集」字段缺失，排片回退到了全季检索。建议先补上 `集: S01E07` 再重跑 clips。".repeat(3) },
        { t: "turn_finished", turn_id: "$turn", stopped: "done", llm_calls: 1, tool_calls: 1, tool_executions: 1, duplicates_rejected: 0, checkpoints: 0, wrapup: "none", duration_s: 3, prompt_chars: 1, lookups: null },
        { t: "stop_points", items: [], turn_id: "$turn" },
      ],
    },
  ]);
  const L = await launchSession(repo, [], { previewOpen: false });
  try {
    await L.page.locator("[data-testid=episode]", { hasText: "SESS-A" }).first().click();
    await L.page.getByTestId("decision").first().waitFor();
    await L.page.getByTestId("composer-input").fill("重排一下");
    await L.page.getByTestId("composer-send").click();
    await L.page.getByTestId("key-problem").waitFor();
    await L.page.locator("[data-testid=conv-row][data-kind=footer]").first().waitFor();
    for (const previewOpen of [false, true]) {
      if ((await L.page.getByTestId("toggle-preview").getAttribute("aria-pressed")) !== String(previewOpen)) await L.page.getByTestId("toggle-preview").click();
      for (const [w, h] of [[1280, 800], [1440, 900]] as const) {
        await L.page.setViewportSize({ width: w, height: h });
        await L.page.waitForTimeout(200);
        const where = `${w}×${h} 预览${previewOpen ? "展开" : "收起"}`;
        const r = await L.page.evaluate(floorProbe, KEY_ELEMENTS);
        expect({ where, missing: r.missing, notFullyVisible: r.notFullyVisible, overlaps: r.overlaps }).toEqual({ where, missing: [], notFullyVisible: [], overlaps: [] });
        expect(r.streamH, `${where} 对话流可见高度`).toBeGreaterThanOrEqual(STREAM_MIN_PX);
        expect(r.centerOverflowX, `${where} 中栏横向溢出（scrollWidth - clientWidth）`).toBeLessThanOrEqual(0);
      }
    }
  } finally {
    await stubQuit(L, "quit").catch(() => undefined);
    await L.app.close().catch(() => undefined);
    fx.cleanup();
  }
});
