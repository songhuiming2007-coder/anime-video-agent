// host 的只读文件系统面：纯函数模块经注入的 FsProbe/FsRead 访问磁盘，便于单测。
// 本文件只含读 API；desktop/ 内唯一写文件的模块是 host/settings.ts（TG-2）。
import * as fs from "node:fs";

export interface StatLike {
  isDirectory(): boolean;
  isFile(): boolean;
  isSymbolicLink(): boolean;
  size: number;
  mtimeMs: number;
}

export interface BigStatLike {
  dev: bigint;
  ino: bigint;
  size: bigint;
  mtimeNs: bigint;
  isFile(): boolean;
  isDirectory(): boolean;
}

export interface FsProbe {
  /** 跟随符号链接 */
  stat(p: string): StatLike;
  lstat(p: string): StatLike;
  statBig(p: string): BigStatLike;
  readlink(p: string): string;
  readdir(p: string): string[];
  realpath(p: string): string;
}

export interface FsRead extends FsProbe {
  readFile(p: string): Uint8Array;
  /** 读 [start, start+length)；返回实际读到的字节 */
  readRange(p: string, start: number, length: number): Uint8Array;
}

export function errnoOf(e: unknown): string | null {
  return e && typeof e === "object" && "code" in e ? String((e as { code: unknown }).code) : null;
}

export const realFs: FsRead = {
  stat: (p) => fs.statSync(p),
  lstat: (p) => fs.lstatSync(p),
  statBig: (p) => fs.statSync(p, { bigint: true }),
  readlink: (p) => fs.readlinkSync(p),
  readdir: (p) => fs.readdirSync(p),
  realpath: (p) => fs.realpathSync.native(p),
  readFile: (p) => fs.readFileSync(p),
  readRange: (p, start, length) => {
    const fd = fs.openSync(p, "r");
    try {
      const buf = Buffer.allocUnsafe(length);
      let got = 0;
      while (got < length) {
        const n = fs.readSync(fd, buf, got, length - got, start + got);
        if (n === 0) break;
        got += n;
      }
      return buf.subarray(0, got);
    } finally {
      fs.closeSync(fd);
    }
  },
};
