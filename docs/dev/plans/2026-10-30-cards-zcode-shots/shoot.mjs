import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { chromium } from "../../../../desktop/node_modules/playwright/index.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const repo = join(here, "../../../..");
const html = pathToFileURL(join(here, "mock.html")).href;
const out = join(repo, "desktop/out/d152-card-shots");

const SCHEMES = [
  ["now", "现状"],
  ["A", "A 分层卡（照 ZCode 信息分层）"],
  ["B", "B 行+展开（照 PermissionDialog）"],
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
  `<!doctype html><meta charset="utf-8"><title>停机卡 / 工具审批卡选型</title>
<style>
  body { font: 14px/1.45 -apple-system, sans-serif; margin: 24px; background: #f7f7f8; color: #1d1d1f; }
  h1 { font-size: 18px; }
  .grid { display: grid; gap: 24px; }
  figure { margin: 0; background: #fff; border: 1px solid rgba(13,13,15,.1); border-radius: 12px; overflow: hidden; }
  img { display: block; width: 100%; }
  figcaption { padding: 8px 12px; font-weight: 600; }
</style>
<h1>停机卡 / 工具审批卡 · ZCode 对齐选型</h1>
<p>建议 A：卡还在，框线原文 / ISO / mtime_ns 退下，命令用代码块。B 改成工具行，命令默认被挤成一行。批准/打回同权，三套都不改。</p>
<div class="grid">${SCHEMES.map(([id, label]) => `<figure><img src="${id}-1440x900.png" alt="${label}"><figcaption>${label}</figcaption></figure>`).join("\n")}</div>
`,
);
await browser.close();
console.log(`wrote ${out}`);
