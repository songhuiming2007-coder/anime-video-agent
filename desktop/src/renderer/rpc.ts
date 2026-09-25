// renderer 端 RPC：经 preload 转交的 MessagePort 直连 host（main 只撮合不转发，§2.2）。
// 端口接收两条独立守卫（§4.4，TI-2）：只接受 event.source === window 的消息；每次撮合只接受一次
// （握手号严格递增才采纳，同一个号至多采纳一次）。
// iframe（含 allow-scripts 的 gallery）向父窗口 postMessage 的同名消息一律忽略。
// RPC 不设超时（S22，经用户同意）：当前端口 close（host 关端口或进程死亡）即把挂起请求以 E_UNREACHABLE 拒绝，
// 断开期间的新请求立即拒绝、不挂起；首次连接前的请求照常等待连接。
import { PROTOCOL_VERSION, type Envelope, type Method, type PushTopic, type RpcError } from "../shared/protocol";

type PushEnvelope = Extract<Envelope, { kind: "push" }>;
type PushHandler = (p: PushEnvelope) => void;

/** 端口断开时 renderer 自己合成的 E_UNREACHABLE 的消息前缀（区别于 host 报告的数据不可达） */
export const HOST_LINK_LOST = "host 连接";

export class RpcFailure extends Error {
  constructor(readonly error: RpcError) {
    super(error.message);
  }
}

export class RpcClient {
  private port: MessagePort | null = null;
  private adopted = 0;
  private disconnected = false;
  private nextId = 1;
  private pending = new Map<number, { resolve: (v: unknown) => void; reject: (e: unknown) => void }>();
  private handlers = new Map<PushTopic, Set<PushHandler>>();
  private readyWaiters: (() => void)[] = [];
  private onConnectFns: (() => void)[] = [];
  private onDisconnectFns: (() => void)[] = [];

  constructor(win: Window) {
    win.addEventListener("message", (e: MessageEvent) => {
      if (e.source !== win) return; // 守卫 1：只认本窗口（preload）投递的端口
      const d = e.data as { type?: unknown; handshake?: unknown } | null;
      if (!d || d.type !== "ava-port" || typeof d.handshake !== "number" || !e.ports[0]) return;
      if (d.handshake <= this.adopted) return; // 守卫 2：每次撮合只接受一次
      this.adopted = d.handshake;
      this.attach(e.ports[0]);
    });
  }

  private rejectPending(message: string): void {
    for (const [, p] of this.pending) p.reject(new RpcFailure({ code: "E_UNREACHABLE", message }));
    this.pending.clear();
  }

  private attach(port: MessagePort): void {
    this.port?.close();
    this.port = port;
    this.disconnected = false;
    this.rejectPending(`${HOST_LINK_LOST}已重置`);
    port.onmessage = (e: MessageEvent<Envelope>) => this.onEnvelope(e.data);
    port.addEventListener("close", () => {
      if (this.port !== port) return; // 换端口时自己关掉的旧端口
      this.port = null;
      this.disconnected = true;
      this.rejectPending(`${HOST_LINK_LOST}已断开`);
      for (const f of this.onDisconnectFns) f();
    });
    port.start();
    for (const w of this.readyWaiters.splice(0)) w();
    for (const f of this.onConnectFns) f();
  }

  /** 当前连接采纳的握手号（0 = 尚未连接）；仅供未打包构建的测试钩子读取 */
  get handshake(): number {
    return this.adopted;
  }

  /** 连接（及每次重新握手）后回调；注册时已连接则立即补发一次 */
  onConnect(fn: () => void): void {
    this.onConnectFns.push(fn);
    if (this.port) fn();
  }

  /** 端口断开（host 死亡或关端口）时回调 */
  onDisconnect(fn: () => void): void {
    this.onDisconnectFns.push(fn);
  }

  get connected(): boolean {
    return this.port !== null;
  }

  ready(): Promise<void> {
    return this.port ? Promise.resolve() : new Promise((r) => this.readyWaiters.push(r));
  }

  private onEnvelope(env: Envelope): void {
    if (!env || env.v !== PROTOCOL_VERSION) return;
    if (env.kind === "res") {
      const p = this.pending.get(env.id);
      if (!p) return;
      this.pending.delete(env.id);
      if (env.ok) p.resolve(env.result);
      else p.reject(new RpcFailure(env.error));
    } else if (env.kind === "push") {
      for (const h of this.handlers.get(env.topic) ?? []) h(env);
    }
  }

  on(topic: PushTopic, h: PushHandler): () => void {
    let set = this.handlers.get(topic);
    if (!set) this.handlers.set(topic, (set = new Set()));
    set.add(h);
    return () => set!.delete(h);
  }

  async call<T>(method: Method, params?: Record<string, unknown>): Promise<T> {
    if (this.disconnected) throw new RpcFailure({ code: "E_UNREACHABLE", message: `${HOST_LINK_LOST}已断开，等待重新连接` });
    await this.ready();
    const id = this.nextId++;
    return new Promise<T>((resolve, reject) => {
      this.pending.set(id, { resolve: resolve as (v: unknown) => void, reject });
      this.port!.postMessage({ v: PROTOCOL_VERSION, kind: "req", id, method, params: params ?? {} } satisfies Envelope);
    });
  }
}
