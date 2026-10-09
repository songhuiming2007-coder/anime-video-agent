// TH-D42-E：建期带入前结束选题会话（Spec 18 §3.3 ③，🔵-3 附带）。SessionManager 单元层：假进程 + 不杀进程组，
// 才造得出「结束序列走完、进程仍活着」——真进程挨了 SIGKILL 一定会死，集成层测不到这一支。
import { describe, expect, it } from "vitest";
import type { Envelope } from "../../src/shared/protocol";
import type { SessionProc, SessionTemplate } from "../../src/host/spawner";
import { SessionManager } from "../../src/host/sessions";

class FakeProc implements SessionProc {
  pid = 4242;
  writes: string[] = [];
  private out: ((b: Buffer) => void)[] = [];
  private exitFns: ((c: number | null, s: string | null) => void)[] = [];
  constructor(private readonly exitOnShutdown: boolean) {}
  write(line: string): void {
    this.writes.push(line);
    if (this.exitOnShutdown && line.includes('"shutdown"')) queueMicrotask(() => this.exit(0));
  }
  onStdout(fn: (b: Buffer) => void): void {
    this.out.push(fn);
  }
  onStderr(): void {}
  onExit(fn: (c: number | null, s: string | null) => void): void {
    this.exitFns.push(fn);
  }
  signal(): void {} // 不理 SIGTERM
  feed(frame: Record<string, unknown>): void {
    for (const fn of this.out) fn(Buffer.from(`${JSON.stringify({ v: 1, sid: null, seq: 1, ...frame })}\n`));
  }
  exit(code: number | null): void {
    for (const fn of this.exitFns) fn(code, null);
  }
}

function harness(exitOnShutdown: boolean) {
  const templates: SessionTemplate[] = [];
  const pushes: Envelope[] = [];
  const mgr = new SessionManager({
    spawnSession: (t) => {
      templates.push(t);
      const p = new FakeProc(exitOnShutdown);
      queueMicrotask(() => p.feed({ t: "ready", episode: null, scope: "auto", continue_status: "new", llm: "ok", degrade_reason: null, code_freeze_ok: true, history_count: 0, session_bytes: 0, other_sessions: [], resume_prompt_tokens: null, resume_prompt_chars: null }));
      return p;
    },
    resolveKey: async () => ({ name: "K", value: "V" }),
    resolveWebKeys: async () => ({}),
    confirm: async () => true,
    now: () => 1,
    bootId: "boot1",
    push: (e) => pushes.push(e),
    diag: () => undefined,
    blocked: () => false,
    repoRoot: () => "/repo",
    isActive: () => false,
    canReadApprovals: () => false,
    killGroup: () => undefined, // 进程组清理失灵：结束序列走完进程仍在
    groupAlive: () => true,
    changed: () => undefined,
    timing: { readyMs: 2000, coalesceMs: 0, settleMs: 60_000, sendAckMs: 1000, answerAckMs: 1000, endWaitMs: 50, killGraceMs: 50 },
  });
  return { mgr, templates, pushes };
}

describe("TH-D42-E 建期前结束选题会话", () => {
  it("进程按 shutdown 退出 → endForMigration 为真；resetAfterMigration 重推空快照（phase none、零条目）", async () => {
    const h = harness(true);
    await h.mgr.resume("idea", { key: "idea", epKey: null, abs: null, mode: "idea" });
    expect(h.templates).toEqual(["SESSION_IDEA"]);
    expect(await h.mgr.endForMigration("idea")).toBe(true);
    expect(h.mgr.snapshot("idea").phase).toBe("exited");
    h.mgr.resetAfterMigration("idea");
    const last = h.pushes.filter((e) => e.kind === "push" && e.topic === "conv.snapshot").at(-1);
    expect(last).toMatchObject({ convKey: "idea", data: { phase: "none", entries: [] } });
    expect(h.mgr.snapshot("idea")).toMatchObject({ phase: "none", entries: [] });
  });

  it("结束序列走完进程仍活着 → endForMigration 为假（调用方不许建期）；resetAfterMigration 不动活进程", async () => {
    const h = harness(false);
    await h.mgr.resume("idea", { key: "idea", epKey: null, abs: null, mode: "idea" });
    expect(await h.mgr.endForMigration("idea")).toBe(false);
    h.mgr.resetAfterMigration("idea");
    expect(h.mgr.snapshot("idea").phase).not.toBe("none");
  });

  it("没有选题会话 → endForMigration 直接为真、零 spawn", async () => {
    const h = harness(true);
    expect(await h.mgr.endForMigration("idea")).toBe(true);
    expect(h.templates).toEqual([]);
  });
});
