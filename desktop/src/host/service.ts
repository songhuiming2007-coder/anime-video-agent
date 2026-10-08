// host 服务：RPC 分发、期订阅、events.jsonl 轮询、状态/对象库读取、heal 调度（Spec 8 §2.3–§2.5、§2.12、§3.2）。
// I1：本模块对 data/ 零写入、零 mkdir。全部 spawn 经 spawner.ts；全部文件读经 fsio。
import * as fs from "node:fs";
import { join } from "node:path";
import type { ApprovalRecord, EventRecord, Reach } from "../shared/contracts";
import {
  ACTIVE_POLL_MS,
  BACKGROUND_POLL_MS,
  EPISODE_LIST_REFRESH_MS,
  EPISODE_STATUS_CONCURRENCY,
  IMPORT_COVER_MAX_BYTES,
  MAX_PARTIAL_BYTES,
  REACH_POLL_MS,
  SAVE_SCRIPT_MAX_BYTES,
  SESSION_ID_RE,
  STATUS_REFRESH_MS,
  VOICE_PARSE_MAX_BYTES,
} from "../shared/constants";
import { degradedNoticesOf, foldEvents, goneRunningJobIds, parseEventLine } from "../shared/fold";
import { LineSplitter } from "../shared/jsonl";
import { losslessSelfCheck, parseLossless, stringifyLossless, toWireApproval, toWireEvent } from "../shared/losslessJson";
import { parseVoiceInfo } from "../shared/voiceInfo";
import { epochSeconds, reduceHumanTime, type HumanTimeRecord } from "../shared/humanTime";
import { HumanTimers, type FlushReason } from "./humanTime";
import { relPathProblem } from "../shared/mediaUrl";
import { repoRootProblem } from "../shared/repoRoot";
import {
  checkParamKeys,
  isMethod,
  type DecideParams,
  PROTOCOL_VERSION,
  type EpisodeDelta,
  type EpisodeSnapshot,
  type EpisodeSummary,
  type EpisodesList,
  type Envelope,
  type ErrCode,
  type Health,
  type JobView,
  type Method,
  type Provenance,
  type RpcError,
  type SessionRow,
  type SnapshotApprovals,
  type ShotsEntry,
  type SnapshotStatus,
  type TreeEntry,
  type ConvKey,
  type ConvSnapshot,
  type CreatedEpisode,
  parseFromIdeaMarker,
  type FingerJson,
  type ImportedCoverJson,
  type SavedFingerprintJson,
  type ScriptStatJson,
  type TimeReadJson,
  type VoiceCheckJson,
  type VoiceInfoJson,
  type VoicePatchJson,
} from "../shared/protocol";
import { decide, DecideFail, parseDecideParams } from "./decide";
import { errnoOf, realFs, type FsRead } from "./fsio";
import { listEpisodes, type EpisodeEntry } from "./episodes";
import { h5Step, HealScheduler, newH5State, type H5State, type HealExecutor, type HealTrigger } from "./heal";
import { diagnoseDataRoot } from "./reach";
import { loadSettings, saveSettings } from "./settings";
import { killGroup, groupAlive, pythonOf, runCore, spawnSession, type CoreResult, type SpawnTag, type Template, type TemplateArgs, type VoiceReadingArgs, REVIEW_RE } from "./spawner";
import { resolveLlmKey, resolveWebKeys, type KeyResolution } from "./secrets";
import { SessionError, SessionManager, type QuitBusy, type SessionTarget, type SessionTiming } from "./sessions";
import { fetchStatus } from "./status";
import { readApprovalRecords } from "./store";
import { pollOnce, resyncState, type TailState } from "./tailer";

export interface HostConfig {
  userData: string;
  isPackaged: boolean;
  appPath: string;
  devRepoRoot: string | null;
}

export interface HostDeps {
  fs: FsRead;
  now: () => number;
  pidAlive: (pid: number) => boolean;
  fetchStatus: (repoRoot: string, epAbs: string) => Promise<SnapshotStatus>;
  selfCheck: () => boolean;
  /** undefined = 真实 HEAL spawn（能力在场时）；null = 永不 spawn；函数 = 测试注入 */
  healExecutor: HealExecutor | null | undefined;
  runCore: <T extends Template>(t: T, args: TemplateArgs[T], ctx: { repoRoot: string }, tag?: SpawnTag, stdinData?: string | Uint8Array) => Promise<CoreResult>;
  /** main 的原生对话框选择 + 二次确认（§2.10）；返回确认后的 repoRoot，取消为 null */
  chooseRepoRoot: () => Promise<string | null>;
  /** browser/抓取卡「批准」前的原生确认框（Spec 10 §2.4 第 5 层）；true = 人点了批准 */
  confirm: (title: string, detail: string) => Promise<boolean>;
  /** 会话进程启动时的随机串（Spec 10 🟡-9）：generation = "<bootId>:<n>" */
  bootId: string;
  /** 密钥解析（§2.9）；默认走 PROBE_KEY_ENV + 钥匙串，测试可注入 */
  resolveSessionKey: (repoRoot: string) => Promise<KeyResolution>;
  /** web 检索密钥（Spec 15 §2.7）；默认走 PROBE_WEB_KEY_ENVS + 钥匙串，测试可注入 */
  resolveSessionWebKeys: (repoRoot: string, llmName: string | null) => Promise<Record<string, string>>;
  /** 会话定时器初值（测试注入缩短） */
  sessionTiming: Partial<SessionTiming>;
  /** 仅未打包构建（TG-6）：测试驱动在此暂停 host（§4.3 after-heal / after-fingerprint-check 等） */
  testHook: (name: string) => Promise<void>;
  /** false = 不启动定时器（测试逐次驱动 tick） */
  timers: boolean;
}

export class RpcFail extends Error {
  constructor(
    readonly code: ErrCode,
    message: string,
    readonly tails: { stdoutTail?: string; stderrTail?: string } = {},
  ) {
    super(message);
  }
}

interface EpisodeRuntime {
  epKey: string;
  abs: string;
  tail: TailState | null;
  splitter: LineSplitter;
  events: EventRecord[];
  malformed: number;
  episodeFieldMismatch: number;
  truncatedHead: boolean;
  eventsState: "absent" | "ok";
  prevMissing: Set<string>;
  jobsKey: string;
  jobs: JobView[];
  seq: number;
  status: SnapshotStatus;
  statusKey: string;
  approvals: SnapshotApprovals;
  approvalsKey: string;
  /** 本次对象库读取的**开始**时刻（打开文件之前取）；§2.7 结算用它，不用读完时刻 */
  approvalsReadStart: number | null;
  approvalRecords: ApprovalRecord[];
  lastGoodApprovals: ApprovalRecord[] | null;
  healedAt: string | null;
  // 活跃期专属
  h2Baseline: string | null;
  h2BaselineTaken: boolean;
  h5: H5State;
  lastActiveTick: number;
  lastStatusTick: number;
  /** tick 在途：setInterval 不等上一次异步 tick 跑完，重叠会让同一批新事件按旧 before 下发两次（S22 🟡-2） */
  ticking: boolean;
  /** 在途 tick 结束时兑现；载入据此等 tick 跑完 */
  tickDone: Promise<void> | null;
  /**
   * 载入（订阅/激活/刷新/重取快照/重新挂载）与 tick 互斥（S22，经用户同意）：快照把 seq 置 0 并包含已读事件，
   * 与之交错的 tick 会按旧 before 把同一批事件以 seq 1 再下发一次。载入先等在途 tick、彼此排队；有载入时 tick 跳过。
   */
  loading: number;
  loadChain: Promise<void>;
}

const HIDDEN_TREE = (name: string) => name === ".DS_Store" || name.endsWith(".tmp") || name.endsWith(".lock");

function defaultPidAlive(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch (e) {
    return errnoOf(e) === "EPERM"; // 进程存在但无权发信号
  }
}

export function validateRepoRoot(repoRoot: string): string | null {
  return repoRootProblem(repoRoot, {
    readText: (p) => {
      try {
        return fs.readFileSync(p, "utf-8");
      } catch {
        return null;
      }
    },
    isExecutable: (p) => {
      try {
        fs.accessSync(p, fs.constants.X_OK);
        return true;
      } catch {
        return false;
      }
    },
    exists: (p) => fs.existsSync(p),
  });
}

export class HostService {
  readonly deps: HostDeps;
  readonly heal: HealScheduler;
  repoRoot: string | null = null;
  repoRootProblem: string | null = null;
  dataRoot: string | null = null;
  losslessJson = false;
  capApprovals = false;
  capDetail = "未探测";
  codeFreeze: Health["codeFreeze"] = { ok: null, detail: "未探测" };
  repoHead: string | null = null;
  provenance: Provenance = { kind: "unknown", message: "尚未探测" };
  reach: Reach = "missing";
  reachDetail = "";
  diagnostics: string[] = [];
  episodes = new Map<string, EpisodeEntry>();
  hiddenUnderscore = 0;
  summaries = new Map<string, Omit<EpisodeSummary, "conv">>();
  /**
   * 刚建好、带入了选题记录、尚未起过会话的期（Spec 18 §3.3 ②，人 2026-10-08 裁决「host 侧一次性标记」）：
   * 该期首次 conv.send 以 SESSION_CONTINUE 起进程，接着迁入段聊——语义等同「先 resume 后 send」，
   * renderer 仍只发一次 conv.send（TG-10 不动），host 不代发任何消息（H-8 不动）。
   */
  private carried = new Set<string>();
  subs = new Map<string, EpisodeRuntime>();
  active: string | null = null;
  /** repoRoot 代号：每次确认切换 +1；只读 spawn 的结果若代号已过期则丢弃（§2.10，红队 R3 m4） */
  repoGen = 0;
  /** 确认切换之后、换好之前：拒绝新 decide（E_BUSY）、不启动新 heal */
  switching = false;
  /** main 的对话框在途（选择与二次确认之间） */
  private choosing = false;
  /** 在途 spawn（含只读）：切换等它们全部结束再换 */
  private inflight = new Set<Promise<unknown>>();
  /** 期内 ack 互斥（同期 ack 串行）；单实例锁保证全机唯一，跨进程由 Spec 3 按期 flock 兜底 */
  private acking = new Set<string>();
  private decideSeq = 0;
  private started = false;
  private timers: NodeJS.Timeout[] = [];
  private send: (env: Envelope) => void = () => {};
  /** 会话管理（Spec 10 §2.1、§4.2） */
  readonly sessions: SessionManager;
  /** 人时计时器（Spec 11 §2.4/§4.3）：审阅面可见即计时，flush 时 spawn RECORD_TIME */
  readonly humanTimers: HumanTimers;
  /** 退出第 5 步开始后拒绝建期等新请求（Spec 10 §2.10、二轮 🔵-6） */
  private sessionQuitting = false;

