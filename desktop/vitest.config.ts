import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    include: ["tests/**/*.test.ts", "tests/**/*.test.tsx"],
    environment: "node",
    testTimeout: 30_000,
    // 真实数据零污染（§7.1）：开跑前记状态，每个测试文件收尾时比对
    globalSetup: ["tests/realData.globalSetup.ts"],
    setupFiles: ["tests/realData.setup.ts"],
  },
});
