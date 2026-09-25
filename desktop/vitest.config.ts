import { defineConfig } from "vitest/config";

// 打包版验证（e2e-packaged/，§7.1 TS 系列）只在显式点名时跑：它依赖 release-build 的产物、会拉起 .app，
// 平常的 `npx vitest run` 不该碰它。`npx vitest run e2e-packaged` 时 include 切到打包版用例。
const packaged = process.argv.some((a) => a.includes("e2e-packaged"));

export default defineConfig({
  test: {
    include: packaged ? ["e2e-packaged/**/*.test.ts"] : ["tests/**/*.test.ts", "tests/**/*.test.tsx"],
    environment: "node",
    testTimeout: packaged ? 120_000 : 30_000,
    // 打包版是单实例 app：用例必须串行
    fileParallelism: !packaged,
    // 真实数据零污染（§7.1）：开跑前记状态，每个测试文件收尾时比对
    globalSetup: ["tests/realData.globalSetup.ts"],
    setupFiles: ["tests/realData.setup.ts"],
  },
});
