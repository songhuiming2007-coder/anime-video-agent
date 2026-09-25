// host ↔ main 仅有的生命周期消息（Spec 8 §2.2）：port、renderer-reset、reach、host-ready、shutdown。
// init / data-root 是 fork 后的一次性配置与 repoRoot 生效通知，不承载业务。
import type { Reach } from "./contracts";

export type MainToHost =
  | { type: "init"; userData: string; isPackaged: boolean; appPath: string; devRepoRoot: string | null }
  | { type: "port" }
  | { type: "renderer-reset" }
  | { type: "shutdown" };

export type HostToMain =
  | { type: "host-ready"; losslessJson: boolean; dataRoot: string | null }
  | { type: "reach"; reach: Reach }
  | { type: "data-root"; dataRoot: string | null };
