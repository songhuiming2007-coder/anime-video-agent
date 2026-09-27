// Spec 10 §3.3 / §2.4 第 5 层：main 侧的原生确认框调度。串行（同一时刻至多一个，窗口模态）；
// host 退出时以 AbortSignal 撤下已打开的确认框、丢弃排队中的全部，不向任何 host 回 confirm-result（二轮 🔵-5）。
// 不 import electron：真实对话框由调用方（main/index.ts）注入，便于单测（TH-22）。

export interface MainConfirmBroker {
  request(reqId: string, title: string, detail: string): void;
  /** host 退出：撤下已打开的一个、丢弃排队中的全部 */
  hostExited(): void;
}

export interface ConfirmStub {
  calls: number;
  respond: boolean;
  last: { title: string; detail: string } | null;
}

export function createMainConfirmBroker(deps: {
  show: (title: string, detail: string, signal: AbortSignal) => Promise<boolean>;
  result: (reqId: string, ok: boolean) => void;
  /** 仅未打包构建的测试桩；由 main/index.ts 在 !isPackaged 守卫下注入 */
  stub?: () => ConfirmStub | undefined;
}): MainConfirmBroker {
  const queue: { reqId: string; title: string; detail: string }[] = [];
  let active: AbortController | null = null;

  const drain = (): void => {
    if (active || queue.length === 0) return;
    const item = queue.shift()!;
    const stub = deps.stub?.();
    if (stub) {
      stub.calls += 1;
      stub.last = { title: item.title, detail: item.detail };
      deps.result(item.reqId, stub.respond);
      drain();
      return;
    }
    const ac = new AbortController();
    active = ac;
    void deps.show(item.title, item.detail, ac.signal).then(
      (ok) => {
        if (active !== ac) return; // 已被 hostExited 撤下：不回复
        active = null;
        deps.result(item.reqId, ok);
        drain();
      },
      () => {
        if (active !== ac) return;
        active = null;
        drain();
      },
    );
  };

  return {
    request(reqId, title, detail) {
      queue.push({ reqId, title, detail });
      drain();
    },
    hostExited() {
      queue.length = 0;
      active?.abort();
      active = null;
    },
  };
}