  constructor(
    readonly cfg: HostConfig,
    deps: Partial<HostDeps> = {},
    private readonly onReach: (r: Reach) => void = () => {},
    private readonly onDataRoot: (dataRoot: string | null) => void = () => {},
  ) {
    this.deps = {
      fs: realFs,
      now: Date.now,
      pidAlive: defaultPidAlive,
      fetchStatus,
      selfCheck: () => losslessSelfCheck(),
      healExecutor: undefined,
      runCore,
      chooseRepoRoot: async () => null,
      confirm: async () => false,
      bootId: Math.random().toString(36).slice(2, 10),
      resolveSessionKey: (root: string) => resolveLlmKey(this.core.bind(this) as Parameters<typeof resolveLlmKey>[0], root),
      resolveSessionWebKeys: (root: string, llmName: string | null) =>
        resolveWebKeys(this.core.bind(this) as Parameters<typeof resolveWebKeys>[0], root, llmName, (m) => this.diag(m)),
      sessionTiming: {},
      testHook: async () => {},
      timers: true,
      ...deps,
    };
    this.heal = new HealScheduler(null, (epKey, r) => this.diag(`heal 失败（${epKey}）：${r.stderrTail.trim().slice(-400)}`));
    this.humanTimers = new HumanTimers(
      () => this.deps.now(),
      (epKey, iv, reason) => this.recordHumanTime(epKey, iv, reason),
      (m) => this.diag(m),
    );
    this.sessions = new SessionManager({
      spawnSession,
      resolveKey: () => this.deps.resolveSessionKey(this.repoRoot ?? ""),
      resolveWebKeys: (llmName) => this.deps.resolveSessionWebKeys(this.repoRoot ?? "", llmName),
      confirm: (title, detail) => this.deps.confirm(title, detail),
      now: () => this.deps.now(),
      bootId: this.deps.bootId,
      push: (env) => this.push(env),
      diag: (m) => this.diag(m),
      blocked: () => this.choosing || this.switching,
      repoRoot: () => this.repoRoot,
      isActive: (epKey) => this.active === epKey,
      canReadApprovals: (epKey) => this.capApprovals && this.reach === "ok" && this.episodes.has(epKey),
      killGroup,
      groupAlive,
      changed: () => this.push({ v: 1, kind: "push", topic: "episodes.summary", data: this.episodesList() }),
      timing: this.deps.sessionTiming,
    });
  }

  /** 默认 HEAL executor：spawn `ava <期> /approvals`（§3.4），退出码必须 0，输出丢弃。 */
  private realHeal: HealExecutor = async (epKey, trigger, decideId) => {
    const e = this.episodes.get(epKey);
    if (!this.repoRoot || !e) return { ok: false, stderrTail: `未知期 ${epKey}` };
    const r = await this.core("HEAL", { ep: e.abs }, { trigger, ...(decideId !== undefined ? { decide: decideId } : {}) });
    return { ok: r.code === 0 && !r.timedOut, stderrTail: r.stderrTail };
  };

  /** 全部 spawn 的唯一出口：登记在途，供 repoRoot 切换等待。 */
  private core<T extends Template>(t: T, args: TemplateArgs[T], tag: SpawnTag = {}, stdinData?: string | Uint8Array): Promise<CoreResult> {
    const repoRoot = this.repoRoot;
    if (!repoRoot) return Promise.resolve({ code: null, signal: null, stdoutTail: "", stderrTail: "无仓库", timedOut: false, stdoutFull: null, stdoutOverflow: false });
    return this.track(this.deps.runCore(t, args, { repoRoot }, tag, stdinData));
  }

  private track<T>(p: Promise<T>): Promise<T> {
    this.inflight.add(p);
    const done = () => this.inflight.delete(p);
    p.then(done, done);
    return p;
  }

  /** 仅未打包构建：测试驱动在此暂停 host（TG-6） */
  private async testHook(name: string): Promise<void> {
    if (this.cfg.isPackaged) return;
    await this.deps.testHook(name);
  }

  // ---------------- 生命周期 ----------------

  async start(): Promise<void> {
    this.losslessJson = this.deps.selfCheck();
    if (!this.losslessJson) this.diag("JSON.parse 不支持 reviver 的 context.source：纳秒指纹无法无损解析，approvals 能力按缺席处理（§3.1 规则 8）");
    this.resolveRepoRoot();
    this.pollReach();
    this.refreshEpisodes();
    await this.probe();
    this.started = true;
    if (this.deps.timers) {
      this.timers.push(setInterval(() => this.pollReach(), REACH_POLL_MS));
      this.timers.push(setInterval(() => this.tickActive(), ACTIVE_POLL_MS));
      this.timers.push(setInterval(() => this.tickBackground(), BACKGROUND_POLL_MS));
      this.timers.push(setInterval(() => void this.refreshEpisodeStatuses(), EPISODE_LIST_REFRESH_MS));
      void this.refreshEpisodeStatuses();
    }
  }

  stop(): void {
    // 退出时 best-effort 结算在途区间（Spec 11 §2.4）：detached 的 RECORD_TIME 会跑完；失败则丢失该段。
    this.humanTimers.flushAll("quit");
    for (const t of this.timers) clearInterval(t);
    this.timers = [];
  }

  attach(send: (env: Envelope) => void): void {
    this.send = send;
  }

  /** renderer 重载或 host 重连：丢弃全部订阅，等 renderer 重新 subscribe */
  resetRenderer(): void {
    this.subs.clear();
    this.active = null;
    this.send = () => {};
  }

  private diag(msg: string): void {
    this.diagnostics.push(`${new Date(this.deps.now()).toISOString()} ${msg}`);
    if (this.diagnostics.length > 200) this.diagnostics.splice(0, this.diagnostics.length - 200);
    this.push({ v: 1, kind: "push", topic: "diag", data: msg });
  }

  private push(env: Envelope): void {
    try {
      this.send(env);
    } catch (e) {
      this.diagnostics.push(`push 失败：${e instanceof Error ? e.message : String(e)}`);
    }
  }

  resolveRepoRoot(): void {
    const settings = loadSettings(this.cfg.userData);
    let candidate: string | null = settings?.repoRoot ?? null;
    if (!candidate && !this.cfg.isPackaged) {
      // 未打包构建：测试开关，否则 desktop/ 所在的仓库（开发便利；打包版只认 settings）
      candidate = this.cfg.devRepoRoot ?? join(this.cfg.appPath, "..");
    }
    if (!candidate) {
      this.repoRoot = null;
      this.repoRootProblem = "尚未选择仓库（settings.json 缺失）";
      this.dataRoot = null;
      return;
    }
    const problem = validateRepoRoot(candidate);
    this.repoRoot = problem ? null : candidate;
    this.repoRootProblem = problem;
    this.dataRoot = problem ? null : join(candidate, "data");
  }

  async probe(): Promise<void> {
    const repoRoot = this.repoRoot;
    if (!repoRoot) {
      this.capApprovals = false;
      this.capDetail = this.repoRootProblem ?? "无仓库";
      this.heal.setExecutor(null);
      this.repoHead = null;
      this.provenance = await this.computeProvenance(); // 打包版无仓库时也要给出溯源判定，不能停在初值
      return;
    }
    const gen = this.repoGen;
    const [appr, freeze, head] = await Promise.all([this.core("PROBE_APPROVALS", {}), this.core("PROBE_FREEZE", {}), this.core("GIT_HEAD", {})]);
    if (gen !== this.repoGen) return; // 代号已过期：结果丢弃
    const probeOk = appr.code === 0;
    this.capApprovals = probeOk && this.losslessJson;
    this.capDetail = !probeOk
      ? "core 尚未提供 approval 对象层（Spec 3 未施工）"
      : !this.losslessJson
        ? "运行时不支持无损解析纳秒指纹（假设 10），决策条只读"
        : "ok";
    this.heal.setExecutor(this.capApprovals ? (this.deps.healExecutor === undefined ? this.realHeal : this.deps.healExecutor) : null);
    this.codeFreeze =
      freeze.code === 0
        ? { ok: true, detail: "pipeline/ 无未提交改动" }
        : { ok: false, detail: freeze.code === 3 ? "pipeline/ 存在未提交改动或 git 检查异常（Code Freeze WARN）" : `探针失败：${freeze.stderrTail.trim().slice(-300)}` };
    this.repoHead = head.code === 0 ? head.stdoutTail.trim() : null;
    this.provenance = await this.computeProvenance();
  }

  private async computeProvenance(): Promise<Provenance> {
    if (!this.cfg.isPackaged) return { kind: "dev" };
    let info: { gitHead?: unknown; desktopDirty?: unknown; lockfileSha256?: unknown };
    try {
      info = JSON.parse(fs.readFileSync(join(this.cfg.appPath, "out/build-info.json"), "utf-8"));
    } catch {
      return { kind: "unknown", message: "build-info.json 缺失" };
    }
    const gitHead = typeof info.gitHead === "string" ? info.gitHead : "";
    if (info.desktopDirty === true) return { kind: "dirty", gitHead };
    if (!this.repoRoot || !this.repoHead) return { kind: "unknown", message: "无法读取当前仓库 HEAD" };
    if (gitHead !== this.repoHead) {
      const d = await this.core("GIT_DESKTOP_DIFF", { buildHead: gitHead });
      if (d.code === 128) return { kind: "incomparable", gitHead };
      if (d.code !== 0) return { kind: "stale", gitHead };
    }
    try {
      const bytes = fs.readFileSync(join(this.repoRoot, "desktop/package-lock.json"));
      const digest = Buffer.from(await globalThis.crypto.subtle.digest("SHA-256", bytes)).toString("hex");
      if (digest !== info.lockfileSha256) return { kind: "stale", gitHead };
    } catch {
      return { kind: "stale", gitHead };
    }
    return { kind: "consistent", gitHead };
  }

