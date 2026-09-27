// TH-1~TH-19、TH-21：host 会话集成（Spec 10 §7.1）。节拍 = 驱动假会话进程写帧 → 断言，不 sleep 等轮询。
import { join } from "node:path";
import { afterAll, describe, expect, it } from "vitest";
import type { ConvSnapshot, Envelope, SnapshotStatus } from "../../src/shared/protocol";
import { HostService, type HostDeps } from "../../src/host/service";
import { spawnLog } from "../../src/host/spawner";
import { cleanup, mkEpisode, treeManifest } from "../helpers";
import { childSignals, fixtureWrite, sessionKey, sessionRepo, sessionRecords, sessionScript, stdinLines, type SessionRepo } from "../fixtures/session";

const roots: string[] = [];
afterAll(() => roots.forEach((c) => cleanup(c)));

function fakeStatus(): (repo: string, ep: string) => Promise<SnapshotStatus> {
  return async (_repo, ep) => ({
    ok: true,
    value: { episode_dir: ep, episode_name: ep.split("/").pop()!, current_step: "03 配音", is_blocked: false, block_reason: null, completed_steps: [], next_action: "", next_command: null, docs_ref: "", advisories: [] },
  });
}

/** 只统计 SESSION_* 模板的 spawn */
function sessionSpawns(): string[] {
  return spawnLog.filter((e) => String(e.template).startsWith("SESSION")).map((e) => String(e.template));
}

function fakeRunCore(): HostDeps["runCore"] {
  return (async (t: string) => ({ code: t === "GIT_HEAD" ? 128 : 0, signal: null, stdoutTail: "", stderrTail: "", timedOut: false, stdoutFull: null, stdoutOverflow: false })) as unknown as HostDeps["runCore"];
}

const READY = (ep: string) => ({ op: "emit", frame: { t: "ready", episode: ep, scope: "pipeline", continue_status: "new", llm: "ok", degrade_reason: null, code_freeze_ok: true, history_count: 0, session_bytes: 0, other_sessions: [] } });
const TURN_STARTED = { t: "turn_started", turn_id: "$turn", rid: "$rid" };
const TURN_ENDED = { t: "turn_finished", turn_id: "$turn", stopped: "done", llm_calls: 1, tool_calls: 0, tool_executions: 0, duplicates_rejected: 0, checkpoints: 0, wrapup: "none", duration_s: 0, prompt_chars: 1 };
const STOP_POINTS = { t: "stop_points", items: [], turn_id: "$turn" };

async function boot(
  opts: { epKey?: string; files?: Record<string, string>; realCore?: boolean; realKey?: boolean; timing?: Record<string, number>; bootId?: string; overrides?: Partial<HostDeps> } = {},
) {
  const repo = sessionRepo();
  roots.push(repo.root);
  const epKey = opts.epKey ?? "SESSION";
  mkEpisode(repo.root, epKey, { "01-topic.md": "# t\n类型：杂谈\n", "04-clips.json": '{"segments":[]}\n', ...(opts.files ?? {}) });
  const pushes: Envelope[] = [];
  const stub = { calls: 0, respond: true, last: null as null | { title: string; detail: string } };
  repo && fixtureWrite(repo.root, "ud/.keep", ""); // userData 目录（saveSettings 用）
  spawnLog.length = 0; // 每个用例从零计 spawn
  const deps: Partial<HostDeps> = {
    timers: false,
    selfCheck: () => true,
    fetchStatus: fakeStatus(),
    healExecutor: null,
    confirm: async (t, d) => {
      stub.calls += 1;
      stub.last = { title: t, detail: d };
      return stub.respond;
    },
    bootId: opts.bootId ?? "boot1",
    sessionTiming: { readyMs: 8000, sendAckMs: 2000, answerAckMs: 2000, endWaitMs: 300, killGraceMs: 300, settleMs: 400, coalesceMs: 0, ...(opts.timing ?? {}) },
    ...(opts.overrides ?? {}),
  };
  if (!opts.realCore) deps.runCore = fakeRunCore();
  if (!opts.realKey) deps.resolveSessionKey = async () => ({ name: "AVA_TEST_KEY", value: "SK-TEST" });
  const svc = new HostService(
    { userData: join(repo.root, "ud"), isPackaged: false, appPath: join(repo.root, "desktop"), devRepoRoot: repo.root },
    deps,
  );
  svc.attach((e) => pushes.push(e));
  await svc.start();
  return { repo, svc, pushes, stub, epKey, key: `ep:${epKey}` as const };
}

async function waitFor(fn: () => boolean | Promise<boolean>, ms = 8000): Promise<void> {
  const t0 = Date.now();
  while (!(await fn())) {
    if (Date.now() - t0 > ms) throw new Error("waitFor 超时");
    await new Promise((r) => setTimeout(r, 20));
  }
}

async function snapOf(svc: HostService, key: string): Promise<ConvSnapshot> {
  return (await svc.dispatch("conv.snapshot", { convKey: key })) as ConvSnapshot;
}

describe("TH-1 懒启动", () => {
  it("activate/subscribe/refresh 不 spawn；首个 conv.send 恰 spawn 1 次且 stdin 首行是 user_message", async () => {
    const { repo, svc, epKey, key } = await boot();
    sessionScript(repo, epKey, [READY(epKey), { op: "serve", on_turn: [TURN_STARTED, TURN_ENDED, STOP_POINTS] }]);
    await svc.dispatch("episode.activate", { epKey });
    await svc.dispatch("episode.subscribe", { epKey });
    await svc.dispatch("episode.refresh", { epKey });
    expect(sessionSpawns()).toEqual([]);

    const r = (await svc.dispatch("conv.send", { convKey: key, text: "hi" })) as { turnId: string };
    expect(r.turnId).toBe("t1");
    expect(sessionSpawns()).toEqual(["SESSION_NEW"]);
    const first = JSON.parse(stdinLines(repo, epKey)[0]) as Record<string, unknown>;
    expect(first).toMatchObject({ v: 1, t: "user_message", text: "hi" });

    await waitFor(async () => (await snapOf(svc, key)).phase === "idle");
    const r2 = (await svc.dispatch("conv.send", { convKey: key, text: "again" })) as { turnId: string };
    expect(r2.turnId).toBe("t2");
    expect(sessionSpawns()).toEqual(["SESSION_NEW"]);
  });
});

