// D80 取证：P13「工具卡不显示命令」到底是没渲染，还是被待答区滚出了视野。
// 真实 app + 真实 core + 假模型；场景照需求草案截图 03：一轮工具跑完、停机点 05 待审时，再让它跑一条要弹卡的 rm。
// 只读量 DOM，不改 DOM。结论写进 out/d80-shots/p13.json，截图两张：聚焦批准后的原样、把待答区滚到顶。
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test } from "../../../../desktop/node_modules/@playwright/test";
import { assistant, startFakeLlm, toolCalls } from "../../../../desktop/e2e/fakeLlm";
import { convSnapshot, launchSession, realCoreFixture, stubQuit } from "../../../../desktop/e2e/sessionFixtures";

const OUT = join(__dirname, "../../../../desktop/out/d80-shots");
const EP = "第12期-命运石之门人物志";

for (const [w, h] of [[1440, 900], [1280, 800]] as const) {
  test(`P13 卡片可见性 ${w}x${h}`, async () => {
    mkdirSync(OUT, { recursive: true });
    const llm = await startFakeLlm();
    const fx = realCoreFixture(llm.url, [EP]);
    const L = await launchSession(fx.repo);
    const p = L.page;
    try {
      await p.setViewportSize({ width: w, height: h });
      await p.locator("[data-testid=episode]").first().click();
      const send = async (t: string) => { await p.getByTestId("composer-input").fill(t); await p.getByTestId("composer-input").press("Enter"); };
      const turns = (n: number) => expect.poll(async () => (await convSnapshot(L, `ep:${EP}`)).entries.filter((e) => e.k === "frame" && e.frame?.t === "turn_finished").length, { timeout: 30_000 }).toBeGreaterThanOrEqual(n);
      llm.push(toolCalls({ name: "read_status", args: {} }), assistant("停在 05。"));
      await send("这期走到哪了");
      await turns(1);
      llm.push(toolCalls({ name: "bash", args: { command: "rm stale.tmp", description: "清理临时文件" } }), { ...assistant("清理完了。"), delayMs: 4000 });
      await send("把临时文件清掉");
      await p.getByTestId("request-answer-approve").waitFor({ timeout: 20_000 });
      await p.waitForTimeout(400);
      await p.screenshot({ path: join(OUT, `p13-${w}-as-is.png`) });
      const measure = () => p.evaluate(() => {
        const card = document.querySelector<HTMLElement>("[data-testid=request-card]")!;
        const text = card.querySelector<HTMLElement>("[data-testid=request-card-text]");
        const dock = card.closest<HTMLElement>(".dock")!;
        const d = dock.getBoundingClientRect();
        const r = text?.getBoundingClientRect();
        return {
          cardText: text?.textContent ?? null,
          hasCommand: (text?.textContent ?? "").includes("rm stale.tmp"),
          hasDangerLine: (text?.textContent ?? "").includes("危险标记:"),
          dock: { top: d.top, bottom: d.bottom, scrollTop: dock.scrollTop, scrollHeight: dock.scrollHeight, clientHeight: dock.clientHeight },
          textBox: r ? { top: r.top, bottom: r.bottom } : null,
          textFullyVisible: r ? r.top >= d.top && r.bottom <= d.bottom : false,
          activeIsApprove: document.activeElement?.getAttribute("data-testid") === "request-answer-approve",
        };
      });
      const asIs = await measure();
      await p.locator(".dock").first().evaluate((el) => { el.scrollTop = 0; });
      await p.waitForTimeout(200);
      await p.screenshot({ path: join(OUT, `p13-${w}-dock-top.png`) });
      const top = await measure();
      writeFileSync(join(OUT, `p13-${w}.json`), JSON.stringify({ asIs, top }, null, 2));
    } finally {
      await stubQuit(L, "quit").catch(() => undefined);
      await L.app.close().catch(() => undefined);
      await llm.close();
      fx.cleanup();
    }
  });
}
