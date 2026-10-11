// D76：「长命令难读难复制」方案的真实窗口截图（未打包构建 × 真实 core × 本地假 LLM × 临时夹具仓，绝不指向真实 data/）。
// 场景照 2026-10-11 伪恋期 09 封面：助手回复里给了一条超长终端命令（D77 之前的原病例）；
// 随后（D77 之后）它改用 bash 工具，命令出现在工具行与命令审批卡上。
// 「现状」不改 DOM；A / B / C 是在真实窗口上改 DOM 的原型（选型用），人选了 C（2026-10-11）并已实现。
// 注意：C 实现之后「0-现状」与「C-已实现」拍的都是实装界面；选型时的现状见 git 历史里的本脚本。
// A / B / C 原型叠在实装界面上会出现双份按钮，只在选型那一版有意义。
// 用法（在 desktop/ 下）：PYTHONDONTWRITEBYTECODE=1 npx playwright test -c ../docs/dev/plans/2026-10-11-copy-button-shots/shots.config.ts
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test } from "../../../../desktop/node_modules/@playwright/test";
import type { Page } from "../../../../desktop/node_modules/@playwright/test";
import { assistant, startFakeLlm, toolCalls } from "../../../../desktop/e2e/fakeLlm";
import { launchSession, realCoreFixture, stubQuit } from "../../../../desktop/e2e/sessionFixtures";

const OUT = join(__dirname, "../../../../desktop/out/d76-shots");
const EP = "2026-10-10-伪恋-橘万里花的进攻哲学";
const IMG_H = "/Users/songguiming/Desktop/Weixin Image_20261011143022.jpg";
const IMG_V = "/Users/songguiming/Desktop/Weixin Image_20261011143107.jpg";

const ANSWER = [
  "两张扩好的图我这边读不到桌面，需要你在终端里跑下面这条命令把它们导进本期封面目录：",
  "",
  "```bash",
  `cat "${IMG_H}" | ava --episode ${EP} /import-cover --name 07-cover/import/marika-h.jpg && cat "${IMG_V}" | ava --episode ${EP} /import-cover --name 07-cover/import/marika-v.jpg`,
  "```",
  "",
  "导完告诉我，我再用 cover_edit 叠标题字：",
  "",
  "```bash",
  `ava --episode ${EP} /cover-edit --source 07-cover/import/marika-h.jpg --output 07-cover/edit-1.png --text "全员被动，只有她在进攻" --size 120 --anchor bc --stroke-width 8`,
  "```",
].join("\n");

// 第一条 bash（拷图）批准后真跑，留下一行工具行；源图放在夹具仓根（期目录之外），路径照样很长
const bashCopy = (root: string): string =>
  `mkdir -p 07-cover/import && cp "${root}/Weixin Image_20261011143022.jpg" 07-cover/import/marika-h.jpg && cp "${root}/Weixin Image_20261011143107.jpg" 07-cover/import/marika-v.jpg`;
// 第二条 bash（裁图）挂卡待答
const BASH_CROP = [
  "sips --cropToHeightWidth 1080 1920 07-cover/import/marika-h.jpg --out 07-cover/import/marika-h-16x9.jpg",
  "sips --cropToHeightWidth 1920 1080 07-cover/import/marika-v.jpg --out 07-cover/import/marika-v-9x16.jpg",
].join(" && ");

// 原型一律走 CSSOM（el.style）：窗口 CSP 不放行注入的 <style> 与 style 属性，CSSOM 赋值不受限（D71 原型同法）。
// 整个原型是一个在页面里执行的函数（闭包带不过去），按方案名分支；先撤掉上一个原型留下的东西。
const PROTO = (doc: Document, variant: string): void => {
  doc.querySelectorAll("[data-d76]").forEach((n) => n.remove());
  doc.querySelectorAll<HTMLElement>("[data-d76s]").forEach((n) => { n.style.cssText = ""; delete n.dataset.d76s; });
  const css = (el: HTMLElement, text: string): void => { el.dataset.d76s = "1"; el.style.cssText += text; };
  const btn = (label: string, done = false): HTMLButtonElement => {
    const b = doc.createElement("button");
    b.className = "ui-btn ui-btn--ghost ui-btn--sm";
    b.dataset.d76 = "1";
    b.textContent = done ? "✓ 已复制" : label;
    b.style.cssText = done ? "color:var(--ok);border-color:var(--ok);background:var(--bg)" : "background:var(--bg)";
    return b;
  };
  const blocks = [...doc.querySelectorAll<HTMLElement>("[data-testid=chat-markdown] pre")];
  const blockCopy = (wrap: boolean, onlyFirst: boolean, doneFirst: boolean): void => {
    blocks.forEach((pre, i) => {
      css(pre, "position:relative");
      if (wrap) {
        css(pre, "white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-all;overflow-x:visible;padding-right:80px");
        const code = pre.querySelector<HTMLElement>("code");
        if (code) css(code, "white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-all");
      }
      if (onlyFirst && i !== 0) return;
      const b = btn("复制", doneFirst && i === 0);
      b.style.cssText += ";position:absolute;top:6px;right:6px;margin:0";
      pre.appendChild(b);
    });
  };
  if (variant === "A") blockCopy(false, true, false);
  if (variant === "B" || variant === "C") blockCopy(true, false, true);
  if (variant === "C") {
    const row = [...doc.querySelectorAll<HTMLElement>("[data-testid=conv-row][data-kind=tool] .conv-tool")].find((r) => r.querySelector("code")?.textContent === "bash");
    row?.querySelector(".spacer")?.after(btn("复制命令"));
    const card = doc.querySelector<HTMLElement>("[data-testid=request-card-text]");
    if (card) {
      const bar = doc.createElement("div");
      bar.dataset.d76 = "1";
      bar.className = "d76-cmdbar";
      bar.style.cssText = "display:flex;justify-content:flex-end;margin-top:8px";
      bar.appendChild(btn("复制命令"));
      card.after(bar);
    }
  }
};

