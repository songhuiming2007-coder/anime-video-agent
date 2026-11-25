// Spec 10 §2.1/§2.7/§2.10/§4.2：会话管理。host 中唯一构造入站帧、写会话 stdin 的模块（TG-11）。
//
// 每个会话键（"idea" 或 "ep:<epKey>"）至多一个 Spec 9 协议进程；懒启动（人发第一条消息或点「继续上次会话」才 spawn）；
// 切期后在后台继续跑。只从 stdout 解析帧（H-9），stderr 只进一个 8 KiB 尾部环形缓冲、从不解析。
// host 从不自行生成 user_message（H-8）：构造入站帧的入口闭集 = send / interrupt / answer / command / 结束序列。
import {
  ANSWER_ACK_TIMEOUT_MS,
  CONV_BUFFER_MAX_BYTES,
  CONV_PUSH_COALESCE_MS,
  FEEDBACK_MAX_BYTES,
  MAX_INBOUND_LINE_BYTES,
  SEND_ACK_TIMEOUT_MS,
  SESSION_END_WAIT_MS,
  SESSION_FRAME_MAX_BYTES,
  SESSION_KILL_GRACE_MS,
  SESSION_READY_TIMEOUT_MS,
  SETTLE_TIMEOUT_MS,
} from "../shared/constants";
import { turnAttention, type Attention } from "../shared/attention";
import { convSummary } from "../shared/convFold";
import { parseOutFrame, type OutFrame } from "../shared/convFrames";
import { LineSplitter } from "../shared/jsonl";
import { stringifyLossless } from "../shared/losslessJson";
import { parseModelSourceInfo, parseModelSources, type ConvEntry, type ConvKey, type ConvPhase, type ConvSnapshot, type Envelope, type ErrCode, type ModelSourceInfo } from "../shared/protocol";
import type { SessionProc, SessionTemplate } from "./spawner";

export class SessionError extends Error {
  constructor(
    readonly code: ErrCode,
    message: string,
  ) {
    super(message);
  }
}

export interface SessionTarget {
  key: ConvKey;
  epKey: string | null;
  abs: string | null;
  mode: "new" | "continue" | "idea";
  /** D45：mode "continue" 时指定恢复哪个会话；不给 = 最近的可恢复会话。D58：mode "idea" 同理（恢复指定选题段） */
  sid?: string;
  /** D58：mode "idea" 时开新段（`--idea --fresh`），不恢复任何段 */
  fresh?: boolean;
}

export interface QuitBusy {
  convKey: string;
  label: string;
  running: boolean;
  openRequests: number;
}

export interface SessionTiming {
  readyMs: number;
  sendAckMs: number;
  answerAckMs: number;
  endWaitMs: number;
  killGraceMs: number;
  settleMs: number;
  coalesceMs: number;
}

const DEFAULT_TIMING: SessionTiming = {
  readyMs: SESSION_READY_TIMEOUT_MS,
  sendAckMs: SEND_ACK_TIMEOUT_MS,
  answerAckMs: ANSWER_ACK_TIMEOUT_MS,
  endWaitMs: SESSION_END_WAIT_MS,
  killGraceMs: SESSION_KILL_GRACE_MS,
  settleMs: SETTLE_TIMEOUT_MS,
  coalesceMs: CONV_PUSH_COALESCE_MS,
};

export interface SessionDeps {
  spawnSession: (t: SessionTemplate, args: { ep?: string; sid?: string; fresh?: boolean; cont?: boolean }, ctx: { repoRoot: string }, extraEnv: Record<string, string>) => SessionProc;
  /** 每次 spawn SESSION_* 前执行一次（§2.9）；失败给 problem 文案，照常 spawn、如实降级 */
  resolveKey: () => Promise<{ name: string; value: string } | { problem: string }>;
  /** Spec 15 §2.7：web 检索链上声明的密钥（LLM 名字之后执行）；自身兜住一切失败，只返回读成功的 */
  resolveWebKeys: (llmName: string | null) => Promise<Record<string, string>>;
  now: () => number;
  bootId: string;
  push: (env: Envelope) => void;
  diag: (s: string) => void;
  /** choosing || switching（🟡-11） */
  blocked: () => boolean;
  repoRoot: () => string | null;
  isActive: (epKey: string) => boolean;
  /** 该期此刻能否读对象库（capApprovals && reach === "ok"）；§2.7 第 4 条 */
  canReadApprovals: (epKey: string) => boolean;
  /** D153：无会话进程时 conv.models 的降级取列表（service 默认实现：PROBE_MODEL_LIST spawn）；测试注入 */
  listProxyModels: (repoRoot: string) => Promise<CommandResult>;
  /** 进程组清理与存活探测（§2.10） */
  killGroup: (pid: number) => void;
  groupAlive: (pid: number) => boolean;
  /** 会话状态有变时通知 service 重推 episodes.summary（徽标） */
  changed: () => void;
  timing?: Partial<SessionTiming>;
  /** D98：该叫人回来看的时刻（新审批卡、新停机点、回合结束）；main 在窗口不在前台时更新 Dock */
  attention?: (a: Attention) => void;
  /** A2：用户主动点击「设为默认」持久化的默认模型 / 推理强度，仅对新开会话生效 */
  defaultPrefs?: () => { model: string | null; effort: string | null };
  /** H8：回合结束时回调（用于刷新企划卷表等派生状态） */
  onTurnFinished?: (key: ConvKey) => void;
}

interface Pending<T> {
  resolve: (v: T) => void;
  reject: (e: SessionError) => void;
}

interface SettleSlot {
  turnId: string;
  stage: "await-stop-points" | "await-read";
  after: number;
}

interface SendWaiter extends Pending<{ turnId: string; queued?: boolean }> {
  text: string;
}

interface SessionState {
  /** 复合键 `${convKey}#${sid ?? "pending:<n>"}`——同 convKey 下可有多段（D157 多会话后台并存） */
  stateKey: string;
  key: ConvKey;
  generation: string;
  phase: ConvPhase;
  turnId: string | null;
  entries: ConvEntry[];
  bytes: number;
  truncatedHead: boolean;
  open: OutFrame[];
  openIds: Map<string, OutFrame>;
  framesLost: number;
  settle: SettleSlot | null;
  settleTimer: NodeJS.Timeout | null;
  keyProblem: string | null;
  /** D45：最近一帧携带的会话号（新会话首回合前为 null）；会话列表据此标出活会话 */
  sid: string | null;
  /** 当前会话已知模型名（ready / model 切换时记录，用于退出登录对应服务商时自动复位） */
  activeModel: string | null;
  /** A2：本段为全新会话，首次发消息时可套用用户主动保存的全局默认模型 / 推理强度 */
  applyDefaults: boolean;
  /** H9：回合进行期间若该服务商退出登录，延迟到 turn_finished 后立即复位模型 */
  pendingModelReset: boolean;
  proc: SessionProc | null;
  pid: number | null;
  splitter: LineSplitter;
  stderrTail: string;
  exitedCode: number | null;
  exitedSignal: string | null;
  readySeen: boolean;
  /** 同一进程内有效的回调代次：重启后旧回调丢弃 */
  procGen: number;
  seq: number;
  buffered: ConvEntry[];
  flushTimer: NodeJS.Timeout | null;
  readyWaiters: Pending<void>[];
  sendWaiters: Map<string, SendWaiter>;
  commandWaiters: Map<string, Pending<CommandResult>>;
  answerWaiters: Map<string, Pending<{ decision: string }>>;
  /** 同一 request_id 同时只允许一个在途答复 */
  answering: Set<string>;
  /** 在途答复对应的 feedback（写 answered_local 用） */
  answerFeedback: Map<string, string | null>;
  endWaiters: Pending<{ code: number | null; signal: string | null }>[];
}

