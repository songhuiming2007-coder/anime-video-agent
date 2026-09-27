// 人时（墙钟·观测量）的纯函数面：读数归并与 per-stop 单区间机（Spec 11 §2.4/§4.2）。
// 口径：审阅面可见即计时、墙钟、失焦照算、**不设时长下限**（与终端停机点停留一致，两端可比）。
import type { TimeReadJson } from "./protocol";

/** 四个人类停机点（与 `pipeline/approvals.py` 的 HUMAN_STOPS 同集）。 */
export const HUMAN_STOPS: readonly string[] = ["02.5", "03.5", "05", "09"];

/** `human_time.json` 条目（终端条目无 `source` 键，桌面条目为 `"desktop"`）。 */
export interface HumanTimeRecord {
  stop: string;
  minutes: number;
  source?: string;
}

const round2 = (n: number) => Math.round(n * 100) / 100;

/** 归并人时读数：按停机点求和，四个停机点之外的归入 otherMin（呈现层事实，不作门禁）。 */
export function reduceHumanTime(records: readonly HumanTimeRecord[]): TimeReadJson {
  const perStop: Record<string, number> = {};
  for (const s of HUMAN_STOPS) perStop[s] = 0;
  let otherMin = 0;
  let totalMin = 0;
  let count = 0;
  for (const r of records) {
    if (typeof r?.stop !== "string" || typeof r?.minutes !== "number" || !Number.isFinite(r.minutes)) continue;
    count += 1;
    totalMin += r.minutes;
    if (HUMAN_STOPS.includes(r.stop)) perStop[r.stop] += r.minutes;
    else otherMin += r.minutes;
  }
  return { perStop, otherMin: round2(otherMin), totalMin: round2(totalMin), count };
}

/** 单区间机的状态：stop → 进入时刻（毫秒）；同一 stop 至多一个在计区间（RF-9 防双计）。 */
export interface HumanTimerState {
  open: Record<string, number>;
}

/** 一段已闭合的区间（毫秒，落盘时换算 epoch 秒）。 */
export interface TimerInterval {
  stop: string;
  enteredAt: number;
  leftAt: number;
}

export const emptyHumanTimer: HumanTimerState = { open: {} };

export interface TimerStep {
  state: HumanTimerState;
  closed: TimerInterval[];
}

/** 审阅面可见性变化。可见时开区间（已在计则不动——双 visible 不双计）；不可见时闭合。 */
export function noteVisible(s: HumanTimerState, stop: string, visible: boolean, now: number): TimerStep {
  const already = s.open[stop];
  if (visible) {
    if (already !== undefined) return { state: s, closed: [] };
    return { state: { open: { ...s.open, [stop]: now } }, closed: [] };
  }
  return closeStop(s, stop, now);
}

/** 关闭某停机点的区间；无区间则原样返回（「flush 恰一次」由状态决定，不由调用点约定）。 */
export function closeStop(s: HumanTimerState, stop: string, now: number): TimerStep {
  const enteredAt = s.open[stop];
  if (enteredAt === undefined) return { state: s, closed: [] };
  const open = { ...s.open };
  delete open[stop];
  return { state: { open }, closed: [{ stop, enteredAt, leftAt: now }] };
}

/** 关闭全部在计区间（切期 / 退出 / ack）。 */
export function closeAll(s: HumanTimerState, now: number): TimerStep {
  const closed: TimerInterval[] = [];
  for (const [stop, enteredAt] of Object.entries(s.open)) closed.push({ stop, enteredAt, leftAt: now });
  return { state: emptyHumanTimer, closed };
}

/** epoch 毫秒 → `--entered/--left` 的 epoch 秒字符串（3 位小数，亚秒精度、无科学计数法）。 */
export function epochSeconds(ms: number): string {
  return (ms / 1000).toFixed(3);
}