describe("TH-2 只读 stdout（H-9）", () => {
  it("stderr 里的合法帧不生效；stdout 的坏行计 frames_lost；合法帧照常处理", async () => {
    const { repo, svc, epKey, key } = await boot();
    const onStderr = JSON.stringify({ v: 1, t: "request", seq: 1, sid: "s1", request_id: "q-stderr", kind: "tool_call", turn_id: "t1", title: "x", card_text: "x", fields: { tool: "write_episode_file" }, options: ["approve", "reject"], feedback_allowed: true });
    sessionScript(repo, epKey, [
      READY(epKey),
      { op: "stderr", text: `${onStderr}\n` },
      { op: "raw", text: "this is not json\n" },
      { op: "emit", frame: { t: "notice", level: "info", code: "hello", text: "ok" } },
      { op: "serve", on_turn: [TURN_STARTED, TURN_ENDED, STOP_POINTS] },
    ]);
    await svc.dispatch("conv.send", { convKey: key, text: "hi" });
    const snap = await snapOf(svc, key);
    expect(snap.open).toEqual([]);
    expect(snap.framesLost).toBe(1);
    expect(snap.entries.some((e) => e.k === "frames_lost" && e.reason === "malformed")).toBe(true);
    expect(snap.entries.some((e) => e.k === "frame" && e.frame.t === "notice")).toBe(true);
    expect(snap.entries.some((e) => e.k === "frame" && e.frame.request_id === "q-stderr")).toBe(false);
  });
});

describe("TH-3 答复绑定（H-1 第 3 层）", () => {
  const TOOL_REQ = { t: "request", request_id: "q1", kind: "tool_call", turn_id: "t1", title: "写稿", card_text: "…", fields: { tool: "write_episode_file", args: {} }, options: ["approve", "reject"], feedback_allowed: true };
  const CHECKPOINT_REQ = { t: "request", request_id: "q2", kind: "checkpoint", turn_id: "t1", title: "检查点", card_text: "…", fields: {}, options: ["continue", "stop"], feedback_allowed: false };
  const ON_ANSWER = { t: "request_closed", request_id: "$request_id", reason: "answered", decision: "$decision", rid: "$rid" };

  async function withCards(timing?: Record<string, number>) {
    const b = await boot({ timing });
    sessionScript(b.repo, b.epKey, [READY(b.epKey), { op: "serve", on_turn: [TURN_STARTED, TOOL_REQ, CHECKPOINT_REQ], on_answer: [ON_ANSWER] }]);
    await b.svc.dispatch("conv.send", { convKey: b.key, text: "hi" });
    await waitFor(async () => (await snapOf(b.svc, b.key)).open.length === 2);
    return b;
  }

  it("未知 requestId → E_STALE 且零写入", async () => {
    const b = await withCards();
    const before = stdinLines(b.repo, b.epKey).length;
    await expect(b.svc.dispatch("conv.answer", { convKey: b.key, requestId: "nope", decision: "approve" })).rejects.toMatchObject({ code: "E_STALE" });
    expect(stdinLines(b.repo, b.epKey).length).toBe(before);
  });

  it("decision 不在 options / checkpoint 带 feedback / feedback 非字符串或超长 → E_BAD_REQUEST", async () => {
    const b = await withCards();
    await expect(b.svc.dispatch("conv.answer", { convKey: b.key, requestId: "q1", decision: "maybe" })).rejects.toMatchObject({ code: "E_BAD_REQUEST" });
    await expect(b.svc.dispatch("conv.answer", { convKey: b.key, requestId: "q2", decision: "stop", feedback: "改一下" })).rejects.toMatchObject({ code: "E_BAD_REQUEST" });
    await expect(b.svc.dispatch("conv.answer", { convKey: b.key, requestId: "q1", decision: "reject", feedback: 5 as unknown as string })).rejects.toMatchObject({ code: "E_BAD_REQUEST" });
    const before = stdinLines(b.repo, b.epKey).length;
    await expect(b.svc.dispatch("conv.answer", { convKey: b.key, requestId: "q1", decision: "reject", feedback: "x".repeat(70_000) })).rejects.toMatchObject({ code: "E_BAD_REQUEST" });
    expect(stdinLines(b.repo, b.epKey).length).toBe(before);
  });

  it("合法答复 → stdin 恰新增 1 行；解析后等于答案帧形状", async () => {
    const b = await withCards();
    const before = stdinLines(b.repo, b.epKey).length;
    const r = (await b.svc.dispatch("conv.answer", { convKey: b.key, requestId: "q1", decision: "reject", feedback: "改成第三人称" })) as { decision: string };
    expect(r.decision).toBe("reject");
    const lines = stdinLines(b.repo, b.epKey);
    expect(lines.length).toBe(before + 1);
    const frame = JSON.parse(lines[lines.length - 1]) as Record<string, unknown>;
    expect(frame).toMatchObject({ v: 1, t: "answer", request_id: "q1", decision: "reject", feedback: "改成第三人称" });
    expect(String(frame.rid)).toMatch(/^[A-Za-z0-9_-]{1,64}$/);
    const snap = await snapOf(b.svc, b.key);
    expect(snap.open.map((o) => o.request_id)).toEqual(["q2"]);
  });

  it("同一请求第二次答复在第一次未回之前 → E_BUSY（假进程不回）", async () => {
    const b = await boot({ timing: { answerAckMs: 300 } });
    sessionScript(b.repo, b.epKey, [READY(b.epKey), { op: "serve", on_turn: [TURN_STARTED, TOOL_REQ] }]);
    await b.svc.dispatch("conv.send", { convKey: b.key, text: "hi" });
    await waitFor(async () => (await snapOf(b.svc, b.key)).open.length === 1);
    const p1 = b.svc.dispatch("conv.answer", { convKey: b.key, requestId: "q1", decision: "approve" }).catch(() => undefined);
    await expect(b.svc.dispatch("conv.answer", { convKey: b.key, requestId: "q1", decision: "approve" })).rejects.toMatchObject({ code: "E_BUSY" });
    await p1;
  });
});

