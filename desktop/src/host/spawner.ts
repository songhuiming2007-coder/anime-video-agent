// spawn 闭集（Spec 8 §3.4）。desktop/ 中唯一 import node:child_process 的文件（TG-3）。
// 全部以 argv 数组调用：shell:false、stdin ignore、detached（子进程为进程组组长）、cwd = repoRoot、环境变量白名单。
import { spawn } from "node:child_process";
import {
  SPAWN_KILL_GRACE_MS,
  SPAWN_LOG_MAX,
  SPAWN_TAIL_BYTES,
  SPAWN_TIMEOUT_ACK_MS,
  SPAWN_TIMEOUT_REVIEW_MS,
  SPAWN_TIMEOUT_SHORT_MS,
  STATUS_STDOUT_MAX_BYTES,
  VOICE_INFO_STDOUT_MAX_BYTES,
  SESSIONS_STDOUT_MAX_BYTES,
  SESSION_ID_RE,
  KEYCHAIN_SERVICE,
} from "../shared/constants";
import type { StopType } from "../shared/contracts";

export type Template =
  | "PROBE_APPROVALS"
  | "PROBE_FREEZE"
  | "GIT_HEAD"
  | "GIT_DESKTOP_DIFF"
  | "STATUS"
  | "HEAL"
  | "APPROVE"
  | "REJECT"
  | "REVIEW_APPROVE"
  | "NEW_EPISODE"
  // Spec 10 S8-R3：密钥探测与钥匙串读取（短命令，规则同现状）
  | "PROBE_KEY_ENV"
  | "KEYCHAIN_READ"
  // Spec 15 §2.7：web 检索链上声明的密钥变量名（短命令，同 PROBE_KEY_ENV）
  | "PROBE_WEB_KEY_ENVS"
  // Spec 11 §3.4：02.5 编辑器 / 03.5 顺听 / 人时（S8-R13 闭集）
  | "SAVE_SCRIPT"
  | "SEAL_SCRIPT"
  | "CHECK_SCRIPT"
  | "VOICE_INFO"
  | "VOICE_PARSE"
  | "VOICE_ADD"
  | "VOICE_REVERT"
  | "VOICE_RETRACT"
  | "RECORD_TIME"
  | "RUN_TTS_APPLY_PATCH"
  // D50-A S6：读音核对 / 全局读音表写入 / 按全局表普通重跑
  | "VOICE_CHECK"
  | "VOICE_GLOBAL"
  | "RUN_TTS"
  // D50-A S7：03.5 卡内结构化打点（只写 manifest 的 human_review，不重合成）
  | "TTS_REVIEW"
  // Spec 12 S8-R18：封面导入（字节走 stdin）
  | "IMPORT_COVER"
  // D45：会话列表 / 删除（移进回收站）
  | "LIST_SESSIONS"
  | "DELETE_SESSION";

/** D50-A S6：读音参数——`pinyin` 与 `homophone + expect` 二选一（core 再校验一遍） */
export interface VoiceReadingArgs {
  word: string;
  pinyin?: string;
  homophone?: string;
  expect?: string;
}

/** 03.5 打点只许这三维、各 1–5（core 的 parse_review_arg 再校验一遍；05 的两维不在这张卡上） */
export const REVIEW_RE = /^voice=[1-5],prosody=[1-5],misread=[1-5]$/;

function readingFlags(a: VoiceReadingArgs): string[] {
  const out = [`--word=${a.word}`];
  if (a.pinyin !== undefined) out.push(`--pinyin=${a.pinyin}`);
  if (a.homophone !== undefined) out.push(`--homophone=${a.homophone}`);
  if (a.expect !== undefined) out.push(`--expect=${a.expect}`);
  return out;
}

/** 长驻会话进程模板（Spec 10 S8-R3）；不经 runCore（stdin pipe、无超时），只用 sessionArgv。 */
export type SessionTemplate = "SESSION_NEW" | "SESSION_CONTINUE" | "SESSION_IDEA";

