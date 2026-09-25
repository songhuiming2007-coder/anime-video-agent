// 停机点 → 自动呼出的预览目标（§2.6 表「预览自动呼出」列）。纯展示映射，不参与任何判定。
import type { MediaRoot } from "./mediaUrl";

export const STOP_PREVIEW: Record<"02.5" | "03.5" | "05" | "09", { root: MediaRoot; rel: string }> = {
  "02.5": { root: "episodes", rel: "02-script.md" },
  "03.5": { root: "episodes", rel: "03-audio" },
  "05": { root: "episodes", rel: "04-review.html" },
  "09": { root: "episodes", rel: "05-final.mp4" },
};
