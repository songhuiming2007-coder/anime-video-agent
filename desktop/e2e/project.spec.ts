// D81 PR-2b：侧栏企划卡片（人选方案 B「分组卡片」）的 e2e——真实 core（复制来的 pipeline）+ 本地假 LLM，夹具全在系统临时目录。
// spec §7.6：企划会话入口与 ready.session_kind、V1 / V2 同名卷打开不同目录、新建企划 / 在企划下建卷、1280 / 1440 底线断言、四处高亮互斥。
import { expect, test, type Page } from "@playwright/test";
import type { Launched } from "./fixtures";
import { assistant, startFakeLlm, type FakeLlm } from "./fakeLlm";
import { convSnapshot, launchSession, realCoreFixture, stubQuit, type SessionFixture } from "./sessionFixtures";
import { fixtureWrite } from "../tests/fixtures/session";

type Ctx = SessionFixture & { L: Launched; llm: FakeLlm };

async function withRealCore(eps: string[], prep: (fx: SessionFixture) => void, fn: (ctx: Ctx) => Promise<void>): Promise<void> {
  const llm = await startFakeLlm();
  const fx = realCoreFixture(llm.url, eps);
  prep(fx);
  const L = await launchSession(fx.repo);
  try {
    await expect(L.page.locator("[data-testid=episode], [data-testid=project]").first()).toBeVisible({ timeout: 30_000 });
    await fn({ ...fx, L, llm });
  } finally {
    await stubQuit(L, "quit").catch(() => undefined);
    await L.app.close().catch(() => undefined);
    await llm.close();
    fx.cleanup();
  }
}

const TABLE = (name: string, dirs: string[]) =>
  [`企划: ${name}`, "", "## 卷表", "", "| 卷序 | 目录 | 年份 | 时长预算 | 片尾 |", "|---|---|---|---|---|",
    ...dirs.map((d, i) => `| ${i + 1} | ${d} | 2011 | 14-16分钟 | ${i === dirs.length - 1 ? "完整保留" : "余韵18秒"} |`), ""].join("\n");

const card = (p: Page, proj: string) => p.locator(`[data-testid=project][data-proj="${proj}"]`);
const volRow = (p: Page, epKey: string) => p.locator(`[data-testid=episode][data-ep="${epKey}"]`);

/** 侧栏里带 aria-current 的入口（选题、期 / 卷行、企划会话按钮）——四者互斥，恰好一个 */
const currentIn = (p: Page) =>
  p.evaluate(() => [...document.querySelectorAll("[data-testid=episodes] :is([data-testid=idea], [data-testid=episode], [data-testid=project-session])[aria-current=true]")].map((e) => (e as HTMLElement).dataset.testid === "project-session" ? `proj:${(e.closest("[data-proj]") as HTMLElement).dataset.proj}` : (e as HTMLElement).dataset.ep ?? (e as HTMLElement).dataset.testid));

type Frame = Record<string, unknown> & { t: string };
async function readyOf(L: Launched, convKey: string): Promise<Frame> {
  await expect.poll(async () => (await convSnapshot(L, convKey)).entries.some((e) => e.k === "frame" && e.frame?.t === "turn_finished"), { timeout: 30_000 }).toBe(true);
  const f = (await convSnapshot(L, convKey)).entries.find((e) => e.k === "frame" && e.frame?.t === "ready");
  return f!.frame as Frame;
}

const send = async (p: Page, text: string) => {
  await p.getByTestId("composer-input").fill(text);
  await p.getByTestId("composer-input").press("Enter");
};

