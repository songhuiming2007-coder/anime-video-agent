// 测试钩子（仅未打包构建；TG-6）：TI-2 读出当前连接采纳的握手号、经当前端口发一次请求；
// TI-8 经当前端口发任意方法与参数（验证 host 的 exact-keys 与无路径参数，不经任何组件）。
import type { Method } from "../shared/protocol";
import type { RpcClient } from "./rpc";

type HookWindow = Window & {
  __avaTestHandshake?: () => number;
  __avaTestHealth?: () => Promise<unknown>;
  __avaTestCall?: (method: Method, params?: Record<string, unknown>) => Promise<unknown>;
  /** D73：置真后预览区下一次渲染抛错（验证错误边界兜住、不整窗白屏） */
  __avaTestCrashPreview?: boolean;
};

export function installTestHooks(isPackaged: boolean, rpc: RpcClient): void {
  if (isPackaged) return;
  const w = window as HookWindow;
  w.__avaTestHandshake = () => rpc.handshake;
  w.__avaTestHealth = () => rpc.call("app.health");
  w.__avaTestCall = (method, params) => rpc.call(method, params);
}

/** D73：放在预览区错误边界之内的探针（仅未打包构建；TG-6）。健康信息未到时按打包版处理。 */
export function CrashProbe({ isPackaged }: { isPackaged: boolean }): null {
  if (isPackaged) return null;
  if ((window as HookWindow).__avaTestCrashPreview === true) throw new Error("测试钩子触发的渲染异常");
  return null;
}