describe("TH-4 进程退出作废（H-5）", () => {
  const onTurn = [
    TURN_STARTED,
    { t: "request", request_id: "qa", kind: "tool_call", turn_id: "t1", title: "a", card_text: "a", fields: { tool: "write_episode_file" }, options: ["approve", "reject"], feedback_allowed: true },
    { t: "request", request_id: "qb", kind: "tool_call", turn_id: "t1", title: "b", card_text: "b", fields: { tool: "write_episode_file" }, options: ["approve", "reject"], feedback_allowed: true },
  ];

  it("exit(1)：open 清空、voided_local 含两个 id、之后答复 E_STALE", async () => {
    const b = await boot();
    sessionScript(b.repo, b.epKey, [READY(b.epKey), { op: "wait", lines: 1 }, { op: "reply", frames: onTurn }, { op: "sleep", seconds: 0.4 }, { op: "exit", code: 1 }]);
    await b.svc.dispatch("conv.send", { convKey: b.key, text: "hi" });
    await waitFor(async () => (await snapOf(b.svc, b.key)).phase === "exited");
    const snap = await snapOf(b.svc, b.key);
    expect(snap.open).toEqual([]);
    const voided = snap.entries.find((e) => e.k === "voided_local");
    expect(voided && voided.k === "voided_local" ? [...voided.requestIds].sort() : []).toEqual(["qa", "qb"]);
    const before = stdinLines(b.repo, b.epKey).length;
    await expect(b.svc.dispatch("conv.answer", { convKey: b.key, requestId: "qa", decision: "approve" })).rejects.toMatchObject({ code: "E_STALE" });
    expect(stdinLines(b.repo, b.epKey).length).toBe(before);
  });

  it("SIGKILL：同一作废语义", async () => {
    const b = await boot();
    sessionScript(b.repo, b.epKey, [READY(b.epKey), { op: "serve", on_turn: onTurn }]);
    await b.svc.dispatch("conv.send", { convKey: b.key, text: "hi" });
    await waitFor(async () => (await snapOf(b.svc, b.key)).open.length === 2);
    const pid = sessionRecords(b.repo, b.epKey).find((r) => r.kind === "pid")!.pid!;
    process.kill(pid, "SIGKILL");
    await waitFor(async () => (await snapOf(b.svc, b.key)).phase === "exited");
    const snap = await snapOf(b.svc, b.key);
    expect(snap.open).toEqual([]);
    expect(snap.entries.some((e) => e.k === "voided_local")).toBe(true);
  });
});

describe("TH-5 不代发（H-8）", () => {
  it("10 个回合内持续发重复帧 → user_message 行数恰等于 conv.send 次数", async () => {
    const { repo, svc, epKey, key } = await boot();
    sessionScript(repo, epKey, [
      READY(epKey),
      {
        op: "serve",
        on_turn: [TURN_STARTED, TURN_ENDED, STOP_POINTS, { t: "notice", level: "info", code: "x", text: "n" }, { t: "ready", episode: epKey, scope: "pipeline", continue_status: "new", llm: "ok", degrade_reason: null, code_freeze_ok: true, history_count: 0, session_bytes: 0, other_sessions: [] }],
      },
    ]);
    for (let i = 0; i < 10; i += 1) {
      await svc.dispatch("conv.send", { convKey: key, text: `m${i}` });
      await waitFor(async () => (await snapOf(svc, key)).phase === "idle");
    }
    expect(stdinLines(repo, epKey).filter((l) => JSON.parse(l).t === "user_message")).toHaveLength(10);
  });

  it("回合运行中 conv.send → E_BUSY、零写入", async () => {
    const { repo, svc, epKey, key } = await boot();
    sessionScript(repo, epKey, [READY(epKey), { op: "serve", on_turn: [TURN_STARTED] }]);
    await svc.dispatch("conv.send", { convKey: key, text: "hi" });
    const before = stdinLines(repo, epKey).length;
    await expect(svc.dispatch("conv.send", { convKey: key, text: "again" })).rejects.toMatchObject({ code: "E_BUSY" });
    expect(stdinLines(repo, epKey).length).toBe(before);
  });
});

describe("TH-6 密钥全链路", () => {
  it("SESSION_* 环境恰多一个键、值为标记串；标记串不进 spawn 日志 / 诊断 / 推送", async () => {
    const marker = `SK-MARKER-${Math.random().toString(36).slice(2)}`;
    const b = await boot({ realCore: true, realKey: true });
    sessionKey(b.repo, marker);
    sessionScript(b.repo, b.epKey, [READY(b.epKey), { op: "dump_env" }, { op: "serve", on_turn: [TURN_STARTED, TURN_ENDED, STOP_POINTS], on_shutdown: "exit" }]);
    await b.svc.dispatch("conv.send", { convKey: b.key, text: "hi" });
    await waitFor(() => sessionRecords(b.repo, b.epKey).some((r) => r.kind === "env"));
    const envRec = sessionRecords(b.repo, b.epKey).find((r) => r.kind === "env")!;
    const env = envRec.env!;
    expect(env.AVA_TEST_KEY).toBe(marker);
    const extra = Object.keys(env).filter((k) => !["PATH", "HOME", "USER", "TMPDIR", "LANG", "PYTHONUTF8", "PYTHONUNBUFFERED", "__CF_USER_TEXT_ENCODING"].includes(k));
    expect(extra).toEqual(["AVA_TEST_KEY"]);
    expect(JSON.stringify(spawnLog)).not.toContain(marker);
    expect(JSON.stringify(b.pushes)).not.toContain(marker);
    expect(b.svc.health().diagnostics.join("\n")).not.toContain(marker);
  });

  it("其余模板（STATUS）的环境不含密钥变量（TI-7 继续成立）", async () => {
    const repo = sessionRepo();
    roots.push(repo.root);
    fixtureWrite(repo.root, "pipeline/status.py", "import os\nfor k in sorted(os.environ): print(k)\n");
    const { svc } = await bootWithRepo(repo);
    const r = await svc.deps.runCore("STATUS", { ep: "/x" }, { repoRoot: repo.root });
    expect(r.code).toBe(0);
    expect(r.stdoutTail).not.toContain("AVA_TEST_KEY");
  });
});

