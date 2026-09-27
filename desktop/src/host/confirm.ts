// Spec 10 §3.3 / §2.4 第 5 层：host 侧的确认框请求端（main 回 confirm-result）。
// reqId = "<hostBootId>:<n>"；新 host 不认识旧 bootId 的 reqId，旧 host 的确认结果永远到不了新 host（🟡-5）。
import type { HostToMain } from "../shared/lifecycle";

export interface HostConfirmBroker {
  request(title: string, detail: string): Promise<boolean>;
  /** 未被任何在途请求认领的 reqId 一律忽略（返回 false），零副作用。 */
  resolve(reqId: string, ok: boolean): boolean;
}

export function createConfirmBroker(deps: { bootId: string; post: (m: HostToMain) => void }): HostConfirmBroker {
  let seq = 0;
  const waiters = new Map<string, (ok: boolean) => void>();
  return {
    request(title, detail) {
      seq += 1;
      const reqId = `${deps.bootId}:${seq}`;
      return new Promise<boolean>((resolve) => {
        waiters.set(reqId, resolve);
        deps.post({ type: "confirm-query", reqId, title, detail });
      });
    },
    resolve(reqId, ok) {
      const w = waiters.get(reqId);
      if (!w) return false;
      waiters.delete(reqId);
      w(ok);
      return true;
    },
  };
}