export interface TemplateArgs {
  PROBE_APPROVALS: Record<string, never>;
  PROBE_FREEZE: Record<string, never>;
  GIT_HEAD: Record<string, never>;
  GIT_DESKTOP_DIFF: { buildHead: string };
  STATUS: { ep: string };
  HEAL: { ep: string };
  /** cover/title 只在 09 定稿时给出（Spec 12 S3-R12：空格固定位置四个独立 argv 元素） */
  APPROVE: { ep: string; stop: StopType; approvalId: string; cover?: string; title?: string };
  REJECT: { ep: string; stop: StopType; approvalId: string; target: string; problem: string };
  /** size / mtimeNs 取自对象钉住的 04-clips.json 指纹；mtimeNs 为 bigint，argv 里写 String(bigint)（§3.1 规则 8） */
  REVIEW_APPROVE: { ep: string; size: number; mtimeNs: bigint };
  /** 建期（Spec 10 S8-R2/§3.7）：期名作为单个 argv 元素传给 core，校验全在 core */
  NEW_EPISODE: { name: string };
  PROBE_KEY_ENV: Record<string, never>;
  /** Spec 10 §2.9：账户名 = core 回答的变量名 */
  KEYCHAIN_READ: { envName: string };
  PROBE_WEB_KEY_ENVS: Record<string, never>;
  // ---- Spec 11 §3.4 / Spec 12 §3.5 ----
  /** mtimeNs 为十进制字符串（沿用规则 8/9）；`-1/-1` 断言文件不存在（从草稿新建） */
  SAVE_SCRIPT: { ep: string; size: string; mtimeNs: string };
  SEAL_SCRIPT: { ep: string };
  /** `scriptAbs` = 期目录内 02-script.md 的绝对路径（core 侧 parser 收 Path） */
  CHECK_SCRIPT: { scriptAbs: string };
  VOICE_INFO: { ep: string };
  VOICE_PARSE: { ep: string };
  VOICE_ADD: { ep: string };
  VOICE_REVERT: { ep: string; label: string };
  VOICE_RETRACT: { ep: string; id: string };
  RECORD_TIME: { ep: string; stop: string; entered: string; left: string };
  /** 长任务：无超时、app 退出不发信号（S8-R17） */
  RUN_TTS_APPLY_PATCH: { ep: string };
  /** 用户输入的词与读音一律 `--flag=value` 单个 argv 元素：值以 `-` 开头也不会被当成选项 */
  VOICE_CHECK: VoiceReadingArgs;
  VOICE_GLOBAL: VoiceReadingArgs & { ep: string; supersede: boolean };
  /** 长任务：同 RUN_TTS_APPLY_PATCH（无超时、退出不发信号） */
  RUN_TTS: { ep: string };
  /** review 在宿主侧先过 REVIEW_RE（只许三维 1–5），作为单个 argv 元素 */
  TTS_REVIEW: { ep: string; review: string };
  /** 原始文件名作为单个 argv 元素；图片字节走 stdin */
  IMPORT_COVER: { ep: string; name: string };
  LIST_SESSIONS: { ep: string };
  /** sid 先过 SESSION_ID_RE，不合格抛错不 spawn */
  DELETE_SESSION: { ep: string; sid: string };
}

/** 写进 spawn 日志的归属标签：heal 的触发编号、decide 关联号（TA-2/TA-11 只统计该次 decide 关联的 spawn） */
export interface SpawnTag {
  trigger?: string;
  decide?: number;
}

export interface CoreResult {
  code: number | null;
  signal: string | null;
  stdoutTail: string;
  stderrTail: string;
  timedOut: boolean;
  /** 仅要求完整读入的模板（STATUS）非 null：逐块累积的完整 stdout；超过上限时为 null 且 stdoutOverflow 为 true */
  stdoutFull: string | null;
  stdoutOverflow: boolean;
}

const GIT = "/usr/bin/git";
/** Spec 10 §2.9：固定服务名；账户名 = 变量名。 */
const DEFAULT_KEYCHAIN_EXEC = "/usr/bin/security";
let keychainExec = DEFAULT_KEYCHAIN_EXEC;

