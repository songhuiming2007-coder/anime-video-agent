// Spec 14 VS-1/VS-2 判据原型：解析 tokens.css 三块，断言深色两块逐值相同、覆盖完整，并算对子表全部对比度。
// 用法：node tk.mjs <tokens.css>。施工时移植为 desktop/tests/static/visual.test.ts 的 VS-1/VS-2。
import { readFileSync } from "node:fs";
import { flatten, ratio, over, parse } from "./contrast.mjs";
// 解析：浅色 = 第一个 :root 块；深色 = @media (prefers-color-scheme: dark) 内的 :root 块
export function parseTokens(css) {
  const body = (re) => { const m = css.match(re); if (!m) throw new Error("block missing " + re); return m[1]; };
  const decls = (b) => Object.fromEntries([...b.matchAll(/^\s*(--[\w-]+):\s*(.+?);\s*$/gm)].map((m) => [m[1], m[2]]));
  const light = decls(body(/^:root \{([\s\S]*?)^\}/m));
  const mediaDark = decls(body(/@media \(prefers-color-scheme: dark\) \{\s*:root:not\(\[data-theme="light"\]\) \{([\s\S]*?)^  \}/m));
  const forcedDark = decls(body(/^:root\[data-theme="dark"\] \{([\s\S]*?)^\}/m));
  return { light, dark: { ...light, ...forcedDark }, darkOnly: forcedDark, mediaDark };
}
const resolve = (t, v) => { let x = v, n = 0; while (/^var\((--[\w-]+)\)$/.test(x)) { x = t[x.match(/^var\((--[\w-]+)\)$/)[1]]; if (++n > 5 || x === undefined) throw new Error("bad alias " + v); } return x; };
const COLOR = /^(#[0-9a-f]{6}|rgba\(\d+, \d+, \d+, [\d.]+\)|var\(--[\w-]+\))$/i;
// 对子表：[前景, 背景栈(底→顶), 下限, 说明]
const S = ["--bg", "--bg-sidebar", "--bg-panel", "--bg-card", "--bg-popover", "--bg-input"];
const SEL = [["--bg-sidebar", "--bg-selected"], ["--bg-panel", "--bg-selected"]];
const HOV = [["--bg-sidebar", "--bg-hover"], ["--bg-panel", "--bg-hover"]];
export const EXPECTED_PAIRS = 290;
const ROWBG = [["--bg-sidebar"], ["--bg-sidebar", "--bg-hover"], ["--bg-sidebar", "--bg-selected"], ["--bg-panel", "--bg-hover"], ["--bg-panel", "--bg-selected"]];
export const PAIRS = [
  ...["--fg", "--fg-muted"].flatMap((f) => [...S.map((b) => [f, [b], 4.5, "正文"]), ...SEL.map((b) => [f, b, 4.5, "选中行文字"]), ...HOV.map((b) => [f, b, 4.5, "悬停行文字"])]),
  ...S.map((b) => ["--fg-subtle", [b], 3, "弱提示（禁用态/装饰，不承载必读文字）"]),
  ...[...ROWBG, ["--bg"], ["--bg-card"]].map((b) => ["--fg-muted", [...b, "--bg-subtle"], 4.5, "中性徽标（含「停机」）"]),
  ["--fg-on-accent", ["--accent"], 4.5, "主按钮文字"], ["--fg-on-accent", ["--accent-hover"], 4.5, "主按钮悬停文字"],
  ...["--bg-panel", "--bg-card", "--bg-sidebar"].map((b) => ["--accent-strong", [b], 4.5, "粉色文字/链接"]),
  ...S.map((b) => ["--focus-ring", [b], 3, "焦点环对各面（offset 2px，环只压在面上）"]),
  ...["--bg-panel", "--bg-card", "--bg-sidebar", "--bg"].map((b) => ["--accent-border", [b], 3, "主按钮边界（非文本 3:1）"]),
  ...["--bg-input", "--bg-panel", "--bg-card", "--bg-sidebar", "--bg"].map((b) => ["--border-input", [b], 3, "输入框边界（非文本 3:1，含侧栏搜索框）"]),
  ...ROWBG.map((b) => ["--fg-muted", b, 3, "状态点与未知圆环（非文本）"]),
  ...["ok", "warn", "danger", "wait"].flatMap((k) => [
    ...["--bg-panel", "--bg-card", "--bg-sidebar"].map((b) => [`--${k}`, [b], 4.5, "状态文字"]),
    ...[["--bg"], ["--bg-panel"], ["--bg-card"], ...ROWBG].map((b) => [`--${k}`, [...b, `--${k}-surface`], 4.5, "状态徽标（含悬停/选中行叠法）"]),
    ...ROWBG.map((b) => [`--${k}`, b, 3, "状态点压在行上（非文本）"]),
  ]),
  ["--overlay-fg", ["--bg-media", "--overlay-bg"], 4.5, "媒体浮层文字（黑画面）"],
  ["--overlay-fg", ["--bg-frame", "--overlay-bg"], 4.5, "媒体浮层文字（白画面）"],
  ...["--syntax-key", "--syntax-str", "--syntax-num", "--syntax-lit", "--syntax-heading", "--syntax-link", "--syntax-meta"].flatMap((f) => ["--bg-panel", "--bg-card", "--bg-input"].map((b) => [f, [b], 4.5, "代码/JSON/Markdown 高亮"])),
];
export function check(css) {
  const t = parseTokens(css), out = [];
  const badFmt = Object.entries(t.light).concat(Object.entries(t.darkOnly)).filter(([k, v]) => /^--(bg|fg|border|accent|focus|ok|warn|danger|wait|overlay|syntax)/.test(k) && !COLOR.test(v));
  if (badFmt.length) return { out: [], missing: [], badFormat: badFmt };
  for (const [theme, tok] of [["light", t.light], ["dark", t.dark]]) {
    for (const [fg, stack, min, why] of PAIRS) {
      const bgc = flatten(stack.map((s) => resolve(tok, tok[s]))); const r = ratio(over(parse(resolve(tok, tok[fg])), bgc), bgc);
      out.push({ theme, fg, bg: stack.join("+"), min, r: Math.round(r * 100) / 100, ok: r >= min, why });
    }
  }
  // 深色块必须覆盖浅色块里的每一个颜色 token（防一个主题漏改）
  const colorKeys = Object.entries(t.light).filter(([, v]) => COLOR.test(v)).map(([k]) => k);
  const missing = colorKeys.filter((k) => !(k in t.darkOnly));
  // 两块深色逐值相同（跟随系统的深色 = 手动深色）
  const mk = Object.keys(t.mediaDark), fk = Object.keys(t.darkOnly);
  const blockDiff = [...new Set([...mk, ...fk])].filter((k) => t.mediaDark[k] !== t.darkOnly[k]);
  if (blockDiff.length) return { out: [], missing, badFormat: [], blockDiff };
  const badFormat = Object.entries(t.light).concat(Object.entries(t.darkOnly)).filter(([k, v]) => /color-mix|hsl|oklch/.test(v));
  return { out, missing, badFormat, blockDiff: [] };
}
if (process.argv[2]) {
  const { out, missing, badFormat, blockDiff } = check(readFileSync(process.argv[2], "utf-8"));
  const bad = out.filter((x) => !x.ok);
  console.log("pairs", out.length, "fail", bad.length, "missingDark", missing, "badFormat", badFormat.length, "blockDiff", blockDiff);
  for (const x of bad) console.log("FAIL", x);
  const mins = {}; for (const x of out) { const k = x.theme + " " + x.why; mins[k] = Math.min(mins[k] ?? 99, x.r); }
  console.log(mins);
  // VS-2 不许空跑（红队二轮 B2）：格式错或两块深色不一致时 check() 提前返回空表，必须以对子数兜底
  if (out.length !== EXPECTED_PAIRS || bad.length || missing.length || badFormat.length || blockDiff.length) { console.log(`EXIT 1：期望 ${EXPECTED_PAIRS} 对全绿`); process.exit(1); }
}
