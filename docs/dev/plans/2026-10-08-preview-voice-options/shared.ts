// D49 / D50 原型共用夹具：临时 repo 副本（真实 core）+ 一期停在 02.5、一期停在 03.5（六段真实静音 wav）。
// 绝不指向真实 data/。
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { expect } from "../../../../desktop/node_modules/@playwright/test";
import type { Page } from "../../../../desktop/node_modules/@playwright/test";
import { ff } from "../../../../desktop/e2e/fixtures";
import { makeFixtureRepo } from "../../../../desktop/tests/helpers";

export const OUT = join(__dirname, "../../../../desktop/out/d49-shots"); // desktop/out 已被 .gitignore 排除
export const SIZES: [number, number][] = [
  [1440, 900],
  [1280, 800],
];
export const EP025 = "2026-10-01-测试番-人物志-一";
export const EP035 = "2026-10-02-测试番-人物志-二";

const PARAS = [
  "搜查官死后，二十区的血腥暂时平息。妹妹搬来和她同住，她学着像个姐姐那样帮妹妹修剪头发，在店里和同事斗嘴争吵。",
  "看着妹妹捡回来的受伤雏鸟，她站在窗边出神。当年父亲也曾带着他们姐弟救过这样一只小鸟，叮嘱她作为姐姐要好好教导弟弟。",
  "为了守住高中生的普通日常，她强忍着生理上的排斥，硬生生把朋友送来的便当咽进胃里。即使吃人类食物会损伤身体，她也不愿放弃那一点温度。",
  "但这层勉强维系的宁静很快被暴风雨撕碎。激进组织为了追查一个失踪的人，一路搜到二十区。踹开咖啡店大门的，竟然就是她朝思暮想的弟弟。",
  "几年未见的重逢没有半分温情。弟弟当众将她按在地上，嘲笑她和人类混在一起过家家太软弱，甚至唾骂死去的父亲是个懦夫。",
  "至亲的践踏与失去同伴的无力，像耳光抽在她脸上。咖啡店构筑的避风港一夜崩塌，她第一次意识到，自己什么都保护不了。",
];
const QUERIES = ["从那之后就一起住了", "要好好教导弟弟", "依子好不容易做出来的", "想让我再把你揍到吐血吗", "跟老爸一个怂样", "店里众人震惊"];

export function scriptText(): string {
  return PARAS.map(
    (p, i) => `## 段落 ${i + 1}\n\n配音：${p}\n\n画面：\n  锚点: S01E0${5 + (i % 4)} 0${3 + i}:1${i}\n  查询: ${QUERIES[i]}\n  备选: 无\n`,
  ).join("\n");
}

export function fixture(): string {
  const repo = makeFixtureRepo();
  const mk = (ep: string, files: Record<string, string>) => {
    const abs = join(repo, "data/episodes", ep);
    mkdirSync(join(abs, "03-audio"), { recursive: true });
    for (const [rel, text] of Object.entries(files)) writeFileSync(join(abs, rel), text);
    return abs;
  };
  const topic = "---\n番: 测试番\n类型: 人物志\n时长目标: 5-6分钟\n---\n# 选题\n";
  mk(EP025, { "01-topic.md": topic, "02-script.draft.md": scriptText(), "02-script.md": scriptText() });
  const ep35 = mk(EP035, { "01-topic.md": topic, "02-script.draft.md": scriptText(), "02-script.md": scriptText() });
  writeFileSync(join(ep35, "02-diff.patch"), "");
  const segs = PARAS.map((_, i) => ({ index: i + 1, label: String(i + 1), file: `seg-${String(i + 1).padStart(2, "0")}.wav`, duration: 3 + i }));
  writeFileSync(join(ep35, "03-audio/manifest.json"), JSON.stringify({ engine: "qwen3_tts", segments: segs }));
  for (const s of segs) ff("-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono", "-t", String(s.duration), join(ep35, "03-audio", s.file));
  return repo;
}

export async function openEp(page: Page, ep: string): Promise<void> {
  await page.locator("[data-testid=episode]", { hasText: ep }).first().click();
  await expect(page.getByTestId("center")).toHaveAttribute("data-ep", ep);
  await expect(page.getByTestId("center")).toHaveAttribute("data-loading", "0", { timeout: 15_000 });
}

export async function shoot(page: Page, name: string): Promise<void> {
  mkdirSync(OUT, { recursive: true });
  for (const [w, h] of SIZES) {
    await page.setViewportSize({ width: w, height: h });
    await page.mouse.move(0, 0);
    await page.waitForTimeout(500);
    await page.screenshot({ path: join(OUT, `${name}-${w}x${h}.png`) });
  }
}