/** 用一个已建好的 repo 起 HostService（TH-6 第二半用） */
async function bootWithRepo(repo: SessionRepo) {
  const pushes: Envelope[] = [];
  const svc = new HostService(
    { userData: join(repo.root, "ud"), isPackaged: false, appPath: join(repo.root, "desktop"), devRepoRoot: repo.root },
    { timers: false, selfCheck: () => true, fetchStatus: fakeStatus(), healExecutor: null, resolveSessionKey: async () => ({ name: "AVA_TEST_KEY", value: "X" }), sessionTiming: { coalesceMs: 0 } },
  );
  svc.attach((e) => pushes.push(e));
  await svc.start();
  return { svc, pushes };
}

describe("TH-7 密钥缺失与不合规", () => {
  async function problemOf(mutate: (repo: SessionRepo) => void): Promise<{ problem: string; spawns: string[] }> {
    const b = await boot({ realCore: true, realKey: true });
    mutate(b.repo);
    sessionScript(b.repo, b.epKey, [READY(b.epKey), { op: "serve", on_turn: [TURN_STARTED, TURN_ENDED, STOP_POINTS], on_shutdown: "exit" }]);
    await b.svc.dispatch("conv.send", { convKey: b.key, text: "hi" });
    const snap = await snapOf(b.svc, b.key);
    return { problem: snap.keyProblem ?? "", spawns: sessionSpawns() };
  }

  it("钥匙串退 44 → 降级文案含正确变量名的设置命令，会话照常 spawn", async () => {
    const { problem, spawns } = await problemOf(() => undefined);
    expect(problem).toContain("security add-generic-password -s ava -a AVA_TEST_KEY -w");
    expect(spawns).toEqual(["SESSION_NEW"]);
  });

  it("配置指名 PATH → 不注入、文案含变量名", async () => {
    const { problem, spawns } = await problemOf((repo) => {
      fixtureWrite(repo.root, "config/agent.local.json", JSON.stringify({ base_url: "http://127.0.0.1:9/v1", model: "fake", api_key_env: "PATH" }));
    });
    expect(problem).toContain("配置指名的变量名不合规");
    expect(problem).toContain("-a PATH -w");
    expect(spawns).toEqual(["SESSION_NEW"]);
  });

  it("值含空格 → 不注入、文案含变量名", async () => {
    const { problem } = await problemOf((repo) => sessionKey(repo, "a b"));
    expect(problem).toContain("不是可打印 ASCII");
    expect(problem).toContain("-a AVA_TEST_KEY -w");
  });

  it("PROBE 输出空串 → 「未能读取 config/agent*.json 的 api_key_env」", async () => {
    const { problem } = await problemOf((repo) => {
      fixtureWrite(repo.root, "config/agent.local.json", JSON.stringify({ base_url: "http://127.0.0.1:9/v1", model: "fake", api_key_env: "" }));
    });
    expect(problem).toContain("未能读取 config/agent*.json 的 api_key_env");
  });
});

describe("TH-8 中断", () => {
  it("当前 turnId → stdin 恰 1 行 interrupt；回合结束后用旧 turnId → E_STALE、零写入", async () => {
    const b = await boot();
    sessionScript(b.repo, b.epKey, [READY(b.epKey), { op: "serve", on_turn: [TURN_STARTED] }]);
    await b.svc.dispatch("conv.send", { convKey: b.key, text: "hi" });
    const before = stdinLines(b.repo, b.epKey).length;
    b.svc.dispatch("conv.interrupt", { convKey: b.key, turnId: "t1" });
    await waitFor(() => stdinLines(b.repo, b.epKey).length === before + 1);
    const lines = stdinLines(b.repo, b.epKey);
    expect(lines.length).toBe(before + 1);
    expect(JSON.parse(lines[lines.length - 1])).toMatchObject({ t: "interrupt", turn_id: "t1" });

    const b2 = await boot();
    sessionScript(b2.repo, b2.epKey, [READY(b2.epKey), { op: "serve", on_turn: [TURN_STARTED, TURN_ENDED] }]);
    await b2.svc.dispatch("conv.send", { convKey: b2.key, text: "hi" });
    await waitFor(async () => (await snapOf(b2.svc, b2.key)).phase === "idle");
    const before2 = stdinLines(b2.repo, b2.epKey).length;
    await expect(async () => b2.svc.dispatch("conv.interrupt", { convKey: b2.key, turnId: "t1" })).rejects.toMatchObject({ code: "E_STALE" });
    expect(stdinLines(b2.repo, b2.epKey).length).toBe(before2);
  });
});

