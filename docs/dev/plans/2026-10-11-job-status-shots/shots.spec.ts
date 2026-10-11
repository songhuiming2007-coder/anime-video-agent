// D71：「做任务的时候 UI 不告诉用户在干什么」三方案的真实窗口截图（未打包构建 × 临时夹具仓，绝不指向真实 data/）。
// 场景照 2026-10-10 伪恋期 03.5：人在顺听面板点了重配，`tts --apply-patch` 正在跑；此前一次机检 rc=1 被时间线记成「failed」。
// 「现状」与「A-已实现」不改 DOM；B / C 是在真实窗口上改 DOM 的原型（选型用，人选了 A）。
// 注意：A 实现之后「现状」图里也会出现作业条（它就是现在的真实界面），选型时的现状见 git 历史里的本脚本。
// 用法（在 desktop/ 下）：PYTHONDONTWRITEBYTECODE=1 npx playwright test -c ../docs/dev/plans/2026-10-11-job-status-shots/shots.config.ts
import { spawn, execFileSync } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test } from "../../../../desktop/node_modules/@playwright/test";
import type { Page } from "../../../../desktop/node_modules/@playwright/test";
import { cleanup, makeFixtureRepo, PY } from "../../../../desktop/tests/helpers";
import { launch } from "../../../../desktop/e2e/fixtures";

const OUT = join(__dirname, "../../../../desktop/out/d71-shots");
const EP = "2026-10-10-伪恋-橘万里花的进攻哲学";
const RUNNING_S = 112; // 截图里「已运行 1:52」
const SCRIPT = [1, 2, 3, 4, 5].map((i) => `## 段落 ${i}\n\n配音：${["千真万确，她从第一集起就在进攻。", "全员都在等，只有她不等。", "这份主动不是莽撞。", "她把每一次见面都当成最后一次。", "所以她赢不赢，其实不重要。"][i - 1]}\n\n画面：\n  查询: 甲\n`).join("\n");

