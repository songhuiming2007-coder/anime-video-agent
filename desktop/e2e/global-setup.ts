import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import { realEventsState } from "../tests/realData";

// e2e 驱动的是 out/ 下的构建产物：每次先构建，避免测到旧产物
// 同时记下真实 data/_events.jsonl 的状态，global-teardown.ts 收尾比对（§7.1 真实数据零污染）
export default function globalSetup(): void {
  process.env.AVA_E2E_REAL_EVENTS_BASELINE = realEventsState();
  execFileSync(resolve(__dirname, "../node_modules/.bin/electron-vite"), ["build"], { cwd: resolve(__dirname, ".."), stdio: "inherit" });
}