/** core 会话命令闭集（`pipeline/agent/protocol.py::_COMMANDS` 里 renderer 能发的那几个；compact 只走对话框 /compact） */
export const COMMAND_NAMES = ["memory_ack", "scope", "models", "model", "thinking"] as const;
export type CommandName = (typeof COMMAND_NAMES)[number];
/** command_result：`models` 另带代理的模型列表、来源元信息与可选档位（D91） */
export interface CommandResult {
  ok: boolean;
  text: string;
  items?: string[];
  efforts?: string[];
  /** D168：每个模型支持的推理强度映射表 */
  model_efforts?: Record<string, string[]>;
  /** 模型来源元信息映射（区分同模型名来自哪家服务商/代理通道） */
  model_sources?: Record<string, ModelSourceInfo>;
  active_model_source?: ModelSourceInfo;
  /** D147：会话未启动时存下的预选（下一条 send 起进程后先下发） */
  pending?: boolean;
  /** D148：模型不在代理列表时 core 透传的提醒（不阻断） */
  warn?: string;
}

const STDERR_TAIL_BYTES = 8 * 1024;
const RID_RE = /^[A-Za-z0-9_-]{1,64}$/;

/** Spec 9 错误码 → host 错误码（冻结，§3.1）。 */
export function mapCoreError(code: string, message: string): SessionError {
  switch (code) {
    case "E_BUSY":
      return new SessionError("E_BUSY", message);
    case "E_STALE":
    case "E_UNKNOWN_REQUEST":
    case "E_REQUEST_CLOSED":
      return new SessionError("E_STALE", message);
    case "E_BAD_REQUEST":
      return new SessionError("E_BAD_REQUEST", message);
    case "E_SESSION_LOCKED":
      return new SessionError("E_SESSION_LOCKED", message);
    default:
      return new SessionError("E_SESSION", `${code}：${message}`);
  }
}

/** Spec 9 §3.6 退出码映射（ready 之前退出时用同一张表；TH-11b）。 */
function mapExitCode(code: number | null, signal: string | null): SessionError {
  if (code === 3) return new SessionError("E_SESSION_LOCKED", "该期已有活跃会话（可能是终端里的 ava）");
  if (code === 4) return new SessionError("E_UNREACHABLE", "期目录或 data/ 不可达");
  return new SessionError("E_SESSION", `会话进程已退出（code=${code ?? "-"} signal=${signal ?? "-"}）`);
}

function ridOf(f: OutFrame): string | null {
  const v = (f as Record<string, unknown>).rid;
  return typeof v === "string" && RID_RE.test(v) ? v : null;
}

/** §2.10：「忙」= 回合在跑或有未答卡。退出确认（quitState）与第 5 步（stopAllForQuit）共用，两边不许分叉。 */
function isBusyForQuit(s: Pick<SessionState, "turnId" | "open">): boolean {
  return s.turnId !== null || s.open.length > 0;
}

/** 结束序列的入站帧（TG-11：shutdown 的构造点恰 1 处）。 */
function SHUTDOWN_FRAME(): Record<string, unknown> {
  return { v: 1, t: "shutdown" };
}

/** D157：会话状态表键——`convKey` 下可挂多段（多会话后台并存），键 = `${convKey}#${sid ?? "pending:<n>"}`。
 * sid 未定的「pending」占位段在拿到真实 sid（session_start 帧）后重键。 */
function stateKeyOf(key: ConvKey, sid: string | null, pendingN: number): string {
  return `${key}#${sid ?? `pending:${pendingN}`}`;
}

export class SessionManager {
  private readonly timing: SessionTiming;
  /** D157：键为 `stateKeyOf` 的复合键，不再直接是 ConvKey */
  private readonly states = new Map<string, SessionState>();
  /** D157：每个 convKey 人正在看的那一段（focused）。send / snapshot / quitState 归到这个键 */
  private readonly focused = new Map<ConvKey, string>();
  /** D147：会话未启动（phase "none" / 无 proc）时预选的模型、推理强度与工作模式；send 起进程后先下发。 */
  private readonly pendingByKey = new Map<ConvKey, { model: string | null; effort: string | null; scope: "asset" | "auto" | null }>();
  /** 串行化同一 convKey 的 send 启动/预设/写帧临界区，防止并发 send 越过 flushPending 乱序发送 */
  private readonly sendChains = new Map<ConvKey, Promise<unknown>>();
  /** D98：每条会话上一帧 stop_points 的内容；停机点没变（每回合都会重发一帧）就不再叫人 */
  private readonly stopKeys = new Map<ConvKey, string>();
  private n = 0;
  private ridSeq = 0;
  private quitting = false;

  constructor(private readonly deps: SessionDeps) {
    this.timing = { ...DEFAULT_TIMING, ...deps.timing };
  }

  private fail(code: ErrCode, message: string): never {
    throw new SessionError(code, message);
  }

  /** D157：convKey 当前 focused 的那段（不限 phase——snapshot 要能看到 exited 与它的条目）。 */
  private focusedState(key: ConvKey): SessionState | null {
    const fk = this.focused.get(key);
    return fk ? this.states.get(fk) ?? null : null;
  }

  /** D157：focused 且还没退出的那段（写操作/懒启动用）。 */
  private activeFocused(key: ConvKey): SessionState | null {
    const s = this.focusedState(key);
    return s && s.phase !== "exited" && s.phase !== "none" ? s : null;
  }

  /** D157：convKey 下 sid 的活 state（没有 = 未起或已退出） */
  private stateOf(key: ConvKey, sid: string): SessionState | null {
    for (const s of this.states.values()) if (s.key === key && s.sid === sid && s.phase !== "exited") return s;
    return null;
  }

  // ---------------- 查询 ----------------

  snapshot(key: ConvKey): ConvSnapshot {
    const s = this.focusedState(key);
    if (!s) {
      return {
        convKey: key,
        generation: `${this.deps.bootId}:${this.n}`,
        seq: 0,
        phase: "none",
        turnId: null,
        entries: [],
        truncatedHead: false,
        open: [],
        framesLost: 0,
        settlePending: null,
        keyProblem: null,
        pending: this.pendingOf(key),
      };
    }
    return {
      convKey: key,
      generation: s.generation,
      seq: s.seq,
      phase: s.phase,
      turnId: s.turnId,
      entries: [...s.entries],
      truncatedHead: s.truncatedHead,
      open: [...s.open],
      framesLost: s.framesLost,
      settlePending: s.settle?.turnId ?? null,
      keyProblem: s.keyProblem,
      pending: this.pendingOf(key),
      sid: s.sid,
    };
  }

  /** D147：snapshot / delta 共用的 pending 视图；没有预选为 undefined（不进 JSON） */
  private pendingOf(key: ConvKey): ConvSnapshot["pending"] {
    const p = this.pendingByKey.get(key);
    if (!p || (p.model === null && p.effort === null && p.scope === null)) return undefined;
    return {
      ...(p.model !== null ? { model: p.model } : {}),
      ...(p.effort !== null ? { effort: p.effort } : {}),
      ...(p.scope !== null ? { scope: p.scope } : {}),
    };
  }

  /** D157：按 convKey 聚合（多段并存时任一活/任一 running、openRequests 求和） */
  summaries(): Map<ConvKey, { live: boolean; running: boolean; openRequests: number }> {
    const out = new Map<ConvKey, { live: boolean; running: boolean; openRequests: number }>();
    for (const s of this.states.values()) {
      const cur = out.get(s.key) ?? { live: false, running: false, openRequests: 0 };
      const c = convSummary({ phase: s.phase, open: s.open });
      cur.live = cur.live || c.live;
      cur.running = cur.running || c.running;
      cur.openRequests += c.openRequests;
      out.set(s.key, cur);
    }
    return out;
  }

  anyLive(): boolean {
    for (const s of this.states.values()) if (s.phase !== "exited" && s.phase !== "none") return true;
    return false;
  }