describe("TH-9 结束与退出序列", () => {
  it("① 空闲假进程收到 shutdown 即退 → 无信号", async () => {
    const b = await boot();
    sessionScript(b.repo, b.epKey, [READY(b.epKey), { op: "serve", on_turn: [TURN_STARTED, TURN_ENDED, STOP_POINTS], on_shutdown: "exit" }]);
    await b.svc.dispatch("conv.send", { convKey: b.key, text: "hi" });
    await b.svc.dispatch("conv.end", { convKey: b.key });
    const recs = sessionRecords(b.repo, b.epKey);
    expect(recs.some((r) => r.kind === "stdin" && JSON.parse(r.line!).t === "shutdown")).toBe(true);
    expect(recs.some((r) => r.kind === "signal")).toBe(false);
  });

  it("② 忽略 shutdown 与 SIGTERM：SIGTERM 只到会话 pid（孙进程未收到），宽限后整组 SIGKILL", async () => {
    const b = await boot({ timing: { endWaitMs: 250, killGraceMs: 250 } });
    sessionScript(b.repo, b.epKey, [READY(b.epKey), { op: "ignore_sigterm" }, { op: "grandchild" }, { op: "serve", on_turn: [TURN_STARTED] }]);
    await b.svc.dispatch("conv.send", { convKey: b.key, text: "hi" });
    await waitFor(() => sessionRecords(b.repo, b.epKey).some((r) => r.kind === "grandchild"));
    const childPid = sessionRecords(b.repo, b.epKey).find((r) => r.kind === "grandchild")!.pid!;
    const r = (await b.svc.dispatch("conv.end", { convKey: b.key })) as { signal: string | null };
    expect(r.signal).toBe("SIGKILL");
    expect(sessionRecords(b.repo, b.epKey).some((x) => x.kind === "signal" && x.signal === "SIGTERM")).toBe(true);
    expect(childSignals(b.repo, b.epKey)).toEqual([]); // 孙进程从未收到 SIGTERM
    await waitFor(() => {
      try {
        process.kill(childPid, 0);
        return false;
      } catch {
        return true;
      }
    });
  });

  it("③ 正常退出但留下同组孙进程 → 退出后该孙进程被 SIGKILL", async () => {
    const b = await boot();
    sessionScript(b.repo, b.epKey, [READY(b.epKey), { op: "grandchild" }, { op: "serve", on_turn: [TURN_STARTED, TURN_ENDED, STOP_POINTS], on_shutdown: "exit" }]);
    await b.svc.dispatch("conv.send", { convKey: b.key, text: "hi" });
    await waitFor(() => sessionRecords(b.repo, b.epKey).some((r) => r.kind === "grandchild"));
    const childPid = sessionRecords(b.repo, b.epKey).find((r) => r.kind === "grandchild")!.pid!;
    await b.svc.dispatch("conv.end", { convKey: b.key });
    await waitFor(() => {
      try {
        process.kill(childPid, 0);
        return false;
      } catch {
        return true;
      }
    });
  });

  it("④ stopAllForQuit：空闲会话收到 shutdown、忙会话收到 SIGTERM", async () => {
    const b = await boot({ epKey: "BUSY", timing: { killGraceMs: 400 } });
    mkEpisode(b.repo.root, "IDLE", { "01-topic.md": "# i\n" });
    b.svc.refreshEpisodes();
    sessionScript(b.repo, "BUSY", [READY("BUSY"), { op: "serve", on_turn: [TURN_STARTED] }]);
    sessionScript(b.repo, "IDLE", [READY("IDLE"), { op: "serve", on_turn: [TURN_STARTED, TURN_ENDED, STOP_POINTS], on_shutdown: "exit" }]);
    await b.svc.dispatch("conv.send", { convKey: "ep:BUSY", text: "hi" });
    await b.svc.dispatch("conv.send", { convKey: "ep:IDLE", text: "hi" });
    await waitFor(async () => (await snapOf(b.svc, "ep:IDLE")).phase === "idle");
    await b.svc.stopAllForQuit();
    const busy = sessionRecords(b.repo, "BUSY");
    const idle = sessionRecords(b.repo, "IDLE");
    expect(busy.some((r) => r.kind === "stdin" && JSON.parse(r.line!).t === "shutdown")).toBe(false);
    expect(busy.some((r) => r.kind === "signal" && r.signal === "SIGTERM")).toBe(true);
    expect(idle.some((r) => r.kind === "stdin" && JSON.parse(r.line!).t === "shutdown")).toBe(true);
    expect(idle.some((r) => r.kind === "signal")).toBe(false);
  });
});

describe("TH-10 renderer 重置", () => {
  it("resetRenderer 后会话进程仍在，snapshot 返回之前的条目与打开请求", async () => {
    const b = await boot();
    sessionScript(b.repo, b.epKey, [READY(b.epKey), { op: "serve", on_turn: [TURN_STARTED, { t: "request", request_id: "q1", kind: "tool_call", turn_id: "t1", title: "t", card_text: "c", fields: { tool: "write_episode_file" }, options: ["approve", "reject"], feedback_allowed: true }] }]);
    await b.svc.dispatch("conv.send", { convKey: b.key, text: "hi" });
    await waitFor(async () => (await snapOf(b.svc, b.key)).open.length === 1);
    const pid = sessionRecords(b.repo, b.epKey).find((r) => r.kind === "pid")!.pid!;
    b.svc.resetRenderer();
    process.kill(pid, 0); // 仍在
    const snap = await snapOf(b.svc, b.key);
    expect(snap.open.map((o) => o.request_id)).toEqual(["q1"]);
    expect(snap.entries.length).toBeGreaterThan(0);
  });
});

describe("TH-11 / TH-11b 租约被占与退出码映射", () => {
  it("TH-11：不发 ready、发 error E_SESSION_LOCKED 后退 3 → E_SESSION_LOCKED，stdin 无 user_message", async () => {
    const b = await boot();
    sessionScript(b.repo, b.epKey, [{ op: "emit", frame: { t: "error", code: "E_SESSION_LOCKED", message: "locked" } }, { op: "exit", code: 3 }]);
    await expect(b.svc.dispatch("conv.send", { convKey: b.key, text: "hi" })).rejects.toMatchObject({ code: "E_SESSION_LOCKED" });
    expect(stdinLines(b.repo, b.epKey)).toEqual([]);
  });

  it("TH-11b：只退 3 → E_SESSION_LOCKED；退 4 → E_UNREACHABLE；退 2 → E_SESSION", async () => {
    for (const [code, expectCode] of [[3, "E_SESSION_LOCKED"], [4, "E_UNREACHABLE"], [2, "E_SESSION"]] as const) {
      const b = await boot();
      sessionScript(b.repo, b.epKey, [{ op: "exit", code }]);
      await expect(b.svc.dispatch("conv.send", { convKey: b.key, text: "hi" })).rejects.toMatchObject({ code: expectCode });
    }
  });
});

describe("TH-12 帧超限", () => {
  it("① 块内含完整超限行 + 合法帧 → oversize、超限行未处理、后续帧照常", async () => {
    const b = await boot();
    sessionScript(b.repo, b.epKey, [
      READY(b.epKey),
      { op: "bigline", bytes: 8 * 1024 * 1024 + 1, then: { t: "notice", level: "info", code: "after", text: "ok" } },
      { op: "serve", on_turn: [TURN_STARTED, TURN_ENDED, STOP_POINTS], on_shutdown: "exit" },
    ]);
    await b.svc.dispatch("conv.send", { convKey: b.key, text: "hi" });
    await waitFor(async () => (await snapOf(b.svc, b.key)).framesLost >= 1);
    const snap = await snapOf(b.svc, b.key);
    expect(snap.framesLost).toBe(1);
    expect(snap.entries.some((e) => e.k === "frames_lost" && e.reason === "oversize")).toBe(true);
    expect(snap.entries.some((e) => e.k === "frame" && e.frame.t === "notice" && e.frame.code === "after")).toBe(true);
  });

  it("② 超限行分块到达（残段超限）→ 同样 oversize、后续合法帧照常", async () => {
    const b = await boot();
    sessionScript(b.repo, b.epKey, [READY(b.epKey), { op: "partial_big", bytes: 8 * 1024 * 1024 + 1, then: { t: "notice", level: "info", code: "after2", text: "ok" } }, { op: "serve", on_turn: [TURN_STARTED, TURN_ENDED, STOP_POINTS], on_shutdown: "exit" }]);
    await b.svc.dispatch("conv.send", { convKey: b.key, text: "hi" });
    await waitFor(async () => (await snapOf(b.svc, b.key)).framesLost >= 1);
    const snap = await snapOf(b.svc, b.key);
    expect(snap.framesLost).toBe(1);
    expect(snap.entries.some((e) => e.k === "frame" && e.frame.t === "notice" && e.frame.code === "after2")).toBe(true);
  });
});

