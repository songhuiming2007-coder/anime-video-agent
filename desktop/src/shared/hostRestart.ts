// host 崩溃重启的退避与熔断（Spec 8 §2.9）：纯函数，main 的看护循环据此决定「多久后重启」还是「停止并显示致命面板」。
import { HOST_CRASH_LIMIT, HOST_CRASH_WINDOW_MS, HOST_RESTART_BASE_MS, HOST_RESTART_MAX_MS } from "./constants";

export interface RestartState {
  /** 熔断窗口内的崩溃时刻 */
  crashes: number[];
  /** 连续崩溃次数（host 存活满一个熔断窗口后清零） */
  consecutive: number;
  /** 最近一次拉起 host 的时刻 */
  lastStart: number;
}

export type RestartAction = { kind: "restart"; delayMs: number } | { kind: "fatal"; crashesInWindow: number };

export function initialRestartState(now: number): RestartState {
  return { crashes: [], consecutive: 0, lastStart: now };
}

export function onHostCrash(s: RestartState, now: number): { state: RestartState; action: RestartAction } {
  const crashes = [...s.crashes.filter((t) => now - t < HOST_CRASH_WINDOW_MS), now];
  const consecutive = now - s.lastStart >= HOST_CRASH_WINDOW_MS ? 1 : s.consecutive + 1;
  const state = { crashes, consecutive, lastStart: s.lastStart };
  if (crashes.length >= HOST_CRASH_LIMIT) return { state, action: { kind: "fatal", crashesInWindow: crashes.length } };
  return { state, action: { kind: "restart", delayMs: Math.min(HOST_RESTART_MAX_MS, HOST_RESTART_BASE_MS * 2 ** (consecutive - 1)) } };
}

export function onHostStarted(s: RestartState, now: number): RestartState {
  return { ...s, lastStart: now };
}