test("D81 PR-2b 企划会话入口：点「企划会话」→ proj: 键、ready.session_kind=project；与卷 / 普通期 / 选题的高亮互斥", async () => {
  await withRealCore(["P/01-a", "P/02-b", "PLAIN-1"], (fx) => fixtureWrite(fx.repo.root, "data/episodes/P/00-project.md", TABLE("测试编年史", ["01-a", "02-b"])), async ({ L, llm }) => {
    const p = L.page;
    expect(await currentIn(p)).toEqual(["idea"]);
    await card(p, "P").getByTestId("project-session").click();
    await expect(p.getByTestId("center")).toHaveAttribute("data-conv", "proj:P");
    await expect(p.getByTestId("center")).toHaveAttribute("data-ep", "");
    await expect(p.getByTestId("project-head")).toContainText("第 2 卷 · 02-b");
    await expect(p.getByTestId("composer-target")).toHaveText("企划会话 · P");
    expect(await currentIn(p)).toEqual(["proj:P"]);
    llm.push(assistant("在企划会话里。"));
    await send(p, "定卷表");
    const ready = await readyOf(L, "proj:P");
    expect([ready.session_kind, ready.project]).toEqual(["project", "P"]);
    // 企划会话的会话列表（proj: 键）挂在卡上，读得到刚才那一段（core /list-sessions 认企划目录）
    await expect(card(p, "P").locator(".ep-session-wrap")).toHaveCount(1, { timeout: 15_000 });
    await expect(card(p, "P").getByTestId("session-error")).toHaveCount(0);

    await volRow(p, "P/01-a").click();
    await expect(p.getByTestId("center")).toHaveAttribute("data-conv", "ep:P/01-a");
    expect(await currentIn(p)).toEqual(["P/01-a"]);
    await volRow(p, "PLAIN-1").click();
    expect(await currentIn(p)).toEqual(["PLAIN-1"]);
    await p.getByTestId("idea").click();
    expect(await currentIn(p)).toEqual(["idea"]);
    await card(p, "P").getByTestId("project-session").click();
    expect(await currentIn(p)).toEqual(["proj:P"]);
  });
});

test("D81 PR-2b V1 / V2 同名卷：两张卡里的 01-借躯降生 打开的是两个不同目录（中栏 data-ep、ready.project 各自对）", async () => {
  // 带一个普通期：共享的 launch() 要等到一行期才返回（全是折叠卡里的卷会白等 30 s 超时）
  await withRealCore(["EGOIST/01-借躯降生", "EGOIST-V2/01-借躯降生", "PLAIN-1"], (fx) => fixtureWrite(fx.repo.root, "data/episodes/EGOIST-V2/00-project.md", TABLE("EGOIST V2", ["01-借躯降生"])), async ({ L, llm }) => {
    const p = L.page;
    // V1 没有 00-project.md：有卷就是企划，标「未立项」
    await expect(card(p, "EGOIST").getByTestId("project-unplanned")).toBeVisible();
    await expect(card(p, "EGOIST-V2").getByTestId("project-unplanned")).toHaveCount(0);
    for (const proj of ["EGOIST", "EGOIST-V2"]) {
      const t = card(p, proj).getByTestId("project-toggle");
      if ((await t.getAttribute("aria-expanded")) !== "true") await t.click();
    }
    for (const proj of ["EGOIST", "EGOIST-V2"]) {
      const key = `${proj}/01-借躯降生`;
      await card(p, proj).locator("[data-testid=episode]").click();
      await expect(p.getByTestId("center")).toHaveAttribute("data-ep", key);
      await expect(p.getByTestId("composer-target")).toHaveText(key);
      expect(await currentIn(p)).toEqual([key]);
      llm.push(assistant(`在 ${proj}。`));
      await send(p, "你在哪一卷");
      const ready = await readyOf(L, `ep:${key}`);
      expect([ready.session_kind, ready.project]).toEqual(["volume", proj]);
    }
  });
});

test("D81 PR-2b 新建企划 → 卡出现（骨架 00-project.md、还没有卷）；在企划下新建卷 → 卷归在该企划卡里并打开", async () => {
  await withRealCore(["PLAIN-1"], () => undefined, async ({ L }) => {
    const p = L.page;
    await p.getByTestId("new-project-toggle").click();
    await p.getByTestId("project-name").fill("新企划");
    await p.getByTestId("project-create").click();
    await expect(card(p, "新企划")).toContainText("还没有卷", { timeout: 20_000 });
    await expect(card(p, "新企划").getByTestId("project-unplanned")).toHaveCount(0); // ava new-project 写骨架 00-project.md（§6.3 ⑱）
    await expect(p.getByTestId("center")).toHaveAttribute("data-conv", "proj:新企划");
    await card(p, "新企划").getByTestId("new-volume-toggle").click();
    await card(p, "新企划").getByTestId("volume-name").fill("01-第一卷");
    await card(p, "新企划").getByTestId("volume-create").click();
    await expect(p.getByTestId("center")).toHaveAttribute("data-ep", "新企划/01-第一卷", { timeout: 20_000 });
    await expect(card(p, "新企划").locator(`[data-testid=episode][data-ep="新企划/01-第一卷"]`)).toBeVisible();
    // 平铺区（卡外）不出现这一卷
    await expect(p.locator(`[data-testid=episodes] > div > [data-testid=episode][data-ep="新企划/01-第一卷"]`)).toHaveCount(0);
    expect(await currentIn(p)).toEqual(["新企划/01-第一卷"]);
  });
});

