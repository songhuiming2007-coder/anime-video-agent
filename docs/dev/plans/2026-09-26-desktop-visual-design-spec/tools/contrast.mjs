// WCAG 2.x 相对亮度与对比度；rgba 先按 alpha 合成到底色
export function parse(c) {
  c = c.trim();
  let m = c.match(/^#([0-9a-f]{6})$/i);
  if (m) { const n = parseInt(m[1], 16); return [n >> 16 & 255, n >> 8 & 255, n & 255, 1]; }
  m = c.match(/^rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([\d.]+)\s*)?\)$/);
  if (m) return [+m[1], +m[2], +m[3], m[4] === undefined ? 1 : +m[4]];
  throw new Error("unparsable color " + c);
}
export function over(top, base) { const a = top[3]; return [0, 1, 2].map((i) => top[i] * a + base[i] * (1 - a)).concat(1); }
function lin(v) { v /= 255; return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }
export function lum(c) { return 0.2126 * lin(c[0]) + 0.7152 * lin(c[1]) + 0.0722 * lin(c[2]); }
export function ratio(a, b) { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); }
// stack: [bottom ... top]；每层可带 alpha
export function flatten(stack) { let acc = parse(stack[0]); if (acc[3] !== 1) throw new Error("bottom must be opaque"); for (const s of stack.slice(1)) acc = over(parse(s), acc); return acc; }
