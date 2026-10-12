// D80 PR-C：方案 A 落地后的真实窗口截图（不注入任何原型 DOM，拍的就是产品代码）。场景与 options.shots.spec.ts 相同，
// 便于和人选型时看过的 A 原型逐张对照。
// 用法（在 desktop/ 下）：
//   PYTHONDONTWRITEBYTECODE=1 npx playwright test -c ../docs/dev/plans/2026-10-12-conv-liveliness-shots/shots.config.ts -g "PR-C"
// 产物：desktop/out/d80-shots/pr-c/
import { execFileSync } from "node:child_process";
import { mkdirSync, rmSync } from "node:fs";
import { join } from "node:path";
import { expect, test } from "../../../../desktop/node_modules/@playwright/test";
import type { Page } from "../../../../desktop/node_modules/@playwright/test";
import { assistant, startFakeLlm, toolCalls } from "../../../../desktop/e2e/fakeLlm";
import { convSnapshot, launchSession, realCoreFixture, stubQuit } from "../../../../desktop/e2e/sessionFixtures";

const OUT = join(__dirname, "../../../../desktop/out/d80-shots/pr-c");
const EP = "第12期-命运石之门人物志";
const bash = (command: string, description: string) => ({ name: "bash", args: { command, description } });

async function shot(p: Page, name: string): Promise<void> {
  await p.getByTestId("conv-stream").evaluate((el) => { el.scrollTop = el.scrollHeight; });
  await p.waitForTimeout(450); // 等高度过渡（--dur-slow 300ms）与入场（--dur-enter 240ms）走完
  await p.getByTestId("center").screenshot({ path: join(OUT, `${name}.png`) });
}

/** 确定性取帧：暂停全部动画并设 currentTime，逐帧截图，ffmpeg 合成 gif（同 options.shots.spec.ts） */
async function gif(p: Page, name: string, ms = 4000, fps = 15): Promise<void> {
  const dir = join(OUT, `_frames-${name}`);
  rmSync(dir, { recursive: true, force: true });
  mkdirSync(dir, { recursive: true });
  for (let i = 0; i < Math.round((ms / 1000) * fps); i++) {
    await p.evaluate((t) => document.getAnimations().forEach((an) => { an.pause(); an.currentTime = t; }), (i * 1000) / fps);
    await p.getByTestId("center").screenshot({ path: join(dir, `${String(i).padStart(3, "0")}.png`), animations: "allow" });
  }
  await p.evaluate(() => document.getAnimations().forEach((an) => an.play()));
  execFileSync("ffmpeg", ["-y", "-loglevel", "error", "-framerate", String(fps), "-i", join(dir, "%03d.png"),
    "-vf", "split[s0][s1];[s0]palettegen=max_colors=128[p];[s1][p]paletteuse=dither=none", "-loop", "0", join(OUT, `${name}.gif`)]);
  rmSync(dir, { recursive: true, force: true });
}

test("PR-C 实现截图：方案 A 落地后的真实窗口", async () => {
  test.setTimeout(300_000);
  rmSync(OUT, { recursive: true, force: true });
  mkdirSync(OUT, { recursive: true });
  const llm = await startFakeLlm();
  const fx = realCoreFixture(llm.url, [EP]);
  const L = await launchSession(fx.repo, [], { previewOpen: false });
  const p = L.page;
  const send = async (t: string) => { await p.getByTestId("composer-input").fill(t); await p.getByTestId("composer-input").press("Enter"); };
  const turnsDone = (n: number) => expect.poll(async () => (await convSnapshot(L, `ep:${EP}`)).entries.filter((e) => e.k === "frame" && e.frame?.t === "turn_finished").length, { timeout: 30_000 }).toBeGreaterThanOrEqual(n);
  try {
    await p.setViewportSize({ width: 1440, height: 900 });
    await p.locator("[data-testid=episode]").first().click();
    // 夹具仓不是干净 git 树：Code Freeze 横幅是夹具伪影，隐藏（D71 同法；只动横幅，不动对话区）
    await p.evaluate(() => document.querySelectorAll<HTMLElement>(".banner").forEach((b) => { if (b.textContent?.includes("Code Freeze")) b.hidden = true; }));

    llm.push(
      toolCalls({ name: "read_status", args: {} }),
      toolCalls(bash("ls -la", "列出期目录"), bash("wc -m 02-script.md", "统计稿件字数")),
      toolCalls({ name: "read_artifact", args: { path: "01-topic.md" } }),
      assistant("## 当前进度\n\n这期停在 **05 审时间码**，稿件字数正常：\n\n- 总字数在上限内\n- `人物:` 标记齐全\n\n下一步需要你在审看页过一遍时间码。"),
    );
    await send("这期走到哪了？顺便看下字数");
    await turnsDone(1);
    await shot(p, "s0-ok-1440");

    llm.push(
      toolCalls(bash("ls _agent", "列出留底目录"), bash("cat 旧稿-v1.md", "读取旧稿"), bash("head -5 02-script.md", "读稿件开头")),
      assistant("旧稿 `旧稿-v1.md` 不在期目录里，`_agent/` 下也没有留底。如果你记得放在哪，告诉我路径；否则只能拿现在的 `02-script.md` 对比。"),
    );
    await send("把旧稿找出来跟现在的对比一下");
    await turnsDone(2);
    await shot(p, "s1-light-1440");
    await p.emulateMedia({ colorScheme: "dark" });
    await shot(p, "s1-dark-1440");
    await p.emulateMedia({ colorScheme: "light" });
    await p.setViewportSize({ width: 1280, height: 800 });
    await shot(p, "s1-light-1280");
    await p.setViewportSize({ width: 1440, height: 900 });

    // 展开细节：回合 1 展开 + 失败那条展开，鼠标停在失败行上（复制图标出现）
    await p.getByTestId("turn-fold").first().click();
    const failRow = p.locator("[data-testid=conv-row][data-kind=tool][data-status=fail]");
    await failRow.getByTestId("tool-toggle").click();
    await failRow.locator(".conv-tool").hover();
    await shot(p, "s2-detail-1440");
    await p.getByTestId("turn-fold").first().click();

    llm.push(toolCalls({ name: "read_status", args: {} }), toolCalls(bash("sleep 30", "等待机检作业")), assistant("机检跑完了。"));
    await send("跑一下机检");
    await expect(p.getByTestId("conv-tool-shimmer")).toHaveCount(1, { timeout: 20_000 });
    await shot(p, "s3-running-1440");
    await gif(p, "s3-running-1440");
  } finally {
    await stubQuit(L, "quit").catch(() => undefined);
    await L.app.close().catch(() => undefined);
    await llm.close();
    fx.cleanup();
  }
});
