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
import { convSummary } from "../shared/convFold";
import { parseOutFrame, type OutFrame } from "../shared/convFrames";
import { LineSplitter } from "../shared/jsonl";
import { stringifyLossless } from "../shared/losslessJson";
import { needsNativeConfirm, renderConfirmDetail } from "../shared/nativeConfirm";
import type { ConvEntry, ConvKey, ConvPhase, ConvSnapshot, Envelope, ErrCode } from "../shared/protocol";
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
  spawnSession: (t: SessionTemplate, args: { ep?: string }, ctx: { repoRoot: string }, extraEnv: Record<string, string>) => SessionProc;
  /** 每次 spawn SESSION_* 前执行一次（§2.9）；失败给 problem 文案，照常 spawn、如实降级 */
  resolveKey: () => Promise<{ name: string; value: string } | { problem: string }>;
  /** → main 的 confirm-query（§2.4 第 5 层） */
  confirm: (title: string, detail: string) => Promise<boolean>;
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
  /** 进程组清理与存活探测（§2.10） */
  killGroup: (pid: number) => void;
  groupAlive: (pid: number) => boolean;
  /** 会话状态有变时通知 service 重推 episodes.summary（徽标） */
  changed: () => void;
  timing?: Partial<SessionTiming>;
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

interface SendWaiter extends Pending<{ turnId: string }> {
  text: string;
}

interface SessionState {
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
  commandWaiters: Map<string, Pending<{ ok: boolean; text: string }>>;
  answerWaiters: Map<string, Pending<{ decision: string }>>;
  /** 同一 request_id 同时只允许一个在途答复 */
  answering: Set<string>;
  /** 在途答复对应的 feedback（写 answered_local 用） */
  answerFeedback: Map<string, string | null>;
  endWaiters: Pending<{ code: number | null; signal: string | null }>[];
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

export class SessionManager {
  private readonly timing: SessionTiming;
  private readonly states = new Map<ConvKey, SessionState>();
  private n = 0;
  private ridSeq = 0;
  private quitting = false;

  constructor(private readonly deps: SessionDeps) {
    this.timing = { ...DEFAULT_TIMING, ...deps.timing };
  }

  private fail(code: ErrCode, message: string): never {
    throw new SessionError(code, message);
  }

  // ---------------- 查询 ----------------

  snapshot(key: ConvKey): ConvSnapshot {
    const s = this.states.get(key);
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
    };
  }

