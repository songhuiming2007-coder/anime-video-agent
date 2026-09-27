// VU-1：formatRelTime 的分档边界（Spec 14 §7.1；tz 固定 Asia/Shanghai，B2）。
import { describe, expect, it } from "vitest";
import { formatRelTime } from "../../src/shared/relTime";

const TZ = "Asia/Shanghai";
// 2026-09-27T12:00:00+08:00（本地正午，跨年/同日边界都在白天，不受 0 点影响）
const NOW = new Date("2026-09-27T12:00:00+08:00").getTime();

describe("VU-1 formatRelTime（Asia/Shanghai）", () => {
  it("59.9 s → 刚刚；负差（时钟回拨）→ 刚刚", () => {
    expect(formatRelTime(NOW - 59_900, NOW, TZ)).toBe("刚刚");
    expect(formatRelTime(NOW + 5 * 60_000, NOW, TZ)).toBe("刚刚"); // 未来 5 min
  });
  it("60 s → 1 分钟（M14 的边界：< 60 s 写成 <= 时这里红）；59 min / 60 min", () => {
    expect(formatRelTime(NOW - 60_000, NOW, TZ)).toBe("1 分钟");
    expect(formatRelTime(NOW - 59 * 60_000, NOW, TZ)).toBe("59 分钟");
    expect(formatRelTime(NOW - 60 * 60_000, NOW, TZ)).toBe("1 小时");
  });
  it("23 h / 24 h", () => {
    expect(formatRelTime(NOW - 23 * 3_600_000, NOW, TZ)).toBe("23 小时");
    expect(formatRelTime(NOW - 24 * 3_600_000, NOW, TZ)).toBe("1 天");
  });
  it("29 天 → N 天；30 天 → M月D日（同年）", () => {
    expect(formatRelTime(NOW - 29 * 86_400_000, NOW, TZ)).toBe("29 天");
    expect(formatRelTime(NOW - 30 * 86_400_000, NOW, TZ)).toBe("8月28日");
  });
  it("跨年 → YYYY年M月D日", () => {
    const newYear = new Date("2026-01-15T12:00:00+08:00").getTime();
    expect(formatRelTime(new Date("2025-12-01T12:00:00+08:00").getTime(), newYear, TZ)).toBe("2025年12月1日");
  });
});