/**
 * 仅未打包构建的测试钩子（TG-6，Spec 10 §3.4）：把 KEYCHAIN_READ 的可执行路径换成夹具脚本。
 * 打包版没有任何调用点；标识以 `__avaTest` 前缀，由 guards.test.ts 的 HOOK_IDENT 覆盖。
 */
export function __avaTestSetKeychainExec(path: string): void {
  keychainExec = path;
}

export function keychainExecPath(): string {
  return keychainExec;
}

export function pythonOf(repoRoot: string): string {
  return `${repoRoot}/.venv/bin/python`;
}

/** buildArgv 的产物：argv + 超时 + stdout 上限 + stdin/PATH 两类受控例外。 */
export interface BuiltCore {
  argv: string[];
  /** null = 无超时（RUN_TTS_APPLY_PATCH，Spec 11 S8-R17 的模板级开口） */
  timeoutMs: number | null;
  stdoutMax?: number;
  /** stdin pipe 例外（Spec 11 S8-R13 / Spec 12 S8-R18）：写入端写完即 end() */
  stdinPipe?: boolean;
  /** git/ffprobe 在 /opt/homebrew/bin（SEAL_SCRIPT 与 RUN_TTS_APPLY_PATCH，A3；SESSION_* 在 spawnSession 里恒开，N49） */
  homebrewPath?: boolean;
}

