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
  /** D80：稳定标识（实时 = `turn_id:index`，历史 = `h:index`），展开状态与 React key 用 */
  id: string;
  name: string;
  summary: string;
  ok: boolean | null; // null = 尚未结束
  duplicate: boolean;
  durationMs: number | null;
  /** 非成功调用逐字保留的原文（H-2/判据 4），成功为 null */
  observation: string | null;
  logs: string[];
  /** D76：bash 的完整命令（帧的条件键；summary 只有前 80 字）。其余工具、历史重建的行为 null */
  command: string | null;
  /** D80：bash 的用途（帧的条件键，工具行主文本）；其余工具或模型没给为 null */
  description: string | null;
  /** D82：成功的 tickets open 带的交接文本（帧的条件键，core 模板生成）；实时帧在 end 阶段取，历史帧同样取。其余为 null */
  handoff: string | null;
  /** D118：失败调用里退出码 1 属正常结果的中性状态词（帧的条件键，core 判定）；其余为 null */
  verdict: string | null;
  /** D80：继续会话时从历史重建的行——不播入场动画 */
  hist?: true;
};

export type ConvRow =
  | { k: "user"; at: number; text: string; hist?: true }
  | { k: "assistant"; at: number; kind: "answer" | "wrapup" | "local_note"; text: string; label: string | null; hist?: true }
  | ToolRow
  | { k: "log"; at: number; lines: string[] }
  | { k: "card"; at: number; requestId: string; label: string; text: string; status: "open" | "approved" | "rejected" | "voided" }
  | { k: "stop"; at: number; text: string }
  | { k: "notice"; at: number; level: string; code: string; text: string }
  /** stopped：`turn_finished.stopped` 原值（D80 回合折叠据此判「已停止」，不解析 text）；
   *  brief：D80 R5 默认显示的短摘要（「N 次模型调用 · 上下文 …」），完整遥测仍在 text、展开可见 */
  | { k: "footer"; at: number; text: string; stopped: string; brief: string; claims: string[] }
  | { k: "historySeparator"; at: number }
  /** 恢复历史里的系统注入（常驻提示、规程、记忆）：Spec 10 §2.3「折叠为『系统注入』」——D57 补落实，默认收起 */
  | { k: "injection"; at: number; text: string }
  | { k: "exited"; at: number; code: number | null; signal: string | null; stderrTail: string }
  | { k: "framesLost"; at: number; count: number; reason: "oversize" | "malformed" };

export interface FoldedConv {
  rows: ConvRow[];
  running: boolean;
  turnId: string | null;
  /** D79：已入队、尚未送达模型的消息（输入框上方的「排队中」气泡） */
  queued: QueuedMsg[];
  /** D79：最近一次被退回输入框的排队消息（回合非正常结束）；key 每次退回唯一，供输入框只回填一次 */
  returned: { key: string; text: string } | null;
}

export type QueuedMsg = { id: string; text: string };

function itemsOf(v: unknown): QueuedMsg[] {
  if (!Array.isArray(v)) return [];
  return v.flatMap((x) => {
    const o = x as Record<string, unknown> | null;
    return o && typeof o.id === "string" && typeof o.text === "string" ? [{ id: o.id, text: o.text }] : [];
  });
}

const CARD_LABEL: Record<string, string> = { tool_call: "工具卡", fetch: "抓取卡", checkpoint: "检查点", memory_ack: "记忆确认" };

function cardLabel(f: OutFrame): string {
  const base = CARD_LABEL[String(f.kind)] ?? "请求卡";
  const tool = f.kind === "tool_call" && f.fields && typeof (f.fields as Record<string, unknown>).tool === "string" ? ` ${String((f.fields as Record<string, unknown>).tool)}` : "";
  return `[${base}]${tool}`;
}

