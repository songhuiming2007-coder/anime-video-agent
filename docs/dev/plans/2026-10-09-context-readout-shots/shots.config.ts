// D64 余项②：上下文读数显眼度的真实窗口截图配置。用法（在 desktop/ 下）：
//   PYTHONDONTWRITEBYTECODE=1 npx playwright test -c ../docs/dev/plans/2026-10-09-context-readout-shots/shots.config.ts
// 复用 desktop/e2e 的 globalSetup（先 electron-vite build）与真实数据零污染 teardown；夹具全在系统临时目录。
import { join } from "node:path";
import { defineConfig } from "../../../../desktop/node_modules/@playwright/test";

const D = join(__dirname, "../../../../desktop");
export default defineConfig({
  testDir: __dirname,
  testMatch: /shots\.spec\.ts$/,
  outputDir: join(D, "out/d64-playwright"),
  globalSetup: join(D, "e2e/global-setup.ts"),
  globalTeardown: join(D, "e2e/global-teardown.ts"),
  timeout: 300_000,
  workers: 1,
  reporter: [["list"]],
});
