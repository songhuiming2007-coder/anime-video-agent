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