  /** §2.10 第 3/4 步：只列忙会话（与第 5 步同一判定）；全空闲时为空 → main 不弹确认框直接退。 */
  quitState(): QuitBusy[] {
    const out: QuitBusy[] = [];
    for (const s of this.states.values()) {
      if (s.phase === "exited" || s.phase === "none" || !isBusyForQuit(s)) continue;
      out.push({ convKey: s.key, label: s.key === "idea" ? "选题会话" : s.key.startsWith("proj:") ? `企划 ${s.key.slice(5)}` : s.key.slice(3), running: s.phase === "running", openRequests: s.open.length });
    }
    return out;
  }

  /** §2.7 第 3 条：service 在活跃期每次读对象库前取 readStart、读完并推完 episode.delta 后调用。 */
  onApprovalsRead(epKey: string, readStart: number): void {
    const s = this.focusedState(`ep:${epKey}` as ConvKey);
    if (!s?.settle || s.settle.stage !== "await-read") return;
    if (readStart <= s.settle.after) return;
    this.settleNow(s, false);
  }

  // ---------------- 对外方法 ----------------

  async send(key: ConvKey, target: SessionTarget, text: string): Promise<{ turnId: string; queued?: boolean; warn?: string }> {
    if (this.quitting) this.fail("E_BUSY", "正在退出");
    if (this.deps.blocked()) this.fail("E_BUSY", "正在切换仓库");
    if (text === "") this.fail("E_BAD_REQUEST", "消息为空");
    const prev = this.sendChains.get(key) ?? Promise.resolve();
    let release!: () => void;
    const gate = new Promise<void>((r) => {
      release = r;
    });
    this.sendChains.set(key, gate);
    let warn: string | null;
    let p: Promise<{ turnId: string; queued?: boolean }>;
    try {
      await prev;
      if (this.quitting) this.fail("E_BUSY", "正在退出");
      if (this.deps.blocked()) this.fail("E_BUSY", "正在切换仓库");
      // D157：发给当前 focused 那段；没有活段时 ensure 懒起一个（pending 或按 target.sid 恢复）
      const s = await this.ensure(key, target);
      this.focused.set(key, s.stateKey);
      if (s.phase === "starting") await this.waitReady(s);
      // D79（照 Pi 的 steering）：回合在跑时照写 user_message，core 排队并回 queued；工具批之后送达模型
      if (s.phase !== "idle" && s.phase !== "running") this.fail("E_BUSY", "该会话正在启动或收尾");
      // D147：会话启动前预选的设置——进程 ready 后、用户消息前依次下发；失败不拦消息，warn 如实带回
      warn = await this.flushPending(key, s);
      const rid = this.rid();
      const framed = { v: 1, t: "user_message", text, rid };
      this.precheck(framed);
      p = new Promise<{ turnId: string; queued?: boolean }>((resolve, reject) => s.sendWaiters.set(rid, { resolve, reject, text }));
      this.writeFrame(s, framed);
    } finally {
      release();
      if (this.sendChains.get(key) === gate) this.sendChains.delete(key);
    }
    const r = await this.race(p, this.timing.sendAckMs, "发送结果未知（超时）");
    return warn === null ? r : { ...r, warn };
  }

  /** D147：把 pending 依次写成 command 帧并等回执；任一失败收集文案、记入对话提示并照旧放行。返回拼接的 warn（无 = null）。
   * 帧构造走 `commandFrame`（TG-11：command 入站帧恰 1 个构造点）。 */
  private async flushPending(key: ConvKey, s: SessionState): Promise<string | null> {
    const pending = this.pendingByKey.get(key);
    const defs = s.applyDefaults ? this.deps.defaultPrefs?.() : undefined;
    s.applyDefaults = false;
    // "default" 哨兵表示新会话启动前用户显式恢复为仓库默认，不套用 settings.json 中的自定义默认
    const model = pending?.model === "default" ? null : pending?.model ?? defs?.model ?? null;
    const effort = pending?.effort === "default" ? null : pending?.effort ?? defs?.effort ?? null;
    const scope = pending?.scope ?? null;
    this.pendingByKey.delete(key);
    const items: ["model" | "thinking" | "scope", string][] = [];
    if (model !== null) items.push(["model", model]);
    if (effort !== null) items.push(["thinking", effort]);
    if (scope !== null) items.push(["scope", scope]);
    if (items.length === 0) return null;
    const labelOf = (name: "model" | "thinking" | "scope") =>
      name === "model" ? "模型" : name === "thinking" ? "推理强度" : "工作模式";
    const warns: string[] = [];
    for (const [name, arg] of items) {
      const rid = this.rid();
      try {
        const framed = this.commandFrame(name, arg, rid);
        this.precheck(framed);
        const p = new Promise<CommandResult>((resolve, reject) => s.commandWaiters.set(rid, { resolve, reject }));
        this.writeFrame(s, framed);
        const res = await this.race(p, this.timing.sendAckMs, `预设${labelOf(name)}结果未知（超时）`);
        if (res.ok && name === "model") s.activeModel = arg === "default" ? null : arg;
        if (!res.ok) warns.push(`预设${labelOf(name)} ${arg} 未生效：${res.text}`);
      } catch (e) {
        warns.push(`预设${labelOf(name)} ${arg} 未生效：${e instanceof Error ? e.message : String(e)}`);
      }
    }
    if (warns.length > 0) {
      const warnText = warns.join("；");
      this.append(s, {
        k: "frame",
        at: this.deps.now(),
        frame: { v: 1, t: "notice", seq: 0, sid: s.sid, level: "warn", code: "pending_preset", text: warnText },
      });
      this.flush(s, true);
      return warnText;
    }
    return null;
  }

  /** command 入站帧的唯一构造点（TG-11）：renderer 的 conv.command 与 D147 的 pending 下发共用。 */
  private commandFrame(name: CommandName, arg: string | null, rid: string): Record<string, unknown> {
    return { v: 1, t: "command", name, arg: name === "scope" || name === "model" || name === "thinking" ? arg : null, rid };
  }

  /** D157：resume 的语义 = 「把 focused 指到 target.sid 那段；没有活进程才懒起」。
   * 别的段活着不拦（多会话并存）；同一 sid 已有活进程也只切 focused、不新起。 */
  async resume(key: ConvKey, target: SessionTarget): Promise<ConvSnapshot> {
    if (this.quitting) this.fail("E_BUSY", "正在退出");
    if (this.deps.blocked()) this.fail("E_BUSY", "正在切换仓库");
    // 只要恢复/切进已有会话，就清掉空白页留下的未生效预选，避免把新开空白页的预选误套到历史会话上
    this.pendingByKey.delete(key);
    // idea 没有独立的 continue 模板：--idea 启动即恒恢复（Spec 18 §3.3）；恢复历史段一律不套用 A2 新会话默认
    const s = await this.ensure(key, { ...target, mode: target.mode === "idea" ? "idea" : "continue" }, false);
    this.focused.set(key, s.stateKey);
    return this.snapshot(key);
  }

  interrupt(key: ConvKey, turnId: string): void {
    if (this.quitting) this.fail("E_BUSY", "正在退出");
    const s = this.activeOf(key);
    if (!s || s.turnId !== turnId) this.fail("E_STALE", "该回合已结束或不匹配");
    this.writeFrame(s, { v: 1, t: "interrupt", turn_id: turnId });
  }