/** stdoutMax：要求完整读入 stdout 的模板给出上限（字节）；其余模板只保留尾部 */
export function buildArgv<T extends Template>(t: T, args: TemplateArgs[T], repoRoot: string): BuiltCore {
  const py = pythonOf(repoRoot);
  switch (t) {
    case "PROBE_APPROVALS":
      return { argv: [py, "-c", "import pipeline.approvals"], timeoutMs: SPAWN_TIMEOUT_SHORT_MS };
    case "PROBE_FREEZE":
      return {
        argv: [py, "-c", "import sys; from pipeline.agent.cli import check_code_freeze; sys.exit(0 if check_code_freeze() else 3)"],
        timeoutMs: SPAWN_TIMEOUT_SHORT_MS,
      };
    case "GIT_HEAD":
      return { argv: [GIT, "-C", repoRoot, "rev-parse", "HEAD"], timeoutMs: SPAWN_TIMEOUT_SHORT_MS };
    case "GIT_DESKTOP_DIFF": {
      const { buildHead } = args as TemplateArgs["GIT_DESKTOP_DIFF"];
      return { argv: [GIT, "-C", repoRoot, "diff", "--quiet", buildHead, "HEAD", "--", "desktop/"], timeoutMs: SPAWN_TIMEOUT_SHORT_MS };
    }
    case "STATUS": {
      const { ep } = args as TemplateArgs["STATUS"];
      // JSON 只能整份解析：取尾部会在输出超过尾部长度时截掉开头（S22 🔵6）
      return { argv: [py, "-m", "pipeline.status", ep, "--json"], timeoutMs: SPAWN_TIMEOUT_SHORT_MS, stdoutMax: STATUS_STDOUT_MAX_BYTES };
    }
    case "HEAL": {
      const { ep } = args as TemplateArgs["HEAL"];
      return { argv: [py, "-m", "pipeline.agent.cli", ep, "/approvals"], timeoutMs: SPAWN_TIMEOUT_ACK_MS };
    }
    case "APPROVE": {
      const { ep, stop, approvalId, cover, title } = args as TemplateArgs["APPROVE"];
      const argv = [py, "-m", "pipeline.agent.cli", ep, "/approve", stop, "--id", approvalId];
      // 09 定稿：argv 追加 --cover <路径> --title <标题>（空格固定位置，与裸形态同一套语法）
      if (cover !== undefined && title !== undefined) argv.push("--cover", cover, "--title", title);
      return { argv, timeoutMs: SPAWN_TIMEOUT_ACK_MS };
    }
    case "REJECT": {
      // target / problem 各为一个 argv 元素，不经 shell、不拼接（Spec 3 §4.2 第 1 条固定位置语法）
      const { ep, stop, approvalId, target, problem } = args as TemplateArgs["REJECT"];
      return { argv: [py, "-m", "pipeline.agent.cli", ep, "/reject", stop, "--id", approvalId, target, problem], timeoutMs: SPAWN_TIMEOUT_ACK_MS };
    }
    case "REVIEW_APPROVE": {
      // 必须用等号形式：tools.py _extract_positional_args 的 valued_flags 不含这两个旗标，
      // 空格形式会把值当成位置参数，validate_pipeline_command 随之不注入期目录（Spec 3 v0.6 §4.5）
      const { ep, size, mtimeNs } = args as TemplateArgs["REVIEW_APPROVE"];
      return {
        argv: [py, "-m", "pipeline.agent.cli", ep, "/run", "review", "--approve", `--expect-size=${size}`, `--expect-mtime-ns=${String(mtimeNs)}`],
        timeoutMs: SPAWN_TIMEOUT_REVIEW_MS,
      };
    }
    case "NEW_EPISODE": {
      // 期名单个 argv 元素（shell:false）：名字里的空格 / 中文 / 怪字符都逐字节到达 core，校验全在 core。
      // 恒带 --from-idea（Spec 18 §3.3）：选题会话没有记录时 core 回 migrated=false，语义等价于不带，零分叉
      const { name } = args as TemplateArgs["NEW_EPISODE"];
      return { argv: [py, "-m", "pipeline.agent.cli", "new", name, "--from-idea"], timeoutMs: SPAWN_TIMEOUT_SHORT_MS };
    }
    case "PROBE_KEY_ENV":
      // 只读配置、不读环境变量（C10-R2）；stdout 就是变量名，缺失为空串
      return { argv: [py, "-c", "import sys; from pipeline.agent.llm import api_key_env_name; sys.stdout.write(api_key_env_name() or '')"], timeoutMs: SPAWN_TIMEOUT_SHORT_MS };
    case "PROBE_WEB_KEY_ENVS":
      // Spec 15 §2.7 第 1/6 条：只读配置、不读环境变量；stdout 每行一个名字（UTF-8、无其他内容），配置无效为空
      return {
        argv: [py, "-c", "import sys; from pipeline.agent.web import web_key_env_names; sys.stdout.write(''.join(n + '\\n' for n in web_key_env_names()))"],
        timeoutMs: SPAWN_TIMEOUT_SHORT_MS,
      };
    case "KEYCHAIN_READ": {
      const { envName } = args as TemplateArgs["KEYCHAIN_READ"];
      return { argv: [keychainExec, "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", envName, "-w"], timeoutMs: SPAWN_TIMEOUT_SHORT_MS };
    }
    // ---- Spec 11 §3.4：停机点组件子命令（全部是 core 裸形态，不过 validate_pipeline_command） ----
    case "SAVE_SCRIPT": {
      const { ep, size, mtimeNs } = args as TemplateArgs["SAVE_SCRIPT"];
      return {
        argv: [py, "-m", "pipeline.agent.cli", ep, "/save-script", `--expect-size=${size}`, `--expect-mtime-ns=${mtimeNs}`],
        timeoutMs: SPAWN_TIMEOUT_ACK_MS,
        stdinPipe: true,
      };
    }
    case "SEAL_SCRIPT": {
      const { ep } = args as TemplateArgs["SEAL_SCRIPT"];
      // 封板在 core 内跑 `git diff --no-index`：git 在 /opt/homebrew/bin，须显式开口（A3）
      return { argv: [py, "-m", "pipeline.agent.cli", ep, "/seal-script"], timeoutMs: SPAWN_TIMEOUT_ACK_MS, homebrewPath: true };
    }
    case "CHECK_SCRIPT": {
      const { scriptAbs } = args as TemplateArgs["CHECK_SCRIPT"];
      return { argv: [py, "-m", "pipeline.check_script", scriptAbs], timeoutMs: SPAWN_TIMEOUT_ACK_MS };
    }
    case "VOICE_INFO": {
      const { ep } = args as TemplateArgs["VOICE_INFO"];
      return { argv: [py, "-m", "pipeline.agent.cli", ep, "/voice-info"], timeoutMs: SPAWN_TIMEOUT_ACK_MS, stdoutMax: VOICE_INFO_STDOUT_MAX_BYTES };
    }
    case "VOICE_PARSE": {
      const { ep } = args as TemplateArgs["VOICE_PARSE"];
      return { argv: [py, "-m", "pipeline.agent.cli", ep, "/voice-parse"], timeoutMs: SPAWN_TIMEOUT_ACK_MS, stdinPipe: true };
    }
    case "VOICE_ADD": {
      const { ep } = args as TemplateArgs["VOICE_ADD"];
      return { argv: [py, "-m", "pipeline.agent.cli", ep, "/voice-add"], timeoutMs: SPAWN_TIMEOUT_ACK_MS, stdinPipe: true };
    }
    case "VOICE_REVERT": {
      const { ep, label } = args as TemplateArgs["VOICE_REVERT"];
      return { argv: [py, "-m", "pipeline.agent.cli", ep, "/voice-revert", label], timeoutMs: SPAWN_TIMEOUT_ACK_MS };
    }
    case "VOICE_RETRACT": {
      const { ep, id } = args as TemplateArgs["VOICE_RETRACT"];
      return { argv: [py, "-m", "pipeline.agent.cli", ep, "/voice-retract", id], timeoutMs: SPAWN_TIMEOUT_ACK_MS };
    }
    case "RECORD_TIME": {
      const { ep, stop, entered, left } = args as TemplateArgs["RECORD_TIME"];
      return {
        argv: [py, "-m", "pipeline.agent.cli", ep, "/record-time", stop, `--entered=${entered}`, `--left=${left}`],
        timeoutMs: SPAWN_TIMEOUT_ACK_MS,
      };
    }
    case "RUN_TTS_APPLY_PATCH": {
      const { ep } = args as TemplateArgs["RUN_TTS_APPLY_PATCH"];
      // 无超时（长任务；S8-R17）；tts/ffprobe 在 PATH 白名单之外，须显式开口
      return { argv: [py, "-m", "pipeline.agent.cli", ep, "/run", "tts", "--apply-patch"], timeoutMs: null, homebrewPath: true };
    }
    case "VOICE_CHECK": {
      const a = args as TemplateArgs["VOICE_CHECK"];
      return { argv: [py, "-m", "pipeline.corrections", "check", ...readingFlags(a)], timeoutMs: SPAWN_TIMEOUT_ACK_MS };
    }
    case "VOICE_GLOBAL": {
      const a = args as TemplateArgs["VOICE_GLOBAL"];
      return {
        argv: [py, "-m", "pipeline.corrections", "global", a.ep, ...readingFlags(a), ...(a.supersede ? ["--supersede"] : [])],
        timeoutMs: SPAWN_TIMEOUT_ACK_MS,
      };
    }
    case "RUN_TTS": {
      const { ep } = args as TemplateArgs["RUN_TTS"];
      return { argv: [py, "-m", "pipeline.agent.cli", ep, "/run", "tts"], timeoutMs: null, homebrewPath: true };
    }
    case "TTS_REVIEW": {
      const { ep, review } = args as TemplateArgs["TTS_REVIEW"];
      if (!REVIEW_RE.test(review)) throw new Error(`打点格式不对：「${review}」`);
      return { argv: [py, "-m", "pipeline.tts", ep, `--review=${review}`], timeoutMs: SPAWN_TIMEOUT_ACK_MS };
    }
    case "IMPORT_COVER": {
      const { ep, name } = args as TemplateArgs["IMPORT_COVER"];
      return { argv: [py, "-m", "pipeline.agent.cli", ep, "/import-cover", `--name=${name}`], timeoutMs: SPAWN_TIMEOUT_ACK_MS, stdinPipe: true };
    }
    case "LIST_SESSIONS": {
      const { ep } = args as TemplateArgs["LIST_SESSIONS"];
      return { argv: [py, "-m", "pipeline.agent.cli", ep, "/list-sessions"], timeoutMs: SPAWN_TIMEOUT_SHORT_MS, stdoutMax: SESSIONS_STDOUT_MAX_BYTES };
    }
    case "DELETE_SESSION": {
      const { ep, sid } = args as TemplateArgs["DELETE_SESSION"];
      return { argv: [py, "-m", "pipeline.agent.cli", ep, "/delete-session", `--sid=${checkedSid(sid)}`], timeoutMs: SPAWN_TIMEOUT_ACK_MS };
    }
    default:
      throw new Error(`未知 spawn 模板 ${String(t)}`);
  }
}

