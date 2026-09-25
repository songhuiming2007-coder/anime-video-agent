// PreviewPane 按扩展名分发（Spec 8 §2.7）。纯函数；不猜格式。
import { extOf } from "./mediaUrl";

export type PreviewKind = "html" | "video" | "audio" | "image" | "markdown" | "json" | "text" | "other";

const BY_EXT: Record<string, PreviewKind> = {
  ".html": "html",
  ".mp4": "video",
  ".mov": "video",
  ".wav": "audio",
  ".flac": "audio",
  ".png": "image",
  ".jpg": "image",
  ".jpeg": "image",
  ".md": "markdown",
  ".json": "json",
  ".log": "text",
  ".patch": "text",
  ".txt": "text",
};

export function previewKind(name: string): PreviewKind {
  return BY_EXT[extOf(name)] ?? "other";
}

/** 目录内音频按文件名自然序排队（seg-02 < seg-10） */
export function naturalAudioQueue(names: string[]): string[] {
  return names.filter((n) => previewKind(n) === "audio").sort((a, b) => a.localeCompare(b, "en", { numeric: true }));
}

/** rVFC mediaTime（秒）→ HH:MM:SS.mmm；v1 不显示帧号 */
export function formatMediaTime(t: number): string {
  const ms = Math.floor(t * 1000 + 1e-6);
  const h = Math.floor(ms / 3_600_000);
  const m = Math.floor((ms % 3_600_000) / 60_000);
  const s = Math.floor((ms % 60_000) / 1000);
  const r = ms % 1000;
  const p = (n: number, w = 2) => String(n).padStart(w, "0");
  return `${p(h)}:${p(m)}:${p(s)}.${p(r, 3)}`;
}
