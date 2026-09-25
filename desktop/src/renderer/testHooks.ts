// 测试钩子（仅未打包构建；TG-6）：TI-2 读出当前连接采纳的握手号、经当前端口发一次请求；
// TI-8 经当前端口发任意方法与参数（验证 host 的 exact-keys 与无路径参数，不经任何组件）。
import type { Method } from "../shared/protocol";
import type { RpcClient } from "./rpc";

type HookWindow = Window & {
  __avaTestHandshake?: () => number;
  __avaTestHealth?: () => Promise<unknown>;
  __avaTestCall?: (method: Method, params?: Record<string, unknown>) => Promise<unknown>;
};

export function installTestHooks(isPackaged: boolean, rpc: RpcClient): void {
  if (isPackaged) return;
  const w = window as HookWindow;
  w.__avaTestHandshake = () => rpc.handshake;
  w.__avaTestHealth = () => rpc.call("app.health");
  w.__avaTestCall = (method, params) => rpc.call(method, params);
}