/** D80 R6.3：卡片行的状态短语（`text` 去掉「[卡类] 工具 · 」前缀）；待答显示为「等待批准」 */
export function cardPhrase(row: { label: string; text: string; status: string }): string {
  if (row.status === "open") return "等待批准";
  const head = `${row.label} · `;
  return row.text.startsWith(head) ? row.text.slice(head.length) : row.text;
}

/** D80 R6.3：「[工具卡] bash」→ { kind: "工具卡", tool: "bash" } */
export function cardParts(label: string): { kind: string; tool: string } {
  const m = /^\[(.+?)\](?: (.+))?$/.exec(label);
  return m ? { kind: m[1], tool: m[2] ?? "" } : { kind: label, tool: "" };
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
 * 一个 `turn_finished` 帧本身测到的读数。0 次模型调用的回合（请求前就失败、本地指令、压缩未执行）什么也没测到：
 * 它带的 prompt_chars 只是本地字数，拿来显示会把上一回合服务商实测的 token 读数换成「约 N 字」
 * （2026-10-13 模型名失效连败三轮）。这种回合视同没有读数：脚注不写上下文，会话头沿用上一读数。
 */
function turnReading(f: OutFrame): ContextReading | null {
  return f.llm_calls === 0 ? null : readingOf(f.prompt_tokens, f.prompt_chars, false, f.context_window);
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
    else if (f.t === "turn_finished") out = turnReading(f) ?? out;
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

/**
 * D105：核查类措辞。模型在没调用任何工具的回合里写「重新调取了音频工程」「反复复核」，界面上原本看不出来，
 * 人是看工具数少才起疑的（10-15 supercell 会话 seq 604–615）。前面是「未 / 待 / 没 / 无 / 不」的不算
 * （「还没核实」「待核实」是如实交代，不是声称）。只提示，不拦截。
 */
const CLAIM_WORDS = /(?<![未待没无不])(查实|复核|核验|核实|调取|查证|一手物证|逐条核对|反复确认)/g;

/** 回复里声称核查过的措辞（去重，按出现顺序） */
export function claimWords(text: string): string[] {
  return [...new Set(Array.from(text.matchAll(CLAIM_WORDS), (m) => m[1]))];
}

/** 本回合（上一个用户消息之后）的回复全文 */
function turnReplyText(rows: readonly ConvRow[]): string {
  const parts: string[] = [];
  for (let i = rows.length - 1; i >= 0; i--) {
    const r = rows[i];
    if (r.k === "user" || r.k === "footer") break;
    if (r.k === "assistant") parts.unshift(r.text);
  }
  return parts.join("\n");
}

/** 0 次工具调用却声称核查过 → 那些措辞；否则空。0 次模型调用的回合没有回复，不判 */
function unbackedClaims(f: OutFrame, rows: readonly ConvRow[]): string[] {
  if (f.tool_calls !== 0 || !(Number(f.llm_calls) > 0)) return [];
  return claimWords(turnReplyText(rows));
}

/** D80 R5：回合末默认只显示调用次数与上下文读数——工具数与用时已在回合折叠行上，其余遥测展开看 */
function footerBrief(f: OutFrame): string {
  const reading = turnReading(f);
  const ctx = reading === null ? null : contextText(reading);
  return `${String(f.llm_calls)} 次模型调用${ctx === null ? "" : ` · ${ctx}`}`;
}

function footerText(f: OutFrame): string {
  const reading = turnReading(f);
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

/** D76：帧里的完整命令（只有 bash 带）；空串不给复制按钮 */
function commandOf(f: OutFrame): string | null {
  return typeof f.command === "string" && f.command !== "" ? f.command : null;
}

/** D82：帧里的交接文本（只有成功的 tickets open 带）；空串视同没给 */
function verdictOf(f: OutFrame): string | null {
  return typeof f.verdict === "string" && f.verdict !== "" ? f.verdict : null;
}

function handoffOf(f: OutFrame): string | null {
  return typeof f.handoff === "string" && f.handoff !== "" ? f.handoff : null;
}

/** D80：帧里的用途（只有 bash 带）；空串视同没给 */
function descriptionOf(f: OutFrame): string | null {
  return typeof f.description === "string" && f.description.trim() !== "" ? f.description.trim() : null;
}

/** 与 core `session.tool_summary` 的 bash 分支同一规则：首行，超 80 字或多行加「…」（历史帧不带 summary，在这里补） */
export function bashSummary(command: string | null): string {
  if (command === null) return "";
  const lines = command.trim().split("\n");
  const first = lines[0] ?? "";
  return first.length > 80 || lines.length > 1 ? `${first.slice(0, 80)}…` : first;
}

/**
 * 把一条已校验的帧折进行序列；`lastTool` 是当前回合里最近一个尚未结束的工具行下标（-1 = 无）；
 * `lastStops` 是上一帧 stop_points 的内容（D80 R5：同一会话里停机点没变化就不再重复成行）。
 */
function foldFrame(rows: ConvRow[], f: OutFrame, at: number, lastTool: { idx: number; lastStops?: string }): void {
  switch (f.t) {
    case "assistant":
      rows.push({ k: "assistant", at, kind: String(f.kind) as "answer" | "wrapup" | "local_note", text: f.text as string, label: null });
      break;
    case "tool": {
      if (f.phase === "start") {
        rows.push({ k: "tool", at, id: `${String(f.turn_id)}:${String(f.index)}`, name: String(f.name), summary: String(f.summary), ok: null, duplicate: false, durationMs: null, observation: null, logs: [], command: commandOf(f), description: descriptionOf(f), handoff: null, verdict: null });
        lastTool.idx = rows.length - 1;
      } else {
        const idx = lastTool.idx;
        const row = idx >= 0 ? rows[idx] : undefined;
        if (!row || row.k !== "tool" || row.ok !== null) {
          // 孤立 tool{end}（缓冲截头后会遇到）：显式成行，不崩、不吞
          rows.push({ k: "tool", at, id: `${String(f.turn_id)}:${String(f.index)}:end@${String(rows.length)}`, name: String(f.name), summary: String(f.summary), ok: f.ok as boolean | null, duplicate: f.duplicate === true, durationMs: null, observation: (f.observation as string | null) ?? null, logs: [], command: commandOf(f), description: descriptionOf(f), handoff: handoffOf(f), verdict: verdictOf(f) });
        } else {
          row.ok = f.ok as boolean | null;
          row.duplicate = f.duplicate === true;
          row.durationMs = at - row.at;
          row.observation = (f.observation as string | null) ?? null;
          row.handoff = handoffOf(f);
          row.verdict = verdictOf(f);
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
        row.status = f.reason === "answered" ? (isApprove(f.decision) ? "approved" : "rejected") : "voided";
        row.text = cardText(row.label, row.status, null, null);
      }
      break;
    }
    case "stop_points": {
      // D80 R5：每回合恰好一帧 stop_points（S9-R4），停机点不变时逐轮重复同一行是噪声——只在内容变化时成行
      const texts = ((f.items as Record<string, unknown>[]) ?? []).map((item) => `停机点 ${String(item.type)} 待审（在待答区处理）`);
      const key = texts.join("\n");
      if (key === lastTool.lastStops) break;
      lastTool.lastStops = key;
      for (const text of texts) rows.push({ k: "stop", at, text });
      break;
    }
    case "command_result":
      // D91：输入框模型 / 推理按钮的回执不进对话流——结果就显示在按钮上（失败在按钮旁一行），每点开一次下拉
      // 就多一行「4 个模型」是噪声。对话框里手打的 /model、/thinking 走 notice，照常成行
      if (MODEL_BUTTON_COMMANDS.has(String(f.name))) break;
      rows.push({ k: "notice", at, level: f.ok ? "info" : "warn", code: `command:${String(f.name)}`, text: String(f.text) });
      break;
    case "notice":
      rows.push({ k: "notice", at, level: String(f.level), code: String(f.code), text: String(f.text) });
      break;
    case "error":
      rows.push({ k: "notice", at, level: "error", code: String(f.code), text: String(f.message) });
      break;
    case "turn_finished":
      // D91：/model、/thinking 的命令回合不进模型，「0 次模型调用」脚注是噪声
      if (f.stopped === "command") break;
      rows.push({ k: "footer", at, text: footerText(f), stopped: String(f.stopped), brief: footerBrief(f), claims: unbackedClaims(f, rows) });
      break;
    default:
      break; // ready / turn_started / bye / history 由 foldConv 主循环处理
  }
}

const MODEL_BUTTON_COMMANDS: ReadonlySet<string> = new Set(["models", "model", "thinking"]);

/** ADR-0030：「本会话都批准」也是批准 */
function isApprove(decision: unknown): boolean {
  return decision === "approve" || decision === "approve_session";
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
  let queued: QueuedMsg[] = [];
  let returned: FoldedConv["returned"] = null;

  for (const e of entries) {
    switch (e.k) {
      case "user":
        rows.push({ k: "user", at: e.at, text: e.text });
        break;
      case "answered_local": {
        const row = findCard(rows, e.requestId);
        if (row) {
          row.status = isApprove(e.decision) ? "approved" : "rejected";
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
        queued = []; // 进程没了：排队消息随之丢失（没有送达，也没有可退回的进程回执）
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
                id: `h:${String(f.index)}@${String(rows.length)}`,
                name: String(f.name ?? "tool"),
                // 历史帧只带工具名（D76 起 bash 另带命令）：摘要只给 bash 补，其余保持空——工具名已在行首
                summary: bashSummary(commandOf(f)),
                ok: f.ok === true,
                duplicate: false,
                durationMs: null,
                observation: f.ok === true ? null : String(f.text ?? ""),
                logs: [],
                command: commandOf(f),
                description: descriptionOf(f),
                handoff: handoffOf(f),
                verdict: verdictOf(f),
                hist: true,
              });
            } else if (role === "user") {
              rows.push({ k: "user", at: e.at, text: String(f.text), hist: true });
            } else if (role === "assistant") {
              rows.push({ k: "assistant", at: e.at, kind: "answer", text: String(f.text), label: null, hist: true });
            } else {
              rows.push({ k: "injection", at: e.at, text: String(f.text) });
            }
            break;
          }
          case "queued":
            queued = [...queued, { id: String(f.id), text: String(f.text) }];
            break;
          case "steer_delivered": {
            // 送达模型的那一刻才进时间线（位置 = 这批工具结果之后），气泡随之消失
            const ids = new Set(Array.isArray(f.ids) ? (f.ids as unknown[]).map(String) : []);
            for (const q of queued) if (ids.has(q.id)) rows.push({ k: "user", at: e.at, text: q.text });
            queued = queued.filter((q) => !ids.has(q.id));
            break;
          }
          case "steer_returned": {
            const items = itemsOf(f.items);
            const ids = new Set(items.map((i) => i.id));
            queued = queued.filter((q) => !ids.has(q.id));
            if (items.length > 0) returned = { key: `${String(f.seq)}:${items.map((i) => i.id).join(",")}`, text: items.map((i) => i.text).join("\n") };
            break;
          }
          case "ready":
            queued = []; // 新进程：旧进程的排队不会再有回执
            break;
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
  return { rows, running, turnId, queued, returned };
}

/** §2.6：徽标值由 host 从帧折叠得出，随 episodes.summary 下发。 */
export function convSummary(s: { phase: ConvSnapshot["phase"]; open: readonly OutFrame[] }): { live: boolean; running: boolean; openRequests: number } {
  return {
    live: s.phase === "starting" || s.phase === "idle" || s.phase === "running" || s.phase === "ending",
    running: s.phase === "running",
    openRequests: s.open.length,
  };
}
