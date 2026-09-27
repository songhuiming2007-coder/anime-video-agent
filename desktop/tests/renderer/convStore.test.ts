import { describe, expect, it } from "vitest";
import { emptyConvStore, reduceConvs } from "../../src/renderer/convStore";
import type { OutFrame } from "../../src/shared/convFrames";
import type { ConvSnapshot } from "../../src/shared/protocol";

const card = (id: string): OutFrame => ({ v: 1, t: "request", seq: 1, sid: null, request_id: id });

const snap = (open: OutFrame[]): ConvSnapshot => ({
  convKey: "ep:A",
  generation: "boot:1",
  seq: 3,
  phase: "running",
  turnId: "t1",
  entries: [],
  truncatedHead: false,
  open,
  framesLost: 0,
  settlePending: null,
  keyProblem: null,
});

describe("待答区跟随 delta 的 open（D36）", () => {
  it("delta 里 open 收缩 → 本地 open 立即少一张卡（整体替换，不做本地合并）", () => {
    let store = reduceConvs(emptyConvStore, { type: "snapshot", snap: snap([card("q6")]) }).store;
    const r = reduceConvs(store, {
      type: "delta",
      delta: { convKey: "ep:A", generation: "boot:1", seq: 4, entries: [], phase: "running", turnId: "t1", open: [card("q7")], keyProblem: null },
    });
    expect(r.resnapshot).toBeNull();
    store = r.store;
    expect(store.convs["ep:A"].open.map((o) => o.request_id)).toEqual(["q7"]);
  });
});
