// TV-4 / TV-5 / TV-10：convStore 分桶、convSummary 徽标、reset-convs（Spec 10 §3.6、§2.6）。
import { describe, expect, it } from "vitest";
import { convSummary } from "../../src/shared/convFold";
import type { OutFrame } from "../../src/shared/convFrames";
import type { ConvDelta, ConvEntry, ConvKey, ConvSnapshot } from "../../src/shared/protocol";
import { emptyConvStore, foldOf, reduceConvs, type ConvStore } from "../../src/renderer/convStore";

function frame(id: string): OutFrame {
  return {
    v: 1, t: "request", seq: 1, sid: "s1", request_id: id, kind: "tool_call", turn_id: "t1", title: "t", card_text: "c",
    fields: { tool: "write_episode_file" }, options: ["approve", "reject"], feedback_allowed: true,
  };
}

function snap(key: ConvKey, generation: string, open: OutFrame[] = []): ConvSnapshot {
  return { convKey: key, generation, seq: 0, phase: "idle", turnId: null, entries: [], truncatedHead: false, open, framesLost: 0, settlePending: null, keyProblem: null };
}

function delta(key: ConvKey, generation: string, seq: number, entries: ConvEntry[] = [], open: OutFrame[] = []): ConvDelta {
  return { convKey: key, generation, seq, entries, phase: "idle", turnId: null, open, keyProblem: null };
}

const apply = (store: ConvStore, ...actions: Parameters<typeof reduceConvs>[1][]): { store: ConvStore; resnapshots: (ConvKey | null)[] } => {
  const resnapshots: (ConvKey | null)[] = [];
  let s = store;
  for (const a of actions) {
    const r = reduceConvs(s, a);
    s = r.store;
    resnapshots.push(r.resnapshot);
  }
  return { store: s, resnapshots };
};

describe("TV-4 多会话分桶与 generation / seq", () => {
  it("各桶的 open 与行互不包含对方的 request_id", () => {
    const { store } = apply(
      emptyConvStore,
      { type: "snapshot", snap: snap("ep:A", "b1:1", [frame("reqA")]) },
      { type: "snapshot", snap: snap("ep:B", "b1:2", [frame("reqB")]) },
      { type: "snapshot", snap: snap("idea", "b1:3", []) },
      { type: "delta", delta: delta("ep:A", "b1:1", 1, [{ k: "user", at: 1, text: "A 的消息" }], [frame("reqA")]) },
      { type: "delta", delta: delta("ep:B", "b1:2", 1, [{ k: "user", at: 2, text: "B 的消息" }], [frame("reqB")]) },
    );
    expect(store.convs["ep:A"].open.map((o) => o.request_id)).toEqual(["reqA"]);
    expect(store.convs["ep:B"].open.map((o) => o.request_id)).toEqual(["reqB"]);
    expect(foldOf(store.convs["ep:A"]).rows.some((r) => r.k === "user" && r.text === "B 的消息")).toBe(false);
    expect(foldOf(store.convs["ep:B"]).rows.some((r) => r.k === "user" && r.text === "A 的消息")).toBe(false);
  });

  it("generation 不同的 delta 被丢弃；seq 跳号请求 snapshot", () => {
    const { store, resnapshots } = apply(
      emptyConvStore,
      { type: "snapshot", snap: snap("ep:A", "b1:1") },
      { type: "delta", delta: delta("ep:A", "b1:0", 1) }, // 旧 generation
      { type: "delta", delta: delta("ep:A", "b1:1", 3) }, // 跳号（应为 1）
    );
    expect(store.convs["ep:A"].seq).toBe(0);
    expect(resnapshots).toEqual([null, null, "ep:A"]);
  });
});

describe("TV-5 convSummary 徽标", () => {
  it("phase / open 各组合", () => {
    expect(convSummary({ phase: "none", open: [] })).toEqual({ live: false, running: false, openRequests: 0 });
    expect(convSummary({ phase: "exited", open: [frame("a")] })).toEqual({ live: false, running: false, openRequests: 1 });
    expect(convSummary({ phase: "starting", open: [] }).live).toBe(true);
    expect(convSummary({ phase: "idle", open: [frame("a"), frame("b")] })).toEqual({ live: true, running: false, openRequests: 2 });
    expect(convSummary({ phase: "running", open: [] })).toEqual({ live: true, running: true, openRequests: 0 });
    expect(convSummary({ phase: "ending", open: [] }).live).toBe(true);
  });
});

