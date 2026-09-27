// 人时计时器的宿主侧（Spec 11 §4.3）：审阅面事件 → per-stop 单区间 → flush 时通知 spawn RECORD_TIME。
// 区间机本体在 shared/humanTime.ts（纯函数、注入 now）；本文件只做「哪一期、怎么记账」的编排。
// 口径：墙钟、失焦照算、**不设时长下限**（0.5 s 也照常 flush，与终端停机点停留一致，红队 🔵-2）。
import { closeAll, closeStop, emptyHumanTimer, HUMAN_STOPS, noteVisible, type HumanTimerState, type TimerInterval } from "../shared/humanTime";

export type FlushReason = "ack" | "close" | "switch" | "quit";

export class HumanTimers {
  private perEp = new Map<string, HumanTimerState>();
  /** 已结算区间总数（测试断言「flush 恰一次」用） */
  private closedCount = 0;

  constructor(
    private readonly now: () => number,
    private readonly record: (epKey: string, iv: TimerInterval, reason: FlushReason) => void,
    private readonly diag: (msg: string) => void = () => {},
  ) {}

  /** 审阅面可见性变化（编辑器 / 顺听面板 / 决策卡呈现）。同 stop 已在计则不动——双 visible 不双计（RF-9）。 */
  noteReviewSurface(epKey: string, stop: string, visible: boolean, reason: FlushReason = "close"): void {
    if (!HUMAN_STOPS.includes(stop)) return; // 未知停机点不进入计时（core 侧同样校验）
    const s = this.perEp.get(epKey) ?? emptyHumanTimer;
    const r = noteVisible(s, stop, visible, this.now());
    this.perEp.set(epKey, r.state);
    this.emit(epKey, r.closed, reason);
  }

  /** ack 成功：该停机点区间立即关闭（不等下一次 visible=false）。 */
  closeStop(epKey: string, stop: string, reason: FlushReason = "ack"): void {
    const s = this.perEp.get(epKey);
    if (!s) return;
    const r = closeStop(s, stop, this.now());
    this.perEp.set(epKey, r.state);
    this.emit(epKey, r.closed, reason);
  }

  /** 切期 / 退出：全部在计区间结算（失焦照算的代价：这些区间会偏长，但两个端口径一致）。 */
  flushAll(reason: FlushReason): void {
    for (const [epKey, s] of this.perEp) {
      const r = closeAll(s, this.now());
      this.perEp.set(epKey, r.state);
      this.emit(epKey, r.closed, reason);
    }
  }

  closed(): number {
    return this.closedCount;
  }

  private emit(epKey: string, closed: readonly TimerInterval[], reason: FlushReason): void {
    for (const iv of closed) {
      this.closedCount += 1;
      try {
        this.record(epKey, iv, reason);
      } catch (e) {
        this.diag(`人时区间结算失败（${epKey} ${iv.stop}）：${e instanceof Error ? e.message : String(e)}`);
      }
    }
  }
}