function checkedSid(sid: string): string {
  if (!SESSION_ID_RE.test(sid)) throw new Error(`会话号形状不对：「${sid}」`);
  return sid;
}

/** SESSION_* 的 argv（Spec 10 §3.4）：`ep` 为 host 映射且刚 stat 过的绝对路径。
 * D45：SESSION_CONTINUE 带 `sid` 时恢复指定会话（`--continue <sid>`），不带时恢复最近的可恢复会话。 */
export function sessionArgv(t: SessionTemplate, ep: string | undefined, repoRoot: string, sid?: string): string[] {
  const py = pythonOf(repoRoot);
  switch (t) {
    case "SESSION_NEW":
      return [py, "-m", "pipeline.agent.protocol", ep as string];
    case "SESSION_CONTINUE":
      return sid === undefined
        ? [py, "-m", "pipeline.agent.protocol", ep as string, "--continue"]
        : [py, "-m", "pipeline.agent.protocol", ep as string, "--continue", checkedSid(sid)];
    case "SESSION_IDEA":
      return [py, "-m", "pipeline.agent.protocol", "--idea"];
  }
}

/**
 * 环境变量白名单（不继承 process.env）。PATH 固定：Finder 启动的 GUI app 拿不到 shell 的 PATH，
 * 固定后「将来加了需要 ffmpeg 的模板」会当场失败（RF-3）。明确排除 AVA_EVENTS_ROOT、*_API_KEY、*_TOKEN、NODE_OPTIONS、ELECTRON_*。
 */