describe("TH-13 切换仓库互斥（S8-R10）", () => {
  it("有活会话时切换 → E_BUSY，对话框桩调用 0 次", async () => {
    let dialogCalls = 0;
    const b = await boot({ overrides: { chooseRepoRoot: async () => { dialogCalls += 1; return null; } } });
    sessionScript(b.repo, b.epKey, [READY(b.epKey), { op: "serve", on_turn: [TURN_STARTED, TURN_ENDED, STOP_POINTS], on_shutdown: "exit" }]);
    await b.svc.dispatch("conv.send", { convKey: b.key, text: "hi" });
    await expect(b.svc.dispatch("app.requestRepoRootChange", {})).rejects.toMatchObject({ code: "E_BUSY" });
    expect(dialogCalls).toBe(0);
  });

  it("choosing 期间 conv.send / conv.resume / episode.create 均 E_BUSY、零 SESSION spawn", async () => {
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    const b = await boot({ overrides: { chooseRepoRoot: async () => { await gate; return null; } } });
    sessionScript(b.repo, b.epKey, [READY(b.epKey)]);
    const p = b.svc.dispatch("app.requestRepoRootChange", {});
    await new Promise((r) => setTimeout(r, 30)); // 让 choosing 生效
    expect(sessionSpawns()).toEqual([]);
    await expect(b.svc.dispatch("conv.send", { convKey: b.key, text: "hi" })).rejects.toMatchObject({ code: "E_BUSY" });
    await expect(b.svc.dispatch("conv.resume", { convKey: b.key })).rejects.toMatchObject({ code: "E_BUSY" });
    await expect(b.svc.dispatch("episode.create", { name: "X" })).rejects.toMatchObject({ code: "E_BUSY" });
    expect(sessionSpawns()).toEqual([]);
    release();
    await p;
  });

  it("NEW_EPISODE 在途时切换等它结束（spawn 顺序可证）", async () => {
    const b = await boot({ realCore: true, overrides: { chooseRepoRoot: async () => b.repo.root } });
    fixtureWrite(b.repo.root, "pipeline/agent/cli.py", "import sys, time\ntime.sleep(0.3)\nsys.exit(0)\n");
    const events: string[] = [];
    const create = b.svc.dispatch("episode.create", { name: "X" }).then(
      () => events.push("create-done"),
      () => events.push("create-failed"),
    );
    await new Promise((r) => setTimeout(r, 30));
    const change = b.svc.dispatch("app.requestRepoRootChange", {}).then(() => events.push("switch-done"));
    await Promise.all([create, change]);
    expect(events.indexOf("create-done")).toBeGreaterThanOrEqual(0);
    expect(events.indexOf("create-done")).toBeLessThan(events.indexOf("switch-done"));
  });
});

describe("TH-14 建期", () => {
  it("argv 逐元素等于 NEW_EPISODE 模板；成功返回 epKey；host 零写入", async () => {
    const b = await boot({ realCore: true });
    // 假 core：不建目录，只退 0
    fixtureWrite(b.repo.root, "pipeline/agent/cli.py", "import sys\nsys.exit(0)\n");
    const before = treeManifest(b.repo.root).filter((l) => !l.includes("__pycache__"));
    const r = (await b.svc.dispatch("episode.create", { name: "新期-x" })) as { epKey: string };
    expect(r.epKey).toBe("新期-x");
    const rec = spawnLog.find((e) => e.template === "NEW_EPISODE")!;
    expect(rec.argv.slice(1)).toEqual(["-m", "pipeline.agent.cli", "new", "新期-x"]);
    expect(treeManifest(b.repo.root).filter((l) => !l.includes("__pycache__"))).toEqual(before);
  });

  it("core 退 2 → E_CORE 且附 stderr 尾部", async () => {
    const b = await boot({ realCore: true });
    fixtureWrite(b.repo.root, "pipeline/agent/cli.py", "import sys\nsys.stderr.write('FAIL 名字不合规\\n')\nsys.exit(2)\n");
    const err = await b.svc.dispatch("episode.create", { name: "bad" }).catch((e: unknown) => e);
    expect(err).toMatchObject({ code: "E_CORE" });
    expect(((err as { tails: { stderrTail: string } }).tails.stderrTail)).toContain("FAIL 名字不合规");
  });
});

describe("TH-15 命令", () => {
  it("scope 带 arg → stdin 1 行且以同 rid 的 command_result 解析；memory_ack 带 arg / 未知 name → E_BAD_REQUEST；无活进程 → E_SESSION", async () => {
    const b = await boot();
    sessionScript(b.repo, b.epKey, [READY(b.epKey), { op: "serve", on_turn: [TURN_STARTED, TURN_ENDED, STOP_POINTS], on_command: [{ t: "command_result", name: "scope", ok: true, text: "asset", rid: "$rid" }], on_shutdown: "exit" }]);
    await b.svc.dispatch("conv.send", { convKey: b.key, text: "hi" });
    const before = stdinLines(b.repo, b.epKey).length;
    const r = (await b.svc.dispatch("conv.command", { convKey: b.key, name: "scope", arg: "asset" })) as { ok: boolean; text: string };
    expect(r).toEqual({ ok: true, text: "asset" });
    expect(stdinLines(b.repo, b.epKey).length).toBe(before + 1);
    await expect(b.svc.dispatch("conv.command", { convKey: b.key, name: "memory_ack", arg: "asset" })).rejects.toMatchObject({ code: "E_BAD_REQUEST" });
    await expect(b.svc.dispatch("conv.command", { convKey: b.key, name: "voice" })).rejects.toMatchObject({ code: "E_BAD_REQUEST" });

    const b2 = await boot();
    await expect(b2.svc.dispatch("conv.command", { convKey: b2.key, name: "memory_ack" })).rejects.toMatchObject({ code: "E_SESSION" });
  });
});

