// §3.1 规则 8/9：纳秒 mtime（约 1.79e18 > 2^53）无损解析；出 host 的协议面不含 bigint。
// 对象库与事件行的唯一解析入口；host 与 shared 内唯一允许调用 JSON.stringify 的文件（TG-9）。
import type {
  ApprovalJson,
  ApprovalRecord,
  ArtifactFingerprint,
  EventJson,
  EventRecord,
} from "./contracts";

interface ReviverContext {
  source?: string;
}
type Reviver = (this: unknown, key: string, value: unknown, ctx?: ReviverContext) => unknown;
const parseWithContext = JSON.parse as unknown as (text: string, reviver: Reviver) => unknown;

function fail(key: string, value: unknown): never {
  throw new TypeError(`${key} 必须是 JSON 整数，实际为 ${value === null ? "null" : typeof value}`);
}

/**
 * mtime_ns → bigint（取 ctx.source 原文，不经 double）；mtime_ns 的值只要不是 JSON 数字就抛错。
 * ctx.source 不是纯十进制整数字面量（含 . 或 e）时 BigInt 抛错。运行时不支持 ctx 时读 ctx.source 抛错——
 * 一律整份解析失败，绝不回退到有损值。
 */
export function parseLossless(text: string): unknown {
  return parseWithContext(text, (k, v, ctx) =>
    k !== "mtime_ns" ? v : typeof v === "number" ? BigInt((ctx as ReviverContext).source as string) : fail(k, v),
  );
}

export const SELF_CHECK_TEXT = '{"mtime_ns":1790171112636927676}';
export const SELF_CHECK_EXPECTED = 1790171112636927676n;

/** 启动自检：解析 SELF_CHECK_TEXT 须得到 SELF_CHECK_EXPECTED。parse 可注入以便单测。 */
export function losslessSelfCheck(parse: (t: string) => unknown = parseLossless): boolean {
  try {
    const r = parse(SELF_CHECK_TEXT) as { mtime_ns?: unknown } | null;
    return r !== null && typeof r === "object" && r.mtime_ns === SELF_CHECK_EXPECTED;
  } catch {
    return false;
  }
}

function debigint(v: unknown): unknown {
  if (typeof v === "bigint") return v.toString();
  if (Array.isArray(v)) return v.map(debigint);
  if (v !== null && typeof v === "object") {
    const out: Record<string, unknown> = {};
    for (const [k, x] of Object.entries(v)) out[k] = debigint(x);
    return out;
  }
  return v;
}

export function toWireApproval(r: ApprovalRecord): ApprovalJson {
  return {
    ...r,
    options: [...r.options],
    feedback: r.feedback ? { ...r.feedback } : null,
    artifacts: r.artifacts.map((a: ArtifactFingerprint) => ({ path: a.path, size: a.size, mtime_ns: a.mtime_ns.toString() })),
  };
}

export function toWireEvent(e: EventRecord): EventJson {
  return { ...e, payload: debigint(e.payload) as Record<string, unknown> };
}

const rawJSON = (JSON as unknown as { rawJSON?: (text: string) => unknown }).rawJSON;

/** bigint → 十进制数字字面量（运行时无 JSON.rawJSON 时退为十进制字符串，仅用于诊断，不参与比对）。 */
export function stringifyLossless(v: unknown): string {
  return JSON.stringify(v, (_k, x) =>
    typeof x === "bigint" ? (rawJSON ? rawJSON(x.toString()) : x.toString()) : x,
  );
}
