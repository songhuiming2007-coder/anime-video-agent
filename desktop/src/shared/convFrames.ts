// Spec 10 §3.2：会话进程出站帧的校验（host 唯一的帧入口）。消费 Spec 9 §3.1 v1 + S9-R1/R2/R4。
// 纯函数、零 I/O：stdout 切行与字节上限在 host/sessions.ts，这里只管「一行是不是合法帧」。
// 规则 3~5：t 必须在出站闭集里；按 t 检查必需键存在且为预期原始类型；多出的键忽略（向前兼容）；
// 任何整数若 !Number.isSafeInteger 一律 malformed（Spec 8 RF-23 同一教训：JSON 数字越过 2^53 已经失真）。

export const OUT_TYPES = [
  "ready",
  "history",
  "turn_started",
  "assistant",
  "tool",
  "request",
  "request_closed",
  "command_result",
  "stop_points",
  "turn_finished",
  "log",
  "notice",
  "error",
  "bye",
] as const;

export type OutType = (typeof OUT_TYPES)[number];

export type OutFrame = { v: 1; t: OutType; seq: number; sid: string | null } & Record<string, unknown>;

export type ParseResult = { ok: true; frame: OutFrame } | { ok: false; reason: "malformed" };

type Kind = "string" | "number" | "boolean" | "array" | "object" | "null";

/**
 * 每类帧的必需键与允许类型（null 表示允许为 null）。缺键或类型不符即 malformed。
 * 导出只为 TX-0（Spec 10 PR4）：拿真实 core 实际发出的键集合与这张表逐类比对，两侧不各写期望。
 */
export const REQUIRED: Record<OutType, Record<string, readonly Kind[]>> = {
  ready: {
    episode: ["string", "null"],
    scope: ["string"],
    continue_status: ["string"],
    llm: ["string"],
    degrade_reason: ["string", "null"],
    code_freeze_ok: ["boolean"],
    history_count: ["number"],
    session_bytes: ["number"],
    other_sessions: ["array"],
  },
  history: { index: ["number"], role: ["string"], text: ["string"], name: ["string", "null"] },
  turn_started: { turn_id: ["string"] },
  assistant: { turn_id: ["string"], kind: ["string"], text: ["string"] },
  tool: {
    turn_id: ["string"],
    phase: ["string"],
    index: ["number"],
    name: ["string"],
    summary: ["string"],
    ok: ["boolean", "null"],
    observation: ["string", "null"],
    duplicate: ["boolean"],
  },
  request: {
    request_id: ["string"],
    kind: ["string"],
    turn_id: ["string", "null"],
    title: ["string"],
    card_text: ["string"],
    fields: ["object"],
    options: ["array"],
    feedback_allowed: ["boolean"],
  },
  request_closed: { request_id: ["string"], reason: ["string"], decision: ["string", "null"] },
  command_result: { name: ["string"], ok: ["boolean"], text: ["string"] },
  stop_points: { items: ["array"], turn_id: ["string", "null"] },
  turn_finished: {
    turn_id: ["string"],
    stopped: ["string"],
    llm_calls: ["number"],
    tool_calls: ["number"],
    tool_executions: ["number"],
    duplicates_rejected: ["number"],
    checkpoints: ["number"],
    wrapup: ["string"],
    duration_s: ["number"],
    prompt_chars: ["number"],
    prompt_tokens: ["number", "null"], // D56：服务商响应 usage.prompt_tokens；不给为 null
    lookups: ["object", "null"], // D48 ①：本回合查证调用按类计数（subs/presence/notes/web）；无模型回合为 null
  },
  log: { stream: ["string"], text: ["string"] },
  notice: { level: ["string"], code: ["string"], text: ["string"] },
  error: { code: ["string"], message: ["string"] },
  bye: { reason: ["string"] },
};

/** history 的 tool 条目多一个必需键 ok（S9-R1）。 */
export const CONDITIONAL: Partial<Record<OutType, (f: Record<string, unknown>) => Record<string, readonly Kind[]> | null>> = {
  history: (f) => (f.role === "tool" ? { ok: ["boolean"] } : null),
};

function kindOf(v: unknown): Kind {
  if (v === null) return "null";
  if (typeof v === "string") return "string";
  if (typeof v === "number") return "number";
  if (typeof v === "boolean") return "boolean";
  if (Array.isArray(v)) return "array";
  if (typeof v === "object") return "object";
  return "string"; // 不会命中任何允许列表
}

/** 整数若超出安全范围即失真：JSON.parse 已经把它变成双精度，无法回头（Spec 9 承诺不含这种数）。 */
function badInteger(f: Record<string, unknown>): boolean {
  for (const v of Object.values(f)) {
    if (typeof v === "number" && Number.isInteger(v) && !Number.isSafeInteger(v)) return true;
  }
  return false;
}

function matches(f: Record<string, unknown>, required: Record<string, readonly Kind[]>): boolean {
  for (const [key, kinds] of Object.entries(required)) {
    if (!Object.prototype.hasOwnProperty.call(f, key)) return false;
    if (!kinds.includes(kindOf(f[key]))) return false;
  }
  return true;
}

export function parseOutFrame(line: string): ParseResult {
  let raw: unknown;
  try {
    raw = JSON.parse(line);
  } catch {
    return { ok: false, reason: "malformed" };
  }
  if (raw === null || typeof raw !== "object" || Array.isArray(raw)) return { ok: false, reason: "malformed" };
  const f = raw as Record<string, unknown>;
  if (f.v !== 1) return { ok: false, reason: "malformed" };
  const t = f.t;
  if (typeof t !== "string" || !(OUT_TYPES as readonly string[]).includes(t)) return { ok: false, reason: "malformed" };
  const type = t as OutType;
  if (typeof f.seq !== "number" || !Number.isFinite(f.seq)) return { ok: false, reason: "malformed" };
  if (!(f.sid === null || typeof f.sid === "string")) return { ok: false, reason: "malformed" };
  if (!matches(f, REQUIRED[type])) return { ok: false, reason: "malformed" };
  const extra = CONDITIONAL[type]?.(f);
  if (extra && !matches(f, extra)) return { ok: false, reason: "malformed" };
  if (badInteger(f)) return { ok: false, reason: "malformed" };
  return { ok: true, frame: f as OutFrame };
}
