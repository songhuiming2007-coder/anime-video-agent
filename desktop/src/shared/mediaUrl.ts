// ava-media:// URL 契约与路径守卫（Spec 8 §3.5）。纯函数；realpath 由 main 注入。
export type MediaRoot = "episodes" | "shots";
export const MEDIA_SCHEME = "ava-media";
export const MEDIA_ROOTS: readonly MediaRoot[] = ["episodes", "shots"];

/** 相对路径的段检查（规则 1 的共享部分，H5 复用）：拒绝空串、绝对路径、空段、. / ..、段内 \ 与 \0。 */
export function relPathProblem(rel: string): string | null {
  if (rel.length === 0) return "空路径";
  if (rel.startsWith("/")) return "绝对路径";
  for (const seg of rel.split("/")) {
    if (seg.length === 0) return "空段";
    if (seg === "." || seg === "..") return "点段";
    if (seg.includes("\\")) return "段内含反斜杠";
    if (seg.includes("\0")) return "含 NUL";
  }
  return null;
}

export const MEDIA_MIME: Record<string, string> = {
  ".html": "text/html; charset=utf-8",
  ".mp4": "video/mp4",
  ".mov": "video/quicktime",
  ".wav": "audio/wav",
  ".flac": "audio/flac",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".md": "text/markdown; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".log": "text/plain; charset=utf-8",
  ".patch": "text/plain; charset=utf-8",
  ".txt": "text/plain; charset=utf-8",
};

export function extOf(name: string): string {
  const base = name.slice(name.lastIndexOf("/") + 1);
  const i = base.lastIndexOf(".");
  return i > 0 ? base.slice(i).toLowerCase() : "";
}

export function encodeMediaUrl(root: MediaRoot, rel: string): string {
  return `${MEDIA_SCHEME}://${root}/${rel.split("/").map(encodeURIComponent).join("/")}`;
}

/**
 * 路径守卫（§3.5，v0.2 收紧）：
 *  1. 逐段解码后，任一段为空、. / ..、含 / 或 \、含 \0 → 400（%2F/%5C 段注入是本规则真正要抓的）；
 *  2. realpath 必须是**所选 root** 实路径的严格后代（不是 dataRoot）；root 内指向 root 外的符号链接一律 403；
 *  3. 扩展名（取 realpath 的）∈ MIME 白名单，否则 415；普通文件判定由调用方 stat（404）。
 */
export function decodeAndGuard(
  url: string,
  rootsReal: Record<MediaRoot, string>,
  realpath: (p: string) => string,
): { ok: true; abs: string; mime: string; root: MediaRoot } | { ok: false; status: 400 | 403 | 404 | 415 } {
  const prefix = `${MEDIA_SCHEME}://`;
  if (!url.startsWith(prefix)) return { ok: false, status: 400 };
  const rest = url.slice(prefix.length).split(/[?#]/, 1)[0];
  const slash = rest.indexOf("/");
  if (slash <= 0) return { ok: false, status: 400 };
  const root = rest.slice(0, slash).toLowerCase();
  if (!(MEDIA_ROOTS as readonly string[]).includes(root)) return { ok: false, status: 404 };
  const rawSegs = rest.slice(slash + 1).split("/");
  const segs: string[] = [];
  for (const raw of rawSegs) {
    let seg: string;
    try {
      seg = decodeURIComponent(raw);
    } catch {
      return { ok: false, status: 400 };
    }
    if (seg.length === 0 || seg === "." || seg === ".." || seg.includes("/") || seg.includes("\\") || seg.includes("\0")) {
      return { ok: false, status: 400 };
    }
    segs.push(seg);
  }
  const rootReal = rootsReal[root as MediaRoot];
  let real: string;
  try {
    real = realpath(`${rootReal}/${segs.join("/")}`);
  } catch {
    return { ok: false, status: 404 };
  }
  if (!real.startsWith(`${rootReal}/`)) return { ok: false, status: 403 };
  const mime = MEDIA_MIME[extOf(real)];
  if (!mime) return { ok: false, status: 415 };
  return { ok: true, abs: real, mime, root: root as MediaRoot };
}

/** 单段 Range 解析：bytes=a-b / a- / -n → [start, end]；多段或越界 → "unsatisfiable"；无头 → null。 */
export function parseRange(header: string | null, size: number): { start: number; end: number } | "unsatisfiable" | null {
  if (!header) return null;
  const m = /^bytes=(.*)$/.exec(header.trim());
  if (!m) return "unsatisfiable";
  if (m[1].includes(",")) return "unsatisfiable";
  const r = /^(\d*)-(\d*)$/.exec(m[1].trim());
  if (!r || (r[1] === "" && r[2] === "")) return "unsatisfiable";
  let start: number;
  let end: number;
  if (r[1] === "") {
    const n = Number(r[2]);
    if (n === 0) return "unsatisfiable";
    start = Math.max(0, size - n);
    end = size - 1;
  } else {
    start = Number(r[1]);
    end = r[2] === "" ? size - 1 : Math.min(Number(r[2]), size - 1);
  }
  if (start > end || start >= size) return "unsatisfiable";
  return { start, end };
}

/** CORS：只有请求 Origin 恰为 renderer 自身源时才放行（沙箱 iframe 的 Origin 是 "null"，读不到）。 */
export function corsAllowOrigin(origin: string | null, allowed: ReadonlySet<string>): string | null {
  return origin !== null && origin !== "null" && allowed.has(origin) ? origin : null;
}
