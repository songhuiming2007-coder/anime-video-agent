import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { chromium } from "../../../../desktop/node_modules/playwright/index.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const repo = join(here, "../../../..");
const html = pathToFileURL(join(here, "mock.html")).href;
const out = join(repo, "desktop/out/d152-editor-shots");

const SCHEMES = [
  ["now", "现状"],
  ["A", "A 幽灵工具栏（照 ZCode 文件头）"],
  ["B", "B 底栏（动作沉底）"],
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

writeFileSync(
  join(out, "index.html"),
  `<!doctype html><meta charset="utf-8"><title>02.5 稿编辑器选型</title>
<style>
  body { font: 14px/1.45 -apple-system, sans-serif; margin: 24px; background: #f7f7f8; color: #1d1d1f; }
  h1 { font-size: 18px; }
  .grid { display: grid; gap: 24px; }
  figure { margin: 0; background: #fff; border: 1px solid rgba(13,13,15,.1); border-radius: 12px; overflow: hidden; }
  img { display: block; width: 100%; }
  figcaption { padding: 8px 12px; font-weight: 600; }
</style>
<h1>02.5 稿编辑器 · ZCode 对齐选型</h1>
<p>建议 A：工具栏改幽灵按钮，跟顺听面板同一套。B 把保存/封板/机检沉到底。栏位仍是 D49 的预览栏内编辑，三套都不改。</p>
<div class="grid">${SCHEMES.map(([id, label]) => `<figure><img src="${id}-1440x900.png" alt="${label}"><figcaption>${label}</figcaption></figure>`).join("\n")}</div>
`,
);
await browser.close();
console.log(`wrote ${out}`);