describe("TV-10 reset-convs（onConnect）", () => {
  it("清桶后旧 generation 的 delta 被丢弃并请求 snapshot；旧 open 不再出现", () => {
    const { store } = apply(emptyConvStore, { type: "snapshot", snap: snap("ep:A", "boot1:3", [frame("old")]) });
    const afterDelta = reduceConvs(store, { type: "delta", delta: delta("ep:A", "boot1:3", 1) });
    expect(afterDelta.store.convs["ep:A"].seq).toBe(1);

    const reset = reduceConvs(afterDelta.store, { type: "reset-convs" });
    expect(Object.keys(reset.store.convs)).toEqual([]); // 旧桶（含旧 open）不再出现

    // onConnect 后新 host 的 snapshot（generation 带新 bootId）
    const fresh = reduceConvs(reset.store, { type: "snapshot", snap: snap("ep:A", "boot2:3") });
    // 投喂 boot2:3、seq 6 的 delta：跳号 → 丢弃并请求 snapshot，不拼到旧对话上
    const r = reduceConvs(fresh.store, { type: "delta", delta: delta("ep:A", "boot2:3", 6, [{ k: "user", at: 9, text: "不该进桶" }]) });
    expect(r.resnapshot).toBe("ep:A");
    expect(r.store.convs["ep:A"].seq).toBe(0);
    expect(r.store.convs["ep:A"].entries).toEqual([]);
  });
});

describe("流式 assistant_delta 与并发工具折行（convFold）", () => {
  it("assistant_delta 逐步拼接，最终 assistant 帧原地替换不重复成行", () => {
    const f = (seq: number, t: OutFrame["t"], extra: Record<string, unknown>): ConvEntry => ({
      k: "frame",
      at: seq * 10,
      frame: { v: 1, seq, sid: "s1", t, ...extra } as OutFrame,
    });
    const { store } = apply(
      emptyConvStore,
      { type: "snapshot", snap: snap("ep:A", "b1:1") },
      {
        type: "delta",
        delta: delta("ep:A", "b1:1", 1, [
          f(1, "turn_started", { turn_id: "t1" }),
          f(2, "assistant_delta", { turn_id: "t1", text: "你好，" }),
          f(3, "assistant_delta", { turn_id: "t1", text: "世界" }),
        ]),
      },
    );
    const mid = foldOf(store.convs["ep:A"]).rows.filter((r) => r.k === "assistant");
    expect(mid).toHaveLength(1);
    expect(mid[0]).toMatchObject({ k: "assistant", text: "你好，世界", streaming: true });

    const { store: s2 } = apply(store, {
      type: "delta",
      delta: delta("ep:A", "b1:1", 2, [f(4, "assistant", { turn_id: "t1", kind: "answer", text: "你好，世界！" })]),
    });
    const fin = foldOf(s2.convs["ep:A"]).rows.filter((r) => r.k === "assistant");
    expect(fin).toHaveLength(1);
    expect(fin[0]).toMatchObject({ k: "assistant", text: "你好，世界！" });
    expect((fin[0] as { streaming?: boolean }).streaming).toBeUndefined();
  });

  it("并发多工具乱序结束时按 turn_id:index 精确匹配各自工具行", () => {
    const f = (seq: number, at: number, t: OutFrame["t"], extra: Record<string, unknown>): ConvEntry => ({
      k: "frame",
      at,
      frame: { v: 1, seq, sid: "s1", t, ...extra } as OutFrame,
    });
    const { store } = apply(
      emptyConvStore,
      { type: "snapshot", snap: snap("ep:A", "b1:1") },
      {
        type: "delta",
        delta: delta("ep:A", "b1:1", 1, [
          f(1, 100, "turn_started", { turn_id: "t1" }),
          f(2, 110, "tool", { turn_id: "t1", phase: "start", index: 0, name: "web_fetch", summary: "u0", ok: null, observation: null, duplicate: false }),
          f(3, 115, "tool", { turn_id: "t1", phase: "start", index: 1, name: "web_fetch", summary: "u1", ok: null, observation: null, duplicate: false }),
          // index 0 先于 index 1 结束（乱序于 lastTool.idx=1）
          f(4, 210, "tool", { turn_id: "t1", phase: "end", index: 0, name: "web_fetch", summary: "u0", ok: true, observation: "ok0", duplicate: false }),
          f(5, 315, "tool", { turn_id: "t1", phase: "end", index: 1, name: "web_fetch", summary: "u1", ok: false, observation: "err1", duplicate: false }),
        ]),
      },
    );
    const tools = foldOf(store.convs["ep:A"]).rows.filter((r) => r.k === "tool");
    expect(tools).toHaveLength(2);
    expect(tools[0]).toMatchObject({ id: "t1:0", ok: true, observation: "ok0", durationMs: 100 });
    expect(tools[1]).toMatchObject({ id: "t1:1", ok: false, observation: "err1", durationMs: 200 });
  });
});