  pollReach(): void {
    const prev = this.reach;
    if (!this.dataRoot) {
      this.reach = "missing";
      this.reachDetail = this.repoRootProblem ?? "无仓库";
    } else {
      const r = diagnoseDataRoot(this.dataRoot, this.deps.fs);
      this.reach = r.reach;
      this.reachDetail = r.detail;
    }
    if (prev !== this.reach) {
      this.onReach(this.reach);
      this.push({ v: 1, kind: "push", topic: "reach", data: { reach: this.reach, detail: this.reachDetail } });
      if (this.reach === "ok" && this.started) void this.remount(); // 启动时的首次可达由 start() 自己探针
    }
  }

  /** 重新挂载（§2.8）：全部 tail resync（generation 单调 +1，不归零）、重新探针、重新 snapshot。 */
  async remount(): Promise<void> {
    this.refreshEpisodes();
    await this.probe();
    for (const rt of [...this.subs.values()]) {
      await this.withLoad(rt, async () => {
        rt.tail = resyncState(rt.tail);
        rt.splitter.reset();
        rt.events = [];
        rt.malformed = 0;
        rt.episodeFieldMismatch = 0;
        rt.truncatedHead = false;
        await this.pushSnapshot(rt);
      });
    }
  }

  refreshEpisodes(): void {
    if (!this.dataRoot || this.reach !== "ok") return;
    try {
      const { visible, hiddenUnderscore } = listEpisodes(join(this.dataRoot, "episodes"), this.deps.fs);
      this.episodes = new Map(visible.map((e) => [e.epKey, e]));
      this.hiddenUnderscore = hiddenUnderscore;
    } catch (e) {
      this.diag(`期列表读取失败：${errnoOf(e) ?? String(e)}`);
    }
  }

  async refreshEpisodeStatuses(): Promise<void> {
    if (!this.repoRoot || this.reach !== "ok") return;
    if (this.switching) return; // N39：切换窗口内代号已 +1 而仓库根还是旧的，此时起跑会拿「新代号 + 旧根」读；切换收尾会自己再刷一次
    this.refreshEpisodes();
    const queue = [...this.episodes.values()];
    const repoRoot = this.repoRoot;
    const gen = this.repoGen;
    const worker = async () => {
      for (let e = queue.shift(); e; e = queue.shift()) {
        const st = await this.track(this.deps.fetchStatus(repoRoot, e.abs));
        if (gen !== this.repoGen) return; // 代号已过期：结果丢弃
        this.summaries.set(e.epKey, {
          epKey: e.epKey,
          mtimeMs: e.mtimeMs,
          currentStep: st.ok ? st.value.current_step : null,
          isBlocked: st.ok ? st.value.is_blocked : null,
        });
      }
    };
    await Promise.all(Array.from({ length: EPISODE_STATUS_CONCURRENCY }, worker));
    if (gen !== this.repoGen) return;
    this.push({ v: 1, kind: "push", topic: "episodes.summary", data: this.episodesList() });
  }

  episodesList(): EpisodesList {
    const convs = this.sessions.summaries();
    return {
      reach: this.reach,
      reachDetail: this.reachDetail,
      hiddenUnderscore: this.hiddenUnderscore,
      episodes: [...this.episodes.values()].map((e) => ({
        ...(this.summaries.get(e.epKey) ?? { epKey: e.epKey, mtimeMs: e.mtimeMs, currentStep: null, isBlocked: null }),
        conv: convs.get(`ep:${e.epKey}` as ConvKey) ?? null,
      })),
      idea: convs.get("idea") ?? null,
    };
  }

  health(): Health {
    return {
      protocolVersion: PROTOCOL_VERSION,
      isPackaged: this.cfg.isPackaged,
      repoRoot: this.repoRoot,
      repoRootProblem: this.repoRootProblem,
      repoHead: this.repoHead,
      python: this.repoRoot ? pythonOf(this.repoRoot) : null,
      losslessJson: this.losslessJson,
      capabilities: { approvals: this.capApprovals, approvalsDetail: this.capDetail },
      codeFreeze: this.codeFreeze,
      buildProvenance: this.provenance,
      reach: this.reach,
      reachDetail: this.reachDetail,
      diagnostics: this.diagnostics.slice(-50),
    };
  }

  // ---------------- RPC ----------------

  async handle(env: Envelope): Promise<void> {
    if (env.kind !== "req") return;
    const reply = (r: { ok: true; result: unknown } | { ok: false; error: RpcError }) =>
      this.push(r.ok ? { v: 1, kind: "res", id: env.id, ok: true, result: r.result } : { v: 1, kind: "res", id: env.id, ok: false, error: r.error });
    if (env.v !== PROTOCOL_VERSION) {
      reply({ ok: false, error: { code: "E_BAD_REQUEST", message: `协议版本不符：${String(env.v)}` } });
      return;
    }
    try {
      const result = await this.dispatch(env.method, env.params);
      reply({ ok: true, result });
    } catch (e) {
      if (e instanceof RpcFail) reply({ ok: false, error: { code: e.code, message: e.message, ...e.tails } });
      else reply({ ok: false, error: { code: "E_BAD_REQUEST", message: e instanceof Error ? e.message : String(e) } });
    }
  }

  async dispatch(method: Method, params: unknown): Promise<unknown> {
    try {
      return await this.dispatchOnce(method, params);
    } catch (e) {
      if (e instanceof DecideFail) throw new RpcFail(e.error.code, e.error.message);
      if (e instanceof SessionError) throw new RpcFail(e.code, e.message);
      throw e;
    }
  }

  private async dispatchOnce(method: Method, params: unknown): Promise<unknown> {
    if (!isMethod(method)) throw new RpcFail("E_BAD_REQUEST", `未知方法 ${String(method)}`);
    const keyProblem = checkParamKeys(method, params);
    if (keyProblem) throw new RpcFail("E_BAD_REQUEST", keyProblem);
    const p = (params ?? {}) as Record<string, string>;
    switch (method) {
      case "app.health":
        return this.health();
      case "app.requestRepoRootChange":
        return this.requestRepoRootChange();
      case "episodes.list":
        this.refreshEpisodes();
        return this.episodesList();
      case "episode.subscribe":
        return this.subscribe(this.epOf(p.epKey));
      case "episode.activate":
        return this.activate(this.epOf(p.epKey));
      case "episode.unsubscribe":
        this.subs.delete(this.epOf(p.epKey).epKey);
        if (this.active === p.epKey) this.active = null;
        return null;
      case "episode.resnapshot": {
        const rt = this.runtimeOf(this.epOf(p.epKey));
        return this.withLoad(rt, async () => this.snapshotOf(rt));
      }
      case "episode.refresh":
        return this.refresh(this.epOf(p.epKey));
      case "tree.list":
        return this.treeList(this.epOf(p.epKey), p.relDir);
      case "shots.list":
        return this.shotsList();
      case "approval.decide":
        return this.decide(parseDecideParams(params as Record<string, unknown>));
      case "episode.create":
        return this.createEpisode(p.name);
      case "conv.send":
        return this.convSend(p.convKey, p.text);
      case "conv.resume":
        return this.convResume(p.convKey);
      case "conv.interrupt":
        this.sessions.interrupt(p.convKey as ConvKey, p.turnId);
        return null;
      case "conv.answer":
        return this.sessions.answer(p.convKey as ConvKey, p.requestId, p.decision, p.feedback ?? null);
      case "conv.command":
        return this.sessions.command(p.convKey as ConvKey, p.name as "memory_ack" | "scope", (p.arg as "asset" | "auto" | undefined) ?? null);
      case "conv.end":
        return this.sessions.end(p.convKey as ConvKey);
      case "conv.snapshot":
        return this.sessions.snapshot(p.convKey as ConvKey);
      case "conv.sessions":
        return this.convSessions(p.convKey);
      case "conv.enter":
        return this.convEnter(p.convKey, p.sid);
      case "conv.fresh":
        return this.convFresh(p.convKey);
      case "conv.delete":
        return this.convDelete(p.convKey, p.sid);
      // ---- Spec 11 §4.3/§4.4 + Spec 12 §4.2 ----
      case "script.stat":
        return this.scriptStat(this.epForIo(p.epKey));
      case "script.save":
        return this.scriptSave(this.epForIo(p.epKey), p.text, p.expectSize, p.expectMtimeNs);
      case "script.seal":
        return this.scriptSeal(this.epForIo(p.epKey));
      case "script.check":
        return this.scriptCheck(this.epForIo(p.epKey));
      case "voice.info":
        return this.voiceInfo(this.epForIo(p.epKey));
      case "voice.parse":
        return this.voiceParse(this.epForIo(p.epKey), p.text);
      case "voice.add":
        return this.voiceAdd(this.epForIo(p.epKey), p.text);
      case "voice.revert":
        return this.voiceRevert(this.epForIo(p.epKey), p.label);
      case "voice.retract":
        return this.voiceRetract(this.epForIo(p.epKey), p.id);
      case "voice.applyPatch":
        return this.voiceApplyPatch(this.epForIo(p.epKey));
      case "voice.check":
        return this.voiceCheck(this.epForIo(p.epKey), readingOf(p));
      case "voice.global":
        return this.voiceGlobal(this.epForIo(p.epKey), readingOf(p), p.supersede === "true");
      case "voice.retts":
        return this.voiceReTts(this.epForIo(p.epKey));
      case "voice.review":
        return this.voiceReview(this.epForIo(p.epKey), p.review);
      case "time.surface":
        return this.timeSurface(this.epForIo(p.epKey), p.stop, p.visible === "true");
      case "time.read":
        return this.timeRead(this.epForIo(p.epKey));
      case "cover.import":
        return this.coverImport(this.epForIo(p.epKey), p.name, (params as Record<string, unknown>).bytes);
      default:
        throw new RpcFail("E_BAD_REQUEST", `未知方法 ${String(method)}`);
    }
  }

  private epOf(epKey: string): EpisodeEntry {
    const e = this.episodes.get(epKey);
    if (!e) throw new RpcFail("E_BAD_REQUEST", `未知期 ${epKey}`);
    return e;
  }

