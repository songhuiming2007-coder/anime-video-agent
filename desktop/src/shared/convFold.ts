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
 * D41：上下文用量读数。口径（读码确认，`llm.py::run_tool_loop::_chat`）：本回合最后一次请求模型时，
 * 全部消息正文的**字符数**（不含工具调用参数与工具定义）——不是 token，也没有上限刻度，所以只给数、不画进度条。
 */
export function charsText(n: number): string {
  if (n < 10_000) return `${String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ",")} 字`;
  return `${(n / 10_000).toFixed(1)} 万字`;
}

function promptChars(f: OutFrame): number | null {
  const v = f.prompt_chars;
  return typeof v === "number" && Number.isSafeInteger(v) && v >= 0 ? v : null;
}

/** D41：当前会话（最后一个 `ready` 之后）最近一次 `turn_finished` 的 `prompt_chars`；还没有回合结束过则为 null。 */
export function lastPromptChars(entries: readonly ConvEntry[]): number | null {
  let out: number | null = null;
  for (const e of entries) {
    if (e.k !== "frame") continue;
    if (e.frame.t === "ready") out = null; // 新会话（含「继续上次会话」）从头算，不沿用上一个会话的读数
    else if (e.frame.t === "turn_finished") out = promptChars(e.frame) ?? out;
  }
  return out;
}

function footerText(f: OutFrame): string {
  const chars = promptChars(f);
  return (
    `模型调用 ${String(f.llm_calls)} · 工具 ${String(f.tool_calls)}（执行 ${String(f.tool_executions)}、重复拒绝 ${String(f.duplicates_rejected)}）` +
    ` · 检查点 ${String(f.checkpoints)} · 用时 ${String(f.duration_s)} s · ${String(f.stopped)} · ${String(f.wrapup)}` +
    (chars === null ? "" : ` · 上下文 ${charsText(chars)}`)
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
              rows.push({ k: "assistant", at: e.at, kind: "local_note", text: String(f.text), label: "系统注入" });
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
