// TH-1~TH-19、TH-21：host 会话集成（Spec 10 §7.1）。节拍 = 驱动假会话进程写帧 → 断言，不 sleep 等轮询。
import { rmSync } from "node:fs";
import { join } from "node:path";
import { afterAll, describe, expect, it } from "vitest";
import type { ConvSnapshot, Envelope, SnapshotStatus } from "../../src/shared/protocol";
import { HostService, type HostDeps } from "../../src/host/service";
import { childEnv, runCore, spawnLog } from "../../src/host/spawner";
import { cleanup, mkEpisode, treeManifest } from "../helpers";
import { createConfirmBroker } from "../../src/host/confirm";
import { createMainConfirmBroker } from "../../src/main/confirm";
import { childSignals, fixtureWrite, sessionKey, sessionKeyFor, sessionRepo, sessionRecords, sessionScript, stdinLines, type SessionRepo } from "../fixtures/session";

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

const READY = (ep: string) => ({ op: "emit", frame: { t: "ready", episode: ep, scope: "pipeline", continue_status: "new", llm: "ok", degrade_reason: null, code_freeze_ok: true, history_count: 0, session_bytes: 0, other_sessions: [], resume_prompt_tokens: null, resume_prompt_chars: null, context_window: null } });
const TURN_STARTED = { t: "turn_started", turn_id: "$turn", rid: "$rid" };
const TURN_ENDED = { t: "turn_finished", turn_id: "$turn", stopped: "done", llm_calls: 1, tool_calls: 0, tool_executions: 0, duplicates_rejected: 0, checkpoints: 0, wrapup: "none", duration_s: 0, prompt_chars: 1, lookups: null, prompt_tokens: null, compacted: false, tokens_before: null, tokens_after: null, context_window: null };
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
        on_turn: [TURN_STARTED, TURN_ENDED, STOP_POINTS, { t: "notice", level: "info", code: "x", text: "n" }, { t: "ready", episode: epKey, scope: "pipeline", continue_status: "new", llm: "ok", degrade_reason: null, code_freeze_ok: true, history_count: 0, session_bytes: 0, other_sessions: [], resume_prompt_tokens: null, resume_prompt_chars: null, context_window: null }],
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
  // Spec 15 §2.7 口径：会话环境只多出 LLM 名 + web 链上声明的名字。本夹具的 web 配置是旧 schema（core 判无效、
  // 不声明任何名字），所以这里仍恰多一个键；有 web 链时的口径见 TH-W1。
  it("SESSION_* 环境恰多一个键、值为标记串，PATH 含 /opt/homebrew/bin（N49）；标记串不进 spawn 日志 / 诊断 / 推送", async () => {
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
    // N49：会话进程实际拿到的 PATH 恰为「系统四段 + /opt/homebrew/bin」（取自子进程内部的 os.environ，不是 spawn 参数）；
    // 其余白名单项与 childEnv 的产物逐项相同——开 PATH 口子不许顺带改别的
    expect(env.PATH).toBe("/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin");
    for (const [k, v] of Object.entries(childEnv(process.env))) if (k !== "PATH") expect(env[k], k).toBe(v);
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

describe("TH-W1～W4 web 检索密钥注入（Spec 15 §2.7）", () => {
  const BASE = ["PATH", "HOME", "USER", "TMPDIR", "LANG", "PYTHONUTF8", "PYTHONUNBUFFERED", "__CF_USER_TEXT_ENCODING"];
  const llmMarker = () => `SK-LLM-${Math.random().toString(36).slice(2)}`;
  const webMarker = () => `tvly-WEB-${Math.random().toString(36).slice(2)}`;

  /** 新 schema 的 web 链（真实 core 的 web_key_env_names 会回答 TAVILY_API_KEY）。 */
  function webChain(repo: SessionRepo): void {
    fixtureWrite(
      repo.root,
      "config/agent/web.local.json",
      JSON.stringify({
        search: { timeout_s: 20, providers: [{ name: "exa_mcp" }, { name: "tavily", api_key_env: "TAVILY_API_KEY" }] },
        fetch: { timeout_s: 30, max_bytes: 1000000, max_chars: 30000 },
      }),
    );
  }

  type Probe = { code?: number | null; stdout?: string; timedOut?: boolean; reject?: boolean };
  /** 真实 runCore，只把 PROBE_WEB_KEY_ENVS（与可选的某个 KEYCHAIN_READ）换成剧本——模拟 core 违约或失败。 */
  function scripted(probe: Probe | null, keychainRejectFor?: string): HostDeps["runCore"] {
    return ((t: string, args: { envName?: string }, ctx: { repoRoot: string }, tag?: object, stdin?: string) => {
      if (t === "PROBE_WEB_KEY_ENVS" && probe) {
        if (probe.reject) return Promise.reject(new Error("probe boom"));
        return Promise.resolve({ code: probe.code === undefined ? 0 : probe.code, signal: null, stdoutTail: probe.stdout ?? "", stderrTail: "", timedOut: probe.timedOut ?? false, stdoutFull: null, stdoutOverflow: false });
      }
      if (t === "KEYCHAIN_READ" && args.envName === keychainRejectFor) return Promise.reject(new Error("keychain boom"));
      return (runCore as (...a: unknown[]) => unknown)(t, args, ctx, tag, stdin);
    }) as unknown as HostDeps["runCore"];
  }

  async function sessionEnv(mutate: (repo: SessionRepo) => void, runCoreOverride?: HostDeps["runCore"]) {
    const b = await boot({ realCore: true, realKey: true, overrides: runCoreOverride ? { runCore: runCoreOverride } : {} });
    mutate(b.repo);
    sessionScript(b.repo, b.epKey, [READY(b.epKey), { op: "dump_env" }, { op: "serve", on_turn: [TURN_STARTED, TURN_ENDED, STOP_POINTS], on_shutdown: "exit" }]);
    // 会话必须照常启动：web 密钥解析的任何异常都不许冒到 conv.send（§2.7 第 6 条）——逃逸转成普通断言失败
    const sent = await b.svc.dispatch("conv.send", { convKey: b.key, text: "hi" }).then(
      () => "started",
      (e: unknown) => `conv.send 抛出：${e instanceof Error ? e.message : String(e)}`,
    );
    expect(sent).toBe("started");
    await waitFor(() => sessionRecords(b.repo, b.epKey).some((r) => r.kind === "env"));
    const env = sessionRecords(b.repo, b.epKey).find((r) => r.kind === "env")!.env!;
    const extra = Object.keys(env).filter((k) => !BASE.includes(k)).sort();
    const snap = await snapOf(b.svc, b.key);
    const diag = b.svc.health().diagnostics.join("\n");
    const keychainReads = (name: string) => spawnLog.filter((e) => e.template === "KEYCHAIN_READ" && (e.argv as string[]).includes(name)).length;
    return { b, env, extra, snap, diag, keychainReads };
  }

  it("TH-W1：钥匙串两条都有 → 会话环境恰多出 LLM 名与 TAVILY_API_KEY；两个值都不进 spawn 日志 / 推送 / 诊断", async () => {
    const m1 = llmMarker();
    const m2 = webMarker();
    const r = await sessionEnv((repo) => {
      webChain(repo);
      sessionKeyFor(repo, "AVA_TEST_KEY", m1);
      sessionKeyFor(repo, "TAVILY_API_KEY", m2);
    });
    expect(r.extra).toEqual(["AVA_TEST_KEY", "TAVILY_API_KEY"]);
    expect(r.env.AVA_TEST_KEY).toBe(m1);
    expect(r.env.TAVILY_API_KEY).toBe(m2);
    expect(r.snap.keyProblem).toBeNull();
    for (const m of [m1, m2]) {
      expect(JSON.stringify(spawnLog)).not.toContain(m);
      expect(JSON.stringify(r.b.pushes)).not.toContain(m);
      expect(r.diag).not.toContain(m);
    }
  });

  for (const [label, webValue] of [["钥匙串退 44", null], ["值非 ASCII", "tvly-é"]] as const) {
    it(`TH-W2：web 密钥${label} → 会话照常启动，keyProblem 为空，LLM 名与值仍在、web 名不在`, async () => {
      const m1 = llmMarker();
      const r = await sessionEnv((repo) => {
        webChain(repo);
        sessionKeyFor(repo, "AVA_TEST_KEY", m1);
        if (webValue !== null) sessionKeyFor(repo, "TAVILY_API_KEY", webValue);
      });
      expect(r.snap.keyProblem).toBeNull();
      expect(r.env.AVA_TEST_KEY).toBe(m1);
      expect(r.extra).toEqual(["AVA_TEST_KEY"]);
      expect(r.diag).toContain("web 检索密钥 TAVILY_API_KEY");
      expect(r.diag).toContain("security add-generic-password -s ava -a TAVILY_API_KEY -w");
    });
  }

  it("TH-W3：假 core 违约回答与 LLM 同名 → KEYCHAIN_READ 对该名只调一次，env 里是 LLM 的值", async () => {
    const m1 = llmMarker();
    const r = await sessionEnv((repo) => sessionKeyFor(repo, "AVA_TEST_KEY", m1), scripted({ stdout: "AVA_TEST_KEY\n" }));
    expect(r.keychainReads("AVA_TEST_KEY")).toBe(1);
    expect(r.extra).toEqual(["AVA_TEST_KEY"]);
    expect(r.env.AVA_TEST_KEY).toBe(m1);
  });

  const NONE: [string, Probe | null, string | undefined][] = [
    ["探针退出码非 0", { code: 1, stdout: "TAVILY_API_KEY\n" }, undefined],
    ["探针超时", { code: null, timedOut: true, stdout: "TAVILY_API_KEY\n" }, undefined],
    ["输出含不合规行", { stdout: "TAVILY_API_KEY\nPATH\n" }, undefined],
    ["探针调用抛异常", { reject: true }, undefined],
    ["钥匙串调用抛异常", null, "TAVILY_API_KEY"],
  ];
  for (const [label, probe, rejectFor] of NONE) {
    it(`TH-W4：${label} → 会话照常启动，keyProblem 为空，LLM 在、无任何 web 名`, async () => {
      const m1 = llmMarker();
      const m2 = webMarker();
      const r = await sessionEnv(
        (repo) => {
          webChain(repo);
          sessionKeyFor(repo, "AVA_TEST_KEY", m1);
          sessionKeyFor(repo, "TAVILY_API_KEY", m2);
        },
        scripted(probe, rejectFor),
      );
      expect(r.snap.keyProblem).toBeNull();
      expect(r.env.AVA_TEST_KEY).toBe(m1);
      expect(r.extra).toEqual(["AVA_TEST_KEY"]);
      expect(r.diag).toContain("web 检索密钥");
      expect(r.diag).not.toContain(m2);
      expect(r.diag).not.toContain(m1);
    });
  }

  it("TH-W4：名字重复 → 只读一次、注入一次", async () => {
    const m2 = webMarker();
    const r = await sessionEnv(
      (repo) => {
        sessionKeyFor(repo, "AVA_TEST_KEY", llmMarker());
        sessionKeyFor(repo, "TAVILY_API_KEY", m2);
      },
      scripted({ stdout: "TAVILY_API_KEY\nTAVILY_API_KEY\n" }),
    );
    expect(r.keychainReads("TAVILY_API_KEY")).toBe(1);
    expect(r.env.TAVILY_API_KEY).toBe(m2);
    expect(r.extra).toEqual(["AVA_TEST_KEY", "TAVILY_API_KEY"]);
  });

  it("TH-W4：超过 4 个 → 只注入前 4 个并记诊断（不含值）", async () => {
    const names = ["TAVILY_A_KEY", "TAVILY_B_KEY", "TAVILY_C_KEY", "TAVILY_D_KEY", "TAVILY_E_KEY"];
    const values = names.map(() => webMarker());
    const r = await sessionEnv(
      (repo) => {
        sessionKeyFor(repo, "AVA_TEST_KEY", llmMarker());
        names.forEach((n, i) => sessionKeyFor(repo, n, values[i]));
      },
      scripted({ stdout: names.join("\n") + "\n" }),
    );
    expect(r.extra).toEqual(["AVA_TEST_KEY", ...names.slice(0, 4)]);
    expect(r.keychainReads("TAVILY_E_KEY")).toBe(0);
    expect(r.diag).toContain("只注入前 4 个");
    for (const v of values) expect(r.diag).not.toContain(v);
    expect(r.snap.keyProblem).toBeNull();
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
    // 前提：孙进程的 SIGTERM 处理器已装好——否则下面「从未收到」可能只是它按默认动作悄悄死了（MUT-11）
    await waitFor(() => childSignals(b.repo, b.epKey).some((x) => x.kind === "child_ready"));
    const r = (await b.svc.dispatch("conv.end", { convKey: b.key })) as { signal: string | null };
    expect(r.signal).toBe("SIGKILL");
    expect(sessionRecords(b.repo, b.epKey).some((x) => x.kind === "signal" && x.signal === "SIGTERM")).toBe(true);
    expect(childSignals(b.repo, b.epKey).filter((x) => x.kind === "child_signal")).toEqual([]); // 孙进程从未收到 SIGTERM
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

  it("⑤ quitState 只列忙会话（D38）：全空闲为空；回合在跑、回合已完但有未答卡各列一条", async () => {
    const b = await boot({ epKey: "IDLE", timing: { killGraceMs: 400 } });
    mkEpisode(b.repo.root, "RUN", { "01-topic.md": "# r\n" });
    mkEpisode(b.repo.root, "CARD", { "01-topic.md": "# c\n" });
    b.svc.refreshEpisodes();
    const CARD_REQ = { t: "request", request_id: "qc", kind: "tool_call", turn_id: "$turn", title: "t", card_text: "c", fields: { tool: "write_episode_file" }, options: ["approve", "reject"], feedback_allowed: true };
    sessionScript(b.repo, "IDLE", [READY("IDLE"), { op: "serve", on_turn: [TURN_STARTED, TURN_ENDED, STOP_POINTS], on_shutdown: "exit" }]);
    sessionScript(b.repo, "RUN", [READY("RUN"), { op: "serve", on_turn: [TURN_STARTED] }]);
    sessionScript(b.repo, "CARD", [READY("CARD"), { op: "serve", on_turn: [TURN_STARTED, CARD_REQ, TURN_ENDED, STOP_POINTS] }]);
    await b.svc.dispatch("conv.send", { convKey: "ep:IDLE", text: "hi" });
    await waitFor(async () => (await snapOf(b.svc, "ep:IDLE")).phase === "idle");
    expect(b.svc.quitState()).toEqual([]);

    await b.svc.dispatch("conv.send", { convKey: "ep:RUN", text: "hi" });
    await b.svc.dispatch("conv.send", { convKey: "ep:CARD", text: "hi" });
    await waitFor(async () => {
      const c = await snapOf(b.svc, "ep:CARD");
      return c.phase === "idle" && c.open.length === 1;
    });
    const busy = b.svc.quitState().sort((x, y) => x.convKey.localeCompare(y.convKey));
    expect(busy).toEqual([
      { convKey: "ep:CARD", label: "CARD", running: false, openRequests: 1 },
      { convKey: "ep:RUN", label: "RUN", running: true, openRequests: 0 },
    ]);
    await b.svc.stopAllForQuit();
  });
});

describe("TH-10 renderer 重置", () => {
  it("resetRenderer 后会话进程仍在，snapshot 返回之前的条目与打开请求", async () => {
    const b = await boot({ timing: { killGraceMs: 200 } });
    sessionScript(b.repo, b.epKey, [READY(b.epKey), { op: "serve", on_turn: [TURN_STARTED, { t: "request", request_id: "q1", kind: "tool_call", turn_id: "t1", title: "t", card_text: "c", fields: { tool: "write_episode_file" }, options: ["approve", "reject"], feedback_allowed: true }] }]);
    await b.svc.dispatch("conv.send", { convKey: b.key, text: "hi" });
    await waitFor(async () => (await snapOf(b.svc, b.key)).open.length === 1);
    const pid = sessionRecords(b.repo, b.epKey).find((r) => r.kind === "pid")!.pid!;
    b.svc.resetRenderer();
    // 等过宽限期再看（M9：信号送达与退出处理都是异步的，同步紧跟的断言在「重置顺手结束会话」的缺陷下
    // 仍然成立——MUT-13 因此存活）
    await new Promise((r) => setTimeout(r, 800));
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

describe("TH-13b 切仓窗口内不起新读取（N39）", () => {
  it("switching 期间 refreshEpisodeStatuses / 活跃期 tick 都不 spawn STATUS；窗口关闭后恢复", async () => {
    let calls = 0;
    let gate: Promise<void> | null = null;
    let clock = 1_000_000;
    const base = fakeStatus();
    const b = await boot({
      overrides: {
        now: () => clock,
        chooseRepoRoot: async () => b.repo.root,
        fetchStatus: async (r, e) => {
          calls += 1;
          if (gate) await gate;
          return base(r, e);
        },
      },
    });
    await b.svc.dispatch("episode.activate", { epKey: b.epKey });
    let release!: () => void;
    gate = new Promise<void>((r) => (release = r));
    const inflight = b.svc.refreshEpisodeStatuses(); // 占住一个在途读取 → 切换的 while 循环会等它，窗口保持打开
    await waitFor(() => calls > 0);
    const before = calls;
    const change = b.svc.dispatch("app.requestRepoRootChange", {});
    await waitFor(() => b.svc.switching);
    clock += 6000; // 活跃期 status 已到期，tick 本会重读
    const inWindow = [b.svc.refreshEpisodeStatuses(), b.svc.tickActive()]; // 不 await：无守卫时它们会卡在闸门上，断言照常先跑
    await new Promise((r) => setTimeout(r, 50));
    expect(calls).toBe(before);
    release();
    await Promise.all([inflight, change, ...inWindow]);
    gate = null;
    const after = calls;
    clock += 6000;
    await b.svc.refreshEpisodeStatuses(); // 窗口已关：恢复正常读取
    expect(calls).toBeGreaterThan(after);
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
    expect(rec.argv.slice(1)).toEqual(["-m", "pipeline.agent.cli", "new", "新期-x", "--from-idea"]);
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

/** 假 core 的 cli.py：按 argv 建出期目录（让 host 的期列表认得它），stdout 打给定的若干行后退 0。 */
function fakeNewEpisodeCli(stdoutLines: string[]): string {
  return [
    "import os, sys",
    "root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))",
    "d = os.path.join(root, 'data', 'episodes', sys.argv[2])",
    "os.makedirs(d)",
    "open(os.path.join(d, '01-topic.md'), 'w').write('# t\\n')",
    ...stdoutLines.map((l) => `print(${JSON.stringify(l)})`),
    "sys.exit(0)",
    "",
  ].join("\n");
}

const IDEA_READY = { op: "emit", frame: { t: "ready", episode: null, scope: "auto", continue_status: "new", llm: "ok", degrade_reason: null, code_freeze_ok: true, history_count: 0, session_bytes: 0, other_sessions: [], resume_prompt_tokens: null, resume_prompt_chars: null, context_window: null } };

describe("TH-D42 建期带入选题记录（Spec 18 §3.3）", () => {
  it("① marker migrated=true → episode.create 返回带入结果；该期首次 conv.send 以 SESSION_CONTINUE 起，标记随即消费", async () => {
    const b = await boot({ realCore: true });
    fixtureWrite(b.repo.root, "pipeline/agent/cli.py", fakeNewEpisodeCli(["[OK] 已立项新期", "[from-idea] migrated=true sid=00ab12cd messages=4"]));
    const r = await b.svc.dispatch("episode.create", { name: "新期-带入" });
    expect(r).toEqual({ epKey: "新期-带入", migrated: true, sid: "00ab12cd", messages: 4 });
    sessionScript(b.repo, "新期-带入", [READY("新期-带入"), { op: "serve", on_turn: [TURN_STARTED, TURN_ENDED, STOP_POINTS], on_shutdown: "exit" }]);
    await b.svc.dispatch("conv.send", { convKey: "ep:新期-带入", text: "把草案写进 01-topic.md" });
    expect(sessionSpawns()).toEqual(["SESSION_CONTINUE"]);
    // 人发的那一条原样到达（host 不代发、不改写，H-8）
    expect(stdinLines(b.repo, "新期-带入").map((l) => JSON.parse(l) as Record<string, unknown>).filter((f) => f.t === "user_message").map((f) => f.text)).toEqual(["把草案写进 01-topic.md"]);
    // 一次性：进程结束后再发，回到 SESSION_NEW
    await b.svc.dispatch("conv.end", { convKey: "ep:新期-带入" });
    await b.svc.dispatch("conv.send", { convKey: "ep:新期-带入", text: "再来" });
    expect(sessionSpawns()).toEqual(["SESSION_CONTINUE", "SESSION_NEW"]);
  });

  it("② marker 缺失 → migrated=false + host diag，期照常建好；首发照旧 SESSION_NEW", async () => {
    const b = await boot({ realCore: true });
    fixtureWrite(b.repo.root, "pipeline/agent/cli.py", fakeNewEpisodeCli(["[OK] 已立项新期"]));
    const r = await b.svc.dispatch("episode.create", { name: "新期-无标注" });
    expect(r).toEqual({ epKey: "新期-无标注", migrated: false, sid: null, messages: 0 });
    expect(b.pushes.some((e) => e.kind === "push" && e.topic === "diag" && String(e.data).includes("没有合式的选题带入标注"))).toBe(true);
    sessionScript(b.repo, "新期-无标注", [READY("新期-无标注"), { op: "serve", on_turn: [TURN_STARTED, TURN_ENDED, STOP_POINTS] }]);
    await b.svc.dispatch("conv.send", { convKey: "ep:新期-无标注", text: "hi" });
    expect(sessionSpawns()).toEqual(["SESSION_NEW"]);
  });

  it("③ 选题会话活着 → 先结束它再 spawn NEW_EPISODE；带入成功后选题会话缓冲清空（phase none、零条目）", async () => {
    let svcRef: HostService | null = null;
    const ideaPhaseAtCreate: string[] = [];
    const b = await boot({
      realCore: true,
      overrides: {
        runCore: (async (t: Parameters<typeof runCore>[0], ...rest: unknown[]) => {
          if (t === "NEW_EPISODE" && svcRef) ideaPhaseAtCreate.push((await snapOf(svcRef, "idea")).phase);
          return (runCore as (...a: unknown[]) => unknown)(t, ...rest);
        }) as unknown as HostDeps["runCore"],
      },
    });
    svcRef = b.svc;
    sessionScript(b.repo, "idea", [IDEA_READY, { op: "serve", on_turn: [TURN_STARTED, { t: "assistant", turn_id: "$turn", kind: "answer", text: "草案在此" }, TURN_ENDED, STOP_POINTS], on_shutdown: "exit" }]);
    await b.svc.dispatch("conv.send", { convKey: "idea", text: "定个选题" });
    await waitFor(async () => (await snapOf(b.svc, "idea")).phase === "idle");
    fixtureWrite(b.repo.root, "pipeline/agent/cli.py", fakeNewEpisodeCli(["[from-idea] migrated=true sid=ab messages=2"]));
    const r = (await b.svc.dispatch("episode.create", { name: "新期-结束选题" })) as { migrated: boolean };
    expect(r.migrated).toBe(true);
    expect(ideaPhaseAtCreate).toEqual(["exited"]); // 建期时选题进程已确实退出
    expect(stdinLines(b.repo, "idea").map((l) => (JSON.parse(l) as { t: string }).t)).toEqual(["user_message", "shutdown"]);
    const idea = await snapOf(b.svc, "idea");
    expect(idea.phase).toBe("none");
    expect(idea.entries).toEqual([]);
  });

  it("④ 未带入（migrated=false）→ 选题会话缓冲保留（旧讨论仍可见，与改造前一致）", async () => {
    const b = await boot({ realCore: true });
    sessionScript(b.repo, "idea", [IDEA_READY, { op: "serve", on_turn: [TURN_STARTED, TURN_ENDED, STOP_POINTS], on_shutdown: "exit" }]);
    await b.svc.dispatch("conv.send", { convKey: "idea", text: "随便聊聊" });
    fixtureWrite(b.repo.root, "pipeline/agent/cli.py", fakeNewEpisodeCli(["[from-idea] migrated=false sid=- messages=0"]));
    await b.svc.dispatch("episode.create", { name: "新期-未带入" });
    const idea = await snapOf(b.svc, "idea");
    expect(idea.phase).toBe("exited");
    expect(idea.entries.some((e) => e.k === "user")).toBe(true);
  });

  // ⑥~⑨：host 侧一次性标记（Spec 18 §9.6 偏差 1）的不变量，N60 补（D42-C 自设变异 V3 / V4 曾存活）
  it("⑥ 带入的期首发失败（进程 ready 前退出）→ 标记不消费，再发仍以 SESSION_CONTINUE 起", async () => {
    const b = await boot({ realCore: true });
    fixtureWrite(b.repo.root, "pipeline/agent/cli.py", fakeNewEpisodeCli(["[from-idea] migrated=true sid=ab messages=2"]));
    await b.svc.dispatch("episode.create", { name: "新期-首发失败" });
    sessionScript(b.repo, "新期-首发失败", []); // 剧本为空：假进程 ready 之前就退出
    await expect(b.svc.dispatch("conv.send", { convKey: "ep:新期-首发失败", text: "第一次" })).rejects.toMatchObject({ code: "E_SESSION" }); // ready 前退出（N60 复核 R-2）
    sessionScript(b.repo, "新期-首发失败", [READY("新期-首发失败"), { op: "serve", on_turn: [TURN_STARTED, TURN_ENDED, STOP_POINTS] }]);
    await b.svc.dispatch("conv.send", { convKey: "ep:新期-首发失败", text: "第二次" });
    expect(sessionSpawns()).toEqual(["SESSION_CONTINUE", "SESSION_CONTINUE"]);
  });

  it("⑦ 带入后切仓（哪怕切回同一个根）→ 标记清空，该期首发以 SESSION_NEW 起", async () => {
    let root = "";
    const b = await boot({ realCore: true, overrides: { chooseRepoRoot: async () => root } });
    root = b.repo.root;
    fixtureWrite(b.repo.root, "pipeline/agent/cli.py", fakeNewEpisodeCli(["[from-idea] migrated=true sid=ab messages=2"]));
    await b.svc.dispatch("episode.create", { name: "新期-切仓" });
    const changed = (await b.svc.dispatch("app.requestRepoRootChange", {})) as { changed: boolean };
    expect(changed.changed).toBe(true);
    sessionScript(b.repo, "新期-切仓", [READY("新期-切仓"), { op: "serve", on_turn: [TURN_STARTED, TURN_ENDED, STOP_POINTS] }]);
    await b.svc.dispatch("conv.send", { convKey: "ep:新期-切仓", text: "hi" });
    expect(sessionSpawns()).toEqual(["SESSION_NEW"]);
  });

  it("⑧ 带入的期先点「继续上次会话」→ 标记随 resume 成功消费；结束后再发以 SESSION_NEW 起", async () => {
    const b = await boot({ realCore: true });
    fixtureWrite(b.repo.root, "pipeline/agent/cli.py", fakeNewEpisodeCli(["[from-idea] migrated=true sid=ab messages=2"]));
    await b.svc.dispatch("episode.create", { name: "新期-先续" });
    sessionScript(b.repo, "新期-先续", [READY("新期-先续"), { op: "serve", on_turn: [TURN_STARTED, TURN_ENDED, STOP_POINTS], on_shutdown: "exit" }]);
    await b.svc.dispatch("conv.resume", { convKey: "ep:新期-先续" });
    await b.svc.dispatch("conv.end", { convKey: "ep:新期-先续" });
    await b.svc.dispatch("conv.send", { convKey: "ep:新期-先续", text: "hi" });
    expect(sessionSpawns()).toEqual(["SESSION_CONTINUE", "SESSION_NEW"]);
  });

  it("⑨ 同名期在 app 外被删后重建、这次未带入 → 旧标记作废，首发以 SESSION_NEW 起", async () => {
    const b = await boot({ realCore: true });
    fixtureWrite(b.repo.root, "pipeline/agent/cli.py", fakeNewEpisodeCli(["[from-idea] migrated=true sid=ab messages=2"]));
    await b.svc.dispatch("episode.create", { name: "新期-重建" });
    rmSync(join(b.repo.root, "data", "episodes", "新期-重建"), { recursive: true });
    // 多打一行让文件长度不同：同秒同长的改写会被 Python 的 .pyc 缓存当成没变（stale .pyc 事故同型）
    fixtureWrite(b.repo.root, "pipeline/agent/cli.py", fakeNewEpisodeCli(["[OK] 重建", "[from-idea] migrated=false sid=- messages=0"]));
    await b.svc.dispatch("episode.create", { name: "新期-重建" });
    sessionScript(b.repo, "新期-重建", [READY("新期-重建"), { op: "serve", on_turn: [TURN_STARTED, TURN_ENDED, STOP_POINTS] }]);
    await b.svc.dispatch("conv.send", { convKey: "ep:新期-重建", text: "hi" });
    expect(sessionSpawns()).toEqual(["SESSION_NEW"]);
  });

  it("⑩ 带入建期 A 后紧接着建 B（_idea 刚清空，B 必未带入）→ 只作废 B 的标记：A 首发仍 SESSION_CONTINUE、B 首发 SESSION_NEW", async () => {
    const b = await boot({ realCore: true });
    fixtureWrite(b.repo.root, "pipeline/agent/cli.py", fakeNewEpisodeCli(["[from-idea] migrated=true sid=ab messages=2"]));
    await b.svc.dispatch("episode.create", { name: "新期-A" });
    // 多打一行让文件长度不同（stale .pyc，见 ⑨）
    fixtureWrite(b.repo.root, "pipeline/agent/cli.py", fakeNewEpisodeCli(["[OK] 第二期", "[from-idea] migrated=false sid=- messages=0"]));
    await b.svc.dispatch("episode.create", { name: "新期-B" });
    sessionScript(b.repo, "新期-A", [READY("新期-A"), { op: "serve", on_turn: [TURN_STARTED, TURN_ENDED, STOP_POINTS] }]);
    sessionScript(b.repo, "新期-B", [READY("新期-B"), { op: "serve", on_turn: [TURN_STARTED, TURN_ENDED, STOP_POINTS] }]);
    await b.svc.dispatch("conv.send", { convKey: "ep:新期-A", text: "接着刚才的选题写" });
    await b.svc.dispatch("conv.send", { convKey: "ep:新期-B", text: "hi" });
    expect(sessionSpawns()).toEqual(["SESSION_CONTINUE", "SESSION_NEW"]);
  });

  it("⑤ conv.resume 对 idea 不再报错：以 SESSION_IDEA 起（core 启动即恒恢复）", async () => {
    const b = await boot();
    sessionScript(b.repo, "idea", [IDEA_READY, { op: "serve", on_turn: [], on_shutdown: "exit" }]);
    await b.svc.dispatch("conv.resume", { convKey: "idea" });
    expect(sessionSpawns()).toEqual(["SESSION_IDEA"]);
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

describe("TH-18 作废的卡立即撤下（D36）", () => {
  const card = (id: string) => ({ t: "request", request_id: id, kind: "fetch", turn_id: "t1", title: id, card_text: id, fields: { no: 1 }, options: ["approve", "reject"], feedback_allowed: false });
  // 真实 core 修后的作废帧形状（Spec 9 §3.1：decision 在场、为 null）
  const VOIDED = (id: string) => ({ t: "request_closed", request_id: id, reason: "voided", decision: null });

  async function run(closed: Record<string, unknown>) {
    const b = await boot();
    // M9 现场的帧序：#6 出卡 → #6 作废 → #7 出卡
    sessionScript(b.repo, b.epKey, [READY(b.epKey), { op: "serve", on_turn: [TURN_STARTED, card("q6"), closed, card("q7")] }]);
    await b.svc.dispatch("conv.send", { convKey: b.key, text: "hi" });
    await waitFor(async () => (await snapOf(b.svc, b.key)).open.some((o) => o.request_id === "q7"));
    return b;
  }

  it("request_closed{voided, decision:null} → open 与徽标同步收缩、零丢帧、最后一条 delta 已不含该卡", async () => {
    const b = await run(VOIDED("q6"));
    const snap = await snapOf(b.svc, b.key);
    expect(snap.framesLost).toBe(0);
    expect(snap.open.map((o) => o.request_id)).toEqual(["q7"]);
    expect(b.svc.episodesList().episodes.find((e) => e.epKey === b.epKey)!.conv).toMatchObject({ openRequests: 1 });
    const deltas = b.pushes.filter((p) => p.kind === "push" && p.topic === "conv.delta") as unknown as { data: { open: { request_id: string }[] } }[];
    expect(deltas.at(-1)!.data.open.map((o) => o.request_id)).toEqual(["q7"]);
  });

  it("钉住根因：缺 decision 的作废帧被 parseOutFrame 判 malformed → 丢帧、卡留在待答区（core 必须带 decision）", async () => {
    const b = await run({ t: "request_closed", request_id: "q6", reason: "voided", cause: "interrupted" });
    const snap = await snapOf(b.svc, b.key);
    expect(snap.framesLost).toBe(1);
    expect(snap.open.map((o) => o.request_id)).toEqual(["q6", "q7"]);
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

  it("拒绝抓取卡与 browser 卡 → 确认框桩调用 0 次（MUT-57）", async () => {
    // M9：上下两条的标题都写「拒绝不需要确认框」，用例体里却从没做过拒绝——MUT-57（拒绝也弹框）因此只有纯函数单测红
    const b = await withCards([FETCH("https://example.com/candidate"), BROWSER("https://example.com/b", "看看")]);
    b.stub.respond = true;
    await b.svc.dispatch("conv.answer", { convKey: b.key, requestId: "qf", decision: "reject" });
    await b.svc.dispatch("conv.answer", { convKey: b.key, requestId: "qb", decision: "reject" });
    expect(b.stub.calls).toBe(0);
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

describe("TH-18b 确认框所属窗口被销毁（D40）", () => {
  const FETCH = { t: "request", request_id: "qf", kind: "fetch", turn_id: "t1", title: "候选", card_text: "…", fields: { no: 1, title: "t", url: "https://example.com/x", type: "image", source: "web", why: "需要素材", expected_dur: "12" }, options: ["approve", "reject"], feedback_allowed: false };
  const ON_ANSWER = { t: "request_closed", request_id: "$request_id", reason: "answered", decision: "$decision", rid: "$rid" };

  it("挂起期间重答 E_BUSY；windowGone → E_STALE、零写入、卡仍打开；之后能重答并恰写 1 行 answer", async () => {
    // 真实的 host/main 两端 broker 串起来；show 模拟 destroy：永不 resolve（Electron 44.4.5 实测）
    let respondNext: ((ok: boolean) => void) | null = null;
    let hang = true;
    const main = createMainConfirmBroker({
      show: () => new Promise<boolean>((r) => { if (hang) return; respondNext = r; }),
      result: (reqId, ok) => hostBroker.resolve(reqId, ok),
    });
    const hostBroker = createConfirmBroker({ bootId: "boot1", post: (m) => { if (m.type === "confirm-query") main.request(m.reqId, m.title, m.detail); } });
    const b = await boot({ overrides: { confirm: (t: string, d: string) => hostBroker.request(t, d) } });
    sessionScript(b.repo, b.epKey, [READY(b.epKey), { op: "serve", on_turn: [TURN_STARTED, FETCH], on_answer: [ON_ANSWER] }]);
    await b.svc.dispatch("conv.send", { convKey: b.key, text: "hi" });
    await waitFor(async () => (await snapOf(b.svc, b.key)).open.length === 1);
    const answers = () => stdinLines(b.repo, b.epKey).filter((l) => JSON.parse(l).t === "answer");

    const first = b.svc.dispatch("conv.answer", { convKey: b.key, requestId: "qf", decision: "approve" }).catch((e) => e);
    await new Promise((r) => setTimeout(r, 50));
    await expect(b.svc.dispatch("conv.answer", { convKey: b.key, requestId: "qf", decision: "approve" })).rejects.toMatchObject({ code: "E_BUSY" });

    main.windowGone();
    // 挂起转普通失败：windowGone 不回复时 first 永不结束，这里在 2 s 内给出「仍挂起」而不是撞用例超时
    const settled = await Promise.race([first, new Promise((r) => setTimeout(() => r({ code: "仍挂起" }), 2000))]);
    expect((settled as { code?: string }).code).toBe("E_STALE");
    expect(answers()).toEqual([]);
    expect((await snapOf(b.svc, b.key)).open.map((o) => o.request_id)).toEqual(["qf"]);

    hang = false;
    const again = b.svc.dispatch("conv.answer", { convKey: b.key, requestId: "qf", decision: "approve" });
    await waitFor(async () => respondNext !== null);
    respondNext!(true);
    await again;
    expect(answers()).toHaveLength(1);
  });
});

describe("TH-19 host 重启后的 generation", () => {
  it("两个不同 bootId 的实例对同一会话键给出不同的 generation", async () => {
    const a = await boot({ bootId: "bootA" });
    const b = await boot({ bootId: "bootB" });
    // 没有会话时的默认快照（snapshot() 的缺省分支）
    expect((await snapOf(a.svc, a.key)).generation).toBe("bootA:0");
    expect((await snapOf(b.svc, b.key)).generation).toBe("bootB:0");
    // spec：各**起一次会话**后比较（M9：此前只比了缺省分支，ensure() 里真正生成会话 generation 的那一行
    // 没被测到——MUT-51 因此存活）
    for (const x of [a, b]) {
      sessionScript(x.repo, x.epKey, [READY(x.epKey), { op: "serve", on_turn: [TURN_STARTED, TURN_ENDED, STOP_POINTS] }]);
      await x.svc.dispatch("conv.send", { convKey: x.key, text: "hi" });
    }
    const sa = await snapOf(a.svc, a.key);
    const sb = await snapOf(b.svc, b.key);
    expect(sa.generation).toBe("bootA:1");
    expect(sb.generation).toBe("bootB:1");
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

// ---------------------------------------------------------------------------
// D45：会话管理（conv.sessions / conv.enter / conv.fresh / conv.delete）
// ---------------------------------------------------------------------------

const SID_A = "aaaaaaaaaaaaaaa1";
const SID_B = "bbbbbbbbbbbbbbb2";

/** 假 core：LIST_SESSIONS 回两行，DELETE_SESSION 按给定退出码；记录每次调用的模板与参数。 */
function adminCore(calls: { t: string; args: Record<string, unknown> }[], opts: { deleteCode?: number; listJson?: string } = {}): HostDeps["runCore"] {
  const rows = [
    { sid: SID_A, messages: 4, assistants: 2, last_activity: "2026-10-08T08:00:00Z", first_user: "改段落三", resumable: true },
    { sid: SID_B, messages: 2, assistants: 1, last_activity: "2026-09-26T11:00:00Z", first_user: "董香第二期做到哪了", resumable: true },
  ];
  return (async (t: string, args: Record<string, unknown>) => {
    calls.push({ t, args });
    const base = { signal: null, stderrTail: "", timedOut: false, stdoutOverflow: false };
    if (t === "LIST_SESSIONS") {
      const out = opts.listJson ?? JSON.stringify(rows);
      return { ...base, code: 0, stdoutTail: out, stdoutFull: out };
    }
    if (t === "LIST_IDEA_SESSIONS") {
      const out = JSON.stringify([{ sid: SID_B, messages: 2, assistants: 1, last_activity: "2026-10-09T06:34:57Z", first_user: "尼古喵喵的素材", resumable: true }]);
      return { ...base, code: 0, stdoutTail: out, stdoutFull: out };
    }
    if (t === "DELETE_SESSION" || t === "DELETE_IDEA_SESSION") {
      const code = opts.deleteCode ?? 0;
      return { ...base, code, stdoutTail: code === 0 ? '{"moved": 4, "trash": "/x"}' : "", stderrTail: code === 3 ? "该期已有活跃会话" : "", stdoutFull: null };
    }
    return { ...base, code: t === "GIT_HEAD" ? 128 : 0, stdoutTail: "", stdoutFull: null };
  }) as unknown as HostDeps["runCore"];
}

/** 剧本：ready 帧带指定 sid（假进程按帧覆盖公共键），shutdown 即退。 */
function scriptWithSid(repo: SessionRepo, epKey: string, sid: string): void {
  sessionScript(repo, epKey, [
    { op: "emit", frame: { ...READY(epKey).frame, sid } },
    { op: "serve", on_turn: [{ ...TURN_STARTED, sid }, { ...TURN_ENDED, sid }, { ...STOP_POINTS, sid }], on_shutdown: "exit" },
  ]);
}

function sessionArgvs(): string[][] {
  return spawnLog.filter((e) => String(e.template).startsWith("SESSION")).map((e) => (e.argv as string[]).slice(3));
}

describe("D45 会话管理", () => {
  it("conv.sessions：字段改名 + 标出活会话；core 输出不合约定 → E_CORE", async () => {
    const calls: { t: string; args: Record<string, unknown> }[] = [];
    const { repo, svc, epKey, key } = await boot({ realCore: true, overrides: { runCore: adminCore(calls) } });
    scriptWithSid(repo, epKey, SID_A);
    await svc.dispatch("conv.send", { convKey: key, text: "hi" });
    await waitFor(async () => (await snapOf(svc, key)).phase === "idle");
    const rows = (await svc.dispatch("conv.sessions", { convKey: key })) as Array<Record<string, unknown>>;
    expect(rows.map((r) => [r.sid, r.live, r.firstUser, r.lastActivity])).toEqual([
      [SID_A, true, "改段落三", "2026-10-08T08:00:00Z"],
      [SID_B, false, "董香第二期做到哪了", "2026-09-26T11:00:00Z"],
    ]);
    // D57 / D58：选题会话也能列（经 core `ava idea /list-sessions`，不带期目录）
    const ideaRows = (await svc.dispatch("conv.sessions", { convKey: "idea" })) as Array<Record<string, unknown>>;
    expect(ideaRows.map((r) => [r.sid, r.live, r.firstUser, r.lastActivity, r.resumable])).toEqual([
      [SID_B, false, "尼古喵喵的素材", "2026-10-09T06:34:57Z", true],
    ]);
    expect(calls.filter((c) => c.t === "LIST_IDEA_SESSIONS").map((c) => c.args)).toEqual([{}]);

    const bad = await boot({ realCore: true, overrides: { runCore: adminCore([], { listJson: '[{"sid": "../x"}]' }) } });
    await expect(bad.svc.dispatch("conv.sessions", { convKey: bad.key })).rejects.toMatchObject({ code: "E_CORE" });
  });

  it("conv.enter：先结束空闲活会话，再 --continue <sid>；进的就是当前会话则不动；非法 sid 不 spawn", async () => {
    const { repo, svc, epKey, key } = await boot({ realCore: true, overrides: { runCore: adminCore([]) } });
    scriptWithSid(repo, epKey, SID_A);
    await svc.dispatch("conv.send", { convKey: key, text: "hi" });
    await waitFor(async () => (await snapOf(svc, key)).phase === "idle");

    await svc.dispatch("conv.enter", { convKey: key, sid: SID_A });
    expect(sessionSpawns()).toEqual(["SESSION_NEW"]);

    scriptWithSid(repo, epKey, SID_B);
    await svc.dispatch("conv.enter", { convKey: key, sid: SID_B });
    expect(sessionSpawns()).toEqual(["SESSION_NEW", "SESSION_CONTINUE"]);
    expect(sessionArgvs()[1].slice(-2)).toEqual(["--continue", SID_B]);
    expect(stdinLines(repo, epKey).some((l) => (JSON.parse(l) as { t: string }).t === "user_message")).toBe(false); // 新进程的记录：没有替人发消息（H-8）
    await waitFor(async () => (await snapOf(svc, key)).phase === "idle");
    const rows = (await svc.dispatch("conv.sessions", { convKey: key })) as Array<{ sid: string; live: boolean }>;
    expect(rows.find((r) => r.live)?.sid).toBe(SID_B);

    await expect(svc.dispatch("conv.enter", { convKey: key, sid: "BBBB" })).rejects.toMatchObject({ code: "E_BAD_REQUEST" });
    expect(sessionSpawns()).toHaveLength(2);
  });

  it("conv.enter / conv.fresh / conv.delete：有回合在跑 → E_BUSY，且不弹确认框", async () => {
    const { repo, svc, epKey, key, stub } = await boot({ realCore: true, overrides: { runCore: adminCore([]) } });
    sessionScript(repo, epKey, [READY(epKey), { op: "serve", on_turn: [TURN_STARTED] }]); // 回合开始后不结束
    await svc.dispatch("conv.send", { convKey: key, text: "hi" });
    await waitFor(async () => (await snapOf(svc, key)).phase === "running");
    await expect(svc.dispatch("conv.enter", { convKey: key, sid: SID_B })).rejects.toMatchObject({ code: "E_BUSY" });
    await expect(svc.dispatch("conv.fresh", { convKey: key })).rejects.toMatchObject({ code: "E_BUSY" });
    await expect(svc.dispatch("conv.delete", { convKey: key, sid: SID_B })).rejects.toMatchObject({ code: "E_BUSY" });
    expect(stub.calls).toBe(0);
    expect((await snapOf(svc, key)).phase).toBe("running");
  });

  it("conv.fresh：结束活会话、清空对话区；下一条消息走 SESSION_NEW", async () => {
    const { repo, svc, epKey, key } = await boot({ realCore: true, overrides: { runCore: adminCore([]) } });
    scriptWithSid(repo, epKey, SID_A);
    await svc.dispatch("conv.send", { convKey: key, text: "hi" });
    await waitFor(async () => (await snapOf(svc, key)).phase === "idle");
    const snap = (await svc.dispatch("conv.fresh", { convKey: key })) as ConvSnapshot;
    expect(snap.phase).toBe("none");
    expect(snap.entries).toEqual([]);
    await svc.dispatch("conv.send", { convKey: key, text: "从头来" });
    expect(sessionSpawns()).toEqual(["SESSION_NEW", "SESSION_NEW"]);
  });

  it("conv.delete：确认框取消 → 一字不删、会话照旧；确认后删活会话 → 先结束再删，对话区清空", async () => {
    const calls: { t: string; args: Record<string, unknown> }[] = [];
    const { repo, svc, epKey, key, stub } = await boot({ realCore: true, overrides: { runCore: adminCore(calls) } });
    scriptWithSid(repo, epKey, SID_A);
    await svc.dispatch("conv.send", { convKey: key, text: "hi" });
    await waitFor(async () => (await snapOf(svc, key)).phase === "idle");

    stub.respond = false;
    expect(await svc.dispatch("conv.delete", { convKey: key, sid: SID_A })).toEqual({ deleted: false, moved: 0 });
    expect(stub.last?.detail).toContain("改段落三");
    expect(stub.last?.detail).toContain("当前打开的会话会先结束");
    expect(calls.filter((c) => c.t === "DELETE_SESSION")).toEqual([]);
    expect((await snapOf(svc, key)).phase).toBe("idle");

    stub.respond = true;
    expect(await svc.dispatch("conv.delete", { convKey: key, sid: SID_A })).toEqual({ deleted: true, moved: 4 });
    expect(calls.filter((c) => c.t === "DELETE_SESSION").map((c) => c.args.sid)).toEqual([SID_A]);
    expect((await snapOf(svc, key)).phase).toBe("none");
    expect(sessionSpawns()).toEqual(["SESSION_NEW"]); // 删的就是活会话：不接回
  });

  it("conv.delete：删别的会话 → 先结束活会话释放租约，删完把它接回来", async () => {
    const calls: { t: string; args: Record<string, unknown> }[] = [];
    const { repo, svc, epKey, key } = await boot({ realCore: true, overrides: { runCore: adminCore(calls) } });
    scriptWithSid(repo, epKey, SID_A);
    await svc.dispatch("conv.send", { convKey: key, text: "hi" });
    await waitFor(async () => (await snapOf(svc, key)).phase === "idle");
    await svc.dispatch("conv.delete", { convKey: key, sid: SID_B });
    expect(calls.filter((c) => c.t === "DELETE_SESSION").map((c) => c.args.sid)).toEqual([SID_B]);
    expect(sessionSpawns()).toEqual(["SESSION_NEW", "SESSION_CONTINUE"]);
    expect(sessionArgvs()[1].slice(-2)).toEqual(["--continue", SID_A]);
  });

  it("conv.delete：core 报租约被占（退出 3）→ E_SESSION_LOCKED；会话已不在列表 → E_STALE 且不弹框", async () => {
    const locked = await boot({ realCore: true, overrides: { runCore: adminCore([], { deleteCode: 3 }) } });
    await expect(locked.svc.dispatch("conv.delete", { convKey: locked.key, sid: SID_B })).rejects.toMatchObject({ code: "E_SESSION_LOCKED" });
    const gone = await boot({ realCore: true, overrides: { runCore: adminCore([]) } });
    await expect(gone.svc.dispatch("conv.delete", { convKey: gone.key, sid: "ccccccccccccccc3" })).rejects.toMatchObject({ code: "E_STALE" });
    expect(gone.stub.calls).toBe(0);
  });
});


// D58：选题会话与期会话同一套会话管理（看 / 进 / 新开 / 删），建期只带当前这段
function ideaScriptWithSid(repo: SessionRepo, sid: string): void {
  sessionScript(repo, "idea", [
    { op: "emit", frame: { ...IDEA_READY.frame, sid } },
    { op: "serve", on_turn: [{ ...TURN_STARTED, sid }, { ...TURN_ENDED, sid }, { ...STOP_POINTS, sid }], on_shutdown: "exit" },
  ]);
}
const ideaArgvs = (): string[][] => sessionArgvs().filter((a) => a.includes("--idea"));

describe("D58 选题会话的会话管理", () => {
  it("conv.enter → --idea --continue <sid>；conv.fresh → 下一次拉起 --idea --fresh，拉起后标记即清", async () => {
    const { repo, svc } = await boot({ realCore: true, overrides: { runCore: adminCore([]) } });
    ideaScriptWithSid(repo, SID_A);
    await svc.dispatch("conv.send", { convKey: "idea", text: "选题甲" });
    await waitFor(async () => (await snapOf(svc, "idea")).phase === "idle");
    expect(ideaArgvs().at(-1)?.slice(-1)).toEqual(["--idea"]);

    ideaScriptWithSid(repo, SID_B);
    await svc.dispatch("conv.enter", { convKey: "idea", sid: SID_B });
    expect(ideaArgvs().at(-1)?.slice(-3)).toEqual(["--idea", "--continue", SID_B]);
    await waitFor(async () => (await snapOf(svc, "idea")).phase === "idle");

    const snap = (await svc.dispatch("conv.fresh", { convKey: "idea" })) as ConvSnapshot;
    expect(snap.phase).toBe("none");
    ideaScriptWithSid(repo, SID_A);
    await svc.dispatch("conv.send", { convKey: "idea", text: "新的选题" });
    expect(ideaArgvs().at(-1)?.slice(-2)).toEqual(["--idea", "--fresh"]);
    await waitFor(async () => (await snapOf(svc, "idea")).phase === "idle");

    await svc.dispatch("conv.end", { convKey: "idea" });
    await waitFor(async () => (await snapOf(svc, "idea")).phase === "exited");
    await svc.dispatch("conv.send", { convKey: "idea", text: "再来" });
    expect(ideaArgvs().at(-1)?.slice(-1)).toEqual(["--idea"]); // 标记已消费：裸 --idea 恢复最近段
  });

  it("conv.delete：确认文案指向 data/_idea 回收站；取消不调用；确认后 DELETE_IDEA_SESSION 带 sid", async () => {
    const calls: { t: string; args: Record<string, unknown> }[] = [];
    const { repo, svc, stub } = await boot({ realCore: true, overrides: { runCore: adminCore(calls) } });
    ideaScriptWithSid(repo, SID_A);
    await svc.dispatch("conv.send", { convKey: "idea", text: "选题甲" });
    await waitFor(async () => (await snapOf(svc, "idea")).phase === "idle");
    stub.respond = false;
    expect(await svc.dispatch("conv.delete", { convKey: "idea", sid: SID_B })).toEqual({ deleted: false, moved: 0 });
    expect(stub.last?.detail).toContain("data/_idea/_agent/session-trash/");
    expect(calls.filter((c) => c.t === "DELETE_IDEA_SESSION")).toEqual([]);
    stub.respond = true;
    expect(await svc.dispatch("conv.delete", { convKey: "idea", sid: SID_B })).toEqual({ deleted: true, moved: 4 });
    expect(calls.filter((c) => c.t === "DELETE_IDEA_SESSION").map((c) => c.args)).toEqual([{ sid: SID_B }]);
    expect(calls.filter((c) => c.t === "DELETE_SESSION")).toEqual([]);
    // 删的是别的段：把刚才为释放租约而结束的活会话按原段接回来
    expect(ideaArgvs().at(-1)?.slice(-3)).toEqual(["--idea", "--continue", SID_A]);
  });

  it("建期：选题活会话号合规 → NEW_EPISODE 带 ideaSid（只带当前这段）；没有活会话不带", async () => {
    const calls: { t: string; args: Record<string, unknown> }[] = [];
    const base = adminCore(calls);
    const runCoreStub = (async (t: string, args: Record<string, unknown>, ...rest: unknown[]) => {
      if (t === "NEW_EPISODE") {
        calls.push({ t, args });
        const out = "[from-idea] migrated=false sid=- messages=0\n";
        return { code: 0, signal: null, stdoutTail: out, stderrTail: "", timedOut: false, stdoutOverflow: false, stdoutFull: null };
      }
      return (base as (...a: unknown[]) => unknown)(t, args, ...rest);
    }) as unknown as HostDeps["runCore"];
    const { repo, svc } = await boot({ realCore: true, overrides: { runCore: runCoreStub } });
    ideaScriptWithSid(repo, SID_A);
    await svc.dispatch("conv.send", { convKey: "idea", text: "选题甲" });
    await waitFor(async () => (await snapOf(svc, "idea")).phase === "idle");
    await svc.dispatch("episode.create", { name: "D58-当前段" });
    await svc.dispatch("episode.create", { name: "D58-无活会话" });
    expect(calls.filter((c) => c.t === "NEW_EPISODE").map((c) => c.args)).toEqual([
      { name: "D58-当前段", ideaSid: SID_A },
      { name: "D58-无活会话" },
    ]);
  });
});

// ---------------------------------------------------------------------------
// D66：回收站查看与选择性彻底清空（conv.trashList / conv.purgeTrash）
// ---------------------------------------------------------------------------

const TRASH_ROWS = [
  { file: `${SID_A}.jsonl`, sid: SID_A, last_ts: "2026-10-09T08:00:00Z", message_count: 4, bytes: 2048 },
  { file: `${SID_A}-20261010T010203000000.jsonl`, sid: SID_A, last_ts: "2026-10-10T01:02:03Z", message_count: 2, bytes: 1536 },
  { file: "fffffffffffffff6.jsonl", sid: null, last_ts: "", message_count: 0, bytes: 0, empty: true },
];

/** 假 core：LIST_TRASH 回三行（含同 sid 两文件与空文件）；PURGE_* 按给定退出码。 */
function trashCore(calls: { t: string; args: Record<string, unknown> }[], opts: { purgeCode?: number; listJson?: string } = {}): HostDeps["runCore"] {
  return (async (t: string, args: Record<string, unknown>) => {
    calls.push({ t, args });
    const base = { signal: null, stderrTail: "", timedOut: false, stdoutOverflow: false };
    if (t === "LIST_TRASH" || t === "LIST_IDEA_TRASH") {
      const out = opts.listJson ?? JSON.stringify(TRASH_ROWS);
      return { ...base, code: 0, stdoutTail: out, stdoutFull: out };
    }
    if (t === "PURGE_TRASH" || t === "PURGE_IDEA_TRASH") {
      const code = opts.purgeCode ?? 0;
      return { ...base, code, stdoutTail: code === 0 ? '{"purged": "x.jsonl"}' : "", stderrTail: code === 3 ? "回收站锁被占" : "", stdoutFull: null };
    }
    return { ...base, code: t === "GIT_HEAD" ? 128 : 0, stdoutTail: "", stdoutFull: null };
  }) as unknown as HostDeps["runCore"];
}

describe("D66 回收站", () => {
  it("conv.trashList：字段改名 + 缺省标记补 false；期 / 选题两叉；core 输出不合约定 → E_CORE", async () => {
    const calls: { t: string; args: Record<string, unknown> }[] = [];
    const { svc, key } = await boot({ realCore: true, overrides: { runCore: trashCore(calls) } });
    const rows = (await svc.dispatch("conv.trashList", { convKey: key })) as Array<Record<string, unknown>>;
    expect(rows.map((r) => [r.file, r.sid, r.messageCount, r.bytes, r.empty, r.parseError, r.partial])).toEqual([
      [`${SID_A}.jsonl`, SID_A, 4, 2048, false, false, false],
      [`${SID_A}-20261010T010203000000.jsonl`, SID_A, 2, 1536, false, false, false],
      ["fffffffffffffff6.jsonl", null, 0, 0, true, false, false],
    ]);
    expect(calls.filter((c) => c.t === "LIST_TRASH").map((c) => c.args)).toEqual([{ ep: expect.any(String) }]);

    await svc.dispatch("conv.trashList", { convKey: "idea" });
    expect(calls.filter((c) => c.t === "LIST_IDEA_TRASH").map((c) => c.args)).toEqual([{}]);

    const bad = await boot({ realCore: true, overrides: { runCore: trashCore([], { listJson: '[{"file": 1}]' }) } });
    await expect(bad.svc.dispatch("conv.trashList", { convKey: bad.key })).rejects.toMatchObject({ code: "E_CORE" });
  });

  it("conv.purgeTrash：确认框在 spawn 之前（取消则一字不动）；确认后按文件删；文案写明「不可恢复」", async () => {
    const calls: { t: string; args: Record<string, unknown> }[] = [];
    const { svc, key, stub } = await boot({ realCore: true, overrides: { runCore: trashCore(calls) } });

    stub.respond = false;
    expect(await svc.dispatch("conv.purgeTrash", { convKey: key, file: `${SID_A}.jsonl` })).toEqual({ purged: false });
    expect(stub.last?.detail).toContain("不可恢复");
    expect(stub.last?.detail).toContain("aaaaaaaa");
    expect(calls.filter((c) => c.t.startsWith("PURGE"))).toEqual([]);

    stub.respond = true;
    expect(await svc.dispatch("conv.purgeTrash", { convKey: key, file: `${SID_A}.jsonl` })).toEqual({ purged: true });
    expect(calls.filter((c) => c.t === "PURGE_TRASH").map((c) => c.args.file)).toEqual([`${SID_A}.jsonl`]);
    // 选题叉：不带期目录
    await svc.dispatch("conv.purgeTrash", { convKey: "idea", file: `${SID_A}.jsonl` });
    expect(calls.filter((c) => c.t === "PURGE_IDEA_TRASH").map((c) => c.args)).toEqual([{ file: `${SID_A}.jsonl` }]);
  });

  it("conv.purgeTrash：退出码 3 → E_SESSION_LOCKED；条目不在列表 → E_STALE 不弹框；坏文件名 → E_BAD_REQUEST 不 spawn", async () => {
    const locked = await boot({ realCore: true, overrides: { runCore: trashCore([], { purgeCode: 3 }) } });
    await expect(locked.svc.dispatch("conv.purgeTrash", { convKey: locked.key, file: `${SID_A}.jsonl` }))
      .rejects.toMatchObject({ code: "E_SESSION_LOCKED" });

    const gone = await boot({ realCore: true, overrides: { runCore: trashCore([]) } });
    await expect(gone.svc.dispatch("conv.purgeTrash", { convKey: gone.key, file: "9999999999999999.jsonl" }))
      .rejects.toMatchObject({ code: "E_STALE" });
    expect(gone.stub.calls).toBe(0);

    const bad = await boot({ realCore: true, overrides: { runCore: trashCore([]) } });
    await expect(bad.svc.dispatch("conv.purgeTrash", { convKey: bad.key, file: "../x.jsonl" }))
      .rejects.toMatchObject({ code: "E_BAD_REQUEST" });
    expect(bad.stub.calls).toBe(0);
  });
});
