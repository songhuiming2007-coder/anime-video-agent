// D49 / D50 附件：预览区·编辑器·03.5 顺听纠错的界面原型截图配置。用法（在 desktop/ 下）：
//   PYTHONDONTWRITEBYTECODE=1 npx playwright test -c ../docs/dev/plans/2026-10-08-preview-voice-options/options.config.ts
// 复用 desktop/e2e 的 globalSetup（先 electron-vite build）与真实数据零污染 teardown；夹具全在系统临时目录。
import { join } from "node:path";
import { defineConfig } from "../../../../desktop/node_modules/@playwright/test";

const D = join(__dirname, "../../../../desktop");
export default defineConfig({
  testDir: __dirname,
  testMatch: /(baseline|options)\.spec\.ts$/,
  outputDir: join(D, "out/d49-playwright"),
  globalSetup: join(D, "e2e/global-setup.ts"),
  globalTeardown: join(D, "e2e/global-teardown.ts"),
  timeout: 300_000,
  workers: 1,
  reporter: [["list"]],
});
