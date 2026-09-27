// `created_at` 的时间比较（Spec 10 S8-R12 / §4.1）：**不按字符串比**。
// Python 的 `isoformat()` 在微秒恰为 0 时省略小数部分（`approvals.py:_utc_now_iso`），
// 于是 "…:00Z" 与 "…:00.000001Z" 按字符串是 `.`(0x2E) < `Z`(0x5A)，前者被判为更新——
// 概率约百万分之一，但错就是错。这里把小数秒补足 6 位后按数值比。
// 无法解析一律抛错：静默退回字符串比较等于把缺陷藏起来（判据：不掩盖错误）。
const ISO = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})$/;

function parseParts(s: string): { ms: number; micros: number } {
  const m = ISO.exec(s);
  if (!m) throw new Error(`无法解析时间戳：${s}`);
  // 先按整秒构造（丢掉小数），小数单独按微秒比——两者合起来是精确的字典序时点比较
  const base = Date.parse(`${m[1]}-${m[2]}-${m[3]}T${m[4]}:${m[5]}:${m[6]}${m[8]}`);
  if (Number.isNaN(base)) throw new Error(`无法解析时间戳：${s}`);
  const micros = Number((m[7] ?? "").padEnd(6, "0").slice(0, 6));
  return { ms: base, micros };
}

/** a < b → 负数；相等 → 0；a > b → 正数。不可解析 → 抛错。 */
export function compareIso(a: string, b: string): number {
  const x = parseParts(a);
  const y = parseParts(b);
  if (x.ms !== y.ms) return x.ms < y.ms ? -1 : 1;
  if (x.micros !== y.micros) return x.micros < y.micros ? -1 : 1;
  return 0;
}