  async answer(key: ConvKey, requestId: string, decision: string, feedback: string | null): Promise<{ decision: string }> {
    if (this.quitting) this.fail("E_BUSY", "正在退出");
    // 冻结顺序（§4.2）：打开集合 → 在途 → 参数校验 → 活进程 → 写。
    // ADR-0030：browser / 抓取不再出卡，会话卡上的原生确认框（§2.4 第 5 层）随之撤掉
    const s = this.focusedState(key);
    const req = s?.openIds.get(requestId);
    if (!s || !req) this.fail("E_STALE", "该请求已关闭或不存在");
    if (s.answering.has(requestId)) this.fail("E_BUSY", "该请求已有答复在途");
    const options = Array.isArray(req.options) ? (req.options as unknown[]).map(String) : [];
    if (!options.includes(decision)) this.fail("E_BAD_REQUEST", `decision 不在选项里：${decision}`);
    const resolved = this.checkFeedback(req, decision, feedback);
    s.answering.add(requestId);
    try {
      if (!s.proc || s.phase === "exited") this.fail("E_SESSION", "会话进程未就绪或已退出");
      const rid = this.rid();
      const framed = { v: 1, t: "answer", request_id: requestId, decision, feedback: resolved, rid };
      this.precheck(framed);
      s.answerFeedback.set(requestId, resolved);
      const p = new Promise<{ decision: string }>((resolve, reject) => s.answerWaiters.set(rid, { resolve, reject }));
      this.writeFrame(s, framed);
      return await this.race(p, this.timing.answerAckMs, "答复结果未知（超时）");
    } finally {
      s.answering.delete(requestId);
    }
  }

  async command(key: ConvKey, name: CommandName, arg: string | null): Promise<CommandResult> {
    if (this.quitting) this.fail("E_BUSY", "正在退出");
    if (!(COMMAND_NAMES as readonly string[]).includes(name)) this.fail("E_BAD_REQUEST", `未知命令 ${String(name)}`);
    if (name === "scope") {
      if (arg !== "asset" && arg !== "auto") this.fail("E_BAD_REQUEST", "scope 命令需要 arg ∈ {asset, auto}");
    } else if (name === "model" || name === "thinking") {
      // D91：arg = 模型名 / 档位 / default；合法性由 core 判（模型名要对照代理列表）
      if (typeof arg !== "string" || arg.trim() === "" || arg.length > 200) this.fail("E_BAD_REQUEST", `${name} 命令需要非空 arg`);
    } else if (arg !== null) {
      this.fail("E_BAD_REQUEST", `${name} 不许带 arg`);
    }
    const live = this.activeFocused(key);
    // D153：models 是纯读列表——会话未启动时不需要进程，降级经 PROBE_MODEL_LIST 取代理列表（D147 曾在此置空；
    // host 不出网，实际 HTTP 在 core 探针里）。进程在时照旧透传 core（core 顺带返回 efforts，降级拿不到就缺省）。
    if ((!live || !live.proc) && name === "models") {
      const repoRoot = this.deps.repoRoot();
      if (!repoRoot) this.fail("E_UNREACHABLE", "仓库未就绪");
      return this.deps.listProxyModels(repoRoot);
    }
    // D147：会话未启动时 model / thinking / scope 存 pending（下一条 send 起进程后先下发）；其余命令仍要活进程。
    // 若用户配置了 A2 自定义默认，arg="default" 记录为 "default" 哨兵以便本新会话临时回退到仓库默认；否则清空 pending
    if ((!live || !live.proc) && (name === "model" || name === "thinking" || name === "scope") && arg !== null) {
      const defs = this.deps.defaultPrefs?.();
      const p = this.pendingByKey.get(key) ?? { model: null, effort: null, scope: null };
      let text: string;
      if (name === "model") {
        p.model = arg === "default" ? (defs?.model ? "default" : null) : arg;
        text = arg === "default" ? "已取消预设模型，将使用默认模型" : `会话启动后将使用模型 ${arg}`;
      } else if (name === "thinking") {
        p.effort = arg === "default" ? (defs?.effort ? "default" : null) : arg;
        text = arg === "default" ? "已取消预设推理强度，将跟随模型默认" : `会话启动后将使用推理强度 ${arg}`;
      } else {
        p.scope = arg === "asset" ? "asset" : null;
        text = arg === "asset" ? "会话启动后将进入素材模式" : "已取消素材模式预选";
      }
      if (p.model === null && p.effort === null && p.scope === null) this.pendingByKey.delete(key);
      else this.pendingByKey.set(key, p);
      this.pushSnapshot(key);
      return { ok: true, text, pending: true };
    }
    const s = live;
    if (!s || !s.proc) this.fail("E_SESSION", "当前还没有正在运行的会话");
    const rid = this.rid();
    const framed = this.commandFrame(name, arg, rid);
    this.precheck(framed);
    const p = new Promise<CommandResult>((resolve, reject) => s.commandWaiters.set(rid, { resolve, reject }));
    this.writeFrame(s, framed);
    const res = await this.race(p, this.timing.sendAckMs, "命令结果未知（超时）");
    if (res.ok && name === "model") s.activeModel = arg === "default" ? null : arg;
    return res;
  }

  /** §2.10「结束会话」：focused 段的 shutdown → 等 → SIGTERM → 宽限 → SIGKILL 整组。 */
  async end(key: ConvKey): Promise<{ code: number | null; signal: string | null }> {
    const s = this.focusedState(key);
    if (!s) return { code: null, signal: null };
    return this.endState(s);
  }

  /** 结束单个 SessionState（§2.10 结束序列主体）。 */
  private async endState(s: SessionState): Promise<{ code: number | null; signal: string | null }> {
    if (!s.proc || s.phase === "exited") return { code: s.exitedCode, signal: s.exitedSignal };
    s.phase = "ending";
    // H4：立刻向 renderer 同步 ending 状态，避免关闭进程宽限期内界面看起来无反应
    if (this.focused.get(s.key) === s.stateKey) this.pushSnapshot(s.key);
    this.deps.changed();
    this.writeFrame(s, SHUTDOWN_FRAME());
    const ended = await this.waitEnd(s, this.timing.endWaitMs);
    if (ended) return ended;
    // 只把 SIGTERM 发给会话 pid：同组里的 browser driver 会在会话处理器之前被杀
    s.proc.signal("SIGTERM", false);
    const grace = await this.waitEnd(s, this.timing.killGraceMs);
    if (grace) return grace;
    if (s.pid !== null) this.deps.killGroup(s.pid);
    return { code: null, signal: "SIGKILL" };
  }

  /**
   * Spec 18 §3.3 ③：建期带入前结束该键下的全部活会话（含 D157 后台并存段，core 要拿选题记录的租约）。
   * 走 §2.10 同一结束序列，之后再等进程真正退出。返回 false = 宽限内仍有进程未退出，调用方不许建期。
   */
  async endForMigration(key: ConvKey): Promise<boolean> {
    return this.endAllForKey(key);
  }

  /** D157：结束同一 convKey 下的所有活段（聚焦段 + 后台并存段），并等待它们全部退出。 */
  async endAllForKey(key: ConvKey): Promise<boolean> {
    const live = [...this.states.values()].filter((s) => s.key === key && s.phase !== "exited" && s.phase !== "none");
    if (live.length === 0) return true;
    await Promise.all(live.map((s) => this.endState(s)));
    const results = await Promise.all(
      live.map(async (s) => ((s.phase as ConvPhase) === "exited" ? true : (await this.waitEnd(s, this.timing.killGraceMs)) !== null)),
    );
    return results.every(Boolean);
  }

  /** 切仓前统一结束全部活会话（不置 quitting 标志，切完仓后继续接新请求）。 */
  async endAllForRepoSwitch(): Promise<boolean> {
    const live = [...this.states.values()].filter((s) => s.phase !== "exited" && s.phase !== "none");
    if (live.length === 0) return true;
    await Promise.all(live.map((s) => this.endState(s)));
    const results = await Promise.all(
      live.map(async (s) => ((s.phase as ConvPhase) === "exited" ? true : (await this.waitEnd(s, this.timing.killGraceMs)) !== null)),
    );
    return results.every(Boolean);
  }

