// TH-22：确认框不串号（Spec 10 §3.3、二轮 🔵-5）——host 侧 reqId 与 main 侧的 AbortSignal。
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { APPROVE_BUTTON, approveBoxOptions, createMainConfirmBroker, quitBoxOptions } from "../../src/main/confirm";
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

// D37-B（2026-10-06 人手定性 + 人裁决方案 (b)）：Return 不触发任何按钮、Esc = 取消、只有鼠标点「批准/退出」才放行。
// 本层钉住传给 showMessageBox 的按钮与 defaultId / cancelId：放行按钮永远不在默认位或取消位。
describe("TH-22b 两处原生确认框的按钮语义（Spec 10 §2.4 第 5 层、§2.10）", () => {
  it("抓取 / browser 确认框：批准在 0、默认与取消都是「取消」，signal 原样带上", () => {
    const ac = new AbortController();
    const o = approveBoxOptions("批准抓取素材？", "URL：http://x", ac.signal);
    expect(o.buttons).toEqual(["批准", "取消"]);
    expect(APPROVE_BUTTON).toBe(0);
    expect(o.buttons[APPROVE_BUTTON]).toBe("批准");
    expect(o.defaultId).toBe(1);
    expect(o.cancelId).toBe(1);
    expect(o.signal).toBe(ac.signal);
    expect(o).toMatchObject({ title: "批准抓取素材？", message: "批准抓取素材？", detail: "URL：http://x" });
  });

  it("退出确认框：退出在 0、默认与取消都是「取消」，正文列出忙会话", () => {
    const o = quitBoxOptions(["D37-TEST · 运行中 · 1 张卡未答"]);
    expect(o.buttons).toEqual(["退出", "取消"]);
    expect(o.buttons[APPROVE_BUTTON]).toBe("退出");
    expect(o.defaultId).toBe(1);
    expect(o.cancelId).toBe(1);
    expect(o.detail.startsWith("D37-TEST · 运行中 · 1 张卡未答\n\n")).toBe(true);
  });

  it("main/index.ts 两处都经这两个函数取选项、按 APPROVE_BUTTON 判放行，不再内联按钮表；窗口 closed 时调 windowGone", () => {
    const src = readFileSync(join(__dirname, "../../src/main/index.ts"), "utf8");
    expect(src).toContain("approveBoxOptions(title, detail, signal)");
    expect(src).toContain("quitBoxOptions(lists)");
    expect(src).not.toMatch(/buttons:\s*\[\s*"(批准|退出)"/);
    expect(src.match(/r\.response === APPROVE_BUTTON/g)).toHaveLength(2);
    // 只认真正执行的语句：行首就是调用（注释掉的 `// mainConfirm.windowGone()` 不算，MUT M5）
    expect(src).toMatch(/win\.on\("closed", \(\) => \{\n\s*win = null;\n\s*mainConfirm\.windowGone\(\);/);
  });
});

describe("TH-22c 非人手路径一律 ok:false（D37-B / D40）", () => {
  function harness() {
    const signals: AbortSignal[] = [];
    const resolvers: ((ok: boolean) => void)[] = [];
    const rejecters: ((e: Error) => void)[] = [];
    const results: [string, boolean][] = [];
    const broker = createMainConfirmBroker({
      show: (_t, _d, signal) => {
        signals.push(signal);
        return new Promise<boolean>((res, rej) => {
          resolvers.push(res);
          rejecters.push(rej);
        });
      },
      result: (reqId, ok) => results.push([reqId, ok]),
    });
    return { broker, signals, resolvers, rejecters, results };
  }
  const tick = () => new Promise((r) => setTimeout(r, 0));

  it("对话框被取消 / abort 收尾（show 回 false）→ ok:false，下一张接着弹", async () => {
    const h = harness();
    h.broker.request("boot1:1", "t1", "d1");
    h.broker.request("boot1:2", "t2", "d2");
    h.resolvers[0](false);
    await tick();
    expect(h.results).toEqual([["boot1:1", false]]);
    expect(h.signals).toHaveLength(2);
  });

  it("对话框抛异常 → ok:false（不回复会让 host 的 await confirm 永久挂起），下一张接着弹", async () => {
    const h = harness();
    h.broker.request("boot1:1", "t1", "d1");
    h.broker.request("boot1:2", "t2", "d2");
    h.rejecters[0](new Error("boom"));
    await tick();
    expect(h.results).toEqual([["boot1:1", false]]);
    expect(h.signals).toHaveLength(2);
  });

  it("窗口被销毁（windowGone）→ 已打开的一个被 abort、它与排队中的全部各回一次 ok:false；迟到的 true 不回", async () => {
    const h = harness();
    h.broker.request("boot1:1", "t1", "d1");
    h.broker.request("boot1:2", "t2", "d2");
    h.broker.windowGone();
    expect(h.signals[0].aborted).toBe(true);
    expect(h.results).toEqual([["boot1:1", false], ["boot1:2", false]]);
    h.resolvers[0](true);
    await tick();
    expect(h.results).toEqual([["boot1:1", false], ["boot1:2", false]]);
    expect(h.signals).toHaveLength(1); // 排队的那张没有再弹
  });

  it("windowGone 之后新的请求照常弹框、人点批准才回 true（能重答）", async () => {
    const h = harness();
    h.broker.request("boot1:1", "t1", "d1");
    h.broker.windowGone();
    h.broker.request("boot1:3", "t3", "d3");
    expect(h.signals).toHaveLength(2);
    h.resolvers[1](true);
    await tick();
    expect(h.results).toEqual([["boot1:1", false], ["boot1:3", true]]);
  });

  it("空闲时 windowGone 不回任何东西", () => {
    const h = harness();
    h.broker.windowGone();
    expect(h.results).toEqual([]);
  });
});