describe("TH-16 后台常驻与徽标", () => {
  it("A 回合运行中激活 B：A 进程存活继续收帧；徽标 running / openRequests 变化", async () => {
    const b = await boot({ epKey: "EP-A" });
    mkEpisode(b.repo.root, "EP-B", { "01-topic.md": "# b\n" });
    b.svc.refreshEpisodes();
    sessionScript(b.repo, "EP-A", [READY("EP-A"), { op: "serve", on_turn: [TURN_STARTED] }]);
    await b.svc.dispatch("conv.send", { convKey: "ep:EP-A", text: "hi" });
    await b.svc.dispatch("episode.activate", { epKey: "EP-B" });
    const list = b.svc.episodesList();
    const a = list.episodes.find((e) => e.epKey === "EP-A")!;
    expect(a.conv).toMatchObject({ live: true, running: true, openRequests: 0 });
    const pid = sessionRecords(b.repo, "EP-A").find((r) => r.kind === "pid")!.pid!;
    process.kill(pid, 0);
  });
});

describe("TH-17 rid 关联（S9-R2）", () => {
  it("不带 rid 的无关 error 只进流内提示，同 rid 的 turn_started 让 conv.send 成功", async () => {
    const b = await boot();
    sessionScript(b.repo, b.epKey, [READY(b.epKey), { op: "serve", on_turn: [{ t: "error", code: "E_BUSY", message: "无关" }, TURN_STARTED] }]);
    const r = (await b.svc.dispatch("conv.send", { convKey: b.key, text: "hi" })) as { turnId: string };
    expect(r.turnId).toBe("t1");
    const snap = await snapOf(b.svc, b.key);
    expect(snap.entries.some((e) => e.k === "frame" && e.frame.t === "error")).toBe(true);
  });
});

describe("TH-18 原生确认框（§2.4 第 5 层）", () => {
  const BROWSER = (url: string, reason: string) => ({ t: "request", request_id: "qb", kind: "tool_call", turn_id: "t1", title: "浏览器", card_text: "…", fields: { tool: "browser", args: { action: "open", url, reason }, target: url }, options: ["approve", "reject"], feedback_allowed: true });
  const FETCH = (url: string, why = "需要素材") => ({ t: "request", request_id: "qf", kind: "fetch", turn_id: "t1", title: "候选", card_text: "…", fields: { no: 1, title: "t", url, type: "image", source: "web", why, expected_dur: "12" }, options: ["approve", "reject"], feedback_allowed: false });
  const WRITE = { t: "request", request_id: "qw", kind: "tool_call", turn_id: "t1", title: "写稿", card_text: "…", fields: { tool: "write_episode_file", args: {} }, options: ["approve", "reject"], feedback_allowed: true };
  const ON_ANSWER = { t: "request_closed", request_id: "$request_id", reason: "answered", decision: "$decision", rid: "$rid" };

  async function withCards(payloads: unknown[], timing?: Record<string, number>) {
    const b = await boot({ timing });
    sessionScript(b.repo, b.epKey, [READY(b.epKey), { op: "serve", on_turn: [TURN_STARTED, ...payloads], on_answer: [ON_ANSWER] }]);
    await b.svc.dispatch("conv.send", { convKey: b.key, text: "hi" });
    await waitFor(async () => (await snapOf(b.svc, b.key)).open.length === payloads.length);
    return b;
  }

  it("桩返回 false：批准抓取卡 → E_STALE、零新增、卡仍打开、桩调用 1 次且 detail 含候选 URL", async () => {
    const b = await withCards([FETCH("https://example.com/candidate")]);
    b.stub.respond = false;
    const before = stdinLines(b.repo, b.epKey).length;
    await expect(b.svc.dispatch("conv.answer", { convKey: b.key, requestId: "qf", decision: "approve" })).rejects.toMatchObject({ code: "E_STALE" });
    expect(stdinLines(b.repo, b.epKey).length).toBe(before);
    expect((await snapOf(b.svc, b.key)).open.map((o) => o.request_id)).toEqual(["qf"]);
    expect(b.stub.calls).toBe(1);
    expect(b.stub.last!.detail).toContain("https://example.com/candidate");
  });

  it("桩返回 true：批准 → 恰 1 行 answer；拒绝不需要确认框；写入卡批准不需要确认框", async () => {
    const b = await withCards([FETCH("https://example.com/candidate"), WRITE]);
    b.stub.respond = true;
    const before = stdinLines(b.repo, b.epKey).length;
    await b.svc.dispatch("conv.answer", { convKey: b.key, requestId: "qf", decision: "approve" });
    expect(stdinLines(b.repo, b.epKey).length).toBe(before + 1);
    const callsAfterApprove = b.stub.calls;
    await b.svc.dispatch("conv.answer", { convKey: b.key, requestId: "qw", decision: "approve" });
    expect(b.stub.calls).toBe(callsAfterApprove);
  });

  it("browser 卡：批准要确认框，拒绝不要", async () => {
    const b = await withCards([BROWSER("https://example.com/b", "看看")]);
    b.stub.respond = true;
    await b.svc.dispatch("conv.answer", { convKey: b.key, requestId: "qb", decision: "approve" });
    expect(b.stub.calls).toBe(1);
    expect(b.stub.last!.title).toBe("批准 browser 调用？");
  });

  it("不可见字符显式化 + 首行计数；自由文本超长截断；确定性 URL 全文", async () => {
    const b = await withCards([FETCH("https://example.com/\u202Ea\u200B", "w".repeat(3000))]);
    b.stub.respond = false;
    await expect(b.svc.dispatch("conv.answer", { convKey: b.key, requestId: "qf", decision: "approve" })).rejects.toMatchObject({ code: "E_STALE" });
    const d = b.stub.last!.detail;
    expect(d.split("\n")[0]).toBe("含 2 个不可见字符，已显式标出");
    expect(d).toContain("URL：https://example.com/⟨U+202E⟩a⟨U+200B⟩");
    expect(d).toContain("…（已截断，共 3000 字符）");
  });

  it("browser 的 reason 伪造 URL 行 + 超长 → 真实目标行在前、伪造行在理由标注之后（MUT-69）", async () => {
    const reason = `URL: https://evil.example\n${"z".repeat(3000)}`;
    const b = await withCards([BROWSER("https://real.example/path", reason)]);
    b.stub.respond = false;
    await expect(b.svc.dispatch("conv.answer", { convKey: b.key, requestId: "qb", decision: "approve" })).rejects.toMatchObject({ code: "E_STALE" });
    const d = b.stub.last!.detail;
    const label = d.indexOf("以下为模型填写的理由（未经核实）：");
    expect(d.indexOf("目标：https://real.example/path")).toBeGreaterThanOrEqual(0);
    expect(d.indexOf("目标：https://real.example/path")).toBeLessThan(label);
    expect(d.indexOf("URL: https://evil.example")).toBeGreaterThan(label);
  });

  it("答复参数多带伪造的 url 键 → E_BAD_REQUEST（exact-keys），detail 恒取自 host 保存的请求", async () => {
    const b = await withCards([FETCH("https://example.com/real")]);
    await expect(b.svc.dispatch("conv.answer", { convKey: b.key, requestId: "qf", decision: "approve", url: "https://evil.example" })).rejects.toMatchObject({ code: "E_BAD_REQUEST" });
    expect(b.stub.calls).toBe(0);
  });

  it("桩等待期间会话退出 → E_STALE、零写入", async () => {
    let resolveConfirm!: (ok: boolean) => void;
    const gate = new Promise<boolean>((r) => (resolveConfirm = r));
    const b = await boot({ overrides: { confirm: async () => gate } });
    sessionScript(b.repo, b.epKey, [READY(b.epKey), { op: "serve", on_turn: [TURN_STARTED, FETCH("https://example.com/x")] }]);
    await b.svc.dispatch("conv.send", { convKey: b.key, text: "hi" });
    await waitFor(async () => (await snapOf(b.svc, b.key)).open.length === 1);
    const pid = sessionRecords(b.repo, b.epKey).find((r) => r.kind === "pid")!.pid!;
    const p = b.svc.dispatch("conv.answer", { convKey: b.key, requestId: "qf", decision: "approve" }).catch((e) => e);
    process.kill(pid, "SIGKILL");
    await waitFor(async () => (await snapOf(b.svc, b.key)).phase === "exited");
    resolveConfirm(true);
    const err = (await p) as { code?: string };
    expect(err.code).toBe("E_STALE");
    expect(stdinLines(b.repo, b.epKey).filter((l) => JSON.parse(l).t === "answer")).toEqual([]);
  });
});