  /** 切仓清空全部旧仓库会话状态与预选，并作废旧进程回调代次以防残留事件回流新仓库。 */
  clearAllForRepoSwitch(): void {
    const err = new SessionError("E_SESSION", "仓库已切换，原会话已终止");
    for (const s of this.states.values()) {
      s.procGen += 1;
      s.phase = "exited";
      s.proc = null;
      if (s.flushTimer) {
        clearTimeout(s.flushTimer);
        s.flushTimer = null;
      }
      if (s.settleTimer) {
        clearTimeout(s.settleTimer);
        s.settleTimer = null;
      }
      for (const w of s.readyWaiters.splice(0)) w.reject(err);
      for (const w of s.sendWaiters.values()) w.reject(err);
      s.sendWaiters.clear();
      for (const w of s.commandWaiters.values()) w.reject(err);
      s.commandWaiters.clear();
      for (const w of s.answerWaiters.values()) w.reject(err);
      s.answerWaiters.clear();
      for (const w of s.endWaiters.splice(0)) w.resolve({ code: null, signal: "SIGKILL" });
    }
    this.states.clear();
    this.focused.clear();
    this.pendingByKey.clear();
    this.stopKeys.clear();
  }

  /** 退出登录某个服务商后：清掉匹配的未启动预选；空闲会话立即复位默认模型，运行中会话记标记待回合结束复位。 */
  resetModelOverrideForProvider(providerId: string): void {
    const prefix = `${providerId}/`;
    const matches = (m: string | null) => m !== null && (m === providerId || m.startsWith(prefix));
    for (const [key, p] of [...this.pendingByKey]) {
      if (matches(p.model)) {
        p.model = null;
        if (p.model === null && p.effort === null && p.scope === null) this.pendingByKey.delete(key);
        this.pushSnapshot(key);
      }
    }
    for (const s of this.states.values()) {
      if (!s.proc || !matches(s.activeModel)) continue;
      if (s.phase === "running") {
        s.pendingModelReset = true;
        continue;
      }
      if (s.phase !== "idle") continue;
      this.sendDefaultModelReset(s);
    }
  }

  private sendDefaultModelReset(s: SessionState): void {
    if (!s.proc) return;
    const rid = this.rid();
    try {
      const framed = this.commandFrame("model", "default", rid);
      this.precheck(framed);
      s.commandWaiters.set(rid, {
        resolve: () => {
          s.activeModel = null;
        },
        reject: () => undefined,
      });
      this.writeFrame(s, framed);
    } catch {
      /* 复位失败不影响退出登录主流程 */
    }
  }

  /** D157：人正在看的那段（focused）的会话号；没有活进程、或新会话首回合前为 null。 */
  liveSid(key: ConvKey): string | null {
    const s = this.activeFocused(key);
    return s ? s.sid : null;
  }

  /** D157：某 sid 是否有活进程（删除/同段双开判定） */
  liveSidExists(key: ConvKey, sid: string): boolean {
    return this.stateOf(key, sid) !== null;
  }

  /** A2：清除自定义默认后，顺带清理已失去意义的 "default" 覆写哨兵并刷新快照。 */
  pruneStaleDefaultPending(): void {
    const defs = this.deps.defaultPrefs?.();
    for (const [key, p] of [...this.pendingByKey]) {
      let changed = false;
      if (p.model === "default" && !defs?.model) {
        p.model = null;
        changed = true;
      }
      if (p.effort === "default" && !defs?.effort) {
        p.effort = null;
        changed = true;
      }
      if (!changed) continue;
      if (p.model === null && p.effort === null && p.scope === null) this.pendingByKey.delete(key);
      this.pushSnapshot(key);
    }
  }

  /** D157：查询指定 sid 的运行态（包括非 focused 的后台并存段）。 */
  sidStatus(key: ConvKey, sid: string): { live: boolean; running: boolean; openCards: number } {
    const s = this.stateOf(key, sid);
    if (!s) return { live: false, running: false, openCards: 0 };
    return { live: true, running: s.phase === "running" || s.phase === "starting", openCards: s.open.length };
  }

  /** 有回合在跑（或正在启动/收尾）时为真——D157 起只反映 focused 段；UI 侧的锁改用 `liveSidExists` 判段。 */
  isBusy(key: ConvKey): boolean {
    const s = this.activeFocused(key);
    return s !== null && s.phase !== "idle";
  }

  /** D157 / A6：该 convKey 下任意活段（含后台并存段）在运行、启动、收尾或有未答卡时为真。 */
  hasAnyBusy(key: ConvKey): boolean {
    for (const s of this.states.values()) {
      if (s.key !== key || s.phase === "exited" || s.phase === "none") continue;
      if (s.phase !== "idle" || s.open.length > 0) return true;
    }
    return false;
  }

  /** 结束 focused 段并等它退出（§2.10 同一结束序列）。false = 宽限内仍未退出。
   * 要结束非 focused 段请调 `endSidAndWait`（多会话下删除别的段用）。 */
  async endAndWait(key: ConvKey): Promise<boolean> {
    const s = this.focusedState(key);
    if (!s || s.phase === "exited") return true;
    await this.endState(s);
    if ((s.phase as ConvPhase) === "exited") return true; // endState 期间 onExit 已把它改成 exited
    return (await this.waitEnd(s, this.timing.killGraceMs)) !== null;
  }

  /** D157：结束指定 sid 的段（删除别的会话时用——不先切 focused）。 */
  async endSidAndWait(key: ConvKey, sid: string): Promise<boolean> {    const s = this.stateOf(key, sid);
    if (!s || s.phase === "exited") return true;
    await this.endState(s);
    if ((s.phase as ConvPhase) === "exited") return true;
    return (await this.waitEnd(s, this.timing.killGraceMs)) !== null;
  }

  /**
   * Spec 18 §3.3 ④：选题记录已带进新期 → 清掉该会话已退出进程的条目缓冲（重推空快照）；
   * 下一次发消息懒启动全新进程。活进程不动（正常流程里此刻它已被 endForMigration 结束）。
   */
  resetAfterMigration(key: ConvKey): void {
    this.reset(key);
  }

  /** D157：「＋ 新会话」——只把 focused 指针清掉（旧段留在后台继续跑，下一条 send 起新段）。
   * 与 `reset` 的区别：reset 要求旧段已退出并丢缓冲；blur 不碰活进程。 */
  blur(key: ConvKey): void {
    this.focused.delete(key);
    this.pushSnapshot(key);
  }

  /** D157：删掉指定 sid 的已退出 state（删除会话后清场；活进程由 endSidAndWait 先结束）。 */
  dropSid(key: ConvKey, sid: string): void {
    for (const [sk, s] of [...this.states]) {
      if (s.key !== key || s.sid !== sid) continue;
      this.states.delete(sk);
      if (this.focused.get(key) === sk) this.focused.delete(key);
    }
    this.pushSnapshot(key);
  }

  /** 清掉 focused 段的条目缓冲（重推空快照）；下一次发消息懒启动全新进程（D45「开新会话」同此）。
   * D157：只清 focused 段；别的活段不受影响。 */
  reset(key: ConvKey): void {
    const fk = this.focused.get(key);
    const s = fk ? this.states.get(fk) : null;
    if (!s || s.phase !== "exited") return;
    this.states.delete(s.stateKey);
    this.focused.delete(key);
    this.pushSnapshot(key);
  }

