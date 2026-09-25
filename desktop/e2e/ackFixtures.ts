// ack 链路 e2e 夹具（Spec 8 §7.1 TA 系列）：临时 repo 副本上的各停机点期目录、「终端」操作、假 core、spawn 日志解析。
// 全部在临时目录里跑，真实 data/ 零污染（global-teardown 比对）。
import { execFileSync, spawnSync } from "node:child_process";
import { mkdirSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { expect, type Page } from "@playwright/test";
import { makeFixtureRepo, tmp } from "../tests/helpers";
import type { Launched } from "./fixtures";

/** 不能被 double 精确表示的纳秒 mtime（TE-13 / TA-2 同款） */
export const NS = "1790171112636927676";

export interface AckRepo {
  repo: string;
  eps: string;
  /** data 在仓库外时的真实目录（外置盘夹具），用例结束一并清理 */
  dataReal: string | null;
}

/** 临时 repo 副本；base 给定时建在该目录下（假设 8：外置盘上跑一次 TA-2） */
export function ackRepo(opts: { withApprovals?: boolean; base?: string } = {}): AckRepo {
  const repo = makeFixtureRepo({ withApprovals: opts.withApprovals });
  if (opts.base) {
    // data 换成指向外置盘临时目录的符号链接（与本机 data -> 外置盘同构）
    const dataReal = join(opts.base, ".ava-e2e-tmp", `data-${process.pid}-${Date.now()}`);
    rmSync(join(repo, "data"), { recursive: true });
    mkdirSync(join(dataReal, "library/shots"), { recursive: true });
    mkdirSync(join(dataReal, "episodes"), { recursive: true });
    symlinkSync(dataReal, join(repo, "data"));
    return { repo, eps: join(repo, "data/episodes"), dataReal };
  }
  return { repo, eps: join(repo, "data/episodes"), dataReal: null };
}

/** 在夹具仓库里以夹具解释器跑 Python（cwd = 夹具 repo，import 的是副本里的 pipeline） */
export function fpy(repo: string, code: string, args: string[] = [], input?: string): string {
  return execFileSync(join(repo, ".venv/bin/python"), ["-c", code, ...args], {
    cwd: repo,
    encoding: "utf-8",
    input,
    env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1", PYTHONUTF8: "1" },
  });
}

/** 「终端」执行 ava 裸形态命令；返回退出码与输出（非 TTY） */
export function ava(repo: string, args: string[], input = ""): { code: number | null; stdout: string; stderr: string } {
  const r = spawnSync(join(repo, ".venv/bin/python"), ["-m", "pipeline.agent.cli", ...args], {
    cwd: repo,
    encoding: "utf-8",
    input,
    env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1", PYTHONUTF8: "1" },
  });
  return { code: r.status, stdout: r.stdout, stderr: r.stderr };
}

export function statusJson(repo: string, ep: string): { current_step: string; is_blocked: boolean } {
  return JSON.parse(execFileSync(join(repo, ".venv/bin/python"), ["-m", "pipeline.status", ep, "--json"], { cwd: repo, encoding: "utf-8", env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1" } }));
}

// ---------------- 各停机点的期目录（形状与 tests/test_approvals.py 的构造函数一致） ----------------

export interface EpOpts {
  /** 04-clips.json 的段时长；与 manifest 的 4.0 不等即段级不对齐 */
  clipDur?: number;
  /** 段带 via: "patch-rescue"（补丁段，review --approve 须二次确认） */
  patch?: boolean;
  /** 把 04-clips.json 的 mtime 设为 NS */
  nsMtime?: boolean;
}

export function epAt03(eps: string, name: string): string {
  const ep = join(eps, name);
  mkdirSync(ep, { recursive: true });
  writeFileSync(join(ep, "01-topic.md"), "# 选题\n类型：杂谈\n");
  writeFileSync(join(ep, "02-script.draft.md"), "# 草稿\n");
  writeFileSync(join(ep, "02-script.md"), "## 段落 1\n配音：测试台词。\n");
  writeFileSync(join(ep, "02-diff.patch"), "--- a\n+++ b\n@@ -1 +1 @@\n-old\n+new\n");
  return ep;
}

export function writeManifest(ep: string, durations: number[] = [4.0]): void {
  mkdirSync(join(ep, "03-audio"), { recursive: true });
  writeFileSync(join(ep, "03-audio/manifest.json"), JSON.stringify({ segments: durations.map((d, i) => ({ index: i + 1, duration: d })) }));
}

export function epAt035(eps: string, name: string): string {
  const ep = epAt03(eps, name);
  writeManifest(ep);
  return ep;
}

export function clipsJson(opts: EpOpts = {}, start = 10.0): string {
  const dur = opts.clipDur ?? 4.0;
  const seg: Record<string, unknown> = { index: 1, status: "ok", duration: 4.0, clips: [{ season: 1, episode: 1, start, dur }] };
  if (opts.patch) seg.via = "patch-rescue";
  return JSON.stringify({ anime: "TestAnime", total_duration: 4.0, segments: [seg] }, null, 2);
}

export function writeClips(repo: string, ep: string, opts: EpOpts = {}, start = 10.0): void {
  writeFileSync(join(ep, "04-clips.json"), clipsJson(opts, start));
  if (opts.nsMtime) fpy(repo, "import os, sys; n = int(sys.argv[2]); os.utime(sys.argv[1], ns=(n, n))", [join(ep, "04-clips.json"), NS]);
}

export function epAt05(repo: string, eps: string, name: string, opts: EpOpts = {}): string {
  const ep = epAt035(eps, name);
  writeClips(repo, ep, opts);
  writeFileSync(join(ep, "04-review.html"), "<!doctype html><meta charset=utf-8><title>审片</title><p>审片页</p>");
  return ep;
}

// ---------------- 对象库与记账读取 ----------------

export interface StoreObj {
  approval_id: string;
  type: string;
  status: string;
  resolved_by: string | null;
  confirmed_by: string | null;
  feedback: { target: string; problem: string } | null;
  artifacts: { path: string; size: number; mtime_ns: number }[];
  created_at: string;
}

export function readStore(ep: string): StoreObj[] {
  try {
    return JSON.parse(readFileSync(join(ep, "_agent/approvals_store.json"), "utf-8"));
  } catch {
    return [];
  }
}

export function storeText(ep: string): string {
  return readFileSync(join(ep, "_agent/approvals_store.json"), "utf-8");
}

export function decisionLines(ep: string): Record<string, unknown>[] {
  try {
    return readFileSync(join(ep, "_agent/approvals.jsonl"), "utf-8").trim().split("\n").filter(Boolean).map((l) => JSON.parse(l));
  } catch {
    return [];
  }
}

export function eventsOf(ep: string): { type: string; payload: Record<string, unknown> }[] {
  try {
    return readFileSync(join(ep, "events.jsonl"), "utf-8").trim().split("\n").filter(Boolean).map((l) => JSON.parse(l));
  } catch {
    return [];
  }
}

// ---------------- 假 core：改写副本里 cli.py 的 __main__ 分派（其余命令照常走真实 main） ----------------

export function fakeCore(repo: string, pythonBody: string): void {
  const f = join(repo, "pipeline/agent/cli.py");
  const src = readFileSync(f, "utf-8");
  const tail = 'if __name__ == "__main__":\n    raise SystemExit(main())\n';
  if (!src.endsWith(tail)) throw new Error("cli.py 结尾不是预期的 __main__ 分派，假 core 无法注入");
  const body = pythonBody
    .split("\n")
    .map((l) => `    ${l}`)
    .join("\n");
  writeFileSync(f, `${src.slice(0, -tail.length)}if __name__ == "__main__":\n    import os as _os, sys as _sys, time as _time, subprocess as _sp\n${body}\n    raise SystemExit(main())\n`);
}

// ---------------- spawn 日志（未打包构建的 host 每次 spawn 打一行 AVA_SPAWN） ----------------

export interface SpawnRec {
  template: string;
  argv: string[];
  trigger: string | null;
  decide: number | null;
}

export function spawns(L: Launched): SpawnRec[] {
  const out: SpawnRec[] = [];
  for (const m of L.stdout().matchAll(/AVA_SPAWN (\{.*\})/g)) out.push(JSON.parse(m[1]));
  return out;
}

/** 最近一次 decide 关联的 spawn（TA-2：只统计该次 decide 关联的 spawn，红队 m4） */
export function lastDecideSpawns(L: Launched): SpawnRec[] {
  const all = spawns(L).filter((s) => s.decide !== null);
  if (all.length === 0) return [];
  const id = Math.max(...all.map((s) => s.decide!));
  return all.filter((s) => s.decide === id);
}

export const ACK_TEMPLATES = ["APPROVE", "REJECT", "REVIEW_APPROVE"];

export function heals(L: Launched): string[] {
  return spawns(L)
    .filter((s) => s.template === "HEAL")
    .map((s) => `${s.argv[3].split("/").pop()}(${(s.trigger ?? "?").split("-")[0]})`);
}

// ---------------- host 侧测试钩子（经 main 中继；仅未打包构建） ----------------

export async function arm(L: Launched, name: string): Promise<void> {
  await L.app.evaluate((_e, n) => (globalThis as unknown as { __avaTestArm: (n: string) => void }).__avaTestArm(n), name);
}

export async function waitHit(L: Launched, name: string, timeout = 15_000): Promise<void> {
  await expect
    .poll(() => L.app.evaluate((_e, n) => (globalThis as unknown as { __avaTestHookHits: string[] }).__avaTestHookHits.includes(n), name), { timeout })
    .toBe(true);
}

export async function release(L: Launched, name: string): Promise<void> {
  await L.app.evaluate((_e, n) => {
    const g = globalThis as unknown as { __avaTestHookHits: string[]; __avaTestRelease: (n: string) => void };
    g.__avaTestHookHits.splice(g.__avaTestHookHits.indexOf(n), 1);
    g.__avaTestRelease(n);
  }, name);
}

export async function hostPid(L: Launched): Promise<number | null> {
  return L.app.evaluate(() => (globalThis as unknown as { __avaTestHostPid: () => number | null }).__avaTestHostPid());
}

/** 经 renderer 当前端口发任意请求（TI-8 / TA-12；不经任何组件） */
export async function call(page: Page, method: string, params?: Record<string, unknown>): Promise<{ ok: true; result: unknown } | { ok: false; code: string; message: string }> {
  return page.evaluate(
    ([m, p]) =>
      (window as unknown as { __avaTestCall: (m: string, p?: unknown) => Promise<unknown> }).__avaTestCall(m as string, p as Record<string, unknown> | undefined).then(
        (result) => ({ ok: true as const, result }),
        (e: { error?: { code: string; message: string } }) => ({ ok: false as const, code: e.error?.code ?? "?", message: e.error?.message ?? String(e) }),
      ),
    [method, params] as const,
  );
}

export async function setDialog(L: Launched, respond: { repoRoot: string | null; confirm: boolean }): Promise<void> {
  await L.app.evaluate((_e, r) => {
    (globalThis as unknown as { __avaTestDialog: { calls: number; respond: unknown } }).__avaTestDialog = { calls: 0, respond: r };
  }, respond);
}

export async function dialogCalls(L: Launched): Promise<number> {
  return L.app.evaluate(() => (globalThis as unknown as { __avaTestDialog?: { calls: number } }).__avaTestDialog?.calls ?? -1);
}

export function epRow(page: Page, epKey: string) {
  return page.locator("[data-testid=episode]").filter({ has: page.locator(".ep-name", { hasText: new RegExp(`^${epKey.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}$`) }) });
}

/** 设为活跃期并等载入（H1 heal + status）真正结束 */
export async function openEp(page: Page, epKey: string): Promise<void> {
  await epRow(page, epKey).click();
  await settled(page, epKey);
}

export async function settled(page: Page, epKey: string): Promise<void> {
  await expect(page.getByTestId("center")).toHaveAttribute("data-ep", epKey);
  await expect(page.getByTestId("center")).toHaveAttribute("data-loading", "0", { timeout: 15_000 });
  await page.locator("[data-testid=current-step]").waitFor();
}

export function card(page: Page, stop: string) {
  return page.locator(`[data-testid=decision][data-stop="${stop}"]`);
}

export { tmp };
