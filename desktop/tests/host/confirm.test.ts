// TH-22：确认框不串号（Spec 10 §3.3、二轮 🔵-5）——host 侧 reqId 与 main 侧的 AbortSignal。
import { describe, expect, it } from "vitest";
import { createMainConfirmBroker } from "../../src/main/confirm";
import { createConfirmBroker } from "../../src/host/confirm";
import type { HostToMain } from "../../src/shared/lifecycle";

describe("TH-22 host 侧 reqId", () => {
  it("reqId 形如 <bootId>:<n>；未知 reqId 被忽略；同 host 的 reqId 才能兑现", async () => {
    const posted: HostToMain[] = [];
    const broker = createConfirmBroker({ bootId: "boot1", post: (m) => posted.push(m) });
    const p = broker.request("批准抓取素材？", "URL：http://x");
    expect(posted).toHaveLength(1);
    expect(posted[0]).toMatchObject({ type: "confirm-query", reqId: "boot1:1", title: "批准抓取素材？", detail: "URL：http://x" });
    // 旧 host 的 confirm-result 到不了新 host
    expect(broker.resolve("BOOT0:9", true)).toBe(false);
    expect(await Promise.race([p.then(() => "resolved"), Promise.resolve("pending")])).toBe("pending");
    expect(broker.resolve("boot1:1", false)).toBe(true);
    expect(await p).toBe(false);
  });
});

describe("TH-22 main 侧撤下与作废", () => {
  it("host 退出 → 已打开的确认框收到 AbortSignal；排队中的全部作废、零回复", async () => {
    const signals: AbortSignal[] = [];
    const results: [string, boolean][] = [];
    let resolver!: (ok: boolean) => void;
    const broker = createMainConfirmBroker({
      show: (_t, _d, signal) => {
        signals.push(signal);
        return new Promise<boolean>((r) => (resolver = r));
      },
      result: (reqId, ok) => results.push([reqId, ok]),
    });
    broker.request("boot1:1", "t1", "d1");
    broker.request("boot1:2", "t2", "d2"); // 排队
    expect(signals).toHaveLength(1);
    broker.hostExited();
    expect(signals[0].aborted).toBe(true);
    expect(results).toEqual([]); // 不向任何 host 回 confirm-result
    resolver(true); // 即便迟到的对话框结果也不回复
    await new Promise((r) => setTimeout(r, 10));
    expect(results).toEqual([]);
  });

  it("未打包构建的桩：串行调用并按桩结果回复", () => {
    const stub = { calls: 0, respond: true, last: null as null | { title: string; detail: string } };
    const results: [string, boolean][] = [];
    const broker = createMainConfirmBroker({ show: async () => true, result: (id, ok) => results.push([id, ok]), stub: () => stub });
    broker.request("boot1:1", "批准 browser 调用？", "操作：open");
    expect(stub.calls).toBe(1);
    expect(stub.last).toEqual({ title: "批准 browser 调用？", detail: "操作：open" });
    expect(results).toEqual([["boot1:1", true]]);
  });
});
