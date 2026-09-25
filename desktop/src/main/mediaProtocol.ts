// 只读媒体协议 ava-media://（Spec 8 §2.7、§3.5）：单一读闸，所有文件内容走同一个路径守卫。
// 只接受 GET/HEAD；单段 Range → 206，多段 → 416；html 附带自己的 CSP 头。
// fd 生命周期（S23 门禁 6 修订，经用户同意）：响应体按 MEDIA_READ_CHUNK_BYTES 分块拉取，每块「打开 → 读 → 关闭」。
// 原实现一条 createReadStream 开到文件末尾：<video> 缓冲满后暂停读取，fd 一直开到播放结束，普通推出外置盘被拒
// （reach 只在卸载之后才变，按 reach 销毁永远晚一步）。截断 Range 响应的做法不可行：Chromium 对开放尾端请求收到
// 截短的 206 后不续发（S23 实测「no supported source」）。reach 非 ok 时 destroyAll 仍终止全部在途流。
// CORS（S21 经用户同意修订 §2.7）：renderer 源是 file://，非 CORS scheme 被 Chromium 拒绝一切跨源读取，文本预览取不到正文。
// 因此开 corsEnabled，但只在请求 Origin 恰为 renderer 自身源时回 ACAO；沙箱 iframe 的 Origin 是 null，照样读不到。
import * as fs from "node:fs";
import { protocol } from "electron";
import type { Reach } from "../shared/contracts";
import { MEDIA_READ_CHUNK_BYTES } from "../shared/constants";
import { corsAllowOrigin, decodeAndGuard, MEDIA_SCHEME, parseRange, type MediaRoot } from "../shared/mediaUrl";

/** 读 [start, start+length) 后立即关闭 fd */
async function readSlice(file: string, start: number, length: number): Promise<Buffer> {
  const fh = await fs.promises.open(file, "r");
  try {
    const buf = Buffer.allocUnsafe(length);
    let got = 0;
    while (got < length) {
      const { bytesRead } = await fh.read(buf, got, length - got, start + got);
      if (bytesRead === 0) break;
      got += bytesRead;
    }
    return buf.subarray(0, got);
  } finally {
    await fh.close();
  }
}

const HTML_CSP: Record<MediaRoot, string> = {
  episodes: "default-src 'none'; img-src ava-media:; style-src 'unsafe-inline'",
  shots: "default-src 'none'; img-src ava-media:; style-src 'unsafe-inline'; script-src 'unsafe-inline'",
};

export function registerMediaScheme(): void {
  // 不给 bypassCSP（electron.d.ts CustomScheme privileges）
  protocol.registerSchemesAsPrivileged([
    { scheme: MEDIA_SCHEME, privileges: { standard: true, secure: true, stream: true, supportFetchAPI: true, corsEnabled: true } },
  ]);
}

function plain(status: number, text: string): Response {
  return new Response(text, { status, headers: { "content-type": "text/plain; charset=utf-8", "x-content-type-options": "nosniff" } });
}

export class MediaServer {
  private dataRoot: string | null = null;
  private reach: Reach = "missing";
  private streams = new Set<ReadableStreamDefaultController<Uint8Array>>();

  /** allowedOrigins：renderer 自身源（file://；未打包构建另加 dev server 源） */
  constructor(private readonly allowedOrigins: ReadonlySet<string>) {}

  setDataRoot(d: string | null): void {
    this.dataRoot = d;
  }

  setReach(r: Reach): void {
    this.reach = r;
  }

  /** 登记中的全部响应流立即终止（此后不再拉取、不再打开 data/ 下的文件）。 */
  destroyAll(): void {
    for (const c of this.streams) {
      try {
        c.error(new Error("data 不可达"));
      } catch {
        /* 已结束 */
      }
    }
    this.streams.clear();
  }

  /** [start, end] 的响应体：每次 pull 读一块并关 fd；在途期间登记，供 destroyAll 终止 */
  private sliceStream(file: string, start: number, end: number): ReadableStream<Uint8Array> {
    let pos = start;
    let ctrl!: ReadableStreamDefaultController<Uint8Array>;
    const done = (): void => {
      this.streams.delete(ctrl);
    };
    return new ReadableStream<Uint8Array>({
      start: (c) => {
        ctrl = c;
        this.streams.add(c);
      },
      pull: async (c) => {
        if (pos > end) {
          done();
          c.close();
          return;
        }
        try {
          const buf = await readSlice(file, pos, Math.min(MEDIA_READ_CHUNK_BYTES, end - pos + 1));
          if (!this.streams.has(c)) return; // 读取期间已被 destroyAll 终止
          if (buf.length === 0) {
            done();
            c.close();
            return;
          }
          pos += buf.length;
          c.enqueue(new Uint8Array(buf));
        } catch (e) {
          done();
          c.error(e);
        }
      },
      cancel: () => done(),
    });
  }

  openStreams(): number {
    return this.streams.size;
  }

  install(): void {
    protocol.handle(MEDIA_SCHEME, (req) => this.serve(req));
  }

  async serve(req: Request): Promise<Response> {
    if (req.method !== "GET" && req.method !== "HEAD") return plain(405, "method not allowed");
    if (!this.dataRoot || this.reach !== "ok") return plain(503, "data 不可达");
    let rootsReal: Record<MediaRoot, string>;
    try {
      rootsReal = {
        episodes: fs.realpathSync.native(`${this.dataRoot}/episodes`),
        shots: fs.realpathSync.native(`${this.dataRoot}/library/shots`),
      };
    } catch {
      return plain(503, "媒体根不可达");
    }
    const g = decodeAndGuard(req.url, rootsReal, (p) => fs.realpathSync.native(p));
    if (!g.ok) return plain(g.status, "rejected");
    let st: fs.Stats;
    try {
      st = fs.statSync(g.abs);
    } catch {
      return plain(404, "not found");
    }
    if (!st.isFile()) return plain(404, "not a regular file");
    const headers: Record<string, string> = {
      "content-type": g.mime,
      "accept-ranges": "bytes",
      "x-content-type-options": "nosniff",
      "cache-control": "no-store",
    };
    if (g.mime.startsWith("text/html")) headers["content-security-policy"] = HTML_CSP[g.root];
    const origin = corsAllowOrigin(req.headers.get("origin"), this.allowedOrigins);
    if (origin !== null) {
      headers["access-control-allow-origin"] = origin;
      headers["access-control-expose-headers"] = "content-range, content-length";
      headers["vary"] = "origin";
    }
    const range = parseRange(req.headers.get("range"), st.size);
    if (range === "unsatisfiable") return new Response(null, { status: 416, headers: { "content-range": `bytes */${st.size}` } });
    const start = range ? range.start : 0;
    const end = range ? range.end : st.size - 1;
    headers["content-length"] = String(st.size === 0 ? 0 : end - start + 1);
    if (range) headers["content-range"] = `bytes ${start}-${end}/${st.size}`;
    const status = range ? 206 : 200;
    if (req.method === "HEAD" || st.size === 0) return new Response(null, { status, headers });
    return new Response(this.sliceStream(g.abs, start, end), { status, headers });
  }
}
