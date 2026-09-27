// 相对时间分档（Spec 14 §3.4，纯函数冻结；TG-3：shared 零 import、零 DOM 全局）。
// 语义：值取期目录 mtime，只在期目录顶层有增删或原子写入时刷新（E6），UI 上的 title 如实说明。

/** 每个时区缓存一个日历格式器（取 y/m/d 判断是否同年与拼「M月D日」） */
const CAL = new Map<string, Intl.DateTimeFormat>();
function ymd(ms: number, tz: string): { y: number; m: number; d: number } {
  let f = CAL.get(tz);
  if (!f) {
    f = new Intl.DateTimeFormat("en-US", { timeZone: tz, year: "numeric", month: "numeric", day: "numeric" });
    CAL.set(tz, f);
  }
  const p = f.formatToParts(ms);
  const get = (t: string) => Number(p.find((x) => x.type === t)!.value);
  return { y: get("year"), m: get("month"), d: get("day") };
}

export function formatRelTime(mtimeMs: number, nowMs: number, tz?: string): string {
  const diff = nowMs - mtimeMs;
  if (diff < 60_000) return "刚刚"; // 含负值（时钟回拨）
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)} 分钟`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)} 小时`;
  if (diff < 30 * 86_400_000) return `${Math.floor(diff / 86_400_000)} 天`;
  const zone = tz ?? new Intl.DateTimeFormat().resolvedOptions().timeZone;
  const a = ymd(mtimeMs, zone);
  const b = ymd(nowMs, zone);
  return a.y === b.y ? `${a.m}月${a.d}日` : `${a.y}年${a.m}月${a.d}日`;
}