  private runtimeOf(e: EpisodeEntry): EpisodeRuntime {
    let rt = this.subs.get(e.epKey);
    if (!rt) {
      rt = {
        epKey: e.epKey,
        abs: e.abs,
        tail: null,
        splitter: new LineSplitter(MAX_PARTIAL_BYTES),
        events: [],
        malformed: 0,
        episodeFieldMismatch: 0,
        truncatedHead: false,
        eventsState: "absent",
        prevMissing: new Set(),
        jobsKey: "",
        jobs: [],
        seq: 0,
        status: { ok: false, code: "E_STALE", message: "尚未读取" },
        statusKey: "",
        approvals: { state: "absent" },
        approvalsKey: "",
        approvalsReadStart: null,
        approvalRecords: [],
        lastGoodApprovals: null,
        healedAt: null,
        h2Baseline: null,
        h2BaselineTaken: false,
        h5: newH5State(),
        lastActiveTick: 0,
        lastStatusTick: 0,
        ticking: false,
        tickDone: null,
        loading: 0,
        loadChain: Promise.resolve(),
      };
      this.subs.set(e.epKey, rt);
    }
    return rt;
  }

  /** 载入类操作的唯一入口：等在途 tick 跑完、与其他载入排队；期间 tick 跳过。 */
  private async withLoad<T>(rt: EpisodeRuntime, fn: () => Promise<T>): Promise<T> {
    rt.loading += 1;
    const prev = rt.loadChain;
    let release!: () => void;
    rt.loadChain = new Promise<void>((r) => (release = r));
    try {
      await prev;
      while (rt.tickDone) await rt.tickDone;
      return await fn();
    } finally {
      rt.loading -= 1;
      release();
    }
  }

  async subscribe(e: EpisodeEntry): Promise<EpisodeSnapshot> {
    const rt = this.runtimeOf(e);
    return this.withLoad(rt, async () => {
      await this.loadAll(rt);
      return this.snapshotOf(rt);
    });
  }

  /** H1：设为活跃期（首次打开、从后台切回、重连后恢复活跃）。重置 H2/H5 基线。 */
  async activate(e: EpisodeEntry): Promise<EpisodeSnapshot> {
    const rt = this.runtimeOf(e);
    this.active = e.epKey;
    return this.withLoad(rt, async () => {
      rt.h2Baseline = null;
      rt.h2BaselineTaken = false;
      rt.h5 = newH5State();
      await this.healAndReload(rt, "H1-activated");
      await this.loadAll(rt);
      this.checkH2(rt); // 这次 status 采样即 H2 基线，不触发
      return this.snapshotOf(rt);
    });
  }

  /** H3：用户点击刷新（仅活跃期）。 */
  async refresh(e: EpisodeEntry): Promise<EpisodeSnapshot> {
    const rt = this.runtimeOf(e);
    return this.withLoad(rt, async () => {
      if (this.active === e.epKey) await this.healAndReload(rt, "H3-user-refresh");
      await this.loadAll(rt);
      // 刷新本身已 heal：工序若在此期间变化，只吸收进 H2 基线，不再补一次 H2
      if (rt.h2BaselineTaken && rt.status.ok) rt.h2Baseline = rt.status.value.current_step;
      return this.snapshotOf(rt);
    });
  }

  private async healAndReload(rt: EpisodeRuntime, trigger: HealTrigger): Promise<void> {
    if (!this.capApprovals) return; // 能力缺席：heal 不 spawn（§2.6 闸 1）
    if (this.switching) return; // 切换中不启动新 heal（§2.10）
    const r = await this.heal.requestHeal(rt.epKey, trigger);
    if (r.ok) rt.healedAt = new Date(this.deps.now()).toISOString();
  }

  /** 首载：分块读完 events.jsonl（块间让出事件循环）、status、对象库（活跃期）。 */
  async loadAll(rt: EpisodeRuntime): Promise<void> {
    for (;;) {
      const more = this.pollEvents(rt);
      if (!more) break;
      await new Promise<void>((r) => setImmediate(r));
    }
    await this.loadStatus(rt);
    this.loadApprovals(rt);
    this.refold(rt);
  }

  /** 驱动一次 pollOnce；返回是否还有未读字节。resync 时丢弃已折叠事件。 */
  pollEvents(rt: EpisodeRuntime): boolean {
    const file = `${rt.abs}/events.jsonl`;
    const r = pollOnce(rt.tail, file, this.deps.fs, rt.splitter);
    if (r.unreachable) return false; // 保留上次快照（标陈旧由 reach/期列表体现），不创建任何东西
    if (r.resynced) {
      rt.events = [];
      rt.malformed = 0;
      rt.episodeFieldMismatch = 0;
      rt.truncatedHead = false;
    }
    if (r.overflow) this.diag(`events.jsonl 出现超过 ${MAX_PARTIAL_BYTES} 字节仍无换行的内容，已丢弃并跳到下一行（${rt.epKey}）`);
    if (r.truncatedHead) rt.truncatedHead = true;
    rt.tail = r.state;
    rt.eventsState = r.absent ? "absent" : "ok";
    for (const line of r.newLines) {
      const parsed = parseEventLine(line);
      if (!parsed.ok) {
        rt.malformed += 1;
        continue;
      }
      // 事件归属真相 = 文件位置；行内 episode 字段不一致只计数
      if (parsed.ev.episode !== rt.epKey) rt.episodeFieldMismatch += 1;
      rt.events.push(parsed.ev);
    }
    return r.more;
  }

  private async loadStatus(rt: EpisodeRuntime): Promise<void> {
    if (!this.repoRoot) {
      rt.status = { ok: false, code: "E_UNREACHABLE", message: this.repoRootProblem ?? "无仓库" };
      return;
    }
    if (this.switching) return; // N39：同上——切换窗口内不起新读取（rt 随后会被 episodes.clear 丢弃）
    const gen = this.repoGen;
    const st = await this.track(this.deps.fetchStatus(this.repoRoot, rt.abs));
    if (!this.cfg.isPackaged) await this.track(this.testHook("status-result")); // TA-12 ③：暂停一个在途 STATUS
    if (gen !== this.repoGen) return; // 代号已过期：结果丢弃，不进入任何 snapshot
    rt.status = st;
    rt.lastStatusTick = this.deps.now();
    const s = rt.status;
    if (s.ok) this.summaries.set(rt.epKey, { epKey: rt.epKey, mtimeMs: this.episodes.get(rt.epKey)?.mtimeMs ?? 0, currentStep: s.value.current_step, isBlocked: s.value.is_blocked });
  }

  /** 对象库只对活跃期读（后台期徽标只看 status，§2.12）；能力缺席时为 unsupported。 */
  loadApprovals(rt: EpisodeRuntime): void {
    if (!this.capApprovals) {
      rt.approvals = { state: "unsupported" };
      rt.approvalRecords = [];
      return;
    }
    rt.approvalsReadStart = this.deps.now(); // 打开文件之前取（§2.7 第 1 条）
    const r = readApprovalRecords(`${rt.abs}/_agent/approvals_store.json`, this.deps.fs);
    if (r.state === "absent") {
      rt.approvals = { state: "absent" };
      rt.approvalRecords = [];
    } else if (r.state === "error") {
      rt.approvals = { state: "error", message: r.message, lastGood: rt.lastGoodApprovals ? rt.lastGoodApprovals.map(toWireApproval) : null };
    } else {
      rt.approvalRecords = r.items;
      rt.lastGoodApprovals = r.items;
      rt.approvals = { state: "ok", items: r.items.map(toWireApproval), skipped: r.skipped, healedAt: rt.healedAt };
    }
  }

  private refold(rt: EpisodeRuntime): void {
    const alive = new Map<number, boolean>();
    const pidAlive = (pid: number) => {
      if (!alive.has(pid)) alive.set(pid, this.deps.pidAlive(pid));
      return alive.get(pid)!;
    };
    rt.jobs = foldEvents(rt.events, this.deps.now(), pidAlive, rt.prevMissing);
    rt.prevMissing = goneRunningJobIds(rt.jobs, pidAlive);
  }

  snapshotOf(rt: EpisodeRuntime): EpisodeSnapshot {
    rt.seq = 0;
    rt.jobsKey = stringifyLossless(rt.jobs);
    rt.statusKey = stringifyLossless(rt.status);
    rt.approvalsKey = stringifyLossless(rt.approvals);
    return {
      epKey: rt.epKey,
      generation: rt.tail?.generation ?? 0,
      seq: 0,
      reach: this.reach,
      status: rt.status,
      approvals: rt.approvals,
      events: {
        state: rt.eventsState,
        offset: rt.tail?.offset ?? 0,
        truncatedHead: rt.truncatedHead,
        items: rt.events.map(toWireEvent),
        malformed: rt.malformed,
        episodeFieldMismatch: rt.episodeFieldMismatch,
      },
      jobs: [...rt.jobs],
      degradedNotices: degradedNoticesOf(rt.events),
    };
  }

  /** 对象库读取完成、且本次读取带来的推送已发出之后才通知会话结算（§2.7 第 3 条）。 */
  private notifyApprovalsRead(rt: EpisodeRuntime): void {
    const readStart = rt.approvalsReadStart;
    if (readStart === null) return;
    rt.approvalsReadStart = null;
    this.sessions.onApprovalsRead(rt.epKey, readStart);
  }

  private async pushSnapshot(rt: EpisodeRuntime): Promise<void> {
    await this.loadAll(rt);
    if (!this.isCurrent(rt)) return;
    const snap = this.snapshotOf(rt);
    this.push({ v: 1, kind: "push", topic: "episode.snapshot", epKey: rt.epKey, generation: snap.generation, seq: 0, data: snap });
    this.notifyApprovalsRead(rt);
  }

  // ---------------- 轮询 ----------------

  /** 一个订阅期的一次 tick：tail 增量、（活跃期）status/对象库/H2/H5，合并成一条 delta。上一次未完成则跳过本次。 */
  async tickEpisode(rt: EpisodeRuntime, isActive: boolean): Promise<void> {
    if (rt.ticking) return;
    if (rt.loading > 0) return; // 载入在途或排队：它的快照会带上这批事件（第 7 条）
    rt.ticking = true;
    let done!: () => void;
    rt.tickDone = new Promise<void>((r) => (done = r));
    try {
      await this.tickEpisodeOnce(rt, isActive);
    } finally {
      rt.ticking = false;
      rt.tickDone = null;
      done();
    }
  }

