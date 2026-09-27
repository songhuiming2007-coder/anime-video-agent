// e2e 会话夹具（Spec 10 §7）：假 protocol.py 的临时仓库 + 未打包构建的确认框/退出桩。
// 复用 vitest 侧的 sessionRepo/fixtureWrite/sessionScript（同一份"会话类夹具"，TF-1/TF-2/TF-3 覆盖它）。
import { statSync } from "node:fs";
import { join } from "node:path";
import { launch, type Launched } from "./fixtures";
import { stringifyLossless } from "../src/shared/losslessJson";
import { cleanup, tmp } from "../tests/helpers";
import { fixtureWrite, sessionRepo, type SessionRepo } from "../tests/fixtures/session";

export interface SessionFixture {
  repo: SessionRepo;
  cleanup: () => void;
}

export function sessionFixture(eps: string[] = ["SESS-A"], opts: { at035?: boolean } = {}): SessionFixture {
  const repo = sessionRepo();
  for (const key of eps) {
    // 所有写入经 fixtureWrite（TF-1 守卫；它自己负责建父目录）
    fixtureWrite(repo.root, `data/episodes/${key}/01-topic.md`, "# 选题\n类型：杂谈\n");
    fixtureWrite(repo.root, `data/episodes/${key}/02-script.md`, "## 段落 1\n配音：测试台词。\n");
    fixtureWrite(repo.root, `data/episodes/${key}/03-audio/manifest.json`, JSON.stringify({ segments: [{ index: 1, duration: 4 }] }));
    fixtureWrite(repo.root, `data/episodes/${key}/03-audio/seg-01.wav`, "");
    if (!opts.at035) fixtureWrite(repo.root, `data/episodes/${key}/04-clips.json`, '{"segments":[]}\n');
  }
  return { repo, cleanup: () => cleanup(repo.root) };
}

/** 写对象库（自动呼出与决策卡都读它；mtime_ns 用无损序列化，避免被 lossless reviver 拒绝）。 */
export function writeStore(repo: SessionRepo, epKey: string, items: Record<string, unknown>[]): void {
  fixtureWrite(repo.root, `data/episodes/${epKey}/_agent/approvals_store.json`, `${stringifyLossless(items)}\n`);
}

/** 取文件的真实指纹，供对象钉住（否则 H5 会自愈、对象可能被 supersede）。 */
export function fingerprintOf(repo: SessionRepo, epKey: string, rel: string): { size: number; mtime_ns: bigint } {
  const st = statSync(join(repo.root, "data/episodes", epKey, rel), { bigint: true });
  return { size: Number(st.size), mtime_ns: st.mtimeNs };
}

export function pendingObj(id: string, type: string, createdAt = "2026-09-25T10:00:00Z", artifactPath = "04-clips.json", fp: { size: number; mtime_ns: bigint } = { size: 3, mtime_ns: 1n }): Record<string, unknown> {
  return {
    approval_id: id, episode: "x", type, status: "pending", options: ["approve", "reject"], created_at: createdAt,
    resolved_at: null, resolved_by: null, confirmed_by: null, confirmed_at: null, feedback: null, note: "",
    artifacts: [{ path: artifactPath, size: fp.size, mtime_ns: fp.mtime_ns }],
  };
}

export async function launchSession(repo: SessionRepo, extra: string[] = []): Promise<Launched> {
  return launch(repo.root, [`--ava-keychain=${repo.keychainPath}`, ...extra], tmp("ud"));
}

/** Spec 10 §2.10 退出确认桩（仅未打包构建）；hold=true 时挂住不回答（TX-8c 重入） */
export async function stubQuit(L: Launched, respond: "quit" | "cancel", hold = false): Promise<void> {
  await L.app.evaluate((_e, r) => {
    (globalThis as unknown as { __avaTestQuit: unknown }).__avaTestQuit = { calls: 0, lists: [], respond: r.respond, hold: r.hold };
  }, { respond, hold });
}

/** 放行被 hold 住的退出确认（TX-8c） */
export async function releaseQuit(L: Launched, respond: "quit" | "cancel"): Promise<void> {
  await L.app.evaluate((_e, r) => {
    (globalThis as unknown as { __avaTestQuitRelease: (r: string) => void }).__avaTestQuitRelease(r);
  }, respond);
}

export async function quitStubCalls(L: Launched): Promise<number> {
  return L.app.evaluate(() => (globalThis as unknown as { __avaTestQuit?: { calls: number } }).__avaTestQuit?.calls ?? -1);
}

/** Spec 10 §3.3 原生确认框桩（仅未打包构建） */
export async function stubConfirm(L: Launched, respond: boolean): Promise<void> {
  await L.app.evaluate((_e, r) => {
    (globalThis as unknown as { __avaTestConfirm: unknown }).__avaTestConfirm = { calls: 0, respond: r, last: null };
  }, respond);
}