describe("TH-19 host 重启后的 generation", () => {
  it("两个不同 bootId 的实例对同一会话键给出不同的 generation", async () => {
    const a = await boot({ bootId: "bootA" });
    const b = await boot({ bootId: "bootB" });
    const sa = await snapOf(a.svc, a.key);
    const sb = await snapOf(b.svc, b.key);
    expect(sa.generation).toBe("bootA:0");
    expect(sb.generation).toBe("bootB:0");
    expect(sa.generation).not.toBe(sb.generation);
  });
});

describe("TH-21 退出期间拒绝新请求", () => {
  it("stopAllForQuit 后 conv.send / conv.answer / episode.create → E_BUSY、零写入零 spawn", async () => {
    const b = await boot();
    await b.svc.stopAllForQuit();
    const before = sessionSpawns().length;
    await expect(b.svc.dispatch("conv.send", { convKey: b.key, text: "hi" })).rejects.toMatchObject({ code: "E_BUSY" });
    await expect(b.svc.dispatch("conv.answer", { convKey: b.key, requestId: "q", decision: "approve" })).rejects.toMatchObject({ code: "E_BUSY" });
    await expect(b.svc.dispatch("episode.create", { name: "X" })).rejects.toMatchObject({ code: "E_BUSY" });
    expect(sessionSpawns().length).toBe(before);
  });
});
describe("TH-20 服务侧结算与 episode.delta 顺序（§2.7 第 3 条）", () => {
  it("活跃期一次对象库读取：先推 episode.delta，再追加 settled{timedOut:false}", async () => {
    const b = await boot({ timing: { settleMs: 60_000 } });
    await b.svc.dispatch("episode.activate", { epKey: b.epKey });
    sessionScript(b.repo, b.epKey, [READY(b.epKey), { op: "serve", on_turn: [TURN_STARTED, TURN_ENDED, STOP_POINTS] }]);
    await b.svc.dispatch("conv.send", { convKey: b.key, text: "hi" });
    await waitFor(async () => (await snapOf(b.svc, b.key)).settlePending === "t1");
    // 对象库出现一个可读的对象（读取会带来变化 → 推 episode.delta）
    const store = [{
      approval_id: "a1", episode: b.epKey, type: "05", status: "pending", options: ["approve", "reject"], created_at: "2026-09-25T10:00:00Z",
      resolved_at: null, resolved_by: null, confirmed_by: null, confirmed_at: null, feedback: null, note: "", artifacts: [{ path: "04-clips.json", size: 1, mtime_ns: 1 }],
    }];
    fixtureWrite(b.repo.root, `data/episodes/${b.epKey}/_agent/approvals_store.json`, JSON.stringify(store));
    await new Promise((r) => setTimeout(r, 5)); // 让 readStart 严格晚于 stop_points 到达
    b.pushes.length = 0;
    await b.svc.tickActive();
    const settledIdx = b.pushes.findIndex((e) => e.kind === "push" && e.topic === "conv.delta" && JSON.stringify(e.data).includes('"settled"'));
    const deltaIdx = b.pushes.findIndex((e) => e.kind === "push" && e.topic === "episode.delta");
    expect(deltaIdx).toBeGreaterThanOrEqual(0);
    expect(settledIdx).toBeGreaterThan(deltaIdx);
    expect(JSON.stringify((b.pushes[settledIdx] as Extract<Envelope, { kind: "push" }>).data)).toContain('"timedOut":false');
  });
});

// bootWithRepo 用于 TH-6 的「其余模板」一例。
void bootWithRepo;
