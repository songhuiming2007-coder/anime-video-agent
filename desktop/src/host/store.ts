// approvals_store.json 直读（Spec 8 §2.3、§3.1 规则 4/8/9）。
// 只经 parseLossless 解析；host 内部形状 ApprovalRecord（mtime_ns: bigint），出 host 前经 toWireApproval。
// 该文件经 paths.atomic_write 整文件替换，读端只会看到旧版或新版；desktop 从不打开 approvals_store.lock。
import type { ApprovalRecord, ArtifactFingerprint } from "../shared/contracts";
import { parseLossless, toWireApproval } from "../shared/losslessJson";
import type { SnapshotApprovals } from "../shared/protocol";
import { errnoOf, type FsProbe, type FsRead } from "./fsio";

export type StoreRead =
  | { state: "absent" }
  | { state: "ok"; items: ApprovalRecord[]; skipped: number }
  | { state: "error"; message: string };

const decoder = new TextDecoder("utf-8");

function strOrNull(v: unknown): string | null | undefined {
  if (v === null || v === undefined) return null;
  return typeof v === "string" ? v : undefined;
}

function parseArtifact(v: unknown): ArtifactFingerprint | null {
  if (v === null || typeof v !== "object") return null;
  const o = v as Record<string, unknown>;
  if (typeof o.path !== "string" || typeof o.mtime_ns !== "bigint") return null;
  // size 仍为 number，但必须是安全整数（规则 8）
  if (typeof o.size !== "number" || !Number.isSafeInteger(o.size)) return null;
  return { path: o.path, size: o.size, mtime_ns: o.mtime_ns };
}

/** 单个元素 → ApprovalRecord；形状不符 → null（调用方跳过并计数，规则 4）。 */
export function parseApproval(v: unknown): ApprovalRecord | null {
  if (v === null || typeof v !== "object" || Array.isArray(v)) return null;
  const o = v as Record<string, unknown>;
  for (const k of ["approval_id", "episode", "type", "status", "created_at"] as const) {
    if (typeof o[k] !== "string") return null;
  }
  if (!Array.isArray(o.artifacts)) return null;
  const artifacts: ArtifactFingerprint[] = [];
  for (const a of o.artifacts) {
    const fp = parseArtifact(a);
    if (!fp) return null;
    artifacts.push(fp);
  }
  const resolved_at = strOrNull(o.resolved_at);
  const resolved_by = strOrNull(o.resolved_by);
  const confirmed_by = strOrNull(o.confirmed_by);
  const confirmed_at = strOrNull(o.confirmed_at);
  if ([resolved_at, resolved_by, confirmed_by, confirmed_at].some((x) => x === undefined)) return null;
  let feedback: ApprovalRecord["feedback"] = null;
  if (o.feedback !== null && o.feedback !== undefined) {
    const f = o.feedback as Record<string, unknown>;
    if (typeof f !== "object" || typeof f.target !== "string" || typeof f.problem !== "string") return null;
    feedback = { target: f.target, problem: f.problem };
  }
  const options = Array.isArray(o.options) && o.options.every((x) => typeof x === "string") ? (o.options as string[]) : ["approve", "reject"];
  return {
    approval_id: o.approval_id as string,
    episode: o.episode as string,
    type: o.type as string,
    status: o.status as string,
    artifacts,
    options,
    created_at: o.created_at as string,
    resolved_at: resolved_at as string | null,
    resolved_by: resolved_by as string | null,
    confirmed_by: confirmed_by as string | null,
    confirmed_at: confirmed_at as string | null,
    feedback,
    note: typeof o.note === "string" ? o.note : "",
  };
}

export function readApprovalRecords(file: string, fs: FsRead): StoreRead {
  let text: string;
  try {
    text = decoder.decode(fs.readFile(file));
  } catch (e) {
    if (errnoOf(e) === "ENOENT") return { state: "absent" };
    return { state: "error", message: `对象库读取失败：${errnoOf(e) ?? String(e)}` };
  }
  let raw: unknown;
  try {
    raw = parseLossless(text);
  } catch (e) {
    return { state: "error", message: `对象库解析失败：${e instanceof Error ? e.message : String(e)}` };
  }
  if (!Array.isArray(raw)) return { state: "error", message: "对象库解析失败：顶层不是数组" };
  const items: ApprovalRecord[] = [];
  let skipped = 0;
  for (const el of raw) {
    const r = parseApproval(el);
    if (r) items.push(r);
    else skipped += 1;
  }
  return { state: "ok", items, skipped };
}

/** 线上形状；整份解析失败时保留上次成功结果（规则 4）。 */
export function readApprovalStore(
  file: string,
  fs: FsRead,
  lastGood: ApprovalRecord[] | null = null,
  healedAt: string | null = null,
): SnapshotApprovals {
  const r = readApprovalRecords(file, fs);
  if (r.state === "absent") return { state: "absent" };
  if (r.state === "error") return { state: "error", message: r.message, lastGood: lastGood ? lastGood.map(toWireApproval) : null };
  return { state: "ok", items: r.items.map(toWireApproval), skipped: r.skipped, healedAt };
}

/** 关联产物当前 (size, mtime_ns) 与钉住值逐项 bigint 相等；文件缺失按 Spec 3 _fingerprint 记为 (-1, -1)。 */
export function fingerprintsMatch(epAbs: string, pinned: ArtifactFingerprint[], fs: FsProbe): boolean {
  for (const a of pinned) {
    let size = -1n;
    let mtime = -1n;
    try {
      const st = fs.statBig(`${epAbs}/${a.path}`);
      size = st.size;
      mtime = st.mtimeNs;
    } catch {
      /* 缺失 → (-1, -1) */
    }
    if (size !== BigInt(a.size) || mtime !== a.mtime_ns) return false;
  }
  return true;
}
