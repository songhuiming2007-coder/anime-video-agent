// spawn 闭集（Spec 8 §3.4）。desktop/ 中唯一 import node:child_process 的文件（TG-3）。
// 全部以 argv 数组调用：shell:false、stdin ignore、detached（子进程为进程组组长）、cwd = repoRoot、环境变量白名单。
// PR2 接入 PROBE_* / GIT_* / STATUS；HEAL / APPROVE / REJECT / REVIEW_APPROVE 属 PR4。
import { spawn } from "node:child_process";
import {
  SPAWN_KILL_GRACE_MS,
  SPAWN_LOG_MAX,
  SPAWN_TAIL_BYTES,
  SPAWN_TIMEOUT_SHORT_MS,
  STATUS_STDOUT_MAX_BYTES,
} from "../shared/constants";

export type Template = "PROBE_APPROVALS" | "PROBE_FREEZE" | "GIT_HEAD" | "GIT_DESKTOP_DIFF" | "STATUS";

export interface TemplateArgs {
  PROBE_APPROVALS: Record<string, never>;
  PROBE_FREEZE: Record<string, never>;
  GIT_HEAD: Record<string, never>;
  GIT_DESKTOP_DIFF: { buildHead: string };
  STATUS: { ep: string };
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

export function pythonOf(repoRoot: string): string {
  return `${repoRoot}/.venv/bin/python`;
}

/** stdoutMax：要求完整读入 stdout 的模板给出上限（字节）；其余模板只保留尾部 */
export function buildArgv<T extends Template>(t: T, args: TemplateArgs[T], repoRoot: string): { argv: string[]; timeoutMs: number; stdoutMax?: number } {
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
    default:
      throw new Error(`未知 spawn 模板 ${String(t)}`);
  }
}

/**
 * 环境变量白名单（不继承 process.env）。PATH 固定：Finder 启动的 GUI app 拿不到 shell 的 PATH，
 * 固定后「将来加了需要 ffmpeg 的模板」会当场失败（RF-3）。明确排除 AVA_EVENTS_ROOT、*_API_KEY、*_TOKEN、NODE_OPTIONS、ELECTRON_*。
 */
export function childEnv(src: Record<string, string | undefined>): Record<string, string> {
  const env: Record<string, string> = {
    PATH: "/usr/bin:/bin:/usr/sbin:/sbin",
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
  template: Template;
  argv: string[];
  at: number;
}

/** 测试与诊断用：本进程最近 SPAWN_LOG_MAX 次 spawn（按时间序，环形缓冲）。 */
export const spawnLog: SpawnLogEntry[] = [];
let spawnCount = 0;

/** 本进程累计 spawn 次数（单调）：「零 spawn」类断言用它，环形缓冲满后 spawnLog.length 不再增长 */
export function spawnTotal(): number {
  return spawnCount;
}

export function recordSpawn(e: SpawnLogEntry): void {
  spawnCount += 1;
  spawnLog.push(e);
  if (spawnLog.length > SPAWN_LOG_MAX) spawnLog.splice(0, spawnLog.length - SPAWN_LOG_MAX);
}

export function runArgv(argv: string[], timeoutMs: number, cwd: string, env: Record<string, string>, stdoutMax?: number): Promise<CoreResult> {
  return new Promise((resolve) => {
    const out = new Tail();
    const full = stdoutMax === undefined ? null : new Full(stdoutMax);
    const err = new Tail();
    let timedOut = false;
    let settled = false;
    let killTimer: NodeJS.Timeout | null = null;
    const child = spawn(argv[0], argv.slice(1), {
      shell: false,
      stdio: ["ignore", "pipe", "pipe"],
      detached: true,
      cwd,
      env,
    });
    const finish = (r: CoreResult) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (killTimer) clearTimeout(killTimer);
      resolve(r);
    };
    child.stdout?.on("data", (b: Buffer) => {
      out.push(b);
      full?.push(b);
    });
    child.stderr?.on("data", (b: Buffer) => err.push(b));
    // 超时对整个进程组发信号：只杀直接子进程会让孙进程成孤儿继续写（红队 M2）
    const timer = setTimeout(() => {
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
    }, timeoutMs);
    const fullOf = () => ({ stdoutFull: full ? full.text() : null, stdoutOverflow: full?.overflow ?? false });
    child.on("error", (e) =>
      finish({ code: null, signal: null, stdoutTail: out.text(), stderrTail: `${err.text()}${e.message}`, timedOut, ...fullOf() }),
    );
    child.on("close", (code, signal) =>
      finish({ code, signal: signal ?? null, stdoutTail: out.text(), stderrTail: err.text(), timedOut, ...fullOf() }),
    );
  });
}

export function runCore<T extends Template>(t: T, args: TemplateArgs[T], ctx: { repoRoot: string }): Promise<CoreResult> {
  const { argv, timeoutMs, stdoutMax } = buildArgv(t, args, ctx.repoRoot);
  recordSpawn({ template: t, argv, at: Date.now() });
  return runArgv(argv, timeoutMs, ctx.repoRoot, childEnv(process.env), stdoutMax);
}
