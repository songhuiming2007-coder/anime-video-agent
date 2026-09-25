// host ↔ main 仅有的生命周期消息（Spec 8 §2.2）：port、renderer-reset、reach、host-ready、shutdown。
// init / data-root 是 fork 后的一次性配置与 repoRoot 生效通知，不承载业务。
// repo-root-dialog / repo-root-chosen：repoRoot 只在 main 里选（§2.10，红队 R2-M5）——host 收到 renderer 的无参请求、
// 先查在途互斥，再请 main 弹原生对话框并二次确认；renderer 从头到尾接触不到路径。
// test-*：仅未打包构建（TG-6）——测试驱动经 main 布置 host 侧钩子（§4.3 testHook）。
import type { Reach } from "./contracts";

export type MainToHost =
  | { type: "init"; userData: string; isPackaged: boolean; appPath: string; devRepoRoot: string | null }
  | { type: "port" }
  | { type: "renderer-reset" }
  | { type: "shutdown" }
  | { type: "repo-root-chosen"; reqId: number; repoRoot: string | null }
  | { type: "test-arm"; name: string }
  | { type: "test-release"; name: string };

export type HostToMain =
  | { type: "host-ready"; losslessJson: boolean; dataRoot: string | null; provenance: string }
  | { type: "reach"; reach: Reach }
  | { type: "data-root"; dataRoot: string | null }
  | { type: "repo-root-dialog"; reqId: number }
  | { type: "test-hook-hit"; name: string };
