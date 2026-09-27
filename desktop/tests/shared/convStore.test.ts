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