  private async tickEpisodeOnce(rt: EpisodeRuntime, isActive: boolean): Promise<void> {
    if (this.reach !== "ok") return;
    const genBefore = rt.tail?.generation ?? 0;
    const before = rt.events.length;
    let dirGone = false;
    for (;;) {
      const more = this.pollEvents(rt);
      if (!more) break;
      await new Promise<void>((res) => setImmediate(res));
    }
    if (!this.dirExists(rt.abs)) {
      // N37：期目录不在，可能只是整个数据根不可达（脱盘/TCC）而 reach 还没轮询到。
      // 先重判可达性：脱盘按 §2.8「保留最后快照并标陈旧」，不能当成期目录被删去刷新（那会清空期列表）
      this.pollReach();
      if (this.reach !== "ok") return;
      dirGone = true;
    }
    if (dirGone) {
      rt.status = { ok: false, code: "E_STALE", message: "期目录已不存在" };
      this.refreshEpisodes();
    } else if (isActive && this.deps.now() - rt.lastStatusTick >= STATUS_REFRESH_MS) {
      await this.loadStatus(rt);
      this.checkH2(rt);
    }
    if (isActive && !dirGone) {
      this.loadApprovals(rt);
      await this.checkH5(rt);
    }
    this.refold(rt);
    if (!this.isCurrent(rt)) return; // 已退订或 repoRoot 已切换：旧 runtime 不再下发（同名期会被新仓库的订阅误收）
    const genAfter = rt.tail?.generation ?? 0;
    if (genAfter !== genBefore) {
      const snap = this.snapshotOf(rt);
      this.push({ v: 1, kind: "push", topic: "episode.snapshot", epKey: rt.epKey, generation: snap.generation, seq: 0, data: snap });
      this.notifyApprovalsRead(rt);
      return;
    }
    const delta: EpisodeDelta = { epKey: rt.epKey, generation: genAfter, seq: rt.seq + 1, newEvents: rt.events.slice(before).map(toWireEvent) };
    let changed = delta.newEvents.length > 0;
    const jk = stringifyLossless(rt.jobs);
    if (jk !== rt.jobsKey) {
      rt.jobsKey = jk;
      delta.jobs = rt.jobs;
      delta.degradedNotices = degradedNoticesOf(rt.events);
      changed = true;
    }
    const sk = stringifyLossless(rt.status);
    if (sk !== rt.statusKey) {
      rt.statusKey = sk;
      delta.status = rt.status;
      changed = true;
    }
    const ak = stringifyLossless(rt.approvals);
    if (ak !== rt.approvalsKey) {
      rt.approvalsKey = ak;
      delta.approvals = rt.approvals;
      changed = true;
    }
    if (!changed) {
      this.notifyApprovalsRead(rt);
      return;
    }
    rt.seq = delta.seq;
    this.push({ v: 1, kind: "push", topic: "episode.delta", epKey: rt.epKey, generation: delta.generation, seq: delta.seq, data: delta });
    this.notifyApprovalsRead(rt); // 先推 episode.delta，再追加 settled（§2.7 第 3 条）
  }

  private isCurrent(rt: EpisodeRuntime): boolean {
    return this.subs.get(rt.epKey) === rt;
  }

  private dirExists(p: string): boolean {
    try {
      return this.deps.fs.stat(p).isDirectory();
    } catch {
      return false;
    }
  }

  /** H2：活跃期 current_step 变化边沿触发一次；基线 = 设为活跃期后的第一次 status 采样。 */
  private checkH2(rt: EpisodeRuntime): void {
    if (!rt.status.ok) return;
    const step = rt.status.value.current_step;
    if (!rt.h2BaselineTaken) {
      rt.h2BaselineTaken = true;
      rt.h2Baseline = step;
      return;
    }
    if (step !== rt.h2Baseline) {
      rt.h2Baseline = step;
      void this.healAndReload(rt, "H2-step-changed").then(() => this.loadApprovals(rt));
    }
  }

  /** H5：活跃期关联产物漂移（§2.12）。 */
  private async checkH5(rt: EpisodeRuntime): Promise<void> {
    if (!this.capApprovals) return;
    const r = h5Step(rt.h5, rt.approvalRecords, (rel) => {
      try {
        const st = this.deps.fs.statBig(`${rt.abs}/${rel}`);
        return { size: st.size, mtimeNs: st.mtimeNs };
      } catch {
        return "missing";
      }
    });
    rt.h5 = r.state;
    for (const p of r.rejectedPaths) this.diag(`H5 拒绝对象库中的可疑路径：${p}（${rt.epKey}）`);
    if (r.fire) {
      await this.healAndReload(rt, "H5-artifact-drift");
      this.loadApprovals(rt);
    }
  }

  async tickActive(): Promise<void> {
    const rt = this.active ? this.subs.get(this.active) : undefined;
    if (rt) await this.tickEpisode(rt, true);
  }

  async tickBackground(): Promise<void> {
    for (const rt of [...this.subs.values()]) {
      if (rt.epKey !== this.active) await this.tickEpisode(rt, false);
    }
  }

  // ---------------- approval.decide（§3.2.1、§4.3） ----------------

  async decide(p: DecideParams): Promise<unknown> {
    try {
      const r = await this.decideOnce(p);
      // ack 成功：该停机点区间立即关闭（Spec 11 §2.4「区间结束于 ack 点击」）
      this.humanTimers.closeStop(p.epKey, p.stop, "ack");
      return r;
    } catch (e) {
      if (e instanceof DecideFail) throw new RpcFail(e.error.code, e.error.message, { stdoutTail: e.error.stdoutTail, stderrTail: e.error.stderrTail });
      throw e;
    }
  }

  private decideOnce(p: DecideParams): Promise<unknown> {
    const decideId = ++this.decideSeq;
    return decide(
      {
        isPackaged: this.cfg.isPackaged,
        capApprovals: () => this.capApprovals,
        capDetail: () => this.capDetail,
        episode: (k) => this.episodes.get(k),
        reach: () => this.reach,
        reachDetail: () => this.reachDetail,
        existsDir: (abs) => this.dirExists(abs),
        refreshEpisodes: () => this.refreshEpisodes(),
        tryAcquire: (k) => {
          if (this.switching || this.acking.has(k)) return false;
          this.acking.add(k);
          return true;
        },
        release: (k) => this.acking.delete(k),
        heal: (k, id) => (this.switching ? Promise.resolve({ ok: false, stderrTail: "切换仓库中，未自愈" }) : this.heal.requestHeal(k, "H4-pre-ack", id)),
        diag: (m) => this.diag(m),
        testHook: (n) => (!this.cfg.isPackaged ? this.testHook(n) : Promise.resolve()),
        readStore: (e) => readApprovalRecords(`${e.abs}/_agent/approvals_store.json`, this.deps.fs),
        fs: this.deps.fs,
        run: (t, args, tag) => this.core(t, args, tag),
        resnapshot: (k) => this.resnapshot(k),
      },
      p,
      decideId,
    );
  }

  /** decide 的 finally：订阅中的期推一次 snapshot（不触发 heal） */
  private async resnapshot(epKey: string): Promise<void> {
    const rt = this.subs.get(epKey);
    if (!rt) return;
    await this.withLoad(rt, () => this.pushSnapshot(rt));
  }

  // ---------------- repoRoot 切换（§2.10；renderer 只发无参请求，路径只在 main 里选） ----------------

  async requestRepoRootChange(): Promise<{ changed: boolean; repoRoot: string | null; problem: string | null }> {
    // 第一段守卫：有 decide 或 heal 在途 → E_BUSY、不弹框；对话框已开着也不再叠一个
    if (this.choosing) throw new RpcFail("E_BUSY", "仓库选择对话框已打开");
    if (this.switching || this.acking.size > 0 || this.heal.busy()) throw new RpcFail("E_BUSY", "有操作在途（审批或自愈），稍后再切换仓库");
    if (this.sessions.anyLive()) throw new RpcFail("E_BUSY", "存在活会话，先结束全部会话再切换仓库");
    let chosen: string | null;
    this.choosing = true;
    try {
      chosen = await this.deps.chooseRepoRoot();
    } finally {
      this.choosing = false;
    }
    if (chosen === null) return { changed: false, repoRoot: this.repoRoot, problem: null };
    const problem = validateRepoRoot(chosen); // main 已校验并由人确认；生效前按同一规则复核
    if (problem) return { changed: false, repoRoot: this.repoRoot, problem };
    // 第二段：进入切换中，代号 +1（在途只读 spawn 的结果随之作废），等在途 spawn 全部结束再换
    this.switching = true;
    this.repoGen += 1;
    try {
      while (this.inflight.size > 0 || this.acking.size > 0 || this.heal.busy()) {
        await Promise.allSettled([...this.inflight]);
        if (this.inflight.size === 0) await new Promise<void>((r) => setTimeout(r, 50)); // decide 停在非 spawn 处时轮询等待
      }
      saveSettings(this.cfg.userData, { version: 1, repoRoot: chosen });
      this.subs.clear();
      this.active = null;
      this.summaries.clear();
      this.carried.clear();
      this.episodes.clear();
      this.resolveRepoRoot();
      this.onDataRoot(this.dataRoot);
      this.pollReach();
      this.refreshEpisodes();
      await this.probe();
    } finally {
      this.switching = false;
    }
    void this.refreshEpisodeStatuses();
    return { changed: true, repoRoot: this.repoRoot, problem: this.repoRootProblem };
  }

  // ---------------- 建期（S8-R2 / Spec 10 §2.5、§3.7） ----------------