  /** §2.10 退出第 5 步：空闲 shutdown、忙 SIGTERM、宽限后 SIGKILL 整组；此后拒绝新请求。 */
  async stopAllForQuit(): Promise<void> {
    this.quitting = true;
    const live = [...this.states.values()].filter((s) => s.phase !== "exited" && s.phase !== "none");
    for (const s of live) {
      s.phase = "ending";
      if (isBusyForQuit(s)) s.proc?.signal("SIGTERM", false);
      else this.writeFrame(s, SHUTDOWN_FRAME());
    }
    const deadline = Date.now() + this.timing.killGraceMs;
    while (live.some((s) => s.phase !== "exited") && Date.now() < deadline) {
      await new Promise<void>((r) => setTimeout(r, 20));
    }
    for (const s of live) {
      if (s.phase === "exited") continue;
      if (s.pid !== null) this.deps.killGroup(s.pid);
      this.finishExit(s, null, "SIGKILL");
    }
  }

  /** 返回 null = 未在超时内退出；否则是退出结果 */
  private waitEnd(s: SessionState, ms: number): Promise<{ code: number | null; signal: string | null } | null> {
    return new Promise((resolve) => {
      if (s.phase === "exited") return resolve({ code: s.exitedCode, signal: s.exitedSignal });
      const timer = setTimeout(() => resolve(null), ms);
      s.endWaiters.push({
        resolve: (v) => {
          clearTimeout(timer);
          resolve(v);
        },
        reject: () => {
          clearTimeout(timer);
          resolve(null);
        },
      });
    });
  }

  // ---------------- 生命周期 ----------------

  private activeOf(key: ConvKey): SessionState | null {
    return this.activeFocused(key);
  }

  /** D157：按 target 找/起一段。`target.sid` 有活进程 → 返回它（只切 focused）；无活进程 → 起新 state。
   * 新段的 stateKey 用 pending 占位（sid 未定）；`target.sid` 段用 `stateKeyOf(key, sid, 0)`。 */
  private async ensure(key: ConvKey, target: SessionTarget, allowDefaults = true): Promise<SessionState> {
    if (target.sid !== undefined) {
      const live = this.stateOf(key, target.sid);
      if (live) return live;
    } else {
      // 不带 sid：接着 focused 的活段（D157：多段并存时只有 focused 吃新消息）
      const cur = this.activeFocused(key);
      if (cur) return cur;
    }
    // D81：`proj:` 键一律走企划会话模板（新开 / 继续由 cont 决定），不按 mode 落到期会话模板
    const t: SessionTemplate = key.startsWith("proj:") ? "SESSION_PROJECT"
      : target.mode === "idea" ? "SESSION_IDEA" : target.mode === "continue" ? "SESSION_CONTINUE" : "SESSION_NEW";
    if (t !== "SESSION_IDEA" && !target.abs) this.fail("E_BAD_REQUEST", "缺少期目录");
    const repoRoot = this.deps.repoRoot();
    if (!repoRoot) this.fail("E_UNREACHABLE", "仓库未就绪");
    this.n += 1;
    const stateKey = stateKeyOf(key, target.sid ?? null, this.n);
    const s: SessionState = {
      stateKey,
      key,
      generation: `${this.deps.bootId}:${this.n}`,
      phase: "starting",
      turnId: null,
      entries: [],
      bytes: 0,
      truncatedHead: false,
      open: [],
      openIds: new Map(),
      framesLost: 0,
      settle: null,
      settleTimer: null,
      keyProblem: null,
      sid: null,
      activeModel: null,
      applyDefaults: allowDefaults && (target.mode === "new" || (target.mode === "idea" && target.sid === undefined)),
      pendingModelReset: false,
      proc: null,
      pid: null,
      splitter: new LineSplitter(SESSION_FRAME_MAX_BYTES),
      stderrTail: "",
      exitedCode: null,
      exitedSignal: null,
      readySeen: false,
      procGen: 0,
      seq: 0,
      buffered: [],
      flushTimer: null,
      readyWaiters: [],
      sendWaiters: new Map(),
      commandWaiters: new Map(),
      answerWaiters: new Map(),
      answering: new Set(),
      answerFeedback: new Map(),
      endWaiters: [],
    };
    this.states.set(stateKey, s);
    this.focused.set(key, stateKey); // 必须先切 focused 再 pushSnapshot，否则会推旧 exited 段的 generation 导致后续 delta 被全部丢弃
    this.pushSnapshot(key);
    // 密钥（§2.9）：任一步失败照常 spawn、如实降级
    const extraEnv: Record<string, string> = {};
    const keyInfo = await this.deps.resolveKey();
    if ("problem" in keyInfo) s.keyProblem = keyInfo.problem;
    else extraEnv[keyInfo.name] = keyInfo.value;
    // Spec 15 §2.7：web 密钥失败只是不注入该名字——LLM 密钥与 keyProblem 不受影响
    const webEnv = await this.deps.resolveWebKeys("problem" in keyInfo ? null : keyInfo.name);
    for (const [name, value] of Object.entries(webEnv)) if (!(name in extraEnv)) extraEnv[name] = value;
    if (s.phase === "exited") this.fail("E_SESSION", "会话进程已退出");
    const proc = this.deps.spawnSession(
      t,
      {
        ep: target.abs ?? undefined,
        ...(target.sid !== undefined ? { sid: target.sid } : {}),
        ...(target.fresh === true ? { fresh: true } : {}),
        ...(t === "SESSION_PROJECT" && target.mode === "continue" ? { cont: true } : {}),
      },
      { repoRoot },
      extraEnv,
    );
    const gen = (s.procGen += 1);
    s.proc = proc;
    s.pid = proc.pid ?? null;
    this.append(s, { k: "spawned", at: this.deps.now(), mode: target.mode, pid: s.pid ?? -1 });
    this.focused.set(key, stateKey); // D157：新起的段即当前段
    proc.onStdout((chunk) => {
      if (s.procGen === gen) this.onStdout(s, chunk);
    });
    proc.onStderr((chunk) => {
      if (s.procGen !== gen) return;
      const all = s.stderrTail + chunk.toString("utf-8");
      s.stderrTail = all.length > STDERR_TAIL_BYTES ? all.slice(-STDERR_TAIL_BYTES) : all;
    });
    proc.onExit((code, signal) => {
      if (s.procGen === gen) this.onExit(s, code, signal);
    });
    await this.waitReady(s);
    return s;
  }

  private waitReady(s: SessionState): Promise<void> {
    if (s.phase !== "starting") return Promise.resolve();
    return new Promise<void>((resolve, reject) => {
      const timer = setTimeout(() => reject(new SessionError("E_SESSION", "会话进程启动未就绪（超时）")), this.timing.readyMs);
      s.readyWaiters.push({
        resolve: () => {
          clearTimeout(timer);
          resolve();
        },
        reject: (e) => {
          clearTimeout(timer);
          reject(e);
        },
      });
    });
  }

  private onStdout(s: SessionState, chunk: Buffer): void {
    const r = s.splitter.push(chunk);
    if (r.overflow) this.lost(s, 1, "oversize");
    for (const line of r.lines) {
      if (Buffer.byteLength(line, "utf-8") > SESSION_FRAME_MAX_BYTES) {
        this.lost(s, 1, "oversize"); // 块内完整行的长度检查（🟡-8；MUT-15）
        continue;
      }
      const parsed = parseOutFrame(line);
      if (!parsed.ok) {
        this.lost(s, 1, "malformed");
        continue;
      }
      this.onFrame(s, parsed.frame);
    }
    this.flush(s);
  }

