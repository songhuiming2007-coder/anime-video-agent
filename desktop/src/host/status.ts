// 工序状态（Spec 8 §2.3）：一律 spawn `python -m pipeline.status <期> --json`，TS 侧不推导工序。
import type { EpisodeStatusJson } from "../shared/contracts";
import { STATUS_STDOUT_MAX_BYTES } from "../shared/constants";
import type { SnapshotStatus } from "../shared/protocol";
import { runCore } from "./spawner";

export function parseStatusJson(stdout: string): EpisodeStatusJson | null {
  let raw: unknown;
  try {
    raw = JSON.parse(stdout);
  } catch {
    return null;
  }
  if (raw === null || typeof raw !== "object") return null;
  const o = raw as Record<string, unknown>;
  const strs = ["episode_dir", "episode_name", "current_step", "next_action", "docs_ref"] as const;
  for (const k of strs) if (typeof o[k] !== "string") return null;
  if (typeof o.is_blocked !== "boolean") return null;
  if (!Array.isArray(o.completed_steps) || !Array.isArray(o.advisories)) return null;
  return {
    episode_dir: o.episode_dir as string,
    episode_name: o.episode_name as string,
    current_step: o.current_step as string,
    is_blocked: o.is_blocked,
    block_reason: typeof o.block_reason === "string" ? o.block_reason : null,
    completed_steps: (o.completed_steps as unknown[]).map(String),
    next_action: o.next_action as string,
    next_command: typeof o.next_command === "string" ? o.next_command : null,
    docs_ref: o.docs_ref as string,
    advisories: (o.advisories as unknown[]).map(String),
  };
}

export async function fetchStatus(repoRoot: string, epAbs: string): Promise<SnapshotStatus> {
  const r = await runCore("STATUS", { ep: epAbs }, { repoRoot });
  if (r.timedOut) return { ok: false, code: "E_TIMEOUT", message: "status 超时；结果未知，请以稍后刷新为准" };
  if (r.code !== 0) return { ok: false, code: "E_CORE", message: `status 退出 ${r.code}：${r.stderrTail.trim().slice(-400)}` };
  if (r.stdoutOverflow || r.stdoutFull === null) return { ok: false, code: "E_CORE", message: `status --json 输出超过 ${STATUS_STDOUT_MAX_BYTES} 字节上限` };
  const v = parseStatusJson(r.stdoutFull);
  if (!v) return { ok: false, code: "E_CORE", message: "status --json 输出无法解析" };
  return { ok: true, value: v };
}
