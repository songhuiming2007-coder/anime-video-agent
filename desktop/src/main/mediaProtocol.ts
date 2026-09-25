// 只读媒体协议 ava-media://（Spec 8 §2.7、§3.5）：单一读闸，所有文件内容走同一个路径守卫。
// 只接受 GET/HEAD；单段 Range → 206，多段 → 416；html 附带自己的 CSP 头；reach 非 ok 时销毁全部流（TP-6）。
// CORS（S21 经用户同意修订 §2.7）：renderer 源是 file://，非 CORS scheme 被 Chromium 拒绝一切跨源读取，文本预览取不到正文。
// 因此开 corsEnabled，但只在请求 Origin 恰为 renderer 自身源时回 ACAO；沙箱 iframe 的 Origin 是 null，照样读不到。
import * as fs from "node:fs";
import { Readable } from "node:stream";
import { protocol } from "electron";
import type { Reach } from "../shared/contracts";
import { corsAllowOrigin, decodeAndGuard, MEDIA_SCHEME, parseRange, type MediaRoot } from "../shared/mediaUrl";

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
  private streams = new Set<fs.ReadStream>();

  /** allowedOrigins：renderer 自身源（file://；未打包构建另加 dev server 源） */
  constructor(private readonly allowedOrigins: ReadonlySet<string>) {}

  setDataRoot(d: string | null): void {
    this.dataRoot = d;
  }

  setReach(r: Reach): void {
    this.reach = r;
  }

  /** 登记中的全部响应流立即 destroy，释放 data/ 下的 fd。 */
  destroyAll(): void {
    for (const s of this.streams) s.destroy();
    this.streams.clear();
  }

  openStreams(): number {
    return this.streams.size;
  }

  install(): void {
    protocol.handle(MEDIA_SCHEME, (req) => this.serve(req));
  }

  serve(req: Request): Response {
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
    const stream = fs.createReadStream(g.abs, { start, end });
    this.streams.add(stream);
    stream.on("close", () => this.streams.delete(stream));
    return new Response(Readable.toWeb(stream) as ReadableStream, { status, headers });
  }
}
