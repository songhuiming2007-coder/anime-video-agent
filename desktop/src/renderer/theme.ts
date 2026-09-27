// 主题三档与侧栏视图偏好（Spec 14 §3.3，🔴-1 方案 a）：localStorage + <html data-theme>，零协议改动。
// 授权依据：Spec 8 §2.9「UI 便利状态由 renderer localStorage 记忆，读写均 try/catch」。
// VS-9：全仓只有本模块碰 localStorage，且每处成员访问都在 try 块里（M32 的形状：去掉 try/catch 两层都红）。

export type Theme = "system" | "light" | "dark";
export type EpisodeViewPref = "time" | "step";

const THEME_KEY = "ava.theme";
const VIEW_KEY = "ava.episodeView";

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

/** system 删 data-theme（跟随系统深色块生效），否则写入 */
export function applyTheme(t: Theme): void {
  if (t === "system") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.setAttribute("data-theme", t);
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
