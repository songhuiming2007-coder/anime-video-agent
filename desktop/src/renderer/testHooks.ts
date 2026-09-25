// 测试钩子（仅未打包构建；TG-6）：TI-2 读出当前连接采纳的握手号、经当前端口发一次请求。
import type { RpcClient } from "./rpc";

type HookWindow = Window & {
  __avaTestHandshake?: () => number;
  __avaTestHealth?: () => Promise<unknown>;
};

export function installTestHooks(isPackaged: boolean, rpc: RpcClient): void {
  if (isPackaged) return;
  const w = window as HookWindow;
  w.__avaTestHandshake = () => rpc.handshake;
  w.__avaTestHealth = () => rpc.call("app.health");
}
