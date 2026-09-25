import { defineConfig } from "@playwright/test";

// 功能 e2e 只跑未打包构建（§7.1：打包版的 inspector 被 fuse 关掉，Playwright 驱动不了）
export default defineConfig({
  testDir: "e2e",
  outputDir: "out/playwright-results", // 构建产物目录已被 .gitignore 排除
  globalSetup: "./e2e/global-setup.ts",
  globalTeardown: "./e2e/global-teardown.ts",
  timeout: 90_000,
  workers: 1,
  reporter: [["list"]],
});
