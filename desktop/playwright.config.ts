import { defineConfig } from "@playwright/test";

// 功能 e2e 只跑未打包构建（§7.1：打包版的 inspector 被 fuse 关掉，Playwright 驱动不了）
export default defineConfig({
  testDir: "e2e",
  outputDir: "out/playwright-results", // 构建产物目录已被 .gitignore 排除
  globalSetup: "./e2e/global-setup.ts",
  globalTeardown: "./e2e/global-teardown.ts",
  timeout: 90_000,
  // N43（2026-09-28 拍板试点）：各用例已有独立临时 repo/userData（mkdtemp）、无共享端口
  // （媒体走自定义 scheme）；globalSetup/Teardown 是进程级钩子，多 worker 下仍只跑一次。
  // 并行 = 自加负载，时序竞态（D31/D32/N37）暴露面变大：负载下全量 ×3 全绿才允许保留。
  workers: 2,
  reporter: [["list"]],
});
