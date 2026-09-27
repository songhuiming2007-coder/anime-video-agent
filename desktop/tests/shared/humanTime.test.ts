// TD-1 人时区间机（Spec 11 §4.2/§7.2）：同 stop 双 visible 不双计；**不设时长下限**；
// switch/quit 时 flush 恰一次；ack 后该 stop 区间关闭。纯函数、注入 now。
import { describe, expect, it } from "vitest";
import { closeAll, closeStop, emptyHumanTimer, epochSeconds, noteVisible, reduceHumanTime, type HumanTimerState } from "../../src/shared/humanTime";

/** 依次施加若干 (stop, visible) 事件，返回最终状态与全部闭合区间 */
function run(start: HumanTimerState, events: [string, boolean, number][]) {
  let s = start;
  const closed = [];
  for (const [stop, visible, now] of events) {
    const r = noteVisible(s, stop, visible, now);
    s = r.state;
    closed.push(...r.closed);
  }
  return { state: s, closed };
}

describe("TD-1 人时区间机", () => {
  it("同 stop 双 visible 不双计：第二次 visible 不重置起点", () => {
    const { closed } = run(emptyHumanTimer, [
      ["02.5", true, 1000],
      ["02.5", true, 2000],
      ["02.5", false, 3000],
    ]);
    expect(closed).toEqual([{ stop: "02.5", enteredAt: 1000, leftAt: 3000 }]);
  });

  it("不设时长下限：0.5 s 的区间照常 flush（MUT-11 的杀手）", () => {
    const { closed } = run(emptyHumanTimer, [
      ["03.5", true, 10_000],
      ["03.5", false, 10_500],
    ]);
    expect(closed).toEqual([{ stop: "03.5", enteredAt: 10_000, leftAt: 10_500 }]);
  });

  it("closeAll（switch/quit）恰一次：关闭全部在计区间，重复调用不再产生区间", () => {
    let s: HumanTimerState = emptyHumanTimer;
    s = noteVisible(s, "02.5", true, 100).state;
    s = noteVisible(s, "05", true, 200).state;
    const a = closeAll(s, 1000);
    expect(a.closed).toEqual([
      { stop: "02.5", enteredAt: 100, leftAt: 1000 },
      { stop: "05", enteredAt: 200, leftAt: 1000 },
    ]);
    const b = closeAll(a.state, 2000);
    expect(b.closed).toEqual([]); // 第二次 flush 零区间
    expect(b.state.open).toEqual({});
  });

  it("ack 后该 stop 区间关闭，其它 stop 不受影响（MUT-12 的杀手）", () => {
    let s: HumanTimerState = emptyHumanTimer;
    s = noteVisible(s, "02.5", true, 1000).state;
    s = noteVisible(s, "05", true, 1000).state;
    const r = closeStop(s, "02.5", 5000);
    expect(r.closed).toEqual([{ stop: "02.5", enteredAt: 1000, leftAt: 5000 }]);
    expect(r.state.open).toEqual({ "05": 1000 }); // 编辑器与决策卡同开也不双计
    // 幂等：再次 close 同一 stop 不产生第二条记录
    expect(closeStop(r.state, "02.5", 9000).closed).toEqual([]);
  });

  it("未可见的 stop 被 close 时不产生记录（零区间的关闭是 no-op）", () => {
    expect(closeStop(emptyHumanTimer, "09", 500).closed).toEqual([]);
  });

  it("epochSeconds：毫秒 → epoch 秒十进制字符串（3 位小数，无科学计数法）", () => {
    expect(epochSeconds(1_790_171_112_636)).toBe("1790171112.636");
    expect(epochSeconds(0)).toBe("0.000");
  });
});

describe("reduceHumanTime 归并", () => {
  it("四个停机点各自求和，之外归 other；合计与条数正确", () => {
    const r = reduceHumanTime([
      { stop: "02.5", minutes: 12.5 },
      { stop: "02.5", minutes: 0.5, source: "desktop" },
      { stop: "03.5", minutes: 30.25, source: "desktop" },
      { stop: "05", minutes: 4 },
      { stop: "09", minutes: 1.25 },
      { stop: "scout", minutes: 0.5 },
    ]);
    expect(r.perStop).toEqual({ "02.5": 13, "03.5": 30.25, "05": 4, "09": 1.25 });
    expect(r.otherMin).toBe(0.5);
    expect(r.totalMin).toBe(49);
    expect(r.count).toBe(6);
  });

  it("损坏条目跳过、不污染合计", () => {
    const r = reduceHumanTime([{ stop: "02.5", minutes: 1 }, { stop: 5 as unknown as string, minutes: 2 }, { stop: "x", minutes: Number.NaN } as never]);
    expect(r.count).toBe(1);
    expect(r.totalMin).toBe(1);
  });
});