/** 用仓内真实 Event 序列化写一行 events.jsonl（时间戳由调用方给，便于造「刚开始跑」的作业） */
function evLine(repo: string, type: string, payload: object, i: number, iso: string): string {
  const code = `import json, sys
from pipeline.jobs import Event
e = Event(event_id=f"evt_17900000000{int(sys.argv[3]):02d}_abcd", timestamp=sys.argv[4], episode=sys.argv[1], type=sys.argv[2], payload=json.loads(sys.argv[5]))
sys.stdout.write(e.to_jsonl_line())`;
  return execFileSync(PY, ["-c", code, EP, type, String(i), iso, JSON.stringify(payload)], { cwd: repo, encoding: "utf-8", env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1" } });
}

const iso = (msAgo: number) => new Date(Date.now() - msAgo).toISOString().replace(/\.(\d{3})Z$/, ".$1000Z");

/** 原型共用：作业条文案（与真实时间线同源字段：命令、期、已运行时长） */
const VARIANTS: Record<string, (doc: Document, a: { ep: string; secs: number }) => void> = {
  "0-现状": () => undefined,
  // 人选 A（2026-10-11）并已实现（JobBar.tsx）：不改 DOM，拍真实实现；B / C 留作对照
  "A-已实现": () => undefined,
  "B-时间线按钮醒目态": (doc, a) => {
    const head = doc.querySelector<HTMLElement>("[data-testid=timeline] .timeline-head")!;
    head.style.background = "var(--wait-surface)";
    head.style.borderTop = "2px solid var(--wait)";
    const btn = head.querySelector<HTMLElement>("[data-testid=timeline-toggle]")!;
    btn.insertAdjacentHTML("afterbegin", '<span class="ui-spinner" style="margin-right:4px"></span>');
    const mm = `${Math.floor(a.secs / 60)}:${String(a.secs % 60).padStart(2, "0")}`;
    btn.insertAdjacentHTML("afterend", `<span class="ui-badge ui-badge--wait" style="animation:ui-pulse 1.6s ease-in-out infinite">运行中 1 · tts ${mm}</span>`);
  },
  "C-面板自管进行中态": (doc, a) => {
    const panel = doc.querySelector<HTMLElement>("[data-testid=voice-panel]")!;
    const mm = `${Math.floor(a.secs / 60)}:${String(a.secs % 60).padStart(2, "0")}`;
    const row = doc.createElement("div");
    row.className = "notice";
    row.style.cssText = "display:flex;align-items:center;gap:8px;background:var(--wait-surface)";
    row.innerHTML = `<span class="ui-spinner"></span>正在重配段 1、5（已完成 1 / 2）· 已运行 ${mm}`;
    const busy = panel.querySelector<HTMLElement>("[data-testid=voice-lock-busy]")!;
    busy.after(row);
    busy.style.display = "none"; // C 用更具体的进行中行替下现有的「增量重配进行中（pid …）」
    for (const l of ["1", "5"]) {
      const h = panel.querySelector(`[data-testid=seg-row][data-label='${l}'] .voice-row-head span`);
      h?.insertAdjacentHTML("afterend", l === "1" ? '<span class="ui-badge ui-badge--ok">已重配</span>' : '<span class="ui-badge ui-badge--wait"><span class="ui-spinner" style="width:10px;height:10px;margin-right:4px"></span>重配中</span>');
    }
  },
};

async function shoot(page: Page, name: string, size: [number, number] = [1280, 800]): Promise<void> {
  mkdirSync(OUT, { recursive: true });
  await page.setViewportSize({ width: size[0], height: size[1] });
  await page.waitForTimeout(400);
  await page.screenshot({ path: join(OUT, `${name}.png`) });
}

test("D71 作业状态提示：现状 / A 全局作业条 / B 时间线醒目态 / C 面板自管", async () => {
  const repo = makeFixtureRepo();
  const ep = join(repo, "data/episodes", EP);
  mkdirSync(join(ep, "03-audio"), { recursive: true });
  writeFileSync(join(ep, "01-topic.md"), "# 选题\n类型：杂谈\n模式：立论\n张力：全员被动内耗 vs 主动进攻\n");
  writeFileSync(join(ep, "02-script.draft.md"), SCRIPT);
  writeFileSync(join(ep, "02-script.md"), SCRIPT);
  writeFileSync(join(ep, "02-diff.patch"), "--- a\n+++ b\n");
  writeFileSync(join(ep, "03-audio/manifest.json"), JSON.stringify({ engine: "qwen3_tts", segments: [1, 2, 3, 4, 5].map((i) => ({ index: i, label: String(i), duration: 4 })) }));
  for (let i = 1; i <= 5; i++) writeFileSync(join(ep, `03-audio/seg-0${i}.wav`), "RIFF");
  writeFileSync(join(ep, "03-audio/corrections.json"), JSON.stringify([{ id: 1, segment: "1", word: "千", target_tone3: "qian1", scope: "global", applied: false }, { id: 2, segment: "5", issue: "语气发飘", kind: "reseed", scope: "segment", applied: false }]));
  // 一个真实活着的进程当「正在跑的 tts」的 pid：宿主按 pid 存活判 running，否则会标「进程已不在」
  const sleeper = spawn("sleep", ["900"], { stdio: "ignore", detached: true });
  const lines = [
    evLine(repo, "job_created", { job_id: "job_chk", command: "check_script" }, 1, iso(600_000)),
    evLine(repo, "job_started", { job_id: "job_chk", command: "check_script", pid: 1 }, 2, iso(599_000)),
    evLine(repo, "job_finished", { job_id: "job_chk", command: "check_script", status: "failed", returncode: 1, duration_s: 1.8 }, 3, iso(597_000)),
    evLine(repo, "job_created", { job_id: "job_tts1", command: "tts" }, 4, iso(500_000)),
    evLine(repo, "job_started", { job_id: "job_tts1", command: "tts", pid: 1 }, 5, iso(499_000)),
    evLine(repo, "job_finished", { job_id: "job_tts1", command: "tts", status: "succeeded", returncode: 0, duration_s: 125.4 }, 6, iso(374_000)),
    evLine(repo, "job_created", { job_id: "job_tts2", command: "tts --apply-patch" }, 7, iso((RUNNING_S + 1) * 1000)),
    evLine(repo, "job_started", { job_id: "job_tts2", command: "tts --apply-patch", pid: sleeper.pid }, 8, iso(RUNNING_S * 1000)),
  ];
  writeFileSync(join(ep, "events.jsonl"), lines.join(""));
  // 真实 --apply-patch 跑着时锁文件在、pid 活着：现状里面板已有一行「增量重配进行中（pid …）」，如实带上
  writeFileSync(join(ep, "03-audio/.apply_patch.lock"), String(sleeper.pid));

  const L = await launch(repo);
  try {
    const p = L.page;
    await p.locator("[data-testid=episode]", { hasText: EP }).first().click();
    await p.locator("[data-testid=tree-row][data-rel='03-audio']").click();
    await p.getByTestId("voice-panel").waitFor({ timeout: 15_000 });
    await expect(p.getByTestId("timeline")).toContainText("作业 3");
    for (const [name, apply] of Object.entries(VARIANTS)) {
      await p.reload();
      await p.locator("[data-testid=episode]", { hasText: EP }).first().click();
      await p.locator("[data-testid=tree-row][data-rel='03-audio']").click();
      await p.getByTestId("voice-panel").waitFor({ timeout: 15_000 });
      await expect(p.getByTestId("voice-lock-busy")).toBeVisible();
      // 顶部 Code Freeze 横幅只因截图时工作区有未提交改动而出现，与选型无关：四张图统一隐藏
      await p.evaluate(() => document.querySelectorAll<HTMLElement>(".banner").forEach((b) => { if (b.textContent?.includes("Code Freeze")) b.style.display = "none"; }));
      await p.evaluate(`(${apply.toString()})(document, ${JSON.stringify({ ep: EP, secs: RUNNING_S })})`);
      await shoot(p, name);
      if (name === "A-已实现") await shoot(p, "A-已实现-1440x900", [1440, 900]);
    }
  } finally {
    await L.app.close().catch(() => undefined);
    try {
      process.kill(-sleeper.pid!);
    } catch {
      sleeper.kill();
    }
    cleanup(repo);
  }
});
