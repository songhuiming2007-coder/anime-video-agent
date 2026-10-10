// Spec 10 §2.3：帧 → 对话流的折叠（host 侧摘要/徽标与 renderer 视图共用同一份逻辑）。纯函数、零 DOM。
//
// 「有损」分两层（§2.3）：事件时间线（events.jsonl）缺口在磁盘上无痕迹；这里的协议帧缺口全部可检测，
// 因此在缺口处显式标出（frames_lost / 截头 / 孤立 tool{end}），不挂常驻有损横幅。
import type { ConvEntry, ConvSnapshot } from "./protocol";
import type { OutFrame } from "./convFrames";
import { LOG_TAIL_LINES } from "./constants";

export type ToolRow = {
  k: "tool";
  at: number;
  name: string;
  summary: string;
  ok: boolean | null; // null = 尚未结束
  duplicate: boolean;
  durationMs: number | null;
  /** 非成功调用逐字保留的原文（H-2/判据 4），成功为 null */
  observation: string | null;
  logs: string[];
};

export type ConvRow =
  | { k: "user"; at: number; text: string }
  | { k: "assistant"; at: number; kind: "answer" | "wrapup" | "local_note"; text: string; label: string | null }
  | ToolRow
  | { k: "log"; at: number; lines: string[] }
  | { k: "card"; at: number; requestId: string; label: string; text: string; status: "open" | "approved" | "rejected" | "voided" }
  | { k: "stop"; at: number; text: string }
  | { k: "notice"; at: number; level: string; code: string; text: string }
  | { k: "footer"; at: number; text: string }
  | { k: "historySeparator"; at: number }
  /** 恢复历史里的系统注入（常驻提示、规程、记忆）：Spec 10 §2.3「折叠为『系统注入』」——D57 补落实，默认收起 */
  | { k: "injection"; at: number; text: string }
  | { k: "exited"; at: number; code: number | null; signal: string | null; stderrTail: string }
  | { k: "framesLost"; at: number; count: number; reason: "oversize" | "malformed" };

export interface FoldedConv {
  rows: ConvRow[];
  running: boolean;
  turnId: string | null;
}

const CARD_LABEL: Record<string, string> = { tool_call: "工具卡", fetch: "抓取卡", checkpoint: "检查点", memory_ack: "记忆确认" };

function cardLabel(f: OutFrame): string {
  const base = CARD_LABEL[String(f.kind)] ?? "请求卡";
  const tool = f.kind === "tool_call" && f.fields && typeof (f.fields as Record<string, unknown>).tool === "string" ? ` ${String((f.fields as Record<string, unknown>).tool)}` : "";
  return `[${base}]${tool}`;
}

function cardText(label: string, status: "open" | "approved" | "rejected" | "voided", feedback: string | null, voidedCause: string | null): string {
  if (status === "open") return `${label} · 待答`;
  if (status === "approved") return `${label} · 已批准`;
  if (status === "rejected") return `${label} · 已拒绝${feedback ? `（附反馈：${feedback}）` : ""}`;
  // 作废原因只在 voided_local 时可知（🔵-2）：无 voided_local 的“已作废”不带原因
  return `${label} · 已作废${voidedCause ? `（${voidedCause}）` : ""}`;
}

/**
 * D41 回落口径：本回合最后一次请求模型时，全部消息正文的**字符数**（不含工具调用参数与工具定义）。
 * 只在服务商没给 token 数时用（D56），显示时带「约」。
 */
export function charsText(n: number): string {
  if (n < 10_000) return `${String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ",")} 字`;
  return `${(n / 10_000).toFixed(1)} 万字`;
}

/** D56：`<1000` 写整数，其余一位小数的 k。 */
export function tokensText(n: number): string {
  return `${tokensNum(n)} token`;
}

function tokensNum(n: number): string {
  return n < 1000 ? String(n) : `${(n / 1000).toFixed(1)}k`;
}

/** D62 人裁决⑤：分母 = 模型标称窗口，整数 k（128000 → 128k）；百万级写 M（1048576 → 1.0M）。 */
export function windowText(n: number): string {
  return n >= 1_000_000 ? `${(n / 1_000_000).toFixed(1)}M` : `${String(Math.round(n / 1000))}k`;
}

const countOf = (v: unknown): number | null => (typeof v === "number" && Number.isSafeInteger(v) && v >= 0 ? v : null);

/**
 * 上下文读数（D41 → D56）。`tokens` = 服务商响应里的 `usage.prompt_tokens`：最近一次请求模型时的输入 token，
 * 含系统提示、工具定义与全部历史，就是上下文窗口的实际占用；服务商不给时为 null，回落到 `chars`（字数）。
 * `previous`（D64）：继续会话后、新回合跑完前，读数取自 `ready` 帧带回的上次测得值，显示时标「上次」。
 */
export type ContextReading = { tokens: number | null; chars: number | null; previous: boolean; ctxWindow?: number };

