// 事件行解析与 job 折叠（Spec 8 §3.1 容错规则、§3.3 折叠规则）。纯函数、确定性。
import { EVENT_TYPES, type EventRecord, type EventType } from "./contracts";
import { PENDING_STALE_MS } from "./constants";
import { parseLossless } from "./losslessJson";
import type { DegradedNotice, JobView } from "./protocol";

const REQUIRED = ["event_id", "timestamp", "episode", "type"] as const;

/** 一行 → 事件；缺必需字段或解析失败（含 mtime_ns 非整数，规则 8）→ ok:false，调用方计入 malformed。 */
export function parseEventLine(line: string): { ok: true; ev: EventRecord } | { ok: false } {
  let raw: unknown;
  try {
    raw = parseLossless(line);
  } catch {
    return { ok: false };
  }
  if (raw === null || typeof raw !== "object" || Array.isArray(raw)) return { ok: false };
  const o = raw as Record<string, unknown>;
  for (const k of REQUIRED) if (typeof o[k] !== "string") return { ok: false };
  const payload = o.payload;
  if (payload === null || typeof payload !== "object" || Array.isArray(payload)) return { ok: false };
  const type = o.type as string;
  const kind: EventType | "unknown" = (EVENT_TYPES as readonly string[]).includes(type) ? (type as EventType) : "unknown";
  return {
    ok: true,
    ev: {
      event_id: o.event_id as string,
      timestamp: o.timestamp as string,
      episode: o.episode as string,
      type,
      kind,
      payload: payload as Record<string, unknown>,
    },
  };
}

function str(v: unknown): string | null {
  return typeof v === "string" ? v : null;
}
function num(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

const FINISHED_STATES = new Set(["succeeded", "failed"]);

/**
 * 按文件行序折叠：job_created → pending；job_started → running（即使缺 created）；
 * job_finished → payload.status；job_blocked → blocked。无 job_id 的事件不进折叠；sidecar_degraded 不进折叠。
 * finishedEventMissing：running 且 pid 已不存在，且上一个 tick 已处于同样情形（prevMissing，连续 2 个 tick）。
 */
export function foldEvents(
  events: EventRecord[],
  now: number,
  pidAlive: (pid: number) => boolean,
  prevMissing: ReadonlySet<string>,
): JobView[] {
  const jobs = new Map<string, JobView>();
  for (const ev of events) {
    if (ev.kind === "sidecar_degraded") continue;
    const p = ev.payload;
    const jobId = str(p.job_id);
    if (!jobId) continue;
    if (!ev.kind.startsWith("job_")) continue;
    let j = jobs.get(jobId);
    if (!j) {
      j = {
        jobId,
        command: null,
        state: "pending",
        lastEventAt: ev.timestamp,
        pid: null,
        returncode: null,
        durationS: null,
        stderrTail: null,
        message: null,
        noFollowupEvents: false,
        finishedEventMissing: false,
      };
      jobs.set(jobId, j);
    }
    j.lastEventAt = ev.timestamp;
    if (str(p.command)) j.command = str(p.command);
    switch (ev.kind) {
      case "job_created":
        j.state = "pending";
        break;
      case "job_started":
        j.state = "running";
        j.pid = num(p.pid);
        break;
      case "job_finished": {
        const st = str(p.status);
        j.state = st && FINISHED_STATES.has(st) ? (st as "succeeded" | "failed") : "failed";
        j.returncode = num(p.returncode);
        j.durationS = num(p.duration_s);
        j.stderrTail = str(p.stderr_tail);
        j.message = str(p.message);
        break;
      }
      case "job_blocked":
        j.state = "blocked";
        j.message = str(p.reason) ?? str(p.message);
        break;
      default:
        break; // job_heartbeat 只刷新 lastEventAt
    }
  }
  const out = [...jobs.values()];
  for (const j of out) {
    const t = Date.parse(j.lastEventAt);
    j.noFollowupEvents = j.state === "pending" && Number.isFinite(t) && now - t > PENDING_STALE_MS;
    j.finishedEventMissing =
      j.state === "running" && j.pid !== null && !pidAlive(j.pid) && prevMissing.has(j.jobId);
  }
  return out;
}

/** 本 tick 中「running 且 pid 已不存在」的 jobId 集合，作为下一 tick 的 prevMissing。 */
export function goneRunningJobIds(jobs: JobView[], pidAlive: (pid: number) => boolean): Set<string> {
  return new Set(jobs.filter((j) => j.state === "running" && j.pid !== null && !pidAlive(j.pid)).map((j) => j.jobId));
}

export function degradedNoticesOf(events: EventRecord[]): DegradedNotice[] {
  return events
    .filter((e) => e.kind === "sidecar_degraded")
    .map((e) => ({ at: e.timestamp, droppedDuringCircuit: num(e.payload.dropped_during_circuit) ?? 0 }));
}