export function childEnv(src: Record<string, string | undefined>, homebrewPath = false): Record<string, string> {
  const path = homebrewPath ? "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin" : "/usr/bin:/bin:/usr/sbin:/sbin";
  const env: Record<string, string> = {
    PATH: path,
    LANG: src.LANG || "en_US.UTF-8",
    PYTHONUTF8: "1",
    PYTHONUNBUFFERED: "1",
  };
  for (const k of ["HOME", "USER", "TMPDIR"] as const) {
    const v = src[k];
    if (v) env[k] = v;
  }
  return env;
}

class Tail {
  private chunks: Buffer[] = [];
  private bytes = 0;
  push(b: Buffer): void {
    this.chunks.push(b);
    this.bytes += b.length;
    while (this.chunks.length > 1 && this.bytes - this.chunks[0].length >= SPAWN_TAIL_BYTES) {
      this.bytes -= this.chunks.shift()!.length;
    }
  }
  text(): string {
    const all = Buffer.concat(this.chunks);
    return all.subarray(Math.max(0, all.length - SPAWN_TAIL_BYTES)).toString("utf-8");
  }
}

/** 完整读入：stdout 分多块到达，逐块累积；累计超过上限即标记溢出并停止累积（子进程输出无法回头重读） */
class Full {
  private chunks: Buffer[] = [];
  private bytes = 0;
  overflow = false;
  constructor(private readonly max: number) {}
  push(b: Buffer): void {
    if (this.overflow) return;
    this.bytes += b.length;
    if (this.bytes > this.max) {
      this.overflow = true;
      this.chunks = [];
      return;
    }
    this.chunks.push(b);
  }
  text(): string | null {
    return this.overflow ? null : Buffer.concat(this.chunks).toString("utf-8"); // 拼接后再解码：多字节字符可能跨块
  }
}

export interface SpawnLogEntry {
  template: Template | SessionTemplate;
  argv: string[];
  at: number;
  trigger?: string;
  decide?: number;
}