  /**
   * 建期：**校验与写入全在 core**（C10-R1），host 只做 exact-keys 与「是字符串」检查，
   * 期名作为单个 argv 元素传入（`shell:false`，无路径字段）；UI 绝不 mkdir（TG-2）。
   * spawn 计入在途（S8-R10）：切仓等它结束。失败原样回 core 的 stderr 尾部。
   *
   * Spec 18 §3.3：core 恒带 --from-idea。选题会话进程活着就先结束它（否则 core 拿不到选题记录的
   * 租约，必报「进行中」）；结束不了 → 不建期、如实报错。core 非零时期可能已建为空期（core 的原子性
   * 约定），重试同名会撞「期目录已存在」——换名或进那个空期继续，原文透传，不另造恢复通道。
   */
  private async createEpisode(name: string): Promise<CreatedEpisode> {
    if (this.sessionQuitting) throw new RpcFail("E_BUSY", "正在退出");
    if (this.choosing || this.switching) throw new RpcFail("E_BUSY", "正在切换仓库");
    if (!this.repoRoot) throw new RpcFail("E_UNREACHABLE", this.repoRootProblem ?? "仓库未就绪");
    if (!(await this.sessions.endForMigration("idea"))) {
      throw new RpcFail("E_BUSY", "选题会话未能结束，未建期（选题记录未动）；请稍后重试");
    }
    const r = await this.core("NEW_EPISODE", { name });
    const tails = { stdoutTail: r.stdoutTail, stderrTail: r.stderrTail };
    if (r.timedOut) throw new RpcFail("E_TIMEOUT", "建期超时，请手动确认期目录", tails);
    if (r.code !== 0) throw new RpcFail("E_CORE", `建期失败（core 退出码 ${r.code ?? `信号 ${r.signal}`}）`, tails);
    this.refreshEpisodes();
    this.push({ v: 1, kind: "push", topic: "episodes.summary", data: this.episodesList() });
    const marker = parseFromIdeaMarker(r.stdoutTail);
    if (marker === null) this.diag(`建期成功，但 core 输出里没有合式的选题带入标注，按「未带入」处理（${name}）`);
    const created: CreatedEpisode = { epKey: name, ...(marker ?? { migrated: false, sid: null, messages: 0 }) };
    if (created.migrated) {
      this.carried.add(name);
      this.sessions.resetAfterMigration("idea"); // §3.3 ④：选题视图不再显示已带走的旧讨论
    } else {
      // 同名期被 app 外删除后重建、这次没带入：上一次留下的标记不许让新期接着旧段起（N60 / D42-C C-2(b)）
      this.carried.delete(name);
    }
    return created;
  }

  // ---------------- 会话（Spec 10 §2.1、§3.1） ----------------

  private convTarget(convKey: string, mode: "new" | "continue"): SessionTarget {
    // idea 的「继续」= core 在 --idea 启动时恒恢复最近段（Spec 18 §3.3），不需要独立的 resume 模板
    if (convKey === "idea") return { key: "idea", epKey: null, abs: null, mode: "idea" };
    if (!convKey.startsWith("ep:")) throw new RpcFail("E_BAD_REQUEST", `未知会话键 ${convKey}`);
    const epKey = convKey.slice(3);
    const e = this.episodes.get(epKey);
    if (!e) throw new RpcFail("E_BAD_REQUEST", `未知期 ${epKey}`);
    return { key: convKey as ConvKey, epKey, abs: e.abs, mode };
  }

  /** §2.1 第 8 条：可达性不 ok 时 conv.send / conv.resume 得 E_UNREACHABLE（已在跑的会话不杀）。 */
  private async convSend(convKey: string, text: string): Promise<{ turnId: string }> {
    if (this.reach !== "ok") throw new RpcFail("E_UNREACHABLE", this.reachDetail);
    const carried = convKey.startsWith("ep:") && this.carried.has(convKey.slice(3));
    const target = this.convTarget(convKey, carried ? "continue" : "new");
    const r = await this.sessions.send(target.key, target, text);
    if (carried) this.carried.delete(convKey.slice(3)); // 失败不消费：下一次发送仍接着迁入段起
    return r;
  }

  private async convResume(convKey: string): Promise<ConvSnapshot> {
    if (this.reach !== "ok") throw new RpcFail("E_UNREACHABLE", this.reachDetail);
    const target = this.convTarget(convKey, "continue");
    const snap = await this.sessions.resume(target.key, target);
    if (convKey.startsWith("ep:")) this.carried.delete(convKey.slice(3));
    return snap;
  }

  // ---------------- 会话管理（D45） ----------------

  /** 会话管理只对期会话开放：idea 是单一滚动段（Spec 18 §3.3），没有「哪一个」可选。 */
  private epConv(convKey: string): SessionTarget & { abs: string } {
    const target = this.convTarget(convKey, "continue");
    if (target.abs === null) throw new RpcFail("E_BAD_REQUEST", "选题会话没有会话列表");
    return target as SessionTarget & { abs: string };
  }

  private checkSid(sid: string): void {
    if (!SESSION_ID_RE.test(sid)) throw new RpcFail("E_BAD_REQUEST", `会话号形状不对：「${sid}」`);
  }

  /** 有回合在跑时一律拒绝：不打断人正在看的回合，也不让未答的卡被悄悄作废。 */
  private refuseIfBusy(key: ConvKey): void {
    if (this.sessions.isBusy(key)) throw new RpcFail("E_BUSY", "该会话有回合在跑或正在启动/收尾，先停止再操作");
  }

  /** 结束空闲活会话并等它退出（释放期租约）；宽限内没退出就报错，不往下做。 */
  private async endLive(key: ConvKey): Promise<void> {
    if (!this.sessions.summaries().get(key)?.live) return;
    if (!(await this.sessions.endAndWait(key))) throw new RpcFail("E_SESSION", "当前会话未能在宽限内退出");
  }

  private async convSessions(convKey: string): Promise<SessionRow[]> {
    const target = this.epConv(convKey);
    const r = await this.core("LIST_SESSIONS", { ep: target.abs });
    const tails = { stdoutTail: r.stdoutTail, stderrTail: r.stderrTail };
    if (r.timedOut) throw new RpcFail("E_TIMEOUT", "读取会话列表超时", tails);
    if (r.code !== 0) throw new RpcFail("E_CORE", r.stderrTail.trim() || "读取会话列表失败", tails);
    let raw: unknown;
    try {
      raw = JSON.parse((r.stdoutFull ?? r.stdoutTail).trim());
    } catch {
      throw new RpcFail("E_CORE", "core 的 /list-sessions 输出不是 JSON", tails);
    }
    if (!Array.isArray(raw)) throw new RpcFail("E_CORE", "core 的 /list-sessions 输出不是列表", tails);
    const live = this.sessions.liveSid(target.key);
    return raw.map((row) => {
      const o = row as Record<string, unknown>;
      if (typeof o.sid !== "string" || !SESSION_ID_RE.test(o.sid) || typeof o.messages !== "number" || typeof o.assistants !== "number"
        || typeof o.last_activity !== "string" || typeof o.first_user !== "string" || typeof o.resumable !== "boolean") {
        throw new RpcFail("E_CORE", "core 的 /list-sessions 行不符合约定", tails);
      }
      return { sid: o.sid, messages: o.messages, assistants: o.assistants, lastActivity: o.last_activity, firstUser: o.first_user, resumable: o.resumable, live: o.sid === live };
    });
  }

  private async convEnter(convKey: string, sid: string): Promise<ConvSnapshot> {
    if (this.reach !== "ok") throw new RpcFail("E_UNREACHABLE", this.reachDetail);
    this.checkSid(sid);
    const target = this.epConv(convKey);
    this.refuseIfBusy(target.key);
    if (this.sessions.liveSid(target.key) === sid) return this.sessions.snapshot(target.key);
    await this.endLive(target.key);
    const snap = await this.sessions.resume(target.key, { ...target, mode: "continue", sid });
    this.carried.delete(convKey.slice(3));
    return snap;
  }

  private async convFresh(convKey: string): Promise<ConvSnapshot> {
    const target = this.epConv(convKey);
    this.refuseIfBusy(target.key);
    await this.endLive(target.key);
    this.sessions.reset(target.key);
    this.carried.delete(convKey.slice(3)); // 迁入标记会让下一条消息走 continue，开新会话时必须清掉
    return this.sessions.snapshot(target.key);
  }

  /** 删除 = 移进 `_agent/session-trash/`（人选的语义，可手工找回）。先原生确认，取消则一字不动。 */
  private async convDelete(convKey: string, sid: string): Promise<{ deleted: boolean; moved: number }> {
    this.checkSid(sid);
    const target = this.epConv(convKey);
    this.refuseIfBusy(target.key);
    const row = (await this.convSessions(convKey)).find((r) => r.sid === sid);
    if (!row) throw new RpcFail("E_STALE", "该会话已不存在");
    const detail = [
      row.firstUser || "（没有用户消息）",
      `${row.messages} 条消息 · 最后活动 ${row.lastActivity}`,
      "将移到本期 _agent/session-trash/，需要时可以手工找回。",
      ...(this.sessions.liveSid(target.key) !== null ? ["当前打开的会话会先结束。"] : []),
    ].join("\n");
    if (!(await this.deps.confirm("把这个会话移到回收站？", detail))) return { deleted: false, moved: 0 };
    this.refuseIfBusy(target.key); // 确认框等待期间可能有人发了消息
    const wasLive = this.sessions.liveSid(target.key);
    await this.endLive(target.key); // 活会话占着期租约，core 拿不到锁就删不了
    if (wasLive === sid) this.sessions.reset(target.key);
    const r = await this.core("DELETE_SESSION", { ep: target.abs, sid });
    const tails = { stdoutTail: r.stdoutTail, stderrTail: r.stderrTail };
    if (r.timedOut) throw new RpcFail("E_TIMEOUT", "删除会话超时", tails);
    if (r.code === 3) throw new RpcFail("E_SESSION_LOCKED", r.stderrTail.trim() || "该期仍有活会话", tails);
    if (r.code !== 0) throw new RpcFail("E_CORE", r.stderrTail.trim() || `删除会话失败（core 退出码 ${r.code}）`, tails);
    let moved = 0;
    try {
      moved = Number((JSON.parse(r.stdoutTail.trim()) as { moved: unknown }).moved) || 0;
    } catch {
      throw new RpcFail("E_CORE", "core 的 /delete-session 输出不是 JSON", tails);
    }
    // 删的是别的会话：把刚才为释放租约而结束的那个接回来，人看到的对话不变
    if (wasLive !== null && wasLive !== sid) await this.sessions.resume(target.key, { ...target, mode: "continue", sid: wasLive });
    return { deleted: true, moved };
  }

  // ---------------- 退出（Spec 10 §2.10） ----------------

  quitState(): QuitBusy[] {
    return this.sessions.quitState();
  }

