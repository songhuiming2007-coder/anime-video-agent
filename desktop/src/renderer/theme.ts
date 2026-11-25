// 主题三档与侧栏视图偏好（Spec 14 §3.3，🔴-1 方案 a）：localStorage + <html data-theme>，零协议改动。
// 授权依据：Spec 8 §2.9「UI 便利状态由 renderer localStorage 记忆，读写均 try/catch」。
// VS-9：全仓只有本模块碰 localStorage，且每处成员访问都在 try 块里（M32 的形状：去掉 try/catch 两层都红）。
// D39 S3：分栏布局（列宽、收起状态）也存在这里，校验与回落规则在 layout.ts::parseLayout（纯函数）。
import { parseLayout, serializeLayout, type Layout } from "./layout";

export type Theme = "system" | "light" | "dark";
export type Accent = "sakura" | "violet" | "sky" | "sage" | "amber" | "graphite";
export const ACCENTS: { id: Accent; label: string }[] = [
  { id: "sakura", label: "樱粉" },
  { id: "violet", label: "暮紫" },
  { id: "sky", label: "晴空" },
  { id: "sage", label: "鼠尾草" },
  { id: "amber", label: "琥珀" },
  { id: "graphite", label: "石墨" },
];
export type EpisodeViewPref = "time" | "step";

const THEME_KEY = "ava.theme";
const ACCENT_KEY = "ava.accent";
const VIEW_KEY = "ava.episodeView";
const LAYOUT_KEY = "ava.layout";

/** 非法值、缺失或读取抛错一律返回 "system"（主题不是安全面，取中性默认值，不当故障） */
export function readTheme(): Theme {
  try {
    const v = localStorage.getItem(THEME_KEY);
    if (v === "system" || v === "light" || v === "dark") return v;
  } catch {
    /* 读取失败按缺省 */
  }
  return "system";
}

/** 写失败只影响重启后的记忆，当次照常生效；返回是否落盘 */
export function saveTheme(t: Theme): boolean {
  try {
    localStorage.setItem(THEME_KEY, t);
    return true;
  } catch {
    return false;
  }
}

let themeTransitionTimer: ReturnType<typeof setTimeout> | null = null;

function pulseThemeTransition(): void {
  if (typeof window === "undefined" || typeof document === "undefined") return;
  if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return;
  const root = document.documentElement;
  root.setAttribute("data-theme-transition", "1");
  if (themeTransitionTimer !== null) clearTimeout(themeTransitionTimer);
  // 105ms 略短于视觉门禁 120ms 等待窗，既平滑过渡又不留半途状态
  themeTransitionTimer = setTimeout(() => {
    themeTransitionTimer = null;
    root.removeAttribute("data-theme-transition");
  }, 105);
}

/** system 删 data-theme（跟随系统深色块生效），否则写入；仅值变化且用户主动切换时开启短促表面过渡 */
export function applyTheme(t: Theme, animate = false): void {
  const el = document.documentElement;
  const next = t === "system" ? null : t;
  if (typeof el.getAttribute === "function" && el.getAttribute("data-theme") === next) return;
  if (animate) pulseThemeTransition();
  if (next === null) el.removeAttribute("data-theme");
  else el.setAttribute("data-theme", next);
}

/** 强调色偏好：默认 sakura（浅樱粉）；非法值/读取失败一律回落 sakura */
export function readAccent(): Accent {
  try {
    const v = localStorage.getItem(ACCENT_KEY);
    if (ACCENTS.some((a) => a.id === v)) return v as Accent;
  } catch {
    /* 读取失败按缺省 */
  }
  return "sakura";
}

export function saveAccent(a: Accent): boolean {
  try {
    localStorage.setItem(ACCENT_KEY, a);
    return true;
  } catch {
    return false;
  }
}

export function applyAccent(a: Accent, animate = false): void {
  const el = document.documentElement;
  const next = a === "sakura" ? null : a;
  if (typeof el.getAttribute === "function" && el.getAttribute("data-accent") === next) return;
  if (animate) pulseThemeTransition();
  if (next === null) el.removeAttribute("data-accent");
  else el.setAttribute("data-accent", next);
}

/** 视图默认「按时间」（D4），纪律同主题 */
export function readEpisodeView(): EpisodeViewPref {
  try {
    const v = localStorage.getItem(VIEW_KEY);
    if (v === "time" || v === "step") return v;
  } catch {
    /* 读取失败按缺省 */
  }
  return "time";
}

export function saveEpisodeView(v: EpisodeViewPref): boolean {
  try {
    localStorage.setItem(VIEW_KEY, v);
    return true;
  } catch {
    return false;
  }
}

/** 读取抛错（隐私模式、存储被禁）与坏数据同样回落默认布局 */
export function readLayout(): Layout {
  let raw: string | null = null;
  try {
    raw = localStorage.getItem(LAYOUT_KEY);
  } catch {
    /* 读取失败按缺省 */
  }
  return parseLayout(raw);
}

export function saveLayout(l: Layout): boolean {
  try {
    localStorage.setItem(LAYOUT_KEY, serializeLayout(l));
    return true;
  } catch {
    return false;
  }
}
