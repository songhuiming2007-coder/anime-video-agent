// approval.decide 的纯函数部分（§3.2.1、§4.3）：参数形状、ackable、verified、§2.6 表的 spawn 计划。端到端见 e2e/ack.spec.ts。
import { describe, expect, it } from "vitest";
import type { ApprovalRecord } from "../../src/shared/contracts";
import { ackable, DecideFail, parseDecideParams, plan, verified } from "../../src/host/decide";

const obj = (over: Partial<ApprovalRecord> = {}): ApprovalRecord => ({
  approval_id: "appr_1",
  episode: "EP",
  type: "05",
  status: "pending",
  artifacts: [{ path: "04-clips.json", size: 285, mtime_ns: 1790171112636927676n }],
  options: ["approve", "reject"],
  created_at: "2026-09-25T00:00:00.000000Z",
  resolved_at: null,
  resolved_by: null,
  confirmed_by: null,
  confirmed_at: null,
  feedback: null,
  note: "",
  ...over,
});

const bad = (raw: Record<string, unknown>) => {
  try {
    parseDecideParams(raw);
  } catch (e) {
    return e instanceof DecideFail ? e.error.code : String(e);
  }
  return "accepted";
};

describe("parseDecideParams（§3.2.1）", () => {
  const base = { epKey: "EP", approvalId: "appr_1", stop: "05" };
  it("合法的 approve / reject", () => {
    expect(parseDecideParams({ ...base, decision: "approve" })).toEqual({ ...base, decision: "approve" });
    const r = parseDecideParams({ ...base, decision: "reject", feedback: { target: " s07 ", problem: "-x\n\"y\"" } });
    expect(r).toEqual({ ...base, decision: "reject", feedback: { target: " s07 ", problem: "-x\n\"y\"" } }); // 原样保留，不 trim 不改写
  });
  it("feedback 两项 trim 后为空、多余键、缺失、approve 带 feedback、未知停机点与决定 → E_BAD_REQUEST", () => {
    expect(bad({ ...base, decision: "reject", feedback: { target: "  ", problem: "p" } })).toBe("E_BAD_REQUEST");
    expect(bad({ ...base, decision: "reject", feedback: { target: "t", problem: "\n" } })).toBe("E_BAD_REQUEST");
    expect(bad({ ...base, decision: "reject", feedback: { target: "t", problem: "p", path: "/x" } })).toBe("E_BAD_REQUEST");
    expect(bad({ ...base, decision: "reject" })).toBe("E_BAD_REQUEST");
    expect(bad({ ...base, decision: "approve", feedback: { target: "t", problem: "p" } })).toBe("E_BAD_REQUEST");
    expect(bad({ ...base, stop: "06", decision: "approve" })).toBe("E_BAD_REQUEST");
    expect(bad({ ...base, decision: "maybe" })).toBe("E_BAD_REQUEST");
  });
});

describe("ackable / verified（§4.3）", () => {
  it("approve：PENDING 或「artifact 已对齐、待确认」；reject 只认 PENDING", () => {
    expect(ackable(obj(), "approve")).toBe(true);
    expect(ackable(obj({ status: "approved", resolved_by: "artifact" }), "approve")).toBe(true);
    expect(ackable(obj({ status: "approved", resolved_by: "artifact" }), "reject")).toBe(false);
    expect(ackable(obj({ status: "approved", resolved_by: "artifact", confirmed_by: "cli" }), "approve")).toBe(false);
    expect(ackable(obj({ status: "approved", resolved_by: "cli" }), "approve")).toBe(false);
    expect(ackable(obj({ status: "superseded" }), "approve")).toBe(false);
    expect(ackable(obj({ status: "rejected" }), "reject")).toBe(false);
  });
  it("verified：approve 要 approved 且由 cli 批准或确认；reject 要 feedback 逐字节相等", () => {
    const p = { epKey: "EP", approvalId: "appr_1", stop: "05" as const };
    expect(verified(obj({ status: "approved", resolved_by: "cli" }), { ...p, decision: "approve" })).toBe(true);
    expect(verified(obj({ status: "approved", resolved_by: "artifact", confirmed_by: "cli" }), { ...p, decision: "approve" })).toBe(true);
    expect(verified(obj({ status: "approved", resolved_by: "artifact" }), { ...p, decision: "approve" })).toBe(false);
    expect(verified(undefined, { ...p, decision: "approve" })).toBe(false);
    const fb = { target: "s07", problem: "a\nb" };
    expect(verified(obj({ status: "rejected", feedback: fb }), { ...p, decision: "reject", feedback: fb })).toBe(true);
    expect(verified(obj({ status: "rejected", feedback: { target: "s07", problem: "a b" } }), { ...p, decision: "reject", feedback: fb })).toBe(false);
  });
});

describe("plan（§2.6 表）", () => {
  const p = { epKey: "EP", approvalId: "appr_1" };
  it("05 PENDING → [REVIEW_APPROVE(钉住指纹, mtime 为 bigint), APPROVE]；05 已对齐 → [APPROVE]；其余一步", () => {
    const s = plan({ ...p, stop: "05", decision: "approve" }, obj(), "/ep");
    expect(s.map((x) => x.t)).toEqual(["REVIEW_APPROVE", "APPROVE"]);
    expect(s[0].args).toEqual({ ep: "/ep", size: 285, mtimeNs: 1790171112636927676n });
    expect(plan({ ...p, stop: "05", decision: "approve" }, obj({ status: "approved", resolved_by: "artifact" }), "/ep").map((x) => x.t)).toEqual(["APPROVE"]);
    expect(plan({ ...p, stop: "03.5", decision: "approve" }, obj({ type: "03.5" }), "/ep").map((x) => x.t)).toEqual(["APPROVE"]);
    expect(plan({ ...p, stop: "05", decision: "reject", feedback: { target: "t", problem: "q" } }, obj(), "/ep")).toEqual([
      { t: "REJECT", args: { ep: "/ep", stop: "05", approvalId: "appr_1", target: "t", problem: "q" } },
    ]);
  });
});
