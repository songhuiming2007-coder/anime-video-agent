// TE-1~TE-7、TE-11：pollOnce 状态机，真实文件系统。同步点铁律：写端先 flush/关闭再读；驱动 N 次 pollOnce，不 sleep。
import { spawn } from "node:child_process";
import { appendFileSync, mkdirSync, readFileSync, renameSync, rmSync, statSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { afterAll, beforeEach, describe, expect, it } from "vitest";
import { LineSplitter } from "../../src/shared/jsonl";
import { realFs } from "../../src/host/fsio";
import { pollOnce, type PollResult, type TailState } from "../../src/host/tailer";
import { cleanup, PY, REPO, tmp, treeManifest } from "../helpers";

const root = tmp("tail");
afterAll(() => cleanup(root));

let dir: string;
let file: string;
let n = 0;
beforeEach(() => {
  dir = join(root, `ep${n++}`);
  mkdirSync(dir);
  file = join(dir, "events.jsonl");
});

const line = (i: number) => `{"event_id":"evt_${String(i).padStart(13, "0")}_abcd","n":${i},"pad":"${"x".repeat(40)}"}\n`;

/** 驱动 pollOnce 直到读完（snapshot 首载的分块语义） */
function drain(state: TailState | null, sp: LineSplitter, limits?: { readChunk: number; snapshotMax: number }) {
  const lines: string[] = [];
  let r: PollResult;
  let resynced = false;
  let truncatedHead = false;
  let s = state;
  do {
    r = pollOnce(s, file, realFs, sp, limits);
    s = r.state;
    lines.push(...r.newLines);
    resynced ||= r.resynced;
    truncatedHead ||= r.truncatedHead;
  } while (r.more);
  return { state: s, lines, resynced, truncatedHead, last: r };
}

describe("TE-1 并发追加完整性（真实 EventPublisher 写端）", () => {
  it("Python 在 flock 内追加 2000 行、每 37 行驱动一次 pollOnce → 下发序列与文件逐行相等", async () => {
    const dataRoot = join(root, "te1-data");
    const ep = join(dataRoot, "episodes", "EP");
    mkdirSync(ep, { recursive: true });
    const evFile = join(ep, "events.jsonl");
    const code = `
import sys
from pathlib import Path
from pipeline.jobs import EventPublisher, EventType
data_root = Path(sys.argv[1]); ep = data_root / "episodes" / "EP"
pub = EventPublisher(data_root=data_root); pub.start()
N, B, i = 2000, 37, 0
while i < N:
    for _ in range(min(B, N - i)):
        pub.emit(EventType.JOB_HEARTBEAT, {"job_id": f"job_{i}", "n": i, "note": "配音"}, episode_dir=ep); i += 1
    pub._queue.join()
    print("batch", flush=True)
    sys.stdin.readline()
pub.close()
print("done", flush=True)
`;
    const child = spawn(PY, ["-c", code, dataRoot], { cwd: REPO, env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1" }, stdio: ["pipe", "pipe", "inherit"] });
    const sp = new LineSplitter(1024 * 1024);
    let state: TailState | null = null;
    const got: string[] = [];
    let polls = 0;
    await new Promise<void>((resolve, reject) => {
      let buf = "";
      child.stdout.on("data", (b: Buffer) => {
        buf += b.toString();
        let i: number;
        while ((i = buf.indexOf("\n")) >= 0) {
          const msg = buf.slice(0, i);
          buf = buf.slice(i + 1);
          let r: PollResult;
          do {
            r = pollOnce(state, evFile, realFs, sp);
            state = r.state;
            got.push(...r.newLines);
            expect(r.resynced).toBe(false);
          } while (r.more);
          polls += 1;
          if (msg === "batch") child.stdin.write("\n");
          else if (msg === "done") resolve();
        }
      });
      child.on("error", reject);
    });
    const fileLines = readFileSync(evFile, "utf-8").split("\n").filter((l) => l.length > 0);
    expect(fileLines).toHaveLength(2000);
    expect(got).toEqual(fileLines);
    expect(polls).toBe(Math.ceil(2000 / 37) + 1);
  });
});

describe("TE-2 半行", () => {
  it("写前半行 → 零下发；补齐 → 恰下发 1 行", () => {
    const sp = new LineSplitter(1024);
    writeFileSync(file, '{"a":');
    let r = pollOnce(null, file, realFs, sp);
    expect(r.newLines).toEqual([]);
    appendFileSync(file, "1}\n");
    r = pollOnce(r.state, file, realFs, sp);
    expect(r.newLines).toEqual(['{"a":1}']);
  });
});

describe("TE-3 截断", () => {
  it("截为 0 再写 3 行（总长小于原 offset）→ resynced、generation 恰 +1、下发恰为新 3 行", () => {
    const sp = new LineSplitter(1024);
    writeFileSync(file, Array.from({ length: 10 }, (_, i) => line(i)).join(""));
    const first = drain(null, sp);
    expect(first.lines).toHaveLength(10);
    const gen0 = first.state!.generation;
    writeFileSync(file, ""); // 同 inode 截断
    appendFileSync(file, line(100) + line(101) + line(102));
    const r = pollOnce(first.state, file, realFs, sp);
    expect(r.resynced).toBe(true);
    expect(r.state!.generation).toBe(gen0 + 1);
    expect(r.newLines).toEqual([line(100), line(101), line(102)].map((l) => l.trimEnd()));
  });
});

describe("TE-4 截断后写长", () => {
  it("inode 不变、写入比原 offset 更长的新内容 → 锚点不符触发 resync，下发等于新文件全文", () => {
    const sp = new LineSplitter(1024);
    writeFileSync(file, line(1) + line(2));
    const first = drain(null, sp);
    const ino = statSync(file).ino;
    const fresh = [line(7), line(8), line(9), line(10)];
    writeFileSync(file, fresh.join(""));
    expect(statSync(file).ino).toBe(ino);
    const r = pollOnce(first.state, file, realFs, sp);
    expect(r.resynced).toBe(true);
    expect(r.newLines).toEqual(fresh.map((l) => l.trimEnd()));
  });
});

describe("TE-5 替换", () => {
  it("以新 inode 文件 rename 覆盖 → resync", () => {
    const sp = new LineSplitter(1024);
    writeFileSync(file, line(1) + line(2) + line(3));
    const first = drain(null, sp);
    writeFileSync(join(dir, "new.jsonl"), line(1) + line(2) + line(3) + line(4));
    renameSync(join(dir, "new.jsonl"), file);
    const r = pollOnce(first.state, file, realFs, sp);
    expect(r.resynced).toBe(true);
    expect(r.state!.generation).toBe(first.state!.generation + 1);
    expect(r.newLines).toHaveLength(4);
  });
});

describe("TE-6 缺席与出现", () => {
  it("文件不存在 → absent；随后创建 → 从 0 读起", () => {
    const sp = new LineSplitter(1024);
    const r1 = pollOnce(null, file, realFs, sp);
    expect(r1.absent).toBe(true);
    expect(r1.resynced).toBe(false);
    expect(r1.state!.offset).toBe(0);
    writeFileSync(file, line(1));
    const r2 = pollOnce(r1.state, file, realFs, sp);
    expect(r2.absent).toBe(false);
    expect(r2.resynced).toBe(false);
    expect(r2.newLines).toEqual([line(1).trimEnd()]);
  });
});

describe("TE-7 超长半行", () => {
  it("1 MiB+1 字节无换行 → 丢弃并跳到下一行，不重读、不死循环；后续完整行照常下发", () => {
    const MAX = 1024 * 1024;
    const sp = new LineSplitter(MAX);
    writeFileSync(file, line(1) + "g".repeat(MAX + 1));
    const r1 = drain(null, sp);
    expect(r1.lines).toEqual([line(1).trimEnd()]);
    expect(r1.last.overflow).toBe(true);
    appendFileSync(file, "tail-of-garbage\n" + line(2));
    const r2 = pollOnce(r1.state, file, realFs, sp);
    expect(r2.overflow).toBe(false);
    expect(r2.resynced).toBe(false);
    expect(r2.newLines).toEqual([line(2).trimEnd()]);
  });
});

describe("TE-11 期目录在两次 poll 间被删", () => {
  it("再 poll → unreachable，期目录仍不存在（I1：绝不 mkdir）", () => {
    const sp = new LineSplitter(1024);
    writeFileSync(file, line(1));
    const r1 = pollOnce(null, file, realFs, sp);
    expect(r1.newLines).toHaveLength(1);
    const before = treeManifest(root).filter((l) => !l.startsWith(`${dir.slice(root.length + 1)}`));
    rmSync(dir, { recursive: true });
    const r2 = pollOnce(r1.state, file, realFs, sp);
    expect(r2.unreachable).toBe(true);
    expect(r2.state).toBe(r1.state); // 保留上次状态
    let exists = true;
    try {
      statSync(dir);
    } catch {
      exists = false;
    }
    expect(exists).toBe(false);
    expect(treeManifest(root).filter((l) => !l.startsWith(`${dir.slice(root.length + 1)}`))).toEqual(before);
  });
});

describe("snapshot 只载尾部", () => {
  it("超过 snapshotMax → 从尾部窗口的第一个换行之后开始，truncatedHead", () => {
    const sp = new LineSplitter(1024);
    const all = Array.from({ length: 50 }, (_, i) => line(i));
    writeFileSync(file, all.join(""));
    const r = drain(null, sp, { readChunk: 256, snapshotMax: all[40].length * 10 + 5 });
    expect(r.truncatedHead).toBe(true);
    expect(r.lines).toEqual(all.slice(40).map((l) => l.trimEnd()));
  });
});