/** 测试与诊断用：本进程最近 SPAWN_LOG_MAX 次 spawn（按时间序，环形缓冲）。 */
export const spawnLog: SpawnLogEntry[] = [];
let spawnCount = 0;

/** 本进程累计 spawn 次数（单调）：「零 spawn」类断言用它，环形缓冲满后 spawnLog.length 不再增长 */
export function spawnTotal(): number {
  return spawnCount;
}

let spawnObserver: ((e: SpawnLogEntry) => void) | null = null;

/** 未打包构建的 e2e 观测口：host 入口据此把每次 spawn 打到 stdout（仅未打包构建接入，TG-6） */
export function setSpawnObserver(fn: ((e: SpawnLogEntry) => void) | null): void {
  spawnObserver = fn;
}

export function recordSpawn(e: SpawnLogEntry): void {
  spawnCount += 1;
  spawnLog.push(e);
  if (spawnLog.length > SPAWN_LOG_MAX) spawnLog.splice(0, spawnLog.length - SPAWN_LOG_MAX);
  spawnObserver?.(e);
}

export function runArgv(
  argv: string[],
  timeoutMs: number | null,
  cwd: string,
  env: Record<string, string>,
  stdoutMax?: number,
  stdinData?: string | Uint8Array,
): Promise<CoreResult> {
  return new Promise((resolve) => {
    const out = new Tail();
    const full = stdoutMax === undefined ? null : new Full(stdoutMax);
    const err = new Tail();
    let timedOut = false;
    let settled = false;
    let killTimer: NodeJS.Timeout | null = null;
    const pipe = stdinData !== undefined;
    const child = spawn(argv[0], argv.slice(1), {
      shell: false,
      stdio: [pipe ? "pipe" : "ignore", "pipe", "pipe"],
      detached: true,
      cwd,
      env,
    });
    if (pipe) {
      // 写入端写完即 end()：core 侧限流读取（Spec 11 §3.1 / Spec 12 §3.1）
      child.stdin?.on("error", () => undefined); // core 早退（如限流拒收）导致的 EPIPE 不炸 host
      child.stdin?.end(stdinData);
    }
    const finish = (r: CoreResult) => {
      if (settled) return;
      settled = true;
      if (timer) clearTimeout(timer);
      if (killTimer) clearTimeout(killTimer);
      resolve(r);
    };
    child.stdout?.on("data", (b: Buffer) => {
      out.push(b);
      full?.push(b);
    });
    child.stderr?.on("data", (b: Buffer) => err.push(b));
    // 超时对整个进程组发信号：只杀直接子进程会让孙进程成孤儿继续写（红队 M2）
    const timer = timeoutMs === null ? null : setTimeout(() => {
      timedOut = true;
      const pid = child.pid;
      if (pid === undefined) return;
      try {
        process.kill(-pid, "SIGTERM");
      } catch {
        /* 进程组已不存在 */
      }
      killTimer = setTimeout(() => {
        try {
          process.kill(-pid, "SIGKILL");
        } catch {
          /* 进程组已不存在 */
        }
      }, SPAWN_KILL_GRACE_MS);
    }, timeoutMs ?? 0);
    const fullOf = () => ({ stdoutFull: full ? full.text() : null, stdoutOverflow: full?.overflow ?? false });
    child.on("error", (e) =>
      finish({ code: null, signal: null, stdoutTail: out.text(), stderrTail: `${err.text()}${e.message}`, timedOut, ...fullOf() }),
    );
    child.on("close", (code, signal) =>
      finish({ code, signal: signal ?? null, stdoutTail: out.text(), stderrTail: err.text(), timedOut, ...fullOf() }),
    );
  });
}

/**
 * 模板的长任务例外（S8-R17）：`RUN_TTS_APPLY_PATCH` **永不**向在途子进程发信号——
 * host 进程退出时该子进程是 detached 的孤儿，自行跑完（`apply_patch_lock` 的 finally 清锁）。
 * 这里只提供一个「是否长任务」的判定，宿主退出路径据此跳过收尾。
 */