/** `ctxWindow`（D62 人裁决⑤）：同一帧带来的当前模型标称窗口；core 窗口表里没有该模型时不带，读数不显示分母。 */
function readingOf(tokens: unknown, chars: unknown, previous: boolean, ctxWindow?: unknown): ContextReading | null {
  const r: ContextReading = { tokens: countOf(tokens), chars: countOf(chars), previous };
  const w = countOf(ctxWindow);
  if (w !== null && w > 0) r.ctxWindow = w;
  return r.tokens === null && r.chars === null ? null : r;
}

/**
 * 「上下文 12.3k token」；知道窗口时带分母「上下文 102.0k / 128k token」（D62 人裁决⑤：标称窗口）；
 * 没有 token 数时「上下文约 9,192 字」——字数与窗口不是同一把尺，不配分母；上次测得的值后缀「（上次）」。
 */
export function contextText(r: ContextReading): string | null {
  const tail = r.previous ? "（上次）" : "";
  if (r.tokens !== null && r.ctxWindow !== undefined) return `上下文 ${tokensNum(r.tokens)} / ${windowText(r.ctxWindow)} token${tail}`;
  if (r.tokens !== null) return `上下文 ${tokensText(r.tokens)}${tail}`;
  if (r.chars !== null) return `上下文约 ${charsText(r.chars)}${tail}`;
  return null;
}

/**
 * 当前会话（最后一个 `ready` 之后）最近一次 `turn_finished` 的读数。还没有回合结束过时：
 * 继续会话取 `ready` 带回的上次读数（D64，`previous`），新会话为 null，不沿用上一个会话的读数。
 */
export function lastContextReading(entries: readonly ConvEntry[]): ContextReading | null {
  let out: ContextReading | null = null;
  for (const e of entries) {
    if (e.k !== "frame") continue;
    const f = e.frame;
    if (f.t === "ready") out = readingOf(f.resume_prompt_tokens, f.resume_prompt_chars, true, f.context_window);
    else if (f.t === "turn_finished") out = readingOf(f.prompt_tokens, f.prompt_chars, false, f.context_window) ?? out;
  }
  return out;
}

const LOOKUP_LABELS: readonly (readonly [string, string])[] = [
  ["subs", "字幕"],
  ["presence", "在场"],
  ["notes", "笔记"],
  ["web", "网页"],
];

/**
 * D48 ①：本回合实际执行的查证调用，按类照实列出（0 也列——人要看的正是「说核对过、字幕却是 0」）。
 * 帧里没有或形状不对（旧 core、无模型回合）则不显示。
 */
export function lookupsText(v: unknown): string | null {
  if (v === null || typeof v !== "object" || Array.isArray(v)) return null;
  const o = v as Record<string, unknown>;
  const parts: string[] = [];
  for (const [k, label] of LOOKUP_LABELS) {
    const n = o[k];
    if (typeof n !== "number" || !Number.isSafeInteger(n) || n < 0) return null;
    parts.push(`${label} ${String(n)}`);
  }
  return `查证 ${parts.join(" · ")}`;
}

function footerText(f: OutFrame): string {
  const reading = readingOf(f.prompt_tokens, f.prompt_chars, false, f.context_window);
  const ctx = reading === null ? null : contextText(reading);
  const lookups = lookupsText(f.lookups);
  return (
    `模型调用 ${String(f.llm_calls)} · 工具 ${String(f.tool_calls)}（执行 ${String(f.tool_executions)}、重复拒绝 ${String(f.duplicates_rejected)}）` +
    ` · 检查点 ${String(f.checkpoints)} · 用时 ${String(f.duration_s)} s · ${String(f.stopped)} · ${String(f.wrapup)}` +
    (lookups === null ? "" : ` · ${lookups}`) +
    (ctx === null ? "" : ` · ${ctx}`)
  );
}

function logText(text: string): string[] {
  return text.split("\n").slice(-LOG_TAIL_LINES);
}