const PLAIN = Array.from({ length: 20 }, (_, i) => `2026-08-${String(i + 1).padStart(2, "0")}-罪恶王冠-楪祈人物志-第${i + 1}篇长名字`);
const PROJ_EPS = [
  ...["01-黑岩射手与ryo的同人时代", "02-nagi接棒的那几年", "03-こしょう与第三代歌姬", "04-十五年后的告别演出"].map((v) => `supercell-编年史/${v}`),
  ...["01-借躯降生", "02-皮套囚徒", "03-终局葬礼"].flatMap((v) => [`EGOIST-传奇企划志/${v}`, `EGOIST-传奇企划志-V2/${v}`]),
];

for (const [w, h] of [[1280, 800], [1440, 900]] as const) {
  test(`D81 PR-2b 底线（UI 规则 5）${w}×${h}：20 个普通期 + 3 个企划全展开——卡头 / 进度条 / 行 / 会话行两两不相交、侧栏不横向溢出、可滚动、中栏不被压破`, async () => {
    await withRealCore([...PLAIN, ...PROJ_EPS], () => undefined, async ({ L }) => {
      const p = L.page;
      await p.setViewportSize({ width: w, height: h });
      for (const proj of ["supercell-编年史", "EGOIST-传奇企划志", "EGOIST-传奇企划志-V2"]) {
        const t = card(p, proj).getByTestId("project-toggle");
        if ((await t.getAttribute("aria-expanded")) !== "true") await t.click();
      }
      await volRow(p, "supercell-编年史/02-nagi接棒的那几年").click(); // 当前卷下挂会话列表
      await card(p, "EGOIST-传奇企划志-V2").getByTestId("new-volume-toggle").click(); // 展开一张建卷表单
      await expect(p.locator("[data-testid=project-volumes] [data-testid=episode]")).toHaveCount(10);
      const r = await p.evaluate(() => {
        const side = document.querySelector("nav.left") as HTMLElement;
        const sel = ".proj-head, .proj-name, .proj-sess, .proj-sub, .proj-bar, .proj-empty, [data-testid=episodes] .ui-row, [data-testid=episodes] .ep-session-wrap, [data-testid=episodes] .new-episode-form";
        const boxes = [...document.querySelectorAll(sel)].map((e) => e.getBoundingClientRect()).filter((b) => b.height > 0);
        const contains = (x: DOMRect, y: DOMRect) => x.left <= y.left + 0.5 && x.right >= y.right - 0.5 && x.top <= y.top + 0.5 && x.bottom >= y.bottom - 0.5;
        let overlap = 0;
        for (let i = 0; i < boxes.length; i++) for (let j = i + 1; j < boxes.length; j++) {
          const a = boxes[i], b = boxes[j];
          if (contains(a, b) || contains(b, a)) continue;
          if (a.left < b.right - 0.5 && b.left < a.right - 0.5 && a.top < b.bottom - 0.5 && b.top < a.bottom - 0.5) overlap++;
        }
        const center = (document.querySelector("[data-testid=center]") as HTMLElement).getBoundingClientRect().width;
        return { n: boxes.length, overlap, xOverflow: side.scrollWidth > side.clientWidth, scrolls: side.scrollHeight > side.clientHeight, center };
      });
      expect(r.n).toBeGreaterThan(40);
      expect(r.overlap).toBe(0);
      expect(r.xOverflow).toBe(false);
      expect(r.scrolls).toBe(true);
      expect(r.center).toBeGreaterThanOrEqual(360); // layout.ts CENTER_MIN
    });
  });
}
