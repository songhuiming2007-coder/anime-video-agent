// 03.5 顺听面板选型截图。静态 mock + 真 token，不启 Electron、不碰 data/。
// 用法（仓库根）：node docs/dev/plans/2026-10-30-voice-panel-zcode-shots/shoot.mjs
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { chromium } from "../../../../desktop/node_modules/playwright/index.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const repo = join(here, "../../../..");
const html = pathToFileURL(join(here, "mock.html")).href;
const out = join(repo, "desktop/out/d152-voice-shots");

const SCHEMES = [
  ["now", "现状"],
  ["A", "A 行列表（照 ZCode file/task-row）"],
  ["A-fix", "A · 就地纠错卡"],
  ["B", "B 列表+详情（照 ZCode PreviewPane）"],
];
const SIZES = [
  [1440, 900],
  [1280, 800],
];

mkdirSync(out, { recursive: true });
const browser = await chromium.launch();
const page = await browser.newPage();
await page.goto(html);

for (const [id, label] of SCHEMES) {
  await page.evaluate(
    ({ id, label }) => {
      document.body.dataset.scheme = id;
      const el = document.getElementById("scheme-label");
      if (el) el.textContent = label;
    },
    { id, label },
  );
  for (const [w, h] of SIZES) {
    await page.setViewportSize({ width: w, height: h });
    await page.waitForTimeout(80);
    await page.screenshot({ path: join(out, `${id}-${w}x${h}.png`) });
  }
}

const cards = SCHEMES.map(([id, label]) => {
  const img = `${id}-1440x900.png`;
  return `<figure><img src="${img}" alt="${label}"><figcaption>${label}</figcaption></figure>`;
}).join("\n");

writeFileSync(
  join(out, "index.html"),
  `<!doctype html><meta charset="utf-8"><title>03.5 顺听面板选型</title>
<style>
  body { font: 14px/1.45 -apple-system, sans-serif; margin: 24px; background: #f7f7f8; color: #1d1d1f; }
  h1 { font-size: 18px; }
  .grid { display: grid; gap: 24px; }
  figure { margin: 0; background: #fff; border: 1px solid rgba(13,13,15,.1); border-radius: 12px; overflow: hidden; }
  img { display: block; width: 100%; }
  figcaption { padding: 8px 12px; font-weight: 600; }
</style>
<h1>03.5 顺听面板 · ZCode 对齐选型</h1>
<p>静态 mock，token 与产品同一份。建议 A（全文仍在，只换皮肤）。B 改了信息结构：段文不再一眼铺开。</p>
<div class="grid">${cards}</div>
`,
);

await browser.close();
console.log(`wrote ${out}`);