/** 把一条已校验的帧折进行序列；`lastTool` 是当前回合里最近一个尚未结束的工具行下标（-1 = 无）。 */
function foldFrame(rows: ConvRow[], f: OutFrame, at: number, lastTool: { idx: number }): void {
  switch (f.t) {
    case "assistant":
      rows.push({ k: "assistant", at, kind: String(f.kind) as "answer" | "wrapup" | "local_note", text: f.text as string, label: null });
      break;
    case "tool": {
      if (f.phase === "start") {
        rows.push({ k: "tool", at, name: String(f.name), summary: String(f.summary), ok: null, duplicate: false, durationMs: null, observation: null, logs: [] });
        lastTool.idx = rows.length - 1;
      } else {
        const idx = lastTool.idx;
        const row = idx >= 0 ? rows[idx] : undefined;
        if (!row || row.k !== "tool" || row.ok !== null) {
          // 孤立 tool{end}（缓冲截头后会遇到）：显式成行，不崩、不吞
          rows.push({ k: "tool", at, name: String(f.name), summary: String(f.summary), ok: f.ok as boolean | null, duplicate: f.duplicate === true, durationMs: null, observation: (f.observation as string | null) ?? null, logs: [] });
        } else {
          row.ok = f.ok as boolean | null;
          row.duplicate = f.duplicate === true;
          row.durationMs = at - row.at;
          row.observation = (f.observation as string | null) ?? null;
          lastTool.idx = -1;
        }
      }
      break;
    }
    case "log": {
      const idx = lastTool.idx;
      const row = idx >= 0 ? rows[idx] : undefined;
      if (row && row.k === "tool" && row.ok === null) row.logs.push(...logText(String(f.text)));
      else rows.push({ k: "log", at, lines: logText(String(f.text)) });
      break;
    }
    case "request": {
      const label = cardLabel(f);
      rows.push({ k: "card", at, requestId: String(f.request_id), label, text: cardText(label, "open", null, null), status: "open" });
      break;
    }
    case "request_closed": {
      const row = findCard(rows, String(f.request_id));
      if (row) {
        row.status = f.reason === "answered" ? (f.decision === "approve" ? "approved" : "rejected") : "voided";
        row.text = cardText(row.label, row.status, null, null);
      }
      break;
    }
    case "stop_points":
      for (const item of (f.items as Record<string, unknown>[]) ?? []) {
        rows.push({ k: "stop", at, text: `停机点 ${String(item.type)} 待审（在待答区处理）` });
      }
      break;
    case "command_result":
      rows.push({ k: "notice", at, level: f.ok ? "info" : "warn", code: `command:${String(f.name)}`, text: String(f.text) });
      break;
    case "notice":
      rows.push({ k: "notice", at, level: String(f.level), code: String(f.code), text: String(f.text) });
      break;
    case "error":
      rows.push({ k: "notice", at, level: "error", code: String(f.code), text: String(f.message) });
      break;
    case "turn_finished":
      rows.push({ k: "footer", at, text: footerText(f) });
      break;
    default:
      break; // ready / turn_started / bye / history 由 foldConv 主循环处理
  }
}

function findCard(rows: ConvRow[], requestId: string): Extract<ConvRow, { k: "card" }> | undefined {
  for (let i = rows.length - 1; i >= 0; i -= 1) {
    const r = rows[i];
    if (r.k === "card" && r.requestId === requestId) return r;
  }
  return undefined;
}

export function foldConv(entries: readonly ConvEntry[]): FoldedConv {
  const rows: ConvRow[] = [];
  const lastTool = { idx: -1 };
  let running = false;
  let turnId: string | null = null;
  let historySeen = false;

  for (const e of entries) {
    switch (e.k) {
      case "user":
        rows.push({ k: "user", at: e.at, text: e.text });
        break;
      case "answered_local": {
        const row = findCard(rows, e.requestId);
        if (row) {
          row.status = e.decision === "approve" ? "approved" : "rejected";
          row.text = cardText(row.label, row.status, e.feedback, null);
        }
        break;
      }
      case "voided_local": {
        for (const id of e.requestIds) {
          const row = findCard(rows, id);
          if (row) {
            row.status = "voided";
            row.text = cardText(row.label, "voided", null, "会话已结束");
          }
        }
        break;
      }
      case "frames_lost":
        rows.push({ k: "framesLost", at: e.at, count: e.count, reason: e.reason });
        break;
      case "exited":
        rows.push({ k: "exited", at: e.at, code: e.code, signal: e.signal, stderrTail: e.stderrTail });
        break;
      case "settled":
      case "spawned":
        break; // 不进行：settled 驱动 autoOpen，spawned 只做诊断
      case "frame": {
        const f = e.frame;
        switch (f.t) {
          case "history": {
            if (!historySeen) {
              historySeen = true;
              rows.push({ k: "historySeparator", at: e.at });
            }
            const role = String(f.role);
            if (role === "tool") {
              rows.push({
                k: "tool",
                at: e.at,
                name: String(f.name ?? "tool"),
                summary: String(f.name ?? ""),
                ok: f.ok === true,
                duplicate: false,
                durationMs: null,
                observation: f.ok === true ? null : String(f.text ?? ""),
                logs: [],
              });
            } else if (role === "user") {
              rows.push({ k: "user", at: e.at, text: String(f.text) });
            } else if (role === "assistant") {
              rows.push({ k: "assistant", at: e.at, kind: "answer", text: String(f.text), label: null });
            } else {
              rows.push({ k: "injection", at: e.at, text: String(f.text) });
            }
            break;
          }
          case "turn_started":
            running = true;
            turnId = String(f.turn_id);
            lastTool.idx = -1;
            break;
          case "turn_finished":
            running = false;
            turnId = null;
            lastTool.idx = -1;
            foldFrame(rows, f, e.at, lastTool);
            break;
          default:
            foldFrame(rows, f, e.at, lastTool);
            break;
        }
        break;
      }
    }
  }
  return { rows, running, turnId };
}

/** §2.6：徽标值由 host 从帧折叠得出，随 episodes.summary 下发。 */
export function convSummary(s: { phase: ConvSnapshot["phase"]; open: readonly OutFrame[] }): { live: boolean; running: boolean; openRequests: number } {
  return {
    live: s.phase === "starting" || s.phase === "idle" || s.phase === "running" || s.phase === "ending",
    running: s.phase === "running",
    openRequests: s.open.length,
  };
}
