// S22（用户选 A）：RpcClient 不设超时；当前端口 close 即拒绝挂起请求（E_UNREACHABLE），断开期间新请求立即拒绝；
// 每个握手号至多采纳一次；换端口时关掉的旧端口不误伤新连接。用 Node 的 MessageChannel 驱动（真实 close 事件）。
import { MessageChannel, type MessagePort as NodePort } from "node:worker_threads";
import { describe, expect, it } from "vitest";
import { RpcClient, RpcFailure } from "../../src/renderer/rpc";
import type { Envelope } from "../../src/shared/protocol";

function harness() {
  let listener: ((e: unknown) => void) | null = null;
  const win = { addEventListener: (_t: string, fn: (e: unknown) => void) => (listener = fn) } as unknown as Window;
  const rpc = new RpcClient(win);
  /** 模拟 main 撮合：host 端应答 app.health（answer=false 时收到请求不回，制造挂起） */
  const handshake = (n: number, answer = true, source: unknown = win) => {
    const ch = new MessageChannel();
    const host = ch.port2 as NodePort;
    host.on("message", (env: Envelope) => {
      if (answer && env.kind === "req") host.postMessage({ v: 1, kind: "res", id: env.id, ok: true, result: { handshake: n } } satisfies Envelope);
    });
    listener!({ source, data: { type: "ava-port", handshake: n }, ports: [ch.port1] });
    return host;
  };
  const code = (p: Promise<unknown>) => p.then(() => "resolved", (e: unknown) => (e instanceof RpcFailure ? e.error.code : String(e)));
  return { rpc, handshake, code, win };
}

describe("RpcClient 端口生命周期", () => {
  it("挂起请求在 host 端口关闭时以 E_UNREACHABLE 拒绝；断开期间新请求立即拒绝；新握手后恢复", async () => {
    const { rpc, handshake, code } = harness();
    const host1 = handshake(1, false);
    const pending = code(rpc.call("app.health"));
    await new Promise((r) => setTimeout(r, 50));
    host1.close();
    expect(await pending).toBe("E_UNREACHABLE");
    expect(await code(rpc.call("app.health"))).toBe("E_UNREACHABLE");
    handshake(2);
    await expect(rpc.call("app.health")).resolves.toEqual({ handshake: 2 });
  });
  it("每个握手号至多采纳一次；更小或相同的号、非本窗口来源一律忽略；换端口关掉的旧端口不误伤新连接", async () => {
    const { rpc, handshake, win } = harness();
    handshake(3);
    await expect(rpc.call("app.health")).resolves.toEqual({ handshake: 3 });
    handshake(3);
    handshake(2);
    handshake(9, true, { notTheWindow: true } as unknown as Window);
    await expect(rpc.call("app.health")).resolves.toEqual({ handshake: 3 });
    expect(rpc.handshake).toBe(3);
    handshake(4);
    await new Promise((r) => setTimeout(r, 50)); // 旧端口的 close 事件已到达
    await expect(rpc.call("app.health")).resolves.toEqual({ handshake: 4 });
    expect(win).toBeDefined();
  });
  it("首次连接之前的请求等待连接，不被拒绝", async () => {
    const { rpc, handshake } = harness();
    const early = rpc.call("app.health");
    handshake(1);
    await expect(early).resolves.toEqual({ handshake: 1 });
  });
});