export function isLongRunning(t: Template): boolean {
  // D50-A S6：RUN_TTS（按全局读音表普通重跑）同为配音长任务，同一例外
  return t === "RUN_TTS_APPLY_PATCH" || t === "RUN_TTS";
}

export function runCore<T extends Template>(
  t: T,
  args: TemplateArgs[T],
  ctx: { repoRoot: string },
  tag: SpawnTag = {},
  stdinData?: string | Uint8Array,
): Promise<CoreResult> {
  const { argv, timeoutMs, stdoutMax, homebrewPath } = buildArgv(t, args, ctx.repoRoot);
  recordSpawn({ template: t, argv, at: Date.now(), ...tag });
  return runArgv(argv, timeoutMs, ctx.repoRoot, childEnv(process.env, homebrewPath === true), stdoutMax, stdinData);
}

/**
 * Spec 10 §3.4：会话进程的窄接口；`ChildProcess` 类型/API 不漏出本文件（TG-3）。
 * stdin 为 pipe、无超时（长驻）；detached 使其成为进程组组长，退出时 host 对整个进程组收尾（§2.10）。
 */
export interface SessionProc {
  readonly pid: number | undefined;
  write(line: string): void;
  onStdout(fn: (chunk: Buffer) => void): void;
  onStderr(fn: (chunk: Buffer) => void): void;
  onExit(fn: (code: number | null, signal: string | null) => void): void;
  /** group=true 时对整组发信号（结束序列的 SIGKILL）；SIGTERM 只发给会话 pid（§2.10）。 */
  signal(sig: NodeJS.Signals, group: boolean): void;
}

export function spawnSession(t: SessionTemplate, args: { ep?: string; sid?: string }, ctx: { repoRoot: string }, extraEnv: Record<string, string>): SessionProc {
  const argv = sessionArgv(t, args.ep, ctx.repoRoot, args.sid);
  // spawn 日志只记模板名与 argv，不记环境（§3.4）；密钥值绝不进任何日志（TH-6）
  recordSpawn({ template: t, argv, at: Date.now() });
  const child = spawn(argv[0], argv.slice(1), {
    shell: false,
    stdio: ["pipe", "pipe", "pipe"],
    detached: true,
    cwd: ctx.repoRoot,
    // N49：会话里 run_pipeline 起的作业（jobs.py 原样继承本 env）按名字调 ffmpeg/ffprobe（与 acquire 的 yt-dlp），
    // 它们只在 /opt/homebrew/bin——与 SEAL_SCRIPT、RUN_TTS_APPLY_PATCH 同一开口（A3）。追加在末尾，系统目录里的同名程序仍优先。
    // extraEnv 只可能是密钥变量：指名 PATH 的配置在注入前就被 validKeyEnvName 拒掉（TH-7），覆盖不到这里。
    env: { ...childEnv(process.env, true), ...extraEnv },
  });
  return {
    pid: child.pid,
    write: (line) => void child.stdin?.write(line),
    onStdout: (fn) => void child.stdout?.on("data", fn),
    onStderr: (fn) => void child.stderr?.on("data", fn),
    onExit: (fn) => child.on("exit", (code, signal) => fn(code, signal ?? null)),
    signal: (sig, group) => {
      const pid = child.pid;
      if (pid === undefined) return;
      try {
        process.kill(group ? -pid : pid, sig);
      } catch {
        /* 进程组已不存在 */
      }
    },
  };
}

/** §2.10 进程组清理：会话 pid 退出后若组内仍有成员，对整组发一次 SIGKILL。 */
export function killGroup(pid: number): void {
  try {
    process.kill(-pid, "SIGKILL");
  } catch {
    /* 进程组已不存在 */
  }
}

/** 组内是否仍有存活成员（探测用；EPERM 也表示存在）。 */
export function groupAlive(pid: number): boolean {
  try {
    process.kill(-pid, 0);
    return true;
  } catch (e) {
    return (e as NodeJS.ErrnoException).code === "EPERM";
  }
}
