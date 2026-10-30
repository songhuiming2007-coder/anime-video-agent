// D98（人选方案 C）：host 什么时候叫人——新审批卡、新停机点、回合结束（会话帧），作业结束（events.jsonl 折叠）。
// main 侧的 Dock 状态机在 tests/shared/attention.test.ts；这里只测 host 报了什么、去没去重。
import { appendFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import type { Attention } from "../../src/shared/attention";
import { HostService } from "../../src/host/service";
import type { SessionProc } from "../../src/host/spawner";
import { SessionManager, type SessionTarget } from "../../src/host/sessions";
import { cleanup, makeFixtureRepo, mkEpisode, py } from "../helpers";

class FakeProc implements SessionProc {
  pid = 12345;
  private out: ((b: Buffer) => void)[] = [];
  write(): void {}
  onStdout(fn: (b: Buffer) => void): void {
    this.out.push(fn);
  }
  onStderr(): void {}
  onExit(): void {}
  signal(): void {}
  feed(frame: Record<string, unknown>): void {
    for (const fn of this.out) fn(Buffer.from(`${JSON.stringify({ v: 1, sid: "s1", seq: 1, ...frame })}\n`));
  }
}

const READY = { t: "ready", episode: "E", scope: "pipeline", continue_status: "new", llm: "ok", degrade_reason: null, code_freeze_ok: true, history_count: 0, session_bytes: 0, other_sessions: [], resume_prompt_tokens: null, resume_prompt_chars: null, context_window: null, session_kind: "episode", project: null, model: "fake", reasoning_effort: null, model_source: "default", effort_source: "default" };
const REQ = (id: string) => ({ t: "request", request_id: id, kind: "tool_call", turn_id: "t1", title: "bash", card_text: "…", fields: { tool: "bash", args: {} }, options: ["approve", "reject"], feedback_allowed: true });
const FINISHED = (turn: string, stopped: string) => ({ t: "turn_finished", turn_id: turn, stopped, llm_calls: 1, tool_calls: 0, tool_executions: 0, duplicates_rejected: 0, checkpoints: 0, wrapup: "none", duration_s: 0, prompt_chars: 1, lookups: null, prompt_tokens: null, compacted: false, tokens_before: null, tokens_after: null, context_window: null });
const STOPS = (items: unknown[], turn: string) => ({ t: "stop_points", items, turn_id: turn });

async function session() {
  const got: Attention[] = [];
  const procs: FakeProc[] = [];
  const swallow = () => undefined;
  const mgr = new SessionManager({
    spawnSession: () => {
      const p = new FakeProc();
      procs.push(p);
      queueMicrotask(() => p.feed(READY));
      return p;
    },
    resolveKey: async () => ({ name: "K", value: "V" }),
    resolveWebKeys: async () => ({}),
    now: () => 1000,
    bootId: "boot1",
    push: swallow,
    diag: swallow,
    blocked: () => false,
    repoRoot: () => "/repo",
    isActive: () => true,
    canReadApprovals: () => true,
    killGroup: swallow,
    listProxyModels: async () => ({ ok: false, text: "no proxy" }),
    groupAlive: () => false,
    changed: swallow,
    timing: { readyMs: 5000, coalesceMs: 0, settleMs: 60_000, sendAckMs: 1000, answerAckMs: 1000, endWaitMs: 100, killGraceMs: 100 },
    attention: (a) => got.push(a),
  });
  const target: SessionTarget = { key: "ep:E", epKey: "E", abs: "/repo/data/episodes/E", mode: "continue" };
  await mgr.resume("ep:E", target);
  return { p: procs[procs.length - 1], got };
}

describe("D98 会话帧 → attention", () => {
  it("新审批卡叫人一次；同一张卡重发不再叫", async () => {
    const { p, got } = await session();
    p.feed(REQ("q1"));
    p.feed(REQ("q1"));
    p.feed(REQ("q2"));
    expect(got).toEqual([
      { kind: "wait", key: "req:ep:E:q1" },
      { kind: "wait", key: "req:ep:E:q2" },
    ]);
  });

  it("停机点：出现时叫人；内容不变（每回合都会重发一帧）不叫；空列表不叫；换了新停机点再叫", async () => {
    const { p, got } = await session();
    const s25 = [{ step: "02.5", reason: "审稿" }];
    p.feed(STOPS([], "t0"));
    p.feed(STOPS(s25, "t1"));
    p.feed(STOPS(s25, "t2"));
    p.feed(STOPS([{ step: "03.5", reason: "顺听" }], "t3"));
    expect(got.map((a) => [a.kind, a.key.startsWith("stop:ep:E:")])).toEqual([
      ["wait", true],
      ["wait", true],
    ]);
    expect(got[0].key).not.toBe(got[1].key);
  });

  it("回合结束：正常答复 info、失败 wait、人点停止不叫", async () => {
    const { p, got } = await session();
    p.feed({ t: "turn_started", turn_id: "t1" });
    p.feed(FINISHED("t1", "done"));
    p.feed({ t: "turn_started", turn_id: "t2" });
    p.feed(FINISHED("t2", "interrupted"));
    p.feed({ t: "turn_started", turn_id: "t3" });
    p.feed(FINISHED("t3", "error"));
    expect(got).toEqual([
      { kind: "info", key: "turn:ep:E:t1" },
      { kind: "wait", key: "turn:ep:E:t3" },
    ]);
  });
});

function evLine(episode: string, type: string, payload: object, i: number): string {
  return py(
    `import json, sys
from pipeline.jobs import Event
e = Event(event_id=f"evt_17900000000{int(sys.argv[3]):02d}_abcd", timestamp="2026-10-15T00:00:%02d.000000Z" % int(sys.argv[3]), episode=sys.argv[1], type=sys.argv[2], payload=json.loads(sys.argv[4]))
sys.stdout.write(e.to_jsonl_line())`,
    [episode, type, String(i), JSON.stringify(payload)],
  );
}

describe("D98 作业结束 → attention（events.jsonl 折叠）", () => {
  let repo: string;
  beforeAll(() => {
    repo = makeFixtureRepo();
  });
  afterAll(() => cleanup(repo));

  it("在跑的作业结束才报：成功 info、失败 wait；开机时已结束的旧作业不补报", async () => {
    const ep = mkEpisode(repo, "JOBS");
    // pid 用测试进程自己：活着，折叠才判 running
    writeFileSync(join(ep, "events.jsonl"),
      evLine("JOBS", "job_created", { job_id: "old", command: "check_script" }, 1) +
      evLine("JOBS", "job_finished", { job_id: "old", status: "succeeded", returncode: 0 }, 2) +
      evLine("JOBS", "job_created", { job_id: "j1", command: "tts" }, 3) +
      evLine("JOBS", "job_started", { job_id: "j1", pid: process.pid }, 4) +
      evLine("JOBS", "job_created", { job_id: "j2", command: "render" }, 5) +
      evLine("JOBS", "job_started", { job_id: "j2", pid: process.pid }, 6));
    const got: Attention[] = [];
    let clock = 1_000_000;
    const svc = new HostService({ userData: join(repo, "ud"), isPackaged: false, appPath: join(repo, "desktop"), devRepoRoot: repo }, { timers: false, now: () => clock, attention: (a) => got.push(a) });
    svc.attach(() => undefined);
    await svc.start();
    await svc.dispatch("episode.activate", { epKey: "JOBS" });
    expect(got).toEqual([]);
    appendFileSync(join(ep, "events.jsonl"),
      evLine("JOBS", "job_finished", { job_id: "j1", status: "succeeded", returncode: 0 }, 7) +
      evLine("JOBS", "job_finished", { job_id: "j2", status: "failed", returncode: 1 }, 8));
    clock += 6000;
    await svc.tickActive();
    expect(got).toEqual([
      { kind: "info", key: "job:JOBS:j1" },
      { kind: "wait", key: "job:JOBS:j2" },
    ]);
    // 会话层的 attention 也要经 service 接到同一个出口（会话帧测试直接 new SessionManager，测不到这一层接线）
    const sessions = (svc as unknown as { sessions: { deps: { attention?: (a: Attention) => void } } }).sessions;
    sessions.deps.attention?.({ kind: "wait", key: "req:ep:JOBS:q1" });
    expect(got.at(-1)).toEqual({ kind: "wait", key: "req:ep:JOBS:q1" });
  });
});
