// 动效与性能实测基准（Step 6）：验证分栏拖拽、面板滑移、长对话滚动、配音进度条满帧率、零长任务与零列表 DOM 变更。
import { copyFileSync, mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test } from "@playwright/test";
import { cleanup, makeFixtureRepo } from "../tests/helpers";
import { buildFixture, ff, launch, openEpisode, pick } from "./fixtures";
import { launchSession, sessionFixture } from "./sessionFixtures";
import { sessionScript } from "../tests/fixtures/session";

function percentile(xs: number[], p: number): number {
  if (xs.length === 0) return 0;
  const s = [...xs].sort((a, b) => a - b);
  return s[Math.min(s.length - 1, Math.floor(s.length * p))];
}

test("P1 分栏拖拽（覆盖 html-frame iframe）：跟手零卡顿、拖拽中 0 次 localStorage 写入、松手落盘 1 次", async () => {
  const fx = buildFixture();
  const L = await launch(fx.repo);
  try {
    await openEpisode(L.page, "E2E-A");
    await pick(L.page, "04-review.html");
    await L.page.getByTestId("html-frame").waitFor({ timeout: 15_000 });
    const splitter = L.page.getByTestId("split-preview");
    await splitter.waitFor();
    const box = await splitter.boundingBox();
    expect(box).not.toBeNull();
    const startX = box!.x + box!.width / 2;
    const startY = box!.y + box!.height / 2;
    const initW = await L.page.locator("#ava-preview").evaluate((el) => el.getBoundingClientRect().width);

    // 注入 localStorage 写入计数 + rAF 帧间隔 + LongTask 观察器
    await L.page.evaluate(() => {
      const w = window as unknown as Record<string, unknown>;
      const origSet = Storage.prototype.setItem;
      w.__perfOrigSet = origSet;
      w.__perfLsCalls = 0;
      Storage.prototype.setItem = function (k: string, v: string) {
        if (k === "ava.layout") w.__perfLsCalls = Number(w.__perfLsCalls ?? 0) + 1;
        return origSet.call(this, k, v);
      };
      const deltas: number[] = [];
      let prev = performance.now();
      w.__perfStop = false;
      const tick = (now: number) => {
        deltas.push(now - prev);
        prev = now;
        if (!w.__perfStop) requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
      w.__perfDeltas = deltas;
      const longs: number[] = [];
      w.__perfLongs = longs;
      try {
        const obs = new PerformanceObserver((list) => {
          for (const e of list.getEntries()) longs.push(e.duration);
        });
        obs.observe({ type: "longtask", buffered: false });
        w.__perfObs = obs;
      } catch {
        // 环境不支持 longtask 时保留空数组
      }
    });

    await L.page.mouse.move(startX, startY);
    await L.page.mouse.down();
    await expect(splitter).toHaveAttribute("data-dragging", "1");
    for (let i = 1; i <= 60; i++) {
      const dx = i <= 30 ? -i * 4 : -(60 - i) * 4 - 40;
      await L.page.mouse.move(startX + dx, startY);
    }
    const lsDuringDrag = await L.page.evaluate(() => Number((window as unknown as Record<string, unknown>).__perfLsCalls ?? 0));
    await L.page.mouse.up();
    await expect(splitter).not.toHaveAttribute("data-dragging", "1");
    await L.page.waitForTimeout(80);

    const stats = await L.page.evaluate(() => {
      const w = window as unknown as Record<string, unknown>;
      w.__perfStop = true;
      (w.__perfObs as PerformanceObserver | undefined)?.disconnect();
      if (w.__perfOrigSet) Storage.prototype.setItem = w.__perfOrigSet as typeof Storage.prototype.setItem;
      return {
        lsTotal: Number(w.__perfLsCalls ?? 0),
        deltas: (w.__perfDeltas as number[]) ?? [],
        longs: (w.__perfLongs as number[]) ?? [],
        finalW: document.querySelector("#ava-preview")?.getBoundingClientRect().width ?? 0,
      };
    });

    const p50 = percentile(stats.deltas, 0.5);
    const p95 = percentile(stats.deltas, 0.95);
    console.log(`[PERF P1 splitter] samples=${stats.deltas.length} p50=${p50.toFixed(1)}ms p95=${p95.toFixed(1)}ms longTasks=${stats.longs.length} lsDuring=${lsDuringDrag} lsTotal=${stats.lsTotal} width=${initW.toFixed(0)}->${stats.finalW.toFixed(0)}`);
    expect(lsDuringDrag).toBe(0);
    expect(stats.lsTotal).toBe(1);
    expect(Math.abs(stats.finalW - initW)).toBeGreaterThan(20);
    expect(stats.longs.length).toBe(0);
    expect(p95).toBeLessThan(35);
  } finally {
    await L.app.close();
    fx.cleanup();
  }
});

test("P2 侧栏与预览栏滑移收起：存在真实过渡中间帧、零长任务、最终准确隐藏", async () => {
  const fx = buildFixture();
  const L = await launch(fx.repo);
  try {
    await openEpisode(L.page, "E2E-A");
    const measureGlide = async (toggleId: string, paneSelector: string) => {
      const beforeW = await L.page.locator(paneSelector).evaluate((el) => el.getBoundingClientRect().width);
      expect(beforeW).toBeGreaterThan(100);
      await L.page.evaluate((sel) => {
        const w = window as unknown as Record<string, unknown>;
        const widths: number[] = [];
        const deltas: number[] = [];
        const longs: number[] = [];
        let prev = performance.now();
        const start = prev;
        w.__glideDone = false;
        const obs = new PerformanceObserver((list) => {
          for (const e of list.getEntries()) longs.push(e.duration);
        });
        try {
          obs.observe({ type: "longtask", buffered: false });
        } catch {
          // ignore
        }
        const step = (now: number) => {
          deltas.push(now - prev);
          prev = now;
          const el = document.querySelector(sel);
          if (el) widths.push(el.getBoundingClientRect().width);
          if (now - start < 380) {
            requestAnimationFrame(step);
          } else {
            obs.disconnect();
            w.__glideRes = { widths, deltas, longs };
            w.__glideDone = true;
          }
        };
        requestAnimationFrame(step);
      }, paneSelector);

      await L.page.getByTestId(toggleId).click();
      await L.page.waitForFunction(() => (window as unknown as Record<string, unknown>).__glideDone === true, { timeout: 5_000 });
      await expect(L.page.locator(paneSelector)).toBeHidden();
      const res = await L.page.evaluate(() => (window as unknown as Record<string, unknown>).__glideRes as { widths: number[]; deltas: number[]; longs: number[] });
      const mids = res.widths.filter((x) => x > 1 && x < beforeW - 1);
      const p95 = percentile(res.deltas, 0.95);
      console.log(`[PERF P2 glide ${toggleId}] before=${beforeW.toFixed(0)}px mids=${mids.length} p95=${p95.toFixed(1)}ms longTasks=${res.longs.length}`);
      expect(mids.length).toBeGreaterThan(0);
      expect(res.longs.length).toBe(0);
      expect(p95).toBeLessThan(35);
    };

    await measureGlide("toggle-left", "#ava-left");
    await measureGlide("toggle-preview", "#ava-preview");
  } finally {
    await L.app.close();
    fx.cleanup();
  }
});

test("P3 长对话滚动（160+ 行）：稳定滚动到底部、零长任务", async () => {
  const fx = sessionFixture(["PERF-C"]);
  const ops: Record<string, unknown>[] = [
    {
      op: "emit",
      frame: {
        t: "ready",
        episode: "PERF-C",
        scope: "pipeline",
        continue_status: "resumed",
        llm: "ok",
        degrade_reason: null,
        code_freeze_ok: true,
        history_count: 160,
        session_bytes: 65536,
        other_sessions: [],
        resume_prompt_tokens: 12000,
        resume_prompt_chars: 18000,
        context_window: 128000,
        session_kind: "episode",
        project: null,
        model: "fake",
        reasoning_effort: null,
        model_source: "default",
        effort_source: "default",
      },
    },
  ];
  for (let i = 0; i < 40; i++) {
    ops.push(
      { op: "emit", frame: { t: "history", index: i * 4, role: "user", text: `第 ${i + 1} 轮问题：请核查这一段的素材与台词衔接`, name: null } },
      { op: "emit", frame: { t: "history", index: i * 4 + 1, role: "tool", text: "", name: "read_artifact", ok: true, summary: `02-script.md · 段 ${i + 1}` } },
      { op: "emit", frame: { t: "history", index: i * 4 + 2, role: "tool", text: "", name: "search_notes", ok: true, summary: `关键词 ${i + 1}` } },
      {
        op: "emit",
        frame: {
          t: "history",
          index: i * 4 + 3,
          role: "assistant",
          text: `第 ${i + 1} 轮答复：已检查完第 ${i + 1} 段，画面与台词一致，无需改动。补充说明：${"内容细节".repeat(12)}`,
          name: null,
        },
      },
    );
  }
  ops.push({
    op: "serve",
    on_turn: [
      { t: "turn_started", turn_id: "$turn", rid: "$rid" },
      { t: "assistant", turn_id: "$turn", kind: "answer", text: "收到，长对话压测就绪。" },
      { t: "turn_finished", turn_id: "$turn", stopped: "done", llm_calls: 1, tool_calls: 0, tool_executions: 0, duplicates_rejected: 0, checkpoints: 0, wrapup: "none", duration_s: 0.2, prompt_chars: 100, lookups: null, prompt_tokens: 12500, compacted: false, tokens_before: null, tokens_after: null, context_window: 128000 },
      { t: "stop_points", items: [], turn_id: "$turn" },
    ],
    on_shutdown: "exit",
  });
  sessionScript(fx.repo, "PERF-C", ops);
  const L = await launchSession(fx.repo);
  try {
    await openEpisode(L.page, "PERF-C");
    await L.page.getByTestId("composer-input").fill("开场");
    await L.page.getByTestId("composer-send").click();
    await expect(L.page.getByTestId("conv-stream")).toContainText("长对话压测就绪", { timeout: 15_000 });
    await L.page.waitForTimeout(250);

    const res = await L.page.evaluate(async () => {
      const el = document.querySelector("[data-testid=conv-stream]") as HTMLElement;
      const maxScroll = Math.max(0, el.scrollHeight - el.clientHeight);
      el.scrollTop = 0;
      const deltas: number[] = [];
      const longs: number[] = [];
      const obs = new PerformanceObserver((list) => {
        for (const e of list.getEntries()) longs.push(e.duration);
      });
      try {
        obs.observe({ type: "longtask", buffered: false });
      } catch {
        // ignore
      }
      await new Promise<void>((resolve) => {
        let i = 0;
        const steps = 40;
        let prev = performance.now();
        const tick = (now: number) => {
          deltas.push(now - prev);
          prev = now;
          i += 1;
          el.scrollTop = Math.round((maxScroll * i) / steps);
          if (i < steps) requestAnimationFrame(tick);
          else resolve();
        };
        requestAnimationFrame(tick);
      });
      obs.disconnect();
      return {
        rowCount: document.querySelectorAll("[data-testid=conv-row]").length,
        maxScroll,
        finalTop: el.scrollTop,
        deltas,
        longs,
      };
    });

    const p50 = percentile(res.deltas, 0.5);
    const p95 = percentile(res.deltas, 0.95);
    console.log(`[PERF P3 conv-scroll] rows=${res.rowCount} maxScroll=${res.maxScroll}px p50=${p50.toFixed(1)}ms p95=${p95.toFixed(1)}ms longTasks=${res.longs.length}`);
    expect(res.rowCount).toBeGreaterThanOrEqual(80);
    expect(res.maxScroll).toBeGreaterThan(2000);
    expect(res.finalTop).toBeGreaterThanOrEqual(res.maxScroll - 4);
    expect(res.longs.length).toBe(0);
    expect(p95).toBeLessThan(35);
  } finally {
    await L.app.close();
    fx.cleanup();
  }
});

test("P4 配音工作台 40 段 60fps 进度条：播放期间 .voice-list 零 DOM 变更、进度条平滑推进", async () => {
  const repo = makeFixtureRepo();
  const ep = join(repo, "data/episodes/PERF-VOICE");
  mkdirSync(join(ep, "03-audio"), { recursive: true });
  writeFileSync(join(ep, "01-topic.md"), "# 性能压测\n类型：杂谈\n");
  const segLines: string[] = [];
  const manifestSegs: { index: number; duration: number }[] = [];
  const sampleWav = join(ep, "03-audio/seg-01.wav");
  ff("-f", "lavfi", "-i", "sine=frequency=440:duration=2.0", sampleWav);
  for (let i = 1; i <= 40; i++) {
    segLines.push(`## 段落 ${i}\n\n配音：第 ${i} 段配音测试文本，用于验证四十段场景下进度条播放时的零列表重渲染。\n\n画面：\n  查询: 测试${i}\n`);
    manifestSegs.push({ index: i, duration: 2.0 });
    const f = join(ep, `03-audio/seg-${String(i).padStart(2, "0")}.wav`);
    if (i > 1) copyFileSync(sampleWav, f);
  }
  writeFileSync(join(ep, "02-script.md"), segLines.join("\n"));
  writeFileSync(join(ep, "02-diff.patch"), "diff\n");
  writeFileSync(join(ep, "03-audio/manifest.json"), JSON.stringify({ engine: "mlx", segments: manifestSegs }));

  const L = await launch(repo);
  try {
    await openEpisode(L.page, "PERF-VOICE");
    await pick(L.page, "03-audio");
    const panel = L.page.getByTestId("voice-panel");
    await panel.waitFor({ timeout: 15_000 });
    await expect(panel.getByTestId("seg-row")).toHaveCount(40);

    await panel.getByTestId("seg-play").first().click();
    await expect(panel.getByTestId("voice-nowplaying")).toBeVisible();
    // 等待音频起播并稳定过初始高亮变更
    await L.page.waitForFunction(() => {
      const a = document.querySelector("[data-testid=voice-audio]") as HTMLAudioElement | null;
      return a !== null && !a.paused && a.currentTime > 0.05;
    }, { timeout: 10_000 });

    const res = await L.page.evaluate(async () => {
      const list = document.querySelector(".voice-list")!;
      const seek = document.querySelector("[data-testid=voice-seek]") as HTMLInputElement;
      const startVal = Number(seek.value);
      let listMutations = 0;
      const mo = new MutationObserver((recs) => {
        listMutations += recs.length;
      });
      mo.observe(list, { childList: true, subtree: true, attributes: true, characterData: true });
      const deltas: number[] = [];
      const longs: number[] = [];
      const obs = new PerformanceObserver((l) => {
        for (const e of l.getEntries()) longs.push(e.duration);
      });
      try {
        obs.observe({ type: "longtask", buffered: false });
      } catch {
        // ignore
      }
      await new Promise<void>((resolve) => {
        let prev = performance.now();
        const t0 = prev;
        const tick = (now: number) => {
          deltas.push(now - prev);
          prev = now;
          if (now - t0 < 650) requestAnimationFrame(tick);
          else resolve();
        };
        requestAnimationFrame(tick);
      });
      mo.disconnect();
      obs.disconnect();
      return {
        startVal,
        endVal: Number(seek.value),
        listMutations,
        deltas,
        longs,
      };
    });

    const p50 = percentile(res.deltas, 0.5);
    const p95 = percentile(res.deltas, 0.95);
    console.log(`[PERF P4 voice-scrubber] seek=${res.startVal.toFixed(2)}->${res.endVal.toFixed(2)}s listMutations=${res.listMutations} p50=${p50.toFixed(1)}ms p95=${p95.toFixed(1)}ms longTasks=${res.longs.length}`);
    expect(res.endVal).toBeGreaterThan(res.startVal + 0.15);
    expect(res.listMutations).toBe(0);
    expect(res.longs.length).toBe(0);
    expect(p95).toBeLessThan(35);
  } finally {
    await L.app.close();
    cleanup(repo);
  }
});

test("P5 动效一致性、可打断性、窄/宽屏 × 深/浅色与 prefers-reduced-motion 实测", async () => {
  const fx = buildFixture();
  const L = await launch(fx.repo, [], undefined, { previewOpen: false });
  try {
    // 1. A3 回归：选题视图下侧栏展开时不重复出现顶栏「新建一期」，收起侧栏后顶栏才浮现
    await expect(L.page.locator(".left [data-testid=new-episode-toggle]")).toBeVisible();
    await expect(L.page.locator("[data-testid=session-head] [data-testid=new-episode-toggle]")).toHaveCount(0);
    await L.page.getByTestId("toggle-left").click();
    await expect(L.page.locator("#ava-left")).toBeHidden();
    await expect(L.page.locator("[data-testid=session-head] [data-testid=new-episode-toggle]")).toBeVisible();
    // 可打断性：点击展开侧栏并在滑移中途立即再次点击收起，再点击展开——最终必须稳态可见、无卡死
    await L.page.getByTestId("toggle-left").click();
    await L.page.waitForTimeout(80);
    await L.page.getByTestId("toggle-left").click();
    await L.page.waitForTimeout(80);
    await L.page.getByTestId("toggle-left").click();
    await expect(L.page.locator("#ava-left")).toBeVisible();
    await expect(L.page.locator("#ava-left")).toHaveAttribute("data-pane-state", "open");

    // 2. PreviewRail 与 .preview 叠放零挤压 + iframe 淡入层正确消退
    await openEpisode(L.page, "E2E-A");
    await pick(L.page, "04-review.html");
    await L.page.getByTestId("html-frame").waitFor({ timeout: 15_000 });
    await expect(L.page.getByTestId("html-frame")).toHaveAttribute("data-loaded", "1", { timeout: 10_000 });
    await expect(L.page.locator(".html-frame-loading")).toHaveAttribute("data-hidden", "1", { timeout: 10_000 });
    await expect(L.page.locator(".html-frame-loading")).toBeHidden();
    // 关闭预览并在真实过渡帧中检查 right-slot 同时承载 closing 面板与 rail 且不纵向堆叠溢出
    await L.page.getByTestId("toggle-preview").click();
    const midBox = await L.page.evaluate(async () => {
      await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
      const slot = document.querySelector(".right-slot") as HTMLElement;
      const rail = document.querySelector(".preview-rail") as HTMLElement | null;
      const pane = document.querySelector("#ava-preview") as HTMLElement | null;
      const sb = slot.getBoundingClientRect();
      const rb = rail?.getBoundingClientRect() ?? null;
      const pb = pane?.getBoundingClientRect() ?? null;
      return {
        paneState: pane?.dataset.paneState ?? "",
        railHeight: rb?.height ?? 0,
        paneHeight: pb?.height ?? 0,
        slotOverflowY: slot.scrollHeight - slot.clientHeight,
        railTopDelta: rb && rb.height > 0 ? Math.abs(rb.top - sb.top) : 999,
        paneTopDelta: pb && pb.height > 0 ? Math.abs(pb.top - sb.top) : 999,
      };
    });
    expect(midBox.paneState).toBe("closing");
    expect(midBox.railHeight).toBeGreaterThan(0);
    expect(midBox.paneHeight).toBeGreaterThan(0);
    expect(midBox.slotOverflowY).toBeLessThanOrEqual(1);
    expect(midBox.railTopDelta).toBeLessThanOrEqual(1);
    expect(midBox.paneTopDelta).toBeLessThanOrEqual(1);
    await expect(L.page.getByTestId("preview-rail")).toBeVisible();

    // 3. 窄屏 (900×720) & 宽屏 (1440×900) × 深色 & 浅色：零横滚 + SegmentedBar 真实切换与 2D 折行对齐
    const readThumbMetrics = () =>
      L.page.evaluate(() => {
        const seg = document.querySelector(".side-head .ui-seg--slide") as HTMLElement | null;
        const thumb = seg?.querySelector(".ui-seg-thumb") as HTMLElement | null;
        const active = seg?.querySelector('button[aria-pressed="true"]') as HTMLElement | null;
        const tb = thumb?.getBoundingClientRect();
        const ab = active?.getBoundingClientRect();
        return {
          overflowX: document.documentElement.scrollWidth - document.documentElement.clientWidth,
          thumbHidden: thumb?.dataset.hidden ?? "1",
          thumbLeft: tb?.left ?? -1,
          thumbTop: tb?.top ?? -1,
          dx: tb && ab ? Math.abs(tb.left - ab.left) : 999,
          dy: tb && ab ? Math.abs(tb.top - ab.top) : 999,
          dw: tb && ab ? Math.abs(tb.width - ab.width) : 999,
          dh: tb && ab ? Math.abs(tb.height - ab.height) : 999,
        };
      });

    for (const scheme of ["light", "dark"] as const) {
      await L.page.emulateMedia({ colorScheme: scheme });
      for (const [w, h] of [[900, 720], [1440, 900]] as const) {
        await L.page.setViewportSize({ width: w, height: h });
        await L.page.waitForTimeout(260);
        const before = await readThumbMetrics();
        expect(before.overflowX, `${scheme} ${w}x${h} overflowX`).toBeLessThanOrEqual(0);
        expect(before.thumbHidden, `${scheme} ${w}x${h} thumbHidden`).toBe("0");
        expect(before.dx, `${scheme} ${w}x${h} thumb dx`).toBeLessThanOrEqual(2);
        expect(before.dy, `${scheme} ${w}x${h} thumb dy`).toBeLessThanOrEqual(2);
        expect(before.dw, `${scheme} ${w}x${h} thumb dw`).toBeLessThanOrEqual(2);
        expect(before.dh, `${scheme} ${w}x${h} thumb dh`).toBeLessThanOrEqual(2);
      }
    }

    // 水平切换 + 2D 折行：验证滑块真实位移并跟到第二行按钮
    const beforeSwitch = await readThumbMetrics();
    await L.page.locator(".side-head .ui-seg--slide button").nth(1).click();
    await L.page.waitForTimeout(280);
    const afterSwitch = await readThumbMetrics();
    expect(afterSwitch.thumbLeft).toBeGreaterThan(beforeSwitch.thumbLeft + 15);
    expect(afterSwitch.dx).toBeLessThanOrEqual(2);
    expect(afterSwitch.dy).toBeLessThanOrEqual(2);

    const wrapCheck = await L.page.evaluate(async () => {
      const seg = document.querySelector(".side-head .ui-seg--slide") as HTMLElement;
      const prevWrap = seg.style.flexWrap;
      const prevMax = seg.style.maxWidth;
      seg.style.flexWrap = "wrap";
      seg.style.maxWidth = "72px";
      await new Promise((r) => setTimeout(r, 280));
      const btns = [...seg.querySelectorAll<HTMLButtonElement>("button")];
      const b0 = btns[0].getBoundingClientRect();
      const b1 = btns[1].getBoundingClientRect();
      const tb = (seg.querySelector(".ui-seg-thumb") as HTMLElement).getBoundingClientRect();
      seg.style.flexWrap = prevWrap;
      seg.style.maxWidth = prevMax;
      return {
        wrappedRowDelta: b1.top - b0.top,
        dx: Math.abs(tb.left - b1.left),
        dy: Math.abs(tb.top - b1.top),
        dw: Math.abs(tb.width - b1.width),
        dh: Math.abs(tb.height - b1.height),
      };
    });
    expect(wrapCheck.wrappedRowDelta).toBeGreaterThan(10);
    expect(wrapCheck.dx).toBeLessThanOrEqual(2);
    expect(wrapCheck.dy).toBeLessThanOrEqual(2);
    expect(wrapCheck.dw).toBeLessThanOrEqual(2);
    expect(wrapCheck.dh).toBeLessThanOrEqual(2);
    await L.page.locator(".side-head .ui-seg--slide button").nth(0).click();

    // 4. prefers-reduced-motion: reduce 立即生效（单次 rAF 窗口内直达稳态，禁止走 240ms 动画）
    await L.page.emulateMedia({ reducedMotion: "reduce" });
    const dur = await L.page.evaluate(() => getComputedStyle(document.querySelector("#ava-preview") as HTMLElement).transitionDuration);
    expect(dur).toBe("0s");
    await L.page.getByTestId("toggle-preview").click();
    const openedInstant = await L.page.evaluate(async () => {
      await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
      const p = document.querySelector("#ava-preview") as HTMLElement;
      return { state: p.dataset.paneState, hidden: p.hidden };
    });
    expect(openedInstant).toEqual({ state: "open", hidden: false });
    await L.page.getByTestId("toggle-preview").click();
    const closedInstant = await L.page.evaluate(async () => {
      await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
      const p = document.querySelector("#ava-preview") as HTMLElement;
      const rail = document.querySelector("[data-testid=preview-rail]") as HTMLElement | null;
      return { state: p.dataset.paneState, hidden: p.hidden, railVisible: Boolean(rail && rail.getBoundingClientRect().height > 0) };
    });
    expect(closedInstant).toEqual({ state: "closed", hidden: true, railVisible: true });
    console.log("[PERF P5 motion+responsive+reducedMotion] all checks passed");
  } finally {
    await L.app.close();
    fx.cleanup();
  }
});
