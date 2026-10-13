// D89：05 批准的补丁段二次确认（Spec §4.4 闸 3）。此前桌面端按钮不带 --confirm-patch，core 去读终端 → EOFError。
// decide() 的指纹 / 解封物两道闸不是这里的考点，桩成通过；端到端见 e2e/ack.spec.ts。
import { describe, expect, it, vi } from "vitest";

vi.mock("../../src/host/store", () => ({ fingerprintsMatch: () => true }));
vi.mock("../../src/host/gateCheck", () => ({ gateMatches: () => true }));

import type { ApprovalRecord } from "../../src/shared/contracts";
import { decide, DecideFail, parsePatchSegs, type DecideCtx } from "../../src/host/decide";
import type { CoreResult } from "../../src/host/spawner";

const pending: ApprovalRecord = {
  approval_id: "appr_1", episode: "EP", type: "05", status: "pending",
  artifacts: [{ path: "04-clips.json", size: 285, mtime_ns: 1790171112636927676n }],
  options: ["approve", "reject"], created_at: "2026-10-13T00:00:00.000000Z",
  resolved_at: null, resolved_by: null, confirmed_by: null, confirmed_at: null, feedback: null, note: "",
};

const ok = (stdout = ""): CoreResult => ({ code: 0, signal: null, stdoutTail: stdout, stderrTail: "", timedOut: false, stdoutFull: null, stdoutOverflow: false });

function harness(opts: { patchDir: boolean; segsOut?: string; confirm?: boolean }) {
  const calls: { t: string; args: Record<string, unknown> }[] = [];
  const confirms: string[] = [];
  let state: ApprovalRecord = pending;
  const ctx: DecideCtx = {
    isPackaged: true,
    capApprovals: () => true,
    capDetail: () => "",
    episode: () => ({ epKey: "EP", abs: "/ep", mtimeMs: 0 }),
    reach: () => "ok",
    reachDetail: () => "",
    existsDir: (abs) => abs === "/ep" || (opts.patchDir && abs === "/ep/04-patch"),
    refreshEpisodes: () => undefined,
    tryAcquire: () => true,
    release: () => undefined,
    heal: async () => ({ ok: true, stderrTail: "" }),
    diag: () => undefined,
    testHook: async () => undefined,
    readStore: () => ({ state: "ok", items: [state] }) as ReturnType<DecideCtx["readStore"]>,
    fs: {} as DecideCtx["fs"],
    run: (async (t: string, args: Record<string, unknown>) => {
      calls.push({ t, args });
      if (t === "REVIEW_PATCH_SEGS") return ok(opts.segsOut ?? '{"patch_segments": []}');
      if (t === "APPROVE") state = { ...pending, status: "approved", resolved_by: "cli" };
      return ok();
    }) as DecideCtx["run"],
    confirm: async (title, detail) => {
      confirms.push(`${title}\n${detail}`);
      return opts.confirm ?? false;
    },
    resnapshot: async () => undefined,
  };
  const go = () => decide(ctx, { epKey: "EP", approvalId: "appr_1", stop: "05", decision: "approve", finalize: null }, 1);
  return { ctx, calls, confirms, go };
}

describe("D89 05 补丁段确认", () => {
  it("有补丁段、人确认 → REVIEW_APPROVE 带 confirmPatch，确认框列出段号", async () => {
    const h = harness({ patchDir: true, segsOut: '{"patch_segments": [7, 10]}', confirm: true });
    expect(await h.go()).toEqual({ approvalId: "appr_1", decision: "approve" });
    expect(h.calls.map((c) => c.t)).toEqual(["REVIEW_PATCH_SEGS", "REVIEW_APPROVE", "APPROVE"]);
    expect(h.calls[1].args.confirmPatch).toBe(true);
    expect(h.confirms).toHaveLength(1);
    expect(h.confirms[0]).toContain("2 个补丁段");
    expect(h.confirms[0]).toContain("段 7、10");
  });

  it("有补丁段、人取消 → 返回 cancelled，只跑了只读查询，零写入", async () => {
    const h = harness({ patchDir: true, segsOut: '{"patch_segments": [7]}', confirm: false });
    expect(await h.go()).toEqual({ approvalId: "appr_1", decision: "approve", cancelled: true });
    expect(h.calls.map((c) => c.t)).toEqual(["REVIEW_PATCH_SEGS"]);
  });

  it("有补丁池但这一版排片没用上补丁段 → 不弹框，不带 confirmPatch", async () => {
    const h = harness({ patchDir: true, segsOut: '{"patch_segments": []}' });
    await h.go();
    expect(h.confirms).toHaveLength(0);
    expect(h.calls.map((c) => c.t)).toEqual(["REVIEW_PATCH_SEGS", "REVIEW_APPROVE", "APPROVE"]);
    expect(h.calls[1].args.confirmPatch).toBeUndefined();
  });

  it("没有 04-patch/ → 不查不弹，spawn 序列与 D89 前一致", async () => {
    const h = harness({ patchDir: false });
    await h.go();
    expect(h.calls.map((c) => c.t)).toEqual(["REVIEW_APPROVE", "APPROVE"]);
    expect(h.calls[0].args).not.toHaveProperty("confirmPatch");
  });

  it("查询输出解析不了 → E_CORE，不批准", async () => {
    const h = harness({ patchDir: true, segsOut: "Traceback (most recent call last):" });
    await expect(h.go()).rejects.toBeInstanceOf(DecideFail);
    expect(h.calls.map((c) => c.t)).toEqual(["REVIEW_PATCH_SEGS"]);
  });
});

describe("parsePatchSegs", () => {
  it("末行 JSON；形状不对一律 null", () => {
    expect(parsePatchSegs('{"patch_segments": [7, 10]}\n')).toEqual([7, 10]);
    expect(parsePatchSegs('WARN 一行噪声\n{"patch_segments": []}')).toEqual([]);
    expect(parsePatchSegs('{"patch_segments": ["7"]}')).toBeNull();
    expect(parsePatchSegs('{"segs": [7]}')).toBeNull();
    expect(parsePatchSegs("")).toBeNull();
  });
});