  /** 退出第 5 步：空闲 shutdown、忙 SIGTERM、宽限后整组 SIGKILL；此后拒绝新请求。 */
  async stopAllForQuit(): Promise<void> {
    this.sessionQuitting = true;
    await this.sessions.stopAllForQuit();
  }

  // ---------------- 镜头画廊（shots 根顶层 *.html；内容仍经 ava-media:// 读） ----------------

  shotsList(): ShotsEntry[] {
    if (!this.dataRoot || this.reach !== "ok") throw new RpcFail("E_UNREACHABLE", this.reachDetail);
    const root = join(this.dataRoot, "library/shots");
    let names: string[];
    try {
      names = this.deps.fs.readdir(root);
    } catch {
      return []; // shots 根尚未建立：没有画廊，不是错误
    }
    const out: ShotsEntry[] = [];
    for (const name of names) {
      if (!name.endsWith(".html") || name.startsWith(".")) continue;
      try {
        const st = this.deps.fs.stat(`${root}/${name}`);
        if (st.isFile()) out.push({ name, size: st.size, mtimeMs: st.mtimeMs });
      } catch {
        /* 悬空链接等：跳过 */
      }
    }
    return out.sort((a, b) => a.name.localeCompare(b.name, "en", { numeric: true }));
  }

  // ---------------- 产物树 ----------------

  treeList(e: EpisodeEntry, relDir: string): TreeEntry[] {
    if (relDir !== "" && relPathProblem(relDir) !== null) throw new RpcFail("E_BAD_REQUEST", `非法目录 ${relDir}`);
    if (this.reach !== "ok") throw new RpcFail("E_UNREACHABLE", this.reachDetail);
    const dir = relDir === "" ? e.abs : `${e.abs}/${relDir}`;
    let names: string[];
    try {
      // 期目录内的符号链接目录不许把浏览带出期目录：子目录的实路径必须是期目录实路径的严格后代
      // （期目录本身可以是软链期目录，对齐 cli.py 的 is_dir()；比较的是两边的 realpath）
      if (relDir !== "") {
        const epReal = this.deps.fs.realpath(e.abs);
        const dirReal = this.deps.fs.realpath(dir);
        if (!dirReal.startsWith(`${epReal}/`)) throw new RpcFail("E_BAD_REQUEST", `目录经符号链接指向期目录之外：${relDir}`);
      }
      names = this.deps.fs.readdir(dir);
    } catch (err) {
      if (err instanceof RpcFail) throw err;
      if (!this.dirExists(e.abs)) {
        this.refreshEpisodes();
        throw new RpcFail("E_STALE", "期目录已不存在");
      }
      throw new RpcFail("E_UNREACHABLE", `目录不可读：${errnoOf(err) ?? String(err)}`);
    }
    const out: TreeEntry[] = [];
    for (const name of names) {
      if (HIDDEN_TREE(name)) continue;
      const rel = relDir === "" ? name : `${relDir}/${name}`;
      try {
        const st = this.deps.fs.stat(`${dir}/${name}`);
        out.push({ name, rel, kind: st.isDirectory() ? "dir" : st.isFile() ? "file" : "other", size: st.size, mtimeMs: st.mtimeMs });
      } catch {
        out.push({ name, rel, kind: "other", size: 0, mtimeMs: 0 });
      }
    }
    return out.sort((a, b) => (a.kind === "dir") !== (b.kind === "dir") ? (a.kind === "dir" ? -1 : 1) : a.name.localeCompare(b.name, "en", { numeric: true }));
  }

  // ---------------- Spec 11/12：停机点深度组件（§4.3/§4.4；host 只 spawn，不写 data/） ----------------

  /** 期就绪检查：期在列表里 + 数据可达 + 期目录仍在。 */
  private epForIo(epKey: string): EpisodeEntry {
    const e = this.epOf(epKey);
    if (this.reach !== "ok") throw new RpcFail("E_UNREACHABLE", this.reachDetail);
    if (!this.dirExists(e.abs)) {
      this.refreshEpisodes();
      throw new RpcFail("E_STALE", "期目录已不存在");
    }
    return e;
  }

  private static fingerOf(fs: FsRead, abs: string): FingerJson | null {
    try {
      const st = fs.statBig(abs);
      if (!st.isFile()) return null;
      return { size: Number(st.size), mtimeNs: st.mtimeNs.toString() };
    } catch {
      return null;
    }
  }

  /** 编辑器的基线指纹（只读；两项都缺席 = 既无正稿也无草稿）。 */
  private scriptStat(e: EpisodeEntry): ScriptStatJson {
    return {
      script: HostService.fingerOf(this.deps.fs, `${e.abs}/02-script.md`),
      draft: HostService.fingerOf(this.deps.fs, `${e.abs}/02-script.draft.md`),
    };
  }

  /**
   * 保存正文（指纹不符即拒存）。stdout 指纹经 `parseLossless` 解析：
   * `mtime_ns` 是约 1.8e18 的纳秒值，普通 `JSON.parse` 丢精度后「刚存完就冲突」（Spec 11 红队 🟡-2）。
   */
  private async scriptSave(e: EpisodeEntry, text: string, expectSize: string, expectMtimeNs: string): Promise<SavedFingerprintJson> {
    if (!this.losslessJson) throw new RpcFail("E_CAPABILITY", "运行时不支持无损解析纳秒指纹，无法校验保存结果");
    if (Buffer.byteLength(text, "utf-8") > SAVE_SCRIPT_MAX_BYTES) throw new RpcFail("E_BAD_REQUEST", `正文超过 ${SAVE_SCRIPT_MAX_BYTES} 字节上限`);
    const r = await this.core("SAVE_SCRIPT", { ep: e.abs, size: expectSize, mtimeNs: expectMtimeNs }, {}, text);
    const tails = { stdoutTail: r.stdoutTail, stderrTail: r.stderrTail };
    if (r.timedOut) throw new RpcFail("E_TIMEOUT", "保存超时，请刷新确认磁盘版本", tails);
    if (r.code !== 0) {
      const msg = r.stderrTail.trim() || `保存失败（core 退出码 ${r.code ?? `信号 ${r.signal}`}）`;
      // 「磁盘版本已变」是语义上的陈旧（而非引擎故障）：UI 据此转入冲突分支
      throw new RpcFail(msg.includes("磁盘版本已变") ? "E_STALE" : "E_CORE", msg, tails);
    }
    let parsed: unknown;
    try {
      parsed = parseLossless(r.stdoutTail.trim());
    } catch {
      parsed = null;
    }
    const fp = parsed as { size?: unknown; mtime_ns?: unknown } | null;
    if (!fp || typeof fp.size !== "number" || typeof fp.mtime_ns !== "bigint") {
      throw new RpcFail("E_CORE", "core 未返回可信的新指纹，请刷新后重试", tails);
    }
    return { size: fp.size, mtimeNs: fp.mtime_ns.toString() };
  }

  /** 封板：core 内同一条 `git diff --no-index`，空 diff 拒封（core 侧退出 1）。 */
  private async scriptSeal(e: EpisodeEntry): Promise<{ bytes: number }> {
    const r = await this.core("SEAL_SCRIPT", { ep: e.abs });
    const tails = { stdoutTail: r.stdoutTail, stderrTail: r.stderrTail };
    if (r.timedOut) throw new RpcFail("E_TIMEOUT", "封板超时", tails);
    if (r.code !== 0) throw new RpcFail("E_CORE", r.stderrTail.trim() || `封板失败（core 退出码 ${r.code ?? `信号 ${r.signal}`}）`, tails);
    const n = Number.parseInt(r.stdoutTail.trim(), 10);
    return { bytes: Number.isFinite(n) ? n : 0 };
  }

  /** 机检：PASS/FAIL/INFO 原文照传，UI 不解析判定语义（退出码 1 = 有 FAIL，不是错误）。 */
  private async scriptCheck(e: EpisodeEntry): Promise<{ code: number | null; stdoutTail: string; stderrTail: string }> {
    const scriptAbs = `${e.abs}/02-script.md`;
    if (HostService.fingerOf(this.deps.fs, scriptAbs) === null) throw new RpcFail("E_STALE", "找不到 02-script.md");
    const r = await this.core("CHECK_SCRIPT", { scriptAbs });
    if (r.timedOut) throw new RpcFail("E_TIMEOUT", "机检超时", { stdoutTail: r.stdoutTail, stderrTail: r.stderrTail });
    return { code: r.code, stdoutTail: r.stdoutTail, stderrTail: r.stderrTail };
  }

  private async voiceInfo(e: EpisodeEntry): Promise<VoiceInfoJson> {
    const r = await this.core("VOICE_INFO", { ep: e.abs });
    const tails = { stdoutTail: r.stdoutTail, stderrTail: r.stderrTail };
    if (r.timedOut) throw new RpcFail("E_TIMEOUT", "voice-info 超时", tails);
    if (r.code !== 0) throw new RpcFail("E_CORE", r.stderrTail.trim() || "voice-info 失败", tails);
    const parsed = parseVoiceInfo((r.stdoutFull ?? r.stdoutTail).trim());
    if (!parsed.ok) throw new RpcFail("E_CORE", "core 的 /voice-info 输出不符合 schema v1", tails);
    return parsed.info;
  }

  private async voiceParse(e: EpisodeEntry, text: string): Promise<VoicePatchJson> {
    if (Buffer.byteLength(text, "utf-8") > VOICE_PARSE_MAX_BYTES) throw new RpcFail("E_BAD_REQUEST", `纠错原文超过 ${VOICE_PARSE_MAX_BYTES} 字节上限`);
    const r = await this.core("VOICE_PARSE", { ep: e.abs }, {}, text);
    const tails = { stdoutTail: r.stdoutTail, stderrTail: r.stderrTail };
    if (r.timedOut) throw new RpcFail("E_TIMEOUT", "解析超时", tails);
    if (r.code !== 0) throw new RpcFail("E_CORE", r.stderrTail.trim() || "纠错原文解析失败", tails);
    try {
      return JSON.parse(r.stdoutTail.trim()) as VoicePatchJson;
    } catch {
      throw new RpcFail("E_CORE", "core 的 /voice-parse 输出不是 JSON", tails);
    }
  }

