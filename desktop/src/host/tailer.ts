// events.jsonl 轮询 tail（Spec 8 §2.4）：纯状态机，fs 注入。不用 fs.watch。
// 单次 poll 判定顺序是契约（MUT-2）：不可达 → 缺席 → (dev,ino) 变化 → size<offset → 锚点不符 → 追加。
import { ANCHOR_BYTES, READ_CHUNK_BYTES, SNAPSHOT_MAX_BYTES } from "../shared/constants";
import type { LineSplitter } from "../shared/jsonl";
import type { FsRead } from "./fsio";

export interface TailState {
  dev: bigint;
  ino: bigint; // 0n = 文件尚未出现过
  offset: number;
  /** offset 之前的最后 min(ANCHOR_BYTES, offset) 个字节 */
  anchor: Uint8Array;
  generation: number;
}

export interface PollResult {
  state: TailState | null;
  newLines: string[];
  resynced: boolean;
  absent: boolean;
  /** 期目录不可达：保留上次状态，不创建任何东西 */
  unreachable: boolean;
  /** partial 超过上限仍无换行：已丢弃并跳到下一个换行 */
  overflow: boolean;
  /** 本次从 0 起读但文件超过 SNAPSHOT_MAX_BYTES，只载了尾部 */
  truncatedHead: boolean;
  /** 读完本次后仍有未读字节（snapshot 首载据此继续分块读） */
  more: boolean;
}

export interface TailLimits {
  readChunk: number;
  snapshotMax: number;
}

const DEFAULT_LIMITS: TailLimits = { readChunk: READ_CHUNK_BYTES, snapshotMax: SNAPSHOT_MAX_BYTES };
const EMPTY = new Uint8Array(0);

function parentOf(file: string): string {
  const i = file.lastIndexOf("/");
  return i > 0 ? file.slice(0, i) : "/";
}

function bytesEqual(a: Uint8Array, b: Uint8Array): boolean {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
  return true;
}

function tailBytes(prev: Uint8Array, next: Uint8Array): Uint8Array {
  if (next.length >= ANCHOR_BYTES) return next.slice(next.length - ANCHOR_BYTES);
  const joined = new Uint8Array(prev.length + next.length);
  joined.set(prev, 0);
  joined.set(next, prev.length);
  return joined.slice(Math.max(0, joined.length - ANCHOR_BYTES));
}

/**
 * 强制从 0 重读、且 generation 保持单调的状态（重新挂载时用；§2.4 断线第三种含义、§3.3 generation 单调 +1）。
 * ino = 0n 让下一次 pollOnce 按「首次看到」接上新文件的 (dev, ino)，generation 原样保留。
 */
export function resyncState(prev: TailState | null): TailState {
  return { dev: 0n, ino: 0n, offset: 0, anchor: EMPTY, generation: (prev?.generation ?? 0) + 1 };
}

export function pollOnce(
  state: TailState | null,
  file: string,
  fs: FsRead,
  splitter: LineSplitter,
  limits: TailLimits = DEFAULT_LIMITS,
): PollResult {
  const base = { newLines: [] as string[], resynced: false, absent: false, unreachable: false, overflow: false, truncatedHead: false, more: false };

  let size: number;
  let dev: bigint;
  let ino: bigint;
  try {
    const st = fs.statBig(file);
    size = Number(st.size);
    dev = st.dev;
    ino = st.ino;
  } catch {
    let dirOk = false;
    try {
      dirOk = fs.stat(parentOf(file)).isDirectory();
    } catch {
      dirOk = false;
    }
    if (!dirOk) return { ...base, state, unreachable: true }; // 顺序 1
    // 顺序 2：文件尚未创建（或已被删）；offset 归零。曾经存在过 → 视为一次 resync
    const existed = state !== null && state.ino !== 0n;
    splitter.reset();
    return {
      ...base,
      state: { dev: 0n, ino: 0n, offset: 0, anchor: EMPTY, generation: (state?.generation ?? 0) + (existed ? 1 : 0) },
      absent: true,
      resynced: existed,
    };
  }

  let s: TailState = state ?? { dev, ino, offset: 0, anchor: EMPTY, generation: 0 };
  let resynced = false;
  const firstSight = state === null || state.ino === 0n;
  if (!firstSight && (dev !== s.dev || ino !== s.ino)) {
    resynced = true; // 顺序 3：被替换
  } else if (size < s.offset) {
    resynced = true; // 顺序 4：截断
  } else if (size >= s.offset && s.offset >= ANCHOR_BYTES) {
    // 显式要求 size ≥ offset：顺序 4 若被删，截断场景也不许由锚点比对接管（MUT-2）
    const now = fs.readRange(file, s.offset - ANCHOR_BYTES, ANCHOR_BYTES);
    if (!bytesEqual(now, s.anchor)) resynced = true; // 顺序 5：截断后又写到更长
  }
  if (resynced) {
    s = { dev, ino, offset: 0, anchor: EMPTY, generation: s.generation + 1 };
    splitter.reset();
  } else if (firstSight) {
    s = { ...s, dev, ino };
  }

  let truncatedHead = false;
  if (s.offset === 0 && size > limits.snapshotMax) {
    // 首载或 resync 时文件过大：只读尾部，从第一个 \n 之后开始
    s = { ...s, offset: size - limits.snapshotMax, anchor: EMPTY };
    splitter.skipToNextLine();
    truncatedHead = true;
  }

  if (size <= s.offset) return { ...base, state: s, resynced, truncatedHead };

  // 顺序 6：正常追加
  const want = Math.min(size - s.offset, limits.readChunk);
  const bytes = fs.readRange(file, s.offset, want);
  const { lines, overflow } = splitter.push(bytes);
  const offset = s.offset + bytes.length;
  const anchor = truncatedHead && bytes.length < ANCHOR_BYTES ? fs.readRange(file, Math.max(0, offset - ANCHOR_BYTES), Math.min(ANCHOR_BYTES, offset)) : tailBytes(s.anchor, bytes);
  return {
    ...base,
    state: { ...s, offset, anchor },
    newLines: lines,
    resynced,
    overflow,
    truncatedHead,
    more: offset < size,
  };
}
