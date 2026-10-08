// D46 真实窗口截图（真实实现，不是注入原型）：未打包构建 × 临时夹具仓（绝不指向真实 data/），假 protocol.py 按剧本出帧。
// 回复文案取自人 2026-10-08 贴来的模型输出（加回了它原本的 Markdown 标记），只作排版夹具，其中剧情断言未经核实。
// 用法（在 desktop/ 下）：PYTHONDONTWRITEBYTECODE=1 npx playwright test -c ../docs/dev/plans/2026-10-08-conv-readability-shots/shots.config.ts
import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { expect, test } from "../../../../desktop/node_modules/@playwright/test";
import type { Page } from "../../../../desktop/node_modules/@playwright/test";
import { launchSession, sessionFixture, stubQuit } from "../../../../desktop/e2e/sessionFixtures";
import { sessionKey, sessionScript } from "../../../../desktop/tests/fixtures/session";

const OUT = join(__dirname, "../../../../desktop/out/d46-shots");
const SIZES: [number, number][] = [
  [1440, 900],
  [1280, 800],
];
const EP = "2026-09-21-东京喰种-雾岛董香人物志-二";

const ANSWER = `导演敏锐！抓出了一个严重的时序错位（串戏）！

### 1. 真实剧情查验（为什么这里会觉得突兀）

**「金木探望生病的董香、发现董香吃了依子便当而胃痛」** 这场戏，真实发生在 **第一季第 5 集**（S01E05 02:00–06:00 左右），也就是月山习绑架西野贵未之前！

- **真实时间线**：S01E05 董香胃痛金木探病 → S01E06–E08 凉子之死与真户吴绪之死 → S01E09 恢复日常（本期时间线起点）。
- **S01E09 真实的日常剧情**：在真户死后，董香把雏实接到自己公寓同住，雏实心情低落；……

### 2. 为什么写稿会写错？

写稿时把 S01E05 与 S01E09 的两次「吃人类食物」记忆发生了张冠李戴的拼接，导致把第五集的探病强行塞进了第九集之后。

---

| 段落 | 原锚点 | 应改为 |
|---|---|---|
| 3 | S01E09 03:20 | S01E05 02:53 |`;

const OBS = JSON.stringify({
  ok: true,
  result: {
    ok: false,
    message: "退出码 1",
    argv: [".venv/bin/python", "-m", "pipeline.check_script", `data/episodes/${EP}/02-script.draft.md`],
    returncode: 1,
    duration_s: 4.93,
    stdout_tail:
      "PASS  段落数 8–45       17 段\nPASS  字数 1285–1542   1303 字\nPASS  时长 5–6 分钟      5.1 分钟\nFAIL  句长起伏 CV ≥0.55  0.49（均长 28.6 字，最长 73）　长短太齐\nPASS  无括号            无\n------------------------------------------------------------\n1 项未过。",
    stderr_tail: "",
    truncated: false,
  },
});

const STARTED = { t: "turn_started", turn_id: "$turn", rid: "$rid" };
const toolStart = (i: number, name: string, summary: string) => ({ t: "tool", turn_id: "$turn", phase: "start", index: i, name, summary, ok: null, observation: null, duplicate: false });
const toolEnd = (i: number, name: string, ok: boolean, observation: string | null) => ({ t: "tool", turn_id: "$turn", phase: "end", index: i, name, summary: "", ok, observation, duplicate: false });
const FINISHED = { t: "turn_finished", turn_id: "$turn", stopped: "done", llm_calls: 3, tool_calls: 2, tool_executions: 2, duplicates_rejected: 0, checkpoints: 0, wrapup: "none", duration_s: 9, prompt_chars: 48213 };
const READY = { op: "emit", frame: { t: "ready", episode: EP, scope: "creative", continue_status: "new", llm: "ok", degrade_reason: null, code_freeze_ok: true, history_count: 0, session_bytes: 0, other_sessions: [] } };

async function shoot(page: Page, name: string): Promise<void> {
  mkdirSync(OUT, { recursive: true });
  for (const [w, h] of SIZES) {
    await page.setViewportSize({ width: w, height: h });
    await page.waitForTimeout(300);
    await page.screenshot({ path: join(OUT, `${name}-${w}x${h}.png`) });
  }
}

test("D46：思考中 / 工具运行中 / 回复渲染 + 原文收起", async () => {
  const fx = sessionFixture([EP]);
  const repo = fx.repo;
  sessionKey(repo, "sk-ava-test-key");
  sessionScript(repo, EP, [
    READY,
    // 第一轮：工具执行中停 4 秒（截「工具运行中」）
    { op: "wait", lines: 1 },
    { op: "reply", frames: [STARTED, toolStart(0, "search_notes", "[字幕] S01E09 依子"), toolEnd(0, "search_notes", true, null), toolStart(1, "run_pipeline", "check_script")] },
    { op: "sleep", seconds: 4 },
    { op: "reply", frames: [toolEnd(1, "run_pipeline", false, OBS)] },
    // 模型生成：再停 4 秒（截「模型思考中」）
    { op: "sleep", seconds: 4 },
    { op: "reply", frames: [{ t: "assistant", turn_id: "$turn", kind: "answer", text: ANSWER }, FINISHED, { t: "stop_points", items: [], turn_id: "$turn" }] },
    { op: "loop" },
  ]);
  const L = await launchSession(repo, [], { previewOpen: false });
  try {
    const p = L.page;
    await p.waitForSelector("[data-testid=episode]");
    await p.locator("[data-testid=episode]", { hasText: EP }).first().click();
    await expect(p.getByTestId("center")).toHaveAttribute("data-ep", EP);
    await p.getByTestId("composer-input").fill("段落3读着很突兀，金木探病是哪一集的？");
    await p.getByTestId("composer-send").click();
    await expect(p.getByTestId("conv-activity")).toHaveAttribute("data-what", "tool");
    await p.waitForTimeout(1500);
    await shoot(p, "d46a-tool-running");
    await expect(p.getByTestId("conv-activity")).toHaveAttribute("data-what", "model");
    await p.waitForTimeout(1500);
    await shoot(p, "d46b-model-thinking");
    await p.locator("[data-testid=conv-row][data-kind=footer]").first().waitFor();
    await p.getByTestId("conv-stream").evaluate((el) => (el.scrollTop = 0));
    await shoot(p, "d46c-answer-top");
    await p.getByTestId("conv-stream").evaluate((el) => (el.scrollTop = el.scrollHeight));
    await shoot(p, "d46d-answer-bottom");
  } finally {
    await stubQuit(L, "quit").catch(() => undefined);
    await L.app.close().catch(() => undefined);
    fx.cleanup();
  }
});