  summaries(): Map<ConvKey, { live: boolean; running: boolean; openRequests: number }> {
    const out = new Map<ConvKey, { live: boolean; running: boolean; openRequests: number }>();
    for (const [key, s] of this.states) out.set(key, convSummary({ phase: s.phase, open: s.open }));
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
      out.push({ convKey: s.key, label: s.key === "idea" ? "选题会话" : s.key.slice(3), running: s.phase === "running", openRequests: s.open.length });
    }
    return out;
  }

  /** §2.7 第 3 条：service 在活跃期每次读对象库前取 readStart、读完并推完 episode.delta 后调用。 */
  onApprovalsRead(epKey: string, readStart: number): void {
    const s = this.states.get(`ep:${epKey}` as ConvKey);
    if (!s?.settle || s.settle.stage !== "await-read") return;
    if (readStart <= s.settle.after) return;
    this.settleNow(s, false);
  }

  // ---------------- 对外方法 ----------------

  async send(key: ConvKey, target: SessionTarget, text: string): Promise<{ turnId: string }> {
    if (this.quitting) this.fail("E_BUSY", "正在退出");
    if (this.deps.blocked()) this.fail("E_BUSY", "正在切换仓库");
    if (text === "") this.fail("E_BAD_REQUEST", "消息为空");
    const s = await this.ensure(key, target);
    if (s.phase === "starting") await this.waitReady(s);
    if (s.phase !== "idle") this.fail("E_BUSY", "该会话有回合在跑");
    const rid = this.rid();
    const framed = { v: 1, t: "user_message", text, rid };
    this.precheck(framed);
    const p = new Promise<{ turnId: string }>((resolve, reject) => s.sendWaiters.set(rid, { resolve, reject, text }));
    this.writeFrame(s, framed);
    return this.race(p, this.timing.sendAckMs, "发送结果未知（超时）");
  }

  async resume(key: ConvKey, target: SessionTarget): Promise<ConvSnapshot> {
    if (this.quitting) this.fail("E_BUSY", "正在退出");
    if (this.deps.blocked()) this.fail("E_BUSY", "正在切换仓库");
    const cur = this.states.get(key);
    if (cur && cur.phase !== "exited") this.fail("E_BUSY", "该会话已有活进程");
    await this.ensure(key, { ...target, mode: "continue" });
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
    // 冻结顺序（§4.2）：打开集合 → 在途 → 参数校验 → 原生确认 → 复查打开集合 → 活进程 → 写
    const s = this.states.get(key);
    const req = s?.openIds.get(requestId);
    if (!s || !req) this.fail("E_STALE", "该请求已关闭或不存在");
    if (s.answering.has(requestId)) this.fail("E_BUSY", "该请求已有答复在途");
    const options = Array.isArray(req.options) ? (req.options as unknown[]).map(String) : [];
    if (!options.includes(decision)) this.fail("E_BAD_REQUEST", `decision 不在选项里：${decision}`);
    const resolved = this.checkFeedback(req, decision, feedback);
    s.answering.add(requestId);
    try {
      if (needsNativeConfirm(req, decision)) {
        const { title, detail } = renderConfirmDetail(req);
        const ok = await this.deps.confirm(title, detail);
        if (!ok) this.fail("E_STALE", "已在确认框取消");
        if (!s.openIds.has(requestId)) this.fail("E_STALE", "该请求已关闭或不存在"); // 等待期间进程退出（打开集合已清空）
      }
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

  async command(key: ConvKey, name: "memory_ack" | "scope", arg: "asset" | "auto" | null): Promise<{ ok: boolean; text: string }> {
    if (this.quitting) this.fail("E_BUSY", "正在退出");
    if (name !== "memory_ack" && name !== "scope") this.fail("E_BAD_REQUEST", `未知命令 ${String(name)}`);
    if (name === "scope") {
      if (arg !== "asset" && arg !== "auto") this.fail("E_BAD_REQUEST", "scope 命令需要 arg ∈ {asset, auto}");
    } else if (arg !== null) {
      this.fail("E_BAD_REQUEST", "memory_ack 不许带 arg");
    }
    const s = this.activeOf(key);
    if (!s || !s.proc) this.fail("E_SESSION", "该会话没有活进程");
    const rid = this.rid();
    const framed = { v: 1, t: "command", name, arg: name === "scope" ? arg : null, rid };
    this.precheck(framed);
    const p = new Promise<{ ok: boolean; text: string }>((resolve, reject) => s.commandWaiters.set(rid, { resolve, reject }));
    this.writeFrame(s, framed);
    return this.race(p, this.timing.sendAckMs, "命令结果未知（超时）");
  }

  /** §2.10「结束会话」：shutdown → 等 SESSION_END_WAIT_MS → SIGTERM 会话 pid → 宽限 → SIGKILL 整组。 */
  async end(key: ConvKey): Promise<{ code: number | null; signal: string | null }> {
    const s = this.states.get(key);
    if (!s || !s.proc || s.phase === "exited") return { code: s?.exitedCode ?? null, signal: s?.exitedSignal ?? null };
    s.phase = "ending";
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
    const s = this.states.get(key);
    return s && s.phase !== "exited" && s.phase !== "none" ? s : null;
  }

  private async ensure(key: ConvKey, target: SessionTarget): Promise<SessionState> {
    const existing = this.states.get(key);
    if (existing && existing.phase !== "exited") return existing;
    const t: SessionTemplate = target.mode === "idea" ? "SESSION_IDEA" : target.mode === "continue" ? "SESSION_CONTINUE" : "SESSION_NEW";
    if (t !== "SESSION_IDEA" && !target.abs) this.fail("E_BAD_REQUEST", "缺少期目录");
    const repoRoot = this.deps.repoRoot();
    if (!repoRoot) this.fail("E_UNREACHABLE", "仓库未就绪");
    this.n += 1;
    const s: SessionState = {
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
    this.states.set(key, s);
    this.pushSnapshot(key);
    // 密钥（§2.9）：任一步失败照常 spawn、如实降级
    const extraEnv: Record<string, string> = {};
    const keyInfo = await this.deps.resolveKey();
    if ("problem" in keyInfo) s.keyProblem = keyInfo.problem;
    else extraEnv[keyInfo.name] = keyInfo.value;
    if (s.phase === "exited") this.fail("E_SESSION", "会话进程已退出");
    const proc = this.deps.spawnSession(t, { ep: target.abs ?? undefined }, { repoRoot }, extraEnv);
    const gen = (s.procGen += 1);
    s.proc = proc;
    s.pid = proc.pid ?? null;
    this.append(s, { k: "spawned", at: this.deps.now(), mode: target.mode, pid: s.pid ?? -1 });
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
    switch (f.t) {
      case "ready":
        s.readySeen = true;
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
          this.append(s, { k: "user", at: this.deps.now(), text: w.text });
          w.resolve({ turnId: s.turnId });
        }
        break;
      }
      case "turn_finished":
        s.phase = "idle";
        s.turnId = null;
        this.registerSettle(s, String(f.turn_id));
        break;
      case "stop_points": {
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
          w.resolve({ ok: f.ok === true, text: String(f.text ?? "") });
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
    s.entries.push(e);
    s.bytes += Buffer.byteLength(stringifyLossless(e), "utf-8");
    while (s.bytes > CONV_BUFFER_MAX_BYTES && s.entries.length > 1) {
      const dropped = s.entries.shift()!;
      s.bytes -= Buffer.byteLength(stringifyLossless(dropped), "utf-8");
      s.truncatedHead = true;
    }
    s.buffered.push(e);
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
    this.deps.push({
      v: 1,
      kind: "push",
      topic: "conv.delta",
      convKey: s.key,
      generation: s.generation,
      seq: s.seq,
      data: { convKey: s.key, generation: s.generation, seq: s.seq, entries, phase: s.phase, turnId: s.turnId, open: [...s.open], keyProblem: s.keyProblem },
    });
    this.deps.changed();
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
    if (Buffer.byteLength(stringifyLossless(frame), "utf-8") > MAX_INBOUND_LINE_BYTES) this.fail("E_BAD_REQUEST", "帧超过 1 MiB 上限");
  }

  private rid(): string {
    this.ridSeq += 1;
    return `r${this.ridSeq}`;
  }

  private checkFeedback(req: OutFrame, decision: string, feedback: string | null): string | null {
    if (feedback === null || feedback === undefined) return null;
    if (typeof feedback !== "string") this.fail("E_BAD_REQUEST", "feedback 必须是字符串");
    const allowed = req.kind === "tool_call" && decision === "reject" && req.feedback_allowed === true;
    if (!allowed) this.fail("E_BAD_REQUEST", "该请求不接受 feedback");
    if (feedback.trim() === "") this.fail("E_BAD_REQUEST", "feedback 不能为空");
    if (Buffer.byteLength(feedback, "utf-8") > FEEDBACK_MAX_BYTES) this.fail("E_BAD_REQUEST", "feedback 超长");
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