  /** 落盘：stdin 传**原文**（core 重新解析）。跨进程只回读条目 dict，不回传解析结果（Spec 11 §2.3）。 */
  private async voiceAdd(e: EpisodeEntry, text: string): Promise<Record<string, unknown>> {
    if (Buffer.byteLength(text, "utf-8") > VOICE_PARSE_MAX_BYTES) throw new RpcFail("E_BAD_REQUEST", `纠错原文超过 ${VOICE_PARSE_MAX_BYTES} 字节上限`);
    const r = await this.core("VOICE_ADD", { ep: e.abs }, {}, text);
    const tails = { stdoutTail: r.stdoutTail, stderrTail: r.stderrTail };
    if (r.timedOut) throw new RpcFail("E_TIMEOUT", "纠错落盘超时", tails);
    if (r.code !== 0) throw new RpcFail("E_CORE", r.stderrTail.trim() || "纠错落盘失败", tails);
    try {
      const v = JSON.parse(r.stdoutTail.trim()) as unknown;
      if (typeof v !== "object" || v === null || Array.isArray(v)) throw new Error("not an object");
      return v as Record<string, unknown>;
    } catch {
      throw new RpcFail("E_CORE", "core 的 /voice-add 输出不是 JSON 对象", tails);
    }
  }

  private async voiceRevert(e: EpisodeEntry, label: string): Promise<null> {
    const r = await this.core("VOICE_REVERT", { ep: e.abs, label });
    if (r.timedOut) throw new RpcFail("E_TIMEOUT", "回滚超时", { stderrTail: r.stderrTail });
    if (r.code !== 0) throw new RpcFail("E_CORE", r.stderrTail.trim() || `回滚失败（core 退出码 ${r.code}）`, { stderrTail: r.stderrTail });
    return null;
  }

  private async voiceRetract(e: EpisodeEntry, id: string): Promise<null> {
    const r = await this.core("VOICE_RETRACT", { ep: e.abs, id });
    if (r.timedOut) throw new RpcFail("E_TIMEOUT", "撤回超时", { stderrTail: r.stderrTail });
    if (r.code !== 0) throw new RpcFail("E_CORE", r.stderrTail.trim() || `撤回失败（core 退出码 ${r.code}）`, { stderrTail: r.stderrTail });
    return null;
  }

  /** done：长任务（无超时、app 退出不发信号、进度经 events.jsonl 观测；S8-R16/R17）。
   *  先请 main 弹原生确认框（Spec 11 §2.3；确认框取消则不 spawn，Spec 9 RF-17 的显式答复）。 */
  private async voiceApplyPatch(e: EpisodeEntry): Promise<{ started: boolean }> {
    if (!(await this.deps.confirm("执行增量重配？", "是否立即执行增量重配 (--apply-patch)?"))) return { started: false };
    const r = await this.core("RUN_TTS_APPLY_PATCH", { ep: e.abs });
    const tails = { stdoutTail: r.stdoutTail, stderrTail: r.stderrTail };
    if (r.timedOut) throw new RpcFail("E_TIMEOUT", "增量重配超时（本模板无超时，此路径不应到达）", tails);
    if (r.code !== 0) throw new RpcFail("E_CORE", r.stderrTail.trim() || `增量重配失败（core 退出码 ${r.code ?? `信号 ${r.signal}`}）`, tails);
    return { started: true };
  }

  /** D50-A S6：读音核对（只读）。core 退出码 1 = 核对不通过——这是结果不是故障，原文行照传 */
  private async voiceCheck(e: EpisodeEntry, a: VoiceReadingArgs): Promise<VoiceCheckJson> {
    void e;
    const r = await this.core("VOICE_CHECK", a);
    if (r.timedOut) throw new RpcFail("E_TIMEOUT", "读音核对超时", { stderrTail: r.stderrTail });
    if (r.code !== 0 && r.code !== 1) throw new RpcFail("E_CORE", r.stderrTail.trim() || "读音核对失败", { stderrTail: r.stderrTail });
    return { ok: r.code === 0, lines: linesOf(r.stdoutTail, r.stderrTail) };
  }

  /** D50-A S6：写全局读音表一个键。人点了「记下」还要过一道原生确认框：它影响所有番、所有期 */
  private async voiceGlobal(e: EpisodeEntry, a: VoiceReadingArgs, supersede: boolean): Promise<VoiceCheckJson> {
    const what = a.pinyin !== undefined ? `拼音直注 ${a.word} → ${a.pinyin}` : `同音字 ${a.word} → ${a.homophone ?? ""}`;
    if (!(await this.deps.confirm("写入全局读音表？", `config/voice.json：${what}${supersede ? "（并删除另一张表的同名条目）" : ""}。影响所有番、所有期。`))) {
      return { ok: false, lines: ["已取消，未写入"] };
    }
    const r = await this.core("VOICE_GLOBAL", { ...a, ep: e.abs, supersede });
    if (r.timedOut) throw new RpcFail("E_TIMEOUT", "写全局读音表超时", { stderrTail: r.stderrTail });
    return { ok: r.code === 0, lines: linesOf(r.stdoutTail, r.stderrTail) };
  }

  /** D50-A S7：03.5 卡内打点 → manifest 的 human_review（合并写，不重合成） */
  private async voiceReview(e: EpisodeEntry, review: string): Promise<VoiceCheckJson> {
    if (!REVIEW_RE.test(review)) throw new RpcFail("E_BAD_REQUEST", `打点格式不对：${review}`);
    const r = await this.core("TTS_REVIEW", { ep: e.abs, review });
    if (r.timedOut) throw new RpcFail("E_TIMEOUT", "打点超时", { stderrTail: r.stderrTail });
    if (r.code !== 0) throw new RpcFail("E_CORE", r.stderrTail.trim() || "打点失败", { stderrTail: r.stderrTail });
    return { ok: true, lines: linesOf(r.stdoutTail, "") };
  }

  private async voiceReTts(e: EpisodeEntry): Promise<{ started: boolean }> {
    if (!(await this.deps.confirm("按全局读音表重配？", "普通重跑 tts（不带 --force）：只重配念法变了的段。"))) return { started: false };
    const r = await this.core("RUN_TTS", { ep: e.abs });
    const tails = { stdoutTail: r.stdoutTail, stderrTail: r.stderrTail };
    if (r.code !== 0) throw new RpcFail("E_CORE", r.stderrTail.trim() || `重配失败（core 退出码 ${r.code ?? `信号 ${r.signal}`}）`, tails);
    return { started: true };
  }

  private timeSurface(e: EpisodeEntry, stop: string, visible: boolean): null {
    this.humanTimers.noteReviewSurface(e.epKey, stop, visible, "close");
    return null;
  }

  /** 区间结算：spawn RECORD_TIME（sidecar 纪律：失败静默、只记诊断，绝不因此阻塞 UI）。 */
  private recordHumanTime(epKey: string, iv: { stop: string; enteredAt: number; leftAt: number }, reason: FlushReason): void {
    const e = this.episodes.get(epKey);
    if (!e || !this.repoRoot) return;
    void this.core("RECORD_TIME", { ep: e.abs, stop: iv.stop, entered: epochSeconds(iv.enteredAt), left: epochSeconds(iv.leftAt) }).then(
      (r) => {
        if (r.code !== 0 || r.timedOut) this.diag(`人时落盘失败（${epKey} ${iv.stop}，${reason}）：${r.stderrTail.trim().slice(-200)}`);
      },
      (err) => this.diag(`人时落盘异常（${epKey} ${iv.stop}）：${err instanceof Error ? err.message : String(err)}`),
    );
  }

  /** 人时读数：host 直读 `human_time.json`（整文件 `os.replace` 替换，与 approvals_store 同一安全论证；A4）。 */
  private timeRead(e: EpisodeEntry): TimeReadJson {
    let records: HumanTimeRecord[] = [];
    try {
      const raw = JSON.parse(Buffer.from(this.deps.fs.readFile(`${e.abs}/human_time.json`)).toString("utf-8")) as unknown;
      if (Array.isArray(raw)) records = raw as HumanTimeRecord[];
    } catch {
      records = []; // 缺席 / 损坏 / 半写：呈现层事实定为「暂无记录」，不是错误
    }
    return reduceHumanTime(records);
  }

  /** 封面导入：字节走 stdin（core 侧限流 + 完整解码 + 不重编码落盘，Spec 12 §2.1）。 */
  private async coverImport(e: EpisodeEntry, name: string, bytes: unknown): Promise<ImportedCoverJson> {
    if (!(bytes instanceof Uint8Array)) throw new RpcFail("E_BAD_REQUEST", "bytes 必须是字节数组");
    if (bytes.byteLength === 0) throw new RpcFail("E_BAD_REQUEST", "空的图片字节");
    if (bytes.byteLength > IMPORT_COVER_MAX_BYTES) throw new RpcFail("E_BAD_REQUEST", `图片字节超过 ${IMPORT_COVER_MAX_BYTES} 字节上限`);
    const r = await this.core("IMPORT_COVER", { ep: e.abs, name }, {}, bytes);
    const tails = { stdoutTail: r.stdoutTail, stderrTail: r.stderrTail };
    if (r.timedOut) throw new RpcFail("E_TIMEOUT", "导入超时", tails);
    if (r.code === 2) throw new RpcFail("E_BAD_REQUEST", r.stderrTail.trim() || "导入参数不合法", tails);
    if (r.code !== 0) throw new RpcFail("E_CORE", r.stderrTail.trim() || `导入失败（core 退出码 ${r.code}）`, tails);
    try {
      const v = JSON.parse(r.stdoutTail.trim()) as ImportedCoverJson;
      if (typeof v.path !== "string") throw new Error("bad path");
      return v;
    } catch {
      throw new RpcFail("E_CORE", "core 的 /import-cover 输出不是合法 JSON", tails);
    }
  }
}

/** D50-A S6：RPC 参数 → 读音参数（只取声明过的键；core 再校验二选一） */
function readingOf(p: Record<string, string | undefined>): VoiceReadingArgs {
  const a: VoiceReadingArgs = { word: p.word ?? "" };
  if (p.pinyin !== undefined) a.pinyin = p.pinyin;
  if (p.homophone !== undefined) a.homophone = p.homophone;
  if (p.expect !== undefined) a.expect = p.expect;
  return a;
}

function linesOf(stdout: string, stderr: string): string[] {
  return `${stdout}\n${stderr}`.split("\n").map((l) => l.trimEnd()).filter((l) => l !== "");
}