  private onFrame(s: SessionState, f: OutFrame): void {
    this.append(s, { k: "frame", at: this.deps.now(), frame: f });
    if (typeof f.sid === "string" && f.sid !== s.sid) {
      // D157：pending 段的 stateKey 用占位符，拿到真实 sid（session_start 帧）后重键；focused 一并迁
      const oldKey = s.stateKey;
      s.sid = f.sid;
      const newKey = stateKeyOf(s.key, f.sid, 0);
      if (oldKey !== newKey) {
        this.states.delete(oldKey);
        s.stateKey = newKey;
        this.states.set(newKey, s);
        if (this.focused.get(s.key) === oldKey) this.focused.set(s.key, newKey);
      }
    }
    switch (f.t) {
      case "ready":
        s.readySeen = true;
        if (typeof f.model === "string" && f.model.trim()) s.activeModel = f.model.trim();
        // A2：若 core 实际恢复了历史会话（例如裸 --idea 续接旧段），不得覆写历史会话的模型/推理强度
        if (f.continue_status === "resumed" || (typeof f.history_count === "number" && f.history_count > 0)) {
          s.applyDefaults = false;
        }
        if (s.phase === "starting") {
          s.phase = "idle";
          for (const w of s.readyWaiters.splice(0)) w.resolve();
        }
        break;
      case "turn_started": {
        s.phase = "running";
        s.turnId = String(f.turn_id);
        const rid = ridOf(f);
        const w = rid ? s.sendWaiters.get(rid) : undefined;
        if (rid && w) {
          s.sendWaiters.delete(rid);
          const safeText = /^\/login\s+\S/i.test(w.text.trim()) ? "/login [凭据参数已脱敏]" : w.text;
          this.append(s, { k: "user", at: this.deps.now(), text: safeText });
          w.resolve({ turnId: s.turnId });
        }
        break;
      }
      case "queued": {
        // D79：入队回执。用户行不在这里追加——送达模型时由 steer_delivered 帧折进时间线（convFold）
        const rid = ridOf(f);
        const w = rid ? s.sendWaiters.get(rid) : undefined;
        if (rid && w) {
          s.sendWaiters.delete(rid);
          w.resolve({ turnId: s.turnId ?? "", queued: true });
        }
        break;
      }
      case "turn_finished": {
        s.phase = "idle";
        s.turnId = null;
        this.registerSettle(s, String(f.turn_id));
        const kind = turnAttention(String(f.stopped));
        if (kind) this.deps.attention?.({ kind, key: `turn:${s.key}:${String(f.turn_id)}` });
        if (s.pendingModelReset) {
          s.pendingModelReset = false;
          this.sendDefaultModelReset(s);
        }
        this.deps.onTurnFinished?.(s.key);
        break;
      }
      case "stop_points": {
        const items = Array.isArray(f.items) ? f.items : [];
        const stopKey = stringifyLossless(items);
        if (items.length > 0 && this.stopKeys.get(s.key) !== stopKey) this.deps.attention?.({ kind: "wait", key: `stop:${s.key}:${stopKey}` });
        this.stopKeys.set(s.key, stopKey);
        const tid = f.turn_id;
        if (s.settle && tid !== null && tid === s.settle.turnId) {
          s.settle = { turnId: s.settle.turnId, stage: "await-read", after: this.deps.now() };
          if (!this.needsRead(s)) this.settleNow(s, false);
        }
        break;
      }
      case "request": {
        const id = String(f.request_id);
        if (!s.openIds.has(id)) {
          s.open.push(f);
          s.openIds.set(id, f);
          this.deps.attention?.({ kind: "wait", key: `req:${s.key}:${id}` });
        }
        break;
      }
      case "request_closed": {
        const id = String(f.request_id);
        s.open = s.open.filter((o) => o.request_id !== id);
        s.openIds.delete(id);
        const rid = ridOf(f);
        const w = rid ? s.answerWaiters.get(rid) : undefined;
        if (rid && w) {
          s.answerWaiters.delete(rid);
          this.append(s, { k: "answered_local", at: this.deps.now(), requestId: id, decision: String(f.decision ?? ""), feedback: s.answerFeedback.get(id) ?? null });
          w.resolve({ decision: String(f.decision ?? "") });
        }
        break;
      }
      case "command_result": {
        const rid = ridOf(f);
        const w = rid ? s.commandWaiters.get(rid) : undefined;
        if (rid && w) {
          s.commandWaiters.delete(rid);
          const items = Array.isArray(f.items) ? f.items.filter((x): x is string => typeof x === "string") : undefined;
          const efforts = Array.isArray(f.efforts) ? f.efforts.filter((x): x is string => typeof x === "string") : undefined;
          let model_efforts: Record<string, string[]> | undefined;
          if (f.model_efforts && typeof f.model_efforts === "object" && !Array.isArray(f.model_efforts)) {
            const cleaned: Record<string, string[]> = {};
            for (const [k, v] of Object.entries(f.model_efforts as Record<string, unknown>)) {
              if (Array.isArray(v)) cleaned[k] = v.filter((x): x is string => typeof x === "string");
            }
            model_efforts = cleaned;
          }
          const model_sources = parseModelSources(f.model_sources);
          const active_model_source = parseModelSourceInfo(f.active_model_source);
          w.resolve({
            ok: f.ok === true,
            text: String(f.text ?? ""),
            ...(items ? { items } : {}),
            ...(efforts ? { efforts } : {}),
            ...(model_efforts ? { model_efforts } : {}),
            ...(model_sources ? { model_sources } : {}),
            ...(active_model_source ? { active_model_source } : {}),
            ...(typeof f.warn === "string" ? { warn: f.warn } : {}),
          });
        }
        break;
      }
      case "error": {
        const rid = ridOf(f);
        if (rid) {
          const mapped = mapCoreError(String(f.code), String(f.message));
          const w = s.sendWaiters.get(rid) ?? s.commandWaiters.get(rid) ?? s.answerWaiters.get(rid);
          s.sendWaiters.delete(rid);
          s.commandWaiters.delete(rid);
          s.answerWaiters.delete(rid);
          w?.reject(mapped);
        }
        break;
      }
      case "bye":
        s.phase = "ending";
        break;
      default:
        break;
    }
  }

  private needsRead(s: SessionState): boolean {
    const epKey = s.key.startsWith("ep:") ? s.key.slice(3) : null;
    if (!epKey) return false;
    if (!this.deps.isActive(epKey)) return false; // 后台期不自动呼出：立即结算
    return this.deps.canReadApprovals(epKey); // 能力缺席或不可达：没有自动呼出可做，立即结算
  }

  private registerSettle(s: SessionState, turnId: string): void {
    if (s.settleTimer) clearTimeout(s.settleTimer);
    s.settle = { turnId, stage: "await-stop-points", after: this.deps.now() };
    s.settleTimer = setTimeout(() => {
      if (!s.settle) return;
      this.deps.diag(`回合结算超时（stage=${s.settle.stage}）`);
      this.settleNow(s, true);
    }, this.timing.settleMs);
  }

  private settleNow(s: SessionState, timedOut: boolean): void {
    const slot = s.settle;
    if (!slot) return;
    if (s.settleTimer) clearTimeout(s.settleTimer);
    s.settleTimer = null;
    s.settle = null;
    this.append(s, { k: "settled", at: this.deps.now(), turnId: slot.turnId, timedOut });
    this.flush(s);
  }

  /** 会话退出处理（冻结 §4.2）。 */
  private onExit(s: SessionState, code: number | null, signal: string | null): void {
    if (s.phase === "exited") return;
    this.finishExit(s, code, signal);
  }

  private finishExit(s: SessionState, code: number | null, signal: string | null): void {
    s.phase = "exited";
    s.turnId = null;
    s.exitedCode = code;
    s.exitedSignal = signal;
    if (s.open.length > 0) {
      const ids = s.open.map((o) => String(o.request_id));
      s.open = [];
      s.openIds.clear();
      this.append(s, { k: "voided_local", at: this.deps.now(), requestIds: ids, cause: "session_exited" });
    }
    this.append(s, { k: "exited", at: this.deps.now(), code, signal, stderrTail: s.stderrTail });
    const err = s.readySeen ? new SessionError("E_SESSION", `会话进程已退出（code=${code ?? "-"} signal=${signal ?? "-"}）`) : mapExitCode(code, signal);
    for (const w of s.sendWaiters.values()) w.reject(err);
    for (const w of s.commandWaiters.values()) w.reject(err);
    for (const w of s.answerWaiters.values()) w.reject(err);
    for (const w of s.readyWaiters) w.reject(err);
    s.sendWaiters.clear();
    s.commandWaiters.clear();
    s.answerWaiters.clear();
    s.readyWaiters = [];
    if (s.settle && s.settle.stage === "await-stop-points") {
      s.settle = { turnId: s.settle.turnId, stage: "await-read", after: this.deps.now() };
      if (!this.needsRead(s)) this.settleNow(s, false);
    }
    if (s.pid !== null && this.deps.groupAlive(s.pid)) this.deps.killGroup(s.pid); // 进程组清理（§2.10）
    for (const w of s.endWaiters.splice(0)) w.resolve({ code, signal });
    this.flush(s, true);
  }