// 0 现状不改 DOM。A：最小改动，代码块右上角「复制」（拍的是鼠标悬停在第一块上的样子：实装时只在悬停/聚焦时出现），长行照旧横向滚动。
// B：复制按钮常驻 + 长行折行显示（复制出来仍是原文一行）；第一块拍的是点过之后的「✓ 已复制」反馈。
// C：B + 命令出现的另外两处也能一键复制完整命令：工具行（摘要只显示前 80 字）、命令审批卡。
const VARIANTS: Record<string, string> = {
  "0-现状": "0",
  "A-悬停复制": "A",
  "B-常驻复制+折行": "B",
  "C-B+工具行与审批卡也可复制": "C",
  "C-已实现": "0",
};

async function shoot(page: Page, name: string, size: [number, number] = [1280, 800]): Promise<void> {
  mkdirSync(OUT, { recursive: true });
  await page.setViewportSize({ width: size[0], height: size[1] });
  await page.waitForTimeout(400);
  await page.screenshot({ path: join(OUT, `${name}.png`) });
}

test("D76 长命令复制：现状 / A 悬停复制 / B 常驻复制+折行 / C 再加工具行与审批卡", async () => {
  const llm = await startFakeLlm();
  const fx = realCoreFixture(llm.url, [EP]);
  const L = await launchSession(fx.repo);
  try {
    const p = L.page;
    for (const f of ["Weixin Image_20261011143022.jpg", "Weixin Image_20261011143107.jpg"]) writeFileSync(join(fx.repo.root, f), Buffer.from([0xff, 0xd8]));
    llm.push(
      assistant(ANSWER),
      toolCalls({ name: "bash", args: { command: bashCopy(fx.repo.root), description: "把两张图拷进封面目录并改名" } }),
      toolCalls({ name: "bash", args: { command: BASH_CROP, description: "裁成 16:9 与 9:16 两张" } }),
    );
    await p.locator("[data-testid=episode]", { hasText: EP }).first().click();
    await p.getByTestId("composer-input").fill("横竖两张扩好了，在桌面上，放进封面目录");
    await p.getByTestId("composer-input").press("Enter");
    await expect(p.getByTestId("chat-markdown").first()).toContainText("import-cover", { timeout: 20_000 });
    await p.getByTestId("composer-input").fill("你直接做，别让我去终端");
    await p.getByTestId("composer-input").press("Enter");
    await expect(p.getByTestId("request-card-text")).toContainText("拷进封面目录", { timeout: 20_000 });
    await p.getByTestId("request-answer-approve").click();
    await expect(p.getByTestId("request-card-text")).toContainText("裁成 16:9", { timeout: 20_000 });
    // 顶部 Code Freeze 横幅只因截图时工作区有未提交改动而出现，与选型无关：统一隐藏
    await p.evaluate(() => document.querySelectorAll<HTMLElement>(".banner").forEach((b) => { if (b.textContent?.includes("Code Freeze")) b.style.display = "none"; }));
    for (const [name, variant] of Object.entries(VARIANTS)) {
      await p.evaluate(`(${PROTO.toString()})(document, ${JSON.stringify(variant)})`);
      // 两处取景：助手回复里的代码块（D77 之前的原病例）、命令审批卡（D77 之后命令出现的地方）
      await p.setViewportSize({ width: 1280, height: 800 });
      await p.evaluate(() => document.querySelector("[data-testid=chat-markdown]")?.scrollIntoView({ block: "start" }));
      await shoot(p, `${name}-1回复`);
      await p.evaluate(() => {
        [...document.querySelectorAll("[data-testid=conv-row][data-kind=tool]")].pop()?.scrollIntoView({ block: "start" });
        (document.querySelector(".d76-cmdbar") ?? document.querySelector("[data-testid=request-card-text]"))?.scrollIntoView({ block: "end" });
      });
      await shoot(p, `${name}-2审批卡`);
    }
  } finally {
    await stubQuit(L, "quit").catch(() => undefined);
    await L.app.close().catch(() => undefined);
    await llm.close();
    fx.cleanup();
  }
});
