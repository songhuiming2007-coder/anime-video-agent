// Spec 10 §3.3 / §2.4 第 5 层：main 侧的原生确认框调度。串行（同一时刻至多一个，窗口模态）；
// host 退出时以 AbortSignal 撤下已打开的确认框、丢弃排队中的全部，不向任何 host 回 confirm-result（二轮 🔵-5）。
// 不 import electron：真实对话框由调用方（main/index.ts）注入，便于单测（TH-22）。

/** 原生确认框选项的最小结构（不 import electron；main/index.ts 原样交给 dialog.showMessageBox） */
export interface NativeBoxOptions {
  type: "question" | "warning";
  title?: string;
  message: string;
  detail: string;
  buttons: string[];
  defaultId: number;
  cancelId: number;
  signal?: AbortSignal;
}

// D37（2026-10-06 人手定性，人裁决方案 (b)）：两处确认框的按钮、defaultId、cancelId 钉死在这里。
// 实测语义：Return 不触发任何按钮、Esc = 取消、只有鼠标点第 0 个按钮（批准/退出）才放行。
// 「放行」按钮永远不是 defaultId / cancelId——改这里须同步 Spec 10 §2.4 第 5 层与 §2.10。
export const APPROVE_BUTTON = 0;

/** §2.4 第 5 层：browser / 抓取卡批准前的确认框 */
export function approveBoxOptions(title: string, detail: string, signal: AbortSignal): NativeBoxOptions {
  return { type: "question", title, message: title, detail, buttons: ["批准", "取消"], defaultId: 1, cancelId: 1, signal };
}

/** §2.10 第 4 步：有忙会话时的退出确认框 */
export function quitBoxOptions(lists: string[]): NativeBoxOptions {
  return {
    type: "warning",
    message: "有会话在运行，仍然退出？",
    detail: `${lists.join("\n")}\n\n正在运行的渲染等作业会被中断；回合不会再做收尾总结。`,
    buttons: ["退出", "取消"],
    defaultId: 1,
    cancelId: 1,
  };
}

export interface MainConfirmBroker {
  request(reqId: string, title: string, detail: string): void;
  /** host 退出：撤下已打开的一个、丢弃排队中的全部 */
  hostExited(): void;
  /**
   * D40：主窗口已关闭或被销毁（`closed`）。挂在该窗口上的确认框在 destroy 时永不 resolve，
   * 所以这里主动撤下，并对已打开的一个与排队中的全部回 ok:false——卡片回到打开状态，人可在新窗口重答。
   */
  windowGone(): void;
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
  let activeReqId: string | null = null;

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
    activeReqId = item.reqId;
    void deps.show(item.title, item.detail, ac.signal).then(
      (ok) => {
        if (active !== ac) return; // 已被 hostExited / windowGone 撤下：迟到的结果一律不回
        active = null;
        activeReqId = null;
        deps.result(item.reqId, ok);
        drain();
      },
      () => {
        if (active !== ac) return;
        active = null;
        activeReqId = null;
        deps.result(item.reqId, false); // D37-B：对话框异常 = 取消；不回复会让 host 的 await confirm 永久挂起（同 D40）
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
      activeReqId = null;
    },
    windowGone() {
      const pending = [...(activeReqId !== null ? [activeReqId] : []), ...queue.map((q) => q.reqId)];
      queue.length = 0;
      active?.abort();
      active = null;
      activeReqId = null;
      for (const reqId of pending) deps.result(reqId, false);
    },
  };
}
