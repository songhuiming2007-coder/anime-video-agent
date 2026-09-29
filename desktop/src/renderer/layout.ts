// D39 S2：分栏布局的纯函数（零 DOM、零 import）。方案 B：左栏 | 对话（主）| 预览（默认收成窄条）。
// 人 2026-09-29 裁决：选 B、预览默认收起、tokens.css 一字不改（列宽不再引用 --sidebar-w / --center-w）。

/** 左栏默认宽：期列表 + 本期文件纵向叠放，240 放得下封面拖放区（方案 A 的 200 放不下，D39 选型页 §3） */
export const LEFT_DEFAULT = 240;
export const LEFT_MIN = 200;
export const LEFT_MAX = 400;
/** 对话列下限：人认可的 1280×800 截图里对话列是 360（D39 2026-09-29） */
export const CENTER_MIN = 360;
/** 预览下限：04-review.html 与 02.5 编辑器在更窄时不可用 */
export const PREVIEW_MIN = 320;
/** 预览首次展开占「左栏右侧」宽度的比例（人拖过之后用人的值） */
export const PREVIEW_DEFAULT_RATIO = 0.4;
/** 拖拽手柄宽度（兼作栏间距）与预览收起后的窄条宽度 */
export const SPLIT_W = 8;
export const RAIL_W = 40;
/** 方向键每次挪动的像素 */
export const KEY_STEP = 16;

export interface Layout {
  leftOpen: boolean;
  leftW: number;
  previewOpen: boolean;
  /** null = 未被人拖过，按 PREVIEW_DEFAULT_RATIO 随窗口算 */
  previewW: number | null;
  /** 左栏「本期文件」段是否展开 */
  filesOpen: boolean;
}

export const DEFAULT_LAYOUT: Layout = { leftOpen: true, leftW: LEFT_DEFAULT, previewOpen: false, previewW: null, filesOpen: true };

/** 持久化格式版本（S3）。格式一变就升版本号：旧值整份回落默认，不做迁移（这是便利状态，不是数据） */
export const LAYOUT_VERSION = 1;
/** 存盘时预览宽的绝对上限：只挡明显的坏值；真正的上限随窗口在 effectivePreviewW 里夹 */
const PREVIEW_STORE_MAX = 4000;

export function serializeLayout(l: Layout): string {
  return JSON.stringify({ v: LAYOUT_VERSION, ...l });
}

/**
 * 从存储读回布局（D39 S3，全局一份，人 2026-09-29 裁决）。坏数据的回落分两级：
 * - 整份不可用（缺失、不是合法 JSON、不是对象、版本不对）→ 整份默认值；
 * - 个别字段坏（类型不对、非有限数）→ 只那个字段取默认，数值按上下限夹紧。
 * 永不抛错：布局不是安全面，读坏了就回到默认，不当故障。
 */
export function parseLayout(raw: string | null): Layout {
  if (raw === null) return DEFAULT_LAYOUT;
  let o: unknown;
  try {
    o = JSON.parse(raw);
  } catch {
    return DEFAULT_LAYOUT;
  }
  if (typeof o !== "object" || o === null || Array.isArray(o)) return DEFAULT_LAYOUT;
  const r = o as Record<string, unknown>;
  if (r.v !== LAYOUT_VERSION) return DEFAULT_LAYOUT;
  const bool = (k: keyof Layout): boolean => (typeof r[k] === "boolean" ? (r[k] as boolean) : (DEFAULT_LAYOUT[k] as boolean));
  const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
  const lw = num(r.leftW);
  const pw = num(r.previewW);
  return {
    leftOpen: bool("leftOpen"),
    leftW: lw === null ? DEFAULT_LAYOUT.leftW : clampLeft(lw),
    previewOpen: bool("previewOpen"),
    previewW: pw === null ? null : clamp(pw, PREVIEW_MIN, PREVIEW_STORE_MAX),
    filesOpen: bool("filesOpen"),
  };
}

const clamp = (v: number, lo: number, hi: number): number => Math.min(hi, Math.max(lo, Math.round(v)));

export function clampLeft(w: number): number {
  return clamp(w, LEFT_MIN, LEFT_MAX);
}

/** 左栏占用（含手柄）；收起时为 0 */
function leftSpan(l: Layout): number {
  return l.leftOpen ? l.leftW + SPLIT_W : 0;
}

/** 预览可取的最大宽：保住对话列下限；窗口太窄时退到下限（此时对话列让位，由 1fr 的 minmax(0) 吸收） */
export function previewMax(mainW: number, l: Layout): number {
  return Math.max(PREVIEW_MIN, mainW - leftSpan(l) - SPLIT_W - CENTER_MIN);
}

export function effectivePreviewW(mainW: number, l: Layout): number {
  const base = l.previewW ?? (mainW - leftSpan(l)) * PREVIEW_DEFAULT_RATIO;
  return clamp(base, PREVIEW_MIN, previewMax(mainW, l));
}

/** `.main` 的 grid-template-columns：列序与 App 里可见子元素一一对应（收起的栏是 hidden，不占轨道） */
export function gridColumns(mainW: number, l: Layout): string {
  const cols: string[] = [];
  if (l.leftOpen) cols.push(`${l.leftW}px`, `${SPLIT_W}px`);
  cols.push("minmax(0, 1fr)");
  if (l.previewOpen) cols.push(`${SPLIT_W}px`, `${effectivePreviewW(mainW, l)}px`);
  else cols.push(`${RAIL_W}px`);
  return cols.join(" ");
}

/**
 * 手柄的键盘操作：← / → 每次 KEY_STEP，Home / End 到下限 / 上限；其余键返回 null（不处理、不拦截）。
 * `dir = 1`：→ 让被调的栏变宽（左栏手柄）；`dir = -1`：← 让被调的栏变宽（预览手柄在预览左侧）。
 */
export function nudge(value: number, key: string, min: number, max: number, dir: 1 | -1): number | null {
  if (key === "ArrowRight") return clamp(value + dir * KEY_STEP, min, max);
  if (key === "ArrowLeft") return clamp(value - dir * KEY_STEP, min, max);
  if (key === "Home") return min;
  if (key === "End") return max;
  return null;
}