  // ---------------- 推送 ----------------

  private append(s: SessionState, e: ConvEntry): void {
    const isDelta = e.k === "frame" && e.frame.t === "assistant_delta";
    if (isDelta) {
      const turnId = String(e.frame.turn_id ?? "");
      const chunk = String(e.frame.text ?? "");
      const prev = s.entries[s.entries.length - 1];
      if (prev && prev.k === "frame" && prev.frame.t === "assistant_delta" && String(prev.frame.turn_id ?? "") === turnId) {
        const merged: ConvEntry = {
          ...prev,
          at: e.at,
          frame: { ...prev.frame, seq: e.frame.seq, text: `${String(prev.frame.text ?? "")}${chunk}` },
        };
        const oldBytes = Buffer.byteLength(stringifyLossless(prev), "utf-8");
        const newBytes = Buffer.byteLength(stringifyLossless(merged), "utf-8");
        s.entries[s.entries.length - 1] = merged;
        s.bytes = Math.max(0, s.bytes - oldBytes) + newBytes;
      } else {
        s.entries.push(e);
        s.bytes += Buffer.byteLength(stringifyLossless(e), "utf-8");
      }
      const prevBuf = s.buffered[s.buffered.length - 1];
      if (prevBuf && prevBuf.k === "frame" && prevBuf.frame.t === "assistant_delta" && String(prevBuf.frame.turn_id ?? "") === turnId) {
        s.buffered[s.buffered.length - 1] = {
          ...prevBuf,
          at: e.at,
          frame: { ...prevBuf.frame, seq: e.frame.seq, text: `${String(prevBuf.frame.text ?? "")}${chunk}` },
        };
      } else {
        s.buffered.push(e);
      }
    } else {
      if (e.k === "frame" && e.frame.t === "assistant" && String(e.frame.kind ?? "") !== "local_note") {
        // 最终 assistant 帧落盘后，裁剪环形缓冲尾部同回合已被取代的 assistant_delta，防止长回复撑爆缓冲
        const turnId = String(e.frame.turn_id ?? "");
        for (let i = s.entries.length - 1; i >= 0; i -= 1) {
          const item = s.entries[i];
          if (item.k !== "frame") break;
          if (item.frame.t === "assistant_delta" && String(item.frame.turn_id ?? "") === turnId) {
            s.entries.splice(i, 1);
            s.bytes = Math.max(0, s.bytes - Buffer.byteLength(stringifyLossless(item), "utf-8"));
            continue;
          }
          if (item.frame.t === "notice" || item.frame.t === "log") continue;
          break;
        }
      }
      s.entries.push(e);
      s.bytes += Buffer.byteLength(stringifyLossless(e), "utf-8");
      s.buffered.push(e);
    }
    while (s.bytes > CONV_BUFFER_MAX_BYTES && s.entries.length > 1) {
      const dropped = s.entries.shift()!;
      s.bytes = Math.max(0, s.bytes - Buffer.byteLength(stringifyLossless(dropped), "utf-8"));
      s.truncatedHead = true;
    }
  }

  private lost(s: SessionState, count: number, reason: "oversize" | "malformed"): void {
    s.framesLost += count;
    this.append(s, { k: "frames_lost", at: this.deps.now(), count, reason });
  }

  private flush(s: SessionState, now = false): void {
    if (this.timing.coalesceMs === 0 || now) return this.flushNow(s);
    if (s.flushTimer) return;
    s.flushTimer = setTimeout(() => {
      s.flushTimer = null;
      this.flushNow(s);
    }, this.timing.coalesceMs);
  }

  private flushNow(s: SessionState): void {
    if (s.flushTimer) {
      clearTimeout(s.flushTimer);
      s.flushTimer = null;
    }
    if (s.buffered.length === 0) return;
    const entries = s.buffered;
    s.buffered = [];
    s.seq += 1;
    // D157：只有当前 focused 段向 renderer 推送 conv.delta；后台并存段不向同 convKey 串推 delta（切换回来时拉 snapshot 即可）
    if (this.focused.get(s.key) === s.stateKey) {
      this.deps.push({
        v: 1,
        kind: "push",
        topic: "conv.delta",
        convKey: s.key,
        generation: s.generation,
        seq: s.seq,
        data: {
          convKey: s.key,
          generation: s.generation,
          seq: s.seq,
          entries,
          phase: s.phase,
          turnId: s.turnId,
          open: [...s.open],
          keyProblem: s.keyProblem,
          pending: this.pendingOf(s.key),
          sid: s.sid,
        },
      });
    }
    // 纯流式文本增量不影响侧栏 phase / open / pending 摘要，跳过 20 Hz 无意义全量广播
    if (entries.some((e) => !(e.k === "frame" && e.frame.t === "assistant_delta"))) {
      this.deps.changed();
    }
  }

  private pushSnapshot(key: ConvKey): void {
    const snap = this.snapshot(key);
    this.deps.push({ v: 1, kind: "push", topic: "conv.snapshot", convKey: key, generation: snap.generation, seq: 0, data: snap });
  }

  // ---------------- 入站帧 ----------------

  private writeFrame(s: SessionState, frame: Record<string, unknown>): void {
    if (!s.proc) this.fail("E_SESSION", "会话进程未就绪或已退出");
    s.proc.write(`${stringifyLossless(frame)}\n`);
  }

  private precheck(frame: Record<string, unknown>): void {
    if (Buffer.byteLength(stringifyLossless(frame), "utf-8") > MAX_INBOUND_LINE_BYTES) this.fail("E_BAD_REQUEST", "消息内容过大（超过 1 MB 上限）");
  }

  private rid(): string {
    this.ridSeq += 1;
    return `r${this.ridSeq}`;
  }

  private checkFeedback(req: OutFrame, decision: string, feedback: string | null): string | null {
    if (feedback === null || feedback === undefined) return null;
    if (typeof feedback !== "string") this.fail("E_BAD_REQUEST", "反馈说明必须是文本");
    const allowed = req.kind === "tool_call" && decision === "reject" && req.feedback_allowed === true;
    if (!allowed) this.fail("E_BAD_REQUEST", "该请求不支持附加反馈说明");
    if (feedback.trim() === "") this.fail("E_BAD_REQUEST", "反馈说明不能为空");
    if (Buffer.byteLength(feedback, "utf-8") > FEEDBACK_MAX_BYTES) this.fail("E_BAD_REQUEST", "反馈说明过长");
    return feedback;
  }

  /** 在途答复等待期间进程退出 → E_SESSION（finishExit 直接 reject）；超时 → E_TIMEOUT（结果未知）。 */
  private race<T>(p: Promise<T>, ms: number, timeoutMessage: string): Promise<T> {
    return new Promise<T>((resolve, reject) => {
      const timer = setTimeout(() => reject(new SessionError("E_TIMEOUT", timeoutMessage)), ms);
      p.then(
        (v) => {
          clearTimeout(timer);
          resolve(v);
        },
        (e) => {
          clearTimeout(timer);
          reject(e);
        },
      );
    });
  }
}
