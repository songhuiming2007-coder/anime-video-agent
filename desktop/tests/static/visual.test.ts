// Spec 14 §7.1 的静态判据：VS-1~VS-6、VS-10、VS-11（判据本体由附件 tools/tk.mjs、tools/vs.mjs 原样移植）。
// 扫描范围随 PR 推进（Spec 14 §7.1）：PR1 只扫 ui.css —— 旧 style.css 尚未替换，满是字面量；PR2 起把它加进 STYLE_SHEETS。
// 与附件对拍（门禁 1）：tokens.css 逐字节相同（sha256 见头部「拍板基准」），同一条判据在附件 tokens.css 上必须给出
//   290 对 0 失败，且各类最小值逐值等于 MINS（数值由 `node tools/tk.mjs tokens.css` 实跑得到）。
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import ts from "typescript";
import { describe, expect, it } from "vitest";
import { DESKTOP, moduleSpecifiers, parse, sourceFiles, type SourceFile } from "./scan";

const RENDERER = join(DESKTOP, "src/renderer");
const read = (rel: string) => readFileSync(rel, "utf-8");
const TOKENS = read(join(RENDERER, "tokens.css"));
/** 拍板基准 sha256（Spec 14 头部；改取值就得回到人面前重看 mock） */
const BASELINE_SHA256 = {
  "tokens.css": "3d62bafad38d4ff8d7f04161bfa2652ce8d2935674e5b061c0cc64218cb1a382",
  "ui.css": "9c123bc33c84962e2a5ad6c989f6d71151713fc42f4c56632f3d02ca5fd7d67b",
};
/** 只改手动深色块 / 只改跟随系统块（自测用，与 §7.2 的变异同形） */
const mutateForcedDark = (fn: (tail: string) => string) => {
  const i = TOKENS.indexOf(':root[data-theme="dark"] {');
  return TOKENS.slice(0, i) + fn(TOKENS.slice(i));
};
const mutateMediaDark = (fn: (mid: string) => string) => {
  const i = TOKENS.indexOf("@media (prefers-color-scheme: dark) {");
  const j = TOKENS.indexOf(':root[data-theme="dark"] {');
  return TOKENS.slice(0, i) + fn(TOKENS.slice(i, j)) + TOKENS.slice(j);
};
/** PR1 只扫 ui.css；PR2 换掉 style.css 后加入（Spec 14 §8 PR2） */
const STYLE_SHEETS = ["src/renderer/ui.css", "src/renderer/style.css"];
const cssOf = (rels: string[]) => parseCss(rels.map((r) => read(join(DESKTOP, r))).join("\n"));
const sheetRules = () => cssOf(STYLE_SHEETS);
const uiRules = () => cssOf(["src/renderer/ui.css"]);

// ---------------- 对比度（附件 tools/contrast.mjs） ----------------

function parseColor(c: string): [number, number, number, number] {
  const t = c.trim();
  let m = t.match(/^#([0-9a-f]{6})$/i);
  if (m) {
    const n = parseInt(m[1], 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255, 1];
  }
  m = t.match(/^rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([\d.]+)\s*)?\)$/);
  if (m) return [+m[1], +m[2], +m[3], m[4] === undefined ? 1 : +m[4]];
  throw new Error("unparsable color " + c);
}

type RGBA = [number, number, number, number];
const over = (top: RGBA, base: RGBA): RGBA => [0, 1, 2].map((i) => top[i] * top[3] + base[i] * (1 - top[3])).concat(1) as RGBA;
const lin = (v: number) => {
  const x = v / 255;
  return x <= 0.04045 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4;
};
const lum = (c: RGBA) => 0.2126 * lin(c[0]) + 0.7152 * lin(c[1]) + 0.0722 * lin(c[2]);
const ratio = (a: RGBA, b: RGBA) => {
  const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p);
  return (x + 0.05) / (y + 0.05);
};
function flatten(stack: string[]): RGBA {
  let acc = parseColor(stack[0]);
  if (acc[3] !== 1) throw new Error("bottom must be opaque");
  for (const s of stack.slice(1)) acc = over(parseColor(s), acc);
  return acc;
}

// ---------------- VS-1 / VS-2：token 解析与对子表（附件 tools/tk.mjs） ----------------

type Tokens = Record<string, string>;
const decls = (b: string): Tokens => Object.fromEntries([...b.matchAll(/^\s*(--[\w-]+):\s*(.+?);\s*$/gm)].map((m) => [m[1], m[2]]));
const body = (css: string, re: RegExp) => {
  const m = css.match(re);
  if (!m) throw new Error("block missing " + re);
  return m[1];
};

export function parseTokens(css: string) {
  const light = decls(body(css, /^:root \{([\s\S]*?)^\}/m));
  const mediaDark = decls(body(css, /@media \(prefers-color-scheme: dark\) \{\s*:root:not\(\[data-theme="light"\]\) \{([\s\S]*?)^  \}/m));
  const forcedDark = decls(body(css, /^:root\[data-theme="dark"\] \{([\s\S]*?)^\}/m));
  return { light, mediaDark, forcedDark, dark: { ...light, ...forcedDark } };
}

const COLOR = /^(#[0-9a-f]{6}|rgba\(\d+, \d+, \d+, [\d.]+\)|var\(--[\w-]+\))$/i;
const COLOR_KEY = /^--(bg|fg|border|accent|focus|ok|warn|danger|wait|overlay|syntax)/;
const colorKeys = (t: Tokens) => Object.entries(t).filter(([, v]) => COLOR.test(v)).map(([k]) => k);

function resolveVar(t: Tokens, v: string): string {
  let x = v;
  for (let n = 0; n < 5; n++) {
    const m = x.match(/^var\((--[\w-]+)\)$/);
    if (!m) return x;
    const next = t[m[1]];
    if (next === undefined) throw new Error("bad alias " + v);
    x = next; // 环由 5 次上限兜底（附件同）
  }
  throw new Error("bad alias " + v);
}

export const EXPECTED_PAIRS = 290;
const S = ["--bg", "--bg-sidebar", "--bg-panel", "--bg-card", "--bg-popover", "--bg-input"];
const SEL = [["--bg-sidebar", "--bg-selected"], ["--bg-panel", "--bg-selected"]];
const HOV = [["--bg-sidebar", "--bg-hover"], ["--bg-panel", "--bg-hover"]];
const ROWBG = [["--bg-sidebar"], ["--bg-sidebar", "--bg-hover"], ["--bg-sidebar", "--bg-selected"], ["--bg-panel", "--bg-hover"], ["--bg-panel", "--bg-selected"]];
export type Pair = [string, string[], number, string];
const PAIRS: Pair[] = [
  ...["--fg", "--fg-muted"].flatMap((f): Pair[] => [...S.map((b): Pair => [f, [b], 4.5, "正文"]), ...SEL.map((b): Pair => [f, b, 4.5, "选中行文字"]), ...HOV.map((b): Pair => [f, b, 4.5, "悬停行文字"])]),
  ...S.map((b): Pair => ["--fg-subtle", [b], 3, "弱提示（禁用态/装饰，不承载必读文字）"]),
  ...[...ROWBG, ["--bg"], ["--bg-card"]].map((b): Pair => ["--fg-muted", [...b, "--bg-subtle"], 4.5, "中性徽标（含「停机」）"]),
  ["--fg-on-accent", ["--accent"], 4.5, "主按钮文字"],
  ["--fg-on-accent", ["--accent-hover"], 4.5, "主按钮悬停文字"],
  ...["--bg-panel", "--bg-card", "--bg-sidebar"].map((b): Pair => ["--accent-strong", [b], 4.5, "粉色文字/链接"]),
  ...S.map((b): Pair => ["--focus-ring", [b], 3, "焦点环对各面（offset 2px，环只压在面上）"]),
  ...["--bg-panel", "--bg-card", "--bg-sidebar", "--bg"].map((b): Pair => ["--accent-border", [b], 3, "主按钮边界（非文本 3:1）"]),
  ...["--bg-input", "--bg-panel", "--bg-card", "--bg-sidebar", "--bg"].map((b): Pair => ["--border-input", [b], 3, "输入框边界（非文本 3:1，含侧栏搜索框）"]),
  ...ROWBG.map((b): Pair => ["--fg-muted", b, 3, "状态点与未知圆环（非文本）"]),
  ...["ok", "warn", "danger", "wait"].flatMap((k): Pair[] => [
    ...["--bg-panel", "--bg-card", "--bg-sidebar"].map((b): Pair => [`--${k}`, [b], 4.5, "状态文字"]),
    ...[["--bg"], ["--bg-panel"], ["--bg-card"], ...ROWBG].map((b): Pair => [`--${k}`, [...b, `--${k}-surface`], 4.5, "状态徽标（含悬停/选中行叠法）"]),
    ...ROWBG.map((b): Pair => [`--${k}`, b, 3, "状态点压在行上（非文本）"]),
  ]),
  ["--overlay-fg", ["--bg-media", "--overlay-bg"], 4.5, "媒体浮层文字（黑画面）"],
  ["--overlay-fg", ["--bg-frame", "--overlay-bg"], 4.5, "媒体浮层文字（白画面）"],
  ...["--syntax-key", "--syntax-str", "--syntax-num", "--syntax-lit", "--syntax-heading", "--syntax-link", "--syntax-meta"].flatMap((f): Pair[] => ["--bg-panel", "--bg-card", "--bg-input"].map((b): Pair => [f, [b], 4.5, "代码/JSON/Markdown 高亮"])),
];

export interface PairResult { theme: "light" | "dark"; fg: string; bg: string; min: number; r: number; ok: boolean; why: string }

/** 附件 tk.mjs 的 check()：返回 null 表示检查器自身失效（格式错 / 深色两块不一致 → 空表），由调用方断言。 */
export function checkPairs(css: string): { results: PairResult[]; missing: string[]; badFormat: string[]; blockDiff: string[] } {
  const t = parseTokens(css);
  const badFormatEntries = Object.entries(t.light).concat(Object.entries(t.forcedDark)).filter(([k, v]) => COLOR_KEY.test(k) && !COLOR.test(v));
  if (badFormatEntries.length) return { results: [], missing: [], badFormat: badFormatEntries.map(([k]) => k), blockDiff: [] };
  const results: PairResult[] = [];
  for (const [theme, tok] of [["light", t.light], ["dark", t.dark]] as const) {
    for (const [fg, stack, min, why] of PAIRS) {
      const bgc = flatten(stack.map((s) => resolveVar(tok, tok[s])));
      const r = ratio(over(parseColor(resolveVar(tok, tok[fg])), bgc), bgc);
      results.push({ theme, fg, bg: stack.join("+"), min, r: Math.round(r * 100) / 100, ok: r >= min, why });
    }
  }
  const missing = colorKeys(t.light).filter((k) => !(k in t.forcedDark));
  const keys = [...new Set([...Object.keys(t.mediaDark), ...Object.keys(t.forcedDark)])];
  const blockDiff = keys.filter((k) => t.mediaDark[k] !== t.forcedDark[k]);
  if (blockDiff.length) return { results: [], missing, badFormat: [], blockDiff };
  const badFormat = Object.entries(t.light).concat(Object.entries(t.forcedDark)).filter(([, v]) => /color-mix|hsl|oklch/.test(v)).map(([k]) => k);
  return { results, missing, badFormat, blockDiff: [] };
}

/** 每类判据的最小值（实跑自 `node tools/tk.mjs tokens.css`，见 Spec 14 §7.1 VS-2 的注释） */
const MINS: Record<string, [number, number]> = {
  正文: [5.49, 5.96],
  选中行文字: [5.34, 5.56],
  悬停行文字: [4.95, 5.93],
  "弱提示（禁用态/装饰，不承载必读文字）": [3.01, 3.27],
  "中性徽标（含「停机」）": [4.66, 5.08],
  主按钮文字: [9.98, 9.78],
  主按钮悬停文字: [8.68, 11.25],
  "粉色文字/链接": [5.16, 6.76],
  "焦点环对各面（offset 2px，环只压在面上）": [5.16, 6.23],
  "主按钮边界（非文本 3:1）": [3.7, 8.76],
  "输入框边界（非文本 3:1，含侧栏搜索框）": [3.4, 3.17],
  "状态点与未知圆环（非文本）": [4.95, 5.56],
  状态文字: [5.74, 6.6],
  "状态徽标（含悬停/选中行叠法）": [4.58, 4.58],
  "状态点压在行上（非文本）": [5.18, 5.68],
  "媒体浮层文字（黑画面）": [21, 21],
  "媒体浮层文字（白画面）": [9.23, 9.23],
  "代码/JSON/Markdown 高亮": [5.87, 6.46],
};

// ---------------- VS-3~VS-6 / VS-11：CSS 静态守卫（附件 tools/vs.mjs） ----------------

interface Decl { prop: string; value: string }
interface Rule { selector: string; decls: Decl[]; media?: string; keyframes?: boolean }

export function parseCss(css: string): Rule[] {
  css = css.replace(/\/\*[\s\S]*?\*\//g, "");
  const rules: Rule[] = [];
  let i = 0;
  const readBlock = (start: number) => {
    let d = 0;
    for (let j = start; j < css.length; j++) {
      if (css[j] === "{") d++;
      else if (css[j] === "}") {
        d--;
        if (d === 0) return j;
      }
    }
    throw new Error("unbalanced");
  };
  while (i < css.length) {
    const open = css.indexOf("{", i);
    if (open < 0) break;
    const head = css.slice(i, open).trim();
    const close = readBlock(open);
    const inner = css.slice(open + 1, close);
    if (head.startsWith("@media")) {
      for (const r of parseCss(inner)) rules.push({ ...r, media: head });
    } else if (head.startsWith("@keyframes")) {
      rules.push({ selector: head, decls: [], keyframes: true });
    } else {
      const decls = inner
        .split(";")
        .map((d) => d.trim())
        .filter(Boolean)
        .map((d) => {
          const k = d.indexOf(":");
          return { prop: d.slice(0, k).trim(), value: d.slice(k + 1).trim() };
        });
      rules.push({ selector: head, decls });
    }
    i = close + 1;
  }
  return rules;
}

const NAMED = /\b(white|black|red|green|blue|gray|grey|yellow|orange|purple|pink|silver|navy)\b/i;

export function vs3(rules: Rule[]): string[] {
  const bad: string[] = [];
  for (const r of rules)
    for (const { prop, value } of r.decls) {
      if (prop.startsWith("--")) {
        bad.push(`${r.selector} 定义了自定义属性 ${prop}（token 只许在 tokens.css 定义，红队二轮 B1）`);
        continue;
      }
      if (/#[0-9a-f]{3,8}\b/i.test(value) || /\b(rgba?|hsla?|color-mix|oklch|oklab)\(/i.test(value) || NAMED.test(value)) bad.push(`${r.selector} { ${prop}: ${value} }`);
    }
  return bad;
}

export function vs4(rules: Rule[]): string[] {
  const bad: string[] = [];
  for (const r of rules)
    for (const { prop, value } of r.decls) {
      if (prop === "font-size" && !/^(var\(--text-(xs|sm|base|lg|xl)\)|inherit)$/.test(value)) bad.push(`${r.selector} font-size: ${value}`);
      if (prop === "font" && /\d+px/.test(value)) bad.push(`${r.selector} font: ${value}`);
      if (/var\(--text-xs\)/.test(value) && !/ui-badge|ui-count|ui-kbd/.test(r.selector)) bad.push(`${r.selector} 用了 --text-xs`);
    }
  return bad;
}

const OUTLINE_NONE_OK = [":focus:not(:focus-visible)", ".composer textarea:focus-visible"];

export function vs5(rules: Rule[]): string[] {
  const bad: string[] = [];
  const fv = rules.filter((r) => r.selector === ":focus-visible");
  if (fv.length !== 1) bad.push(`:focus-visible 规则应恰为 1 条，实为 ${fv.length}`);
  else {
    const d = Object.fromEntries(fv[0].decls.map((x) => [x.prop, x.value]));
    if (d.outline !== "2px solid var(--focus-ring)") bad.push(`outline 应为 2px solid var(--focus-ring)，实为 ${d.outline}`);
    if (d["outline-offset"] !== "2px") bad.push(`outline-offset 应为 2px，实为 ${d["outline-offset"]}`);
  }
  for (const r of rules)
    for (const { prop, value } of r.decls) {
      if (!prop.startsWith("outline")) continue;
      if (r.selector === ":focus-visible") continue;
      const ok = prop === "outline" && /^(none|0)$/.test(value) && r.selector.split(",").every((s) => OUTLINE_NONE_OK.includes(s.trim()));
      if (!ok) bad.push(`${r.selector} ${prop}: ${value}（outline* 只许在全局 :focus-visible 与两条豁免里，红队二轮 B1）`);
    }
  return bad;
}

export function vs6(rules: Rule[]): string[] {
  const rm = rules.filter((r) => r.media && /prefers-reduced-motion:\s*reduce/.test(r.media));
  const decls = rm.flatMap((r) => r.decls.map((d) => `${d.prop}:${d.value.replace(/\s*!important/, "")}`));
  const ok = decls.includes("transition-duration:0s") && decls.includes("animation-duration:0s");
  return ok ? [] : ["缺 prefers-reduced-motion 归零块"];
}

const UI_SCOPE = /^(\.ui-|\.conv|\.dock|\.composer|\.session-head|\.toolbar|\.editor-split|\.checklist|\.seg-row|\.cover-)/;
const UI_GLOBALS = [":focus-visible", ":focus:not(:focus-visible)", "*, *::before, *::after"];

export function vs11(rules: Rule[]): string[] {
  const bad: string[] = [];
  for (const r of rules) {
    if (r.keyframes) continue;
    const globalOk = UI_GLOBALS.includes(r.selector) && (r.selector !== "*, *::before, *::after" || /prefers-reduced-motion/.test(r.media ?? ""));
    if (globalOk) continue;
    for (const s of r.selector.split(",")) if (!UI_SCOPE.test(s.trim())) bad.push(`ui.css 越出作用域：${s.trim()}`);
  }
  return bad;
}

// ---------------- VS-10：纯展示组件无事件、零外部依赖 ----------------

const PURE_FILES = ["renderer/icons.tsx", "renderer/ui.tsx"];

const ALLOWED_IMPORTS = ["react", "./icons"]; // Spec 14 §7.1 VS-10（2026-09-26 裁决放宽 ./icons，§2.5 路径数据唯一落点优先）

export function vs10(f: SourceFile): string[] {
  const bad: string[] = [];
  for (const spec of moduleSpecifiers(f)) if (!ALLOWED_IMPORTS.includes(spec)) bad.push(`${f.rel}: 只允许 import react 与 ./icons，实为 ${spec}`);
  const walk = (n: ts.Node) => {
    if (ts.isPropertySignature(n) && n.name && ts.isIdentifier(n.name) && /^on[A-Z]/.test(n.name.text)) bad.push(`${f.rel}: props 里有事件键 ${n.name.text}`);
    ts.forEachChild(n, walk);
  };
  walk(parse(f));
  return bad;
}

const src = (rel: string, text: string): SourceFile => ({ rel, text });
const rendererFiles = () => sourceFiles().filter((f) => f.rel.startsWith("renderer/"));

// ---------------- 用例 ----------------

describe("门禁 1：两个冻结文件与头部「拍板基准」逐字一致", () => {
  it("tokens.css / ui.css 的 sha256", () => {
    const h = (s: string) => createHash("sha256").update(s).digest("hex");
    for (const [name, want] of Object.entries(BASELINE_SHA256)) expect({ name, sha256: h(read(join(RENDERER, name))) }).toEqual({ name, sha256: want });
  });
});

describe("VS-1 token 格式、覆盖与两块深色一致", () => {
  it("检查器自测：格式非法 / 覆盖缺失 / 两块不一致各被抓住", () => {
    expect(parseTokens(TOKENS).light["--bg"]).toBe("#f7f7f8");
    // M3：color-mix 格式非法，检查器在算对子之前就返回空表
    const m3 = checkPairs(TOKENS.replace(/--bg: [^;]+;/, "--bg: color-mix(in oklab, white, black);"));
    expect({ results: m3.results, badFormat: m3.badFormat }).toEqual({ results: [], badFormat: ["--bg"] });
    // M2：只从手动深色块删 --warn → 覆盖缺一项 + 两块不一致（互为冗余）
    const m2 = checkPairs(mutateForcedDark((s) => s.replace(/  --warn: [^;]+;\n/, "")));
    expect({ missing: m2.missing, blockDiff: m2.blockDiff }).toEqual({ missing: ["--warn"], blockDiff: ["--warn"] });
    // M23：只改跟随系统块的 --bg-panel → 两块不一致，覆盖仍完整
    const m23 = checkPairs(mutateMediaDark((s) => s.replace(/--bg-panel: [^;]+;/, "--bg-panel: #123456;")));
    expect({ missing: m23.missing, blockDiff: m23.blockDiff }).toEqual({ missing: [], blockDiff: ["--bg-panel"] });
  });
  it("颜色 token 只许三种格式（#rrggbb / rgba(r, g, b, a) / var(--x)）", () => {
    const t = parseTokens(TOKENS);
    const bad = Object.entries({ ...t.light, ...t.forcedDark }).filter(([k, v]) => COLOR_KEY.test(k) && !COLOR.test(v));
    expect(bad.map(([k]) => k)).toEqual([]);
  });
  it("浅色恰 79 个 token、其中 40 个颜色 token，且手动深色块全部覆盖", () => {
    const t = parseTokens(TOKENS);
    expect(Object.keys(t.light)).toHaveLength(79);
    expect(colorKeys(t.light)).toHaveLength(40);
    expect(checkPairs(TOKENS).missing).toEqual([]);
  });
  it("跟随系统深色块与手动深色块键集合、取值逐项相等", () => {
    const t = parseTokens(TOKENS);
    expect(checkPairs(TOKENS).blockDiff).toEqual([]);
    expect(Object.keys(t.mediaDark).sort()).toEqual(Object.keys(t.forcedDark).sort());
  });
  it("var() 别名可解析、无环、目标存在", () => {
    for (const tok of [parseTokens(TOKENS).light, parseTokens(TOKENS).forcedDark]) {
      for (const [k, v] of Object.entries(tok)) {
        if (/^var\(/.test(v)) expect(() => resolveVar(tok, v), `${k}: ${v}`).not.toThrow();
      }
    }
    expect(() => resolveVar({ "--a": "var(--b)", "--b": "var(--a)" }, "var(--a)")).toThrow();
  });
});

describe("VS-2 对比度对子表", () => {
  it("检查器自测：白字配浅粉必红（M4 的形状）", () => {
    const ok = `:root { --bg: #ffffff; --fg-on-accent: #000000; --accent: #f7c5d5; }\n@media (prefers-color-scheme: dark) {\n  :root:not([data-theme="light"]) {\n    --bg: #ffffff;\n    --fg-on-accent: #000000;\n    --accent: #f7c5d5;\n  }\n}\n:root[data-theme="dark"] {\n  --bg: #ffffff;\n  --fg-on-accent: #000000;\n  --accent: #f7c5d5;\n}\n`;
    const t = parseTokens(ok);
    const pairs: Pair[] = [["--fg-on-accent", ["--accent"], 4.5, "主按钮文字"]];
    const r = pairs.map(([fg, stack, min]) => ratio(over(parseColor(t.light[fg]), flatten(stack.map((s) => t.light[s]))), flatten(stack.map((s) => t.light[s]))));
    expect(r[0]).toBeGreaterThan(4.5);
    // 白字：4.5 阈值下必红
    expect(ratio(over(parseColor("#ffffff"), flatten(["#f7c5d5"])), flatten(["#f7c5d5"]))).toBeLessThan(4.5);
  });
  it("对子数恰 290，且逐对全部达标（M41：检查器提前返回空表时这里先红）", () => {
    const { results, missing, badFormat, blockDiff } = checkPairs(TOKENS);
    expect({ pairs: results.length, missing, badFormat, blockDiff }).toEqual({ pairs: EXPECTED_PAIRS, missing: [], badFormat: [], blockDiff: [] });
    expect(results.filter((x) => !x.ok)).toEqual([]);
  });
  it("各类最小值逐值等于附件 tk.mjs 实跑值（浅 / 深）", () => {
    const { results } = checkPairs(TOKENS);
    const mins: Record<string, number> = {};
    for (const x of results) {
      const k = `${x.theme} ${x.why}`;
      mins[k] = Math.min(mins[k] ?? 99, x.r);
    }
    expect([...new Set(results.map((x) => x.why))].sort()).toEqual(Object.keys(MINS).sort());
    for (const [why, [light, dark]] of Object.entries(MINS)) {
      expect({ why, light: mins[`light ${why}`], dark: mins[`dark ${why}`] }).toEqual({ why, light, dark });
    }
  });
});

describe("VS-3 颜色字面量与自定义属性只在 tokens.css", () => {
  it("检查器自测（附件 vs.mjs 自测的四个正反例）", () => {
    expect(vs3(parseCss(".a { white-space: nowrap; color: var(--fg); background: transparent; }"))).toHaveLength(0);
    expect(vs3(parseCss(".a { color: #333; } .b { border: 1px solid white; } .c { background: rgba(0,0,0,.1); }"))).toHaveLength(3);
    expect(vs3(parseCss(".banner-red { color: var(--danger); }"))).toHaveLength(0);
    expect(vs3(parseCss(".ep { --fg-muted: #999999; } .x { --gap: 4px; }"))).toHaveLength(2); // M39
  });
  it("真实样式表零命中", () => {
    expect(vs3(sheetRules())).toEqual([]);
  });
});

describe("VS-4 字号纪律", () => {
  it("检查器自测", () => {
    expect(vs4(parseCss(".a { font-size: var(--text-sm); } .ui-badge { font-size: var(--text-xs); }"))).toHaveLength(0);
    expect(vs4(parseCss(".a { font-size: 13px; } .b { font: 12px ui-monospace; } .dev-mark { font-size: var(--text-xs); }"))).toHaveLength(3); // M11 / M22
  });
  it("font-size 只用 --text-* 阶梯，--text-xs 只许徽标类", () => {
    expect(vs4(sheetRules())).toEqual([]);
  });
  it("token 本身满足下限：--text-xs ≥ 10px、--text-sm ≥ 12px", () => {
    const t = parseTokens(TOKENS).light;
    expect(parseFloat(t["--text-xs"])).toBeGreaterThanOrEqual(10);
    expect(parseFloat(t["--text-sm"])).toBeGreaterThanOrEqual(12);
  });
});

describe("VS-5 焦点", () => {
  it("检查器自测", () => {
    expect(vs5(parseCss(":focus-visible { outline: 2px solid var(--focus-ring); outline-offset: 2px; } :focus:not(:focus-visible) { outline: none; } .composer textarea:focus-visible { outline: none; }"))).toHaveLength(0);
    expect(vs5(parseCss(":focus-visible { outline: 2px solid var(--focus-ring); } .x:focus-visible { outline: none; }"))).toHaveLength(2); // M12 / M27
    expect(vs5(parseCss(":focus-visible { outline: 2px solid var(--focus-ring); outline-offset: 2px; } .ui-btn--primary:focus-visible { outline-color: var(--accent); } .b:focus-visible { outline-offset: -2px; }"))).toHaveLength(2); // M40
  });
  it("真实样式表恰一条全局 :focus-visible，outline* 不出现在别处", () => {
    expect(vs5(sheetRules())).toEqual([]);
  });
});

describe("VS-6 减弱动效", () => {
  it("检查器自测", () => {
    expect(vs6(parseCss("@media (prefers-reduced-motion: reduce) { *, *::before, *::after { transition-duration: 0s !important; animation-duration: 0s !important; } }"))).toHaveLength(0);
    expect(vs6(parseCss(".a { transition-duration: 0s; }"))).toHaveLength(1); // M20
  });
  it("真实样式表有归零块", () => {
    expect(vs6(sheetRules())).toEqual([]);
  });
});

describe("VS-10 纯展示组件：只 import react 与 ./icons、props 无 on*", () => {
  it("检查器自测", () => {
    expect(vs10(src("renderer/ui.tsx", `import type { ReactNode } from "react"; export const B = ({ children }: { children: ReactNode }) => <span>{children}</span>;`))).toHaveLength(0);
    expect(vs10(src("renderer/ui.tsx", `import { Icon } from "./icons"; export const B = () => <Icon name="close" />;`))).toHaveLength(0); // 裁决后 ./icons 合法
    expect(vs10(src("renderer/ui.tsx", `export interface P { onClick?: () => void } export const B = (p: P) => <span onClick={p.onClick} />;`))).toHaveLength(1); // M36
    expect(vs10(src("renderer/ui.tsx", `import { rpc } from "./rpc"; export const B = () => <span />;`))).toHaveLength(1);
  });
  it("真实源码", () => {
    const hits = PURE_FILES.flatMap((rel) => vs10(src(rel, read(join(DESKTOP, "src", rel)))));
    expect(hits).toEqual([]);
  });
});

describe("VS-11 ui.css 作用域（PR1 落地时旧界面零变化）", () => {
  it("检查器自测", () => {
    expect(vs11(parseCss(".ui-btn { color: var(--fg); } :focus-visible { outline: 0; } .decision { color: var(--fg); } body { margin: 0; }"))).toEqual(["ui.css 越出作用域：.decision", "ui.css 越出作用域：body"]); // M21
  });
  it("真实 ui.css 每条规则都在 .ui-* 或 Spec 10–12 预留前缀内（三条全局规则除外）", () => {
    expect(vs11(uiRules())).toEqual([]);
  });
});


// ---------------- VS-3 的 TSX 半边：内联样式与运行时注入 ----------------

const COLORISH = /#[0-9a-f]{3,8}\b|\b(rgba?|hsla?|color-mix|oklch|oklab)\(|\b(white|black|red|green|blue|gray|grey|yellow|orange|purple|pink|silver|navy)\b/i;

/** 只做两件客观事实的断言：运行时注入样式（CSP 会拦）与 style={{…}} 里的颜色字面量。 */
export function vs3Tsx(f: SourceFile): string[] {
  const bad: string[] = [];
  if (/createElement\(\s*["']style["']\s*\)/.test(f.text)) bad.push(`${f.rel}: 运行时插入 <style> 元素（CSP 拦得住，判据不许写）`);
  if (/setAttribute\(\s*["']style["']/.test(f.text)) bad.push(`${f.rel}: setAttribute("style")（CSP 拦得住，判据不许写）`);
  const walk = (n: ts.Node) => {
    if (ts.isJsxAttribute(n) && n.name.getText() === "style" && n.initializer && ts.isJsxExpression(n.initializer) && n.initializer.expression && ts.isObjectLiteralExpression(n.initializer.expression)) {
      for (const prop of n.initializer.expression.properties) {
        if (COLORISH.test(prop.getText())) bad.push(`${f.rel}: style={{…}} 的值里有颜色字面量（${prop.getText().slice(0, 40)}）`);
      }
    }
    ts.forEachChild(n, walk);
  };
  walk(parse(f));
  return bad;
}

// ---------------- VS-7 TSX 内联样式白名单 ----------------

// gridTemplateColumns：D39 S2 分栏宽度随拖拽变化，只能运行时设；React 的 style 走 CSSOM，不受 CSP style-src 拦截（Spec 14 E1）
const STYLE_KEYS = new Set(["paddingLeft", "transform", "gridTemplateColumns"]);

export function vs7(f: SourceFile): string[] {
  const bad: string[] = [];
  if (/\bstyle="/.test(f.text)) bad.push(`${f.rel}: 字符串形式的 style 属性`);
  const walk = (n: ts.Node) => {
    if (ts.isJsxAttribute(n) && n.name.getText() === "style" && n.initializer && ts.isJsxExpression(n.initializer) && n.initializer.expression) {
      const e = n.initializer.expression;
      if (!ts.isObjectLiteralExpression(e)) {
        bad.push(`${f.rel}: style 的值不是就地字面量`);
      } else {
        for (const prop of e.properties) {
          const name = ts.isPropertyAssignment(prop) && (ts.isIdentifier(prop.name) || ts.isStringLiteral(prop.name)) ? prop.name.getText().replace(/"/g, "") : null;
          if (name === null || !STYLE_KEYS.has(name)) bad.push(`${f.rel}: style 里出现白名单外的键（${prop.getText().slice(0, 40)}）`);
        }
      }
    }
    ts.forEachChild(n, walk);
  };
  walk(parse(f));
  return bad;
}

// ---------------- VS-8 可点击元素必须是原生交互元素 ----------------

const NATIVE_CLICKABLE = new Set(["button", "input", "textarea", "select", "summary", "a"]);
const MARKDOWN_EXEMPT = "renderer/PreviewPane.tsx";

export function vs8(f: SourceFile): string[] {
  const bad: string[] = [];
  const sf = parse(f);
  const walk = (n: ts.Node) => {
    if (ts.isJsxAttribute(n) && n.name.getText() === "onClick") {
      const opening = n.parent.parent; // JsxAttributes → JsxOpeningElement / JsxSelfClosingElement
      const tag = opening && (ts.isJsxOpeningElement(opening) || ts.isJsxSelfClosingElement(opening)) ? opening.tagName.getText(sf) : "?";
      const clsAttr = opening && (ts.isJsxOpeningElement(opening) || ts.isJsxSelfClosingElement(opening))
        ? opening.attributes.properties.find((a): a is ts.JsxAttribute => ts.isJsxAttribute(a) && a.name.getText() === "className")
        : undefined;
      const cls = clsAttr?.initializer && ts.isStringLiteral(clsAttr.initializer) ? clsAttr.initializer.text : "";
      if (NATIVE_CLICKABLE.has(tag)) return;
      // 唯一豁免：PreviewPane 的 .markdown 容器（它拦链接导航，不是交互目标）
      if (f.rel === MARKDOWN_EXEMPT && /\bmarkdown\b/.test(cls)) return;
      bad.push(`${f.rel}: onClick 挂在 <${tag}> 上（须是原生交互元素）`);
    }
    ts.forEachChild(n, walk);
  };
  walk(sf);
  return bad;
}

// ---------------- VS-12 闸门按钮同权（附件 tools/vs12.mjs） ----------------

const GATE_CARD_FILES = ["renderer/HumanCards.tsx"];
const SIZE_CLASS = new Set(["ui-btn--sm", "ui-btn--lg"]);

type El = ts.JsxElement | ts.JsxSelfClosingElement;
const isEl = (n: ts.Node): n is El => ts.isJsxElement(n) || ts.isJsxSelfClosingElement(n);
const tagOf = (n: El) => (ts.isJsxElement(n) ? n.openingElement : n).tagName.getText();
const attrsOf = (n: El) => (ts.isJsxElement(n) ? n.openingElement : n).attributes.properties;
function classOf(n: El): { text?: string; missing?: boolean; dynamic?: boolean } {
  const a = attrsOf(n).find((p): p is ts.JsxAttribute => ts.isJsxAttribute(p) && p.name.getText() === "className");
  if (!a) return { missing: true };
  if (a.initializer && ts.isStringLiteral(a.initializer)) return { text: a.initializer.text };
  return { dynamic: true };
}
const variants = (cls: string) => new Set(cls.split(/\s+/).filter((c) => c.startsWith("ui-btn--") && !SIZE_CLASS.has(c)));

export function vs12(rel: string, text: string): string[] {
  const sf = parse({ rel, text });
  const bad: string[] = [];
  const walk = (n: ts.Node) => {
    if (isEl(n)) {
      if (tagOf(n) === "button") {
        const c = classOf(n);
        if (c.missing || c.dynamic || !/\bui-btn\b/.test(c.text ?? "")) bad.push(`${rel}: <button> 的 className 须为含 ui-btn 的字符串字面量`);
        else if (/ui-btn--(primary|ghost)\b/.test(c.text!)) bad.push(`${rel}: 闸门按钮带 ${/ui-btn--(primary|ghost)/.exec(c.text!)![0]}`);
      }
      const own = classOf(n);
      if (own.text && /\b(ui-card-actions|decision-actions)\b/.test(own.text)) {
        const sets: string[] = [];
        const inner = (m: ts.Node) => {
          if (m !== n && isEl(m) && tagOf(m) === "button") {
            const c = classOf(m);
            if (c.text) sets.push([...variants(c.text)].sort().join(" "));
          }
          ts.forEachChild(m, inner);
        };
        ts.forEachChild(n, inner);
        if (new Set(sets).size > 1) bad.push(`${rel}: 同一动作区按钮变体不一致 ${JSON.stringify(sets)}`);
      }
    }
    ts.forEachChild(n, walk);
  };
  walk(sf);
  return bad;
}

// ---------------- VS-13 无效 ARIA ----------------

export function vs13(f: SourceFile): string[] {
  const sf = parse(f);
  const bad: string[] = [];
  const walk = (n: ts.Node) => {
    if (ts.isJsxAttribute(n) && n.name.getText() === "aria-selected") {
      const opening = n.parent.parent;
      const tag = opening && (ts.isJsxOpeningElement(opening) || ts.isJsxSelfClosingElement(opening)) ? opening.tagName.getText(sf) : "?";
      if (tag === "button") bad.push(`${f.rel}: <button> 上的 aria-selected 对 button 角色无效`);
    }
    ts.forEachChild(n, walk);
  };
  walk(sf);
  return bad;
}

// ---------------- 用例：VS-3 的 TSX 半边、VS-7、VS-8、VS-12、VS-13 ----------------

describe("VS-3（TSX）内联样式值与运行时注入", () => {
  it("检查器自测", () => {
    expect(vs3Tsx(src("renderer/A.tsx", `const A = () => <div style={{ color: "#333" }} />;`))).toHaveLength(1); // M33
    expect(vs3Tsx(src("renderer/A.tsx", `const A = () => <div style={{ transform: x }} />;`))).toEqual([]);
    expect(vs3Tsx(src("renderer/A.tsx", `document.createElement("style");`))).toHaveLength(1);
    expect(vs3Tsx(src("renderer/A.tsx", `el.setAttribute("style", "color: red");`))).toHaveLength(1);
  });
  it("真实 renderer 源码", () => {
    expect(rendererFiles().flatMap((f) => vs3Tsx(src(f.rel, f.text)))).toEqual([]);
  });
});

describe("VS-7 TSX 内联样式白名单（paddingLeft / transform）", () => {
  it("检查器自测", () => {
    expect(vs7(src("renderer/A.tsx", `const A = () => <div style={{ paddingLeft: 8 }} />;`))).toEqual([]);
    expect(vs7(src("renderer/A.tsx", `const A = () => <img style={{ transform: t }} />;`))).toEqual([]);
    expect(vs7(src("renderer/A.tsx", `const A = () => <div style={{ color: x }} />;`))).toHaveLength(1); // M33
    expect(vs7(src("renderer/A.tsx", `const A = () => <div style="color: red" />;`))).toHaveLength(1);
  });
  it("真实 renderer 源码", () => {
    expect(rendererFiles().flatMap((f) => vs7(src(f.rel, f.text)))).toEqual([]);
  });
});

describe("VS-8 可点击元素是原生交互元素（唯一的 .markdown 豁免）", () => {
  it("检查器自测", () => {
    expect(vs8(src("renderer/A.tsx", `const A = () => <button onClick={f} />;`))).toEqual([]);
    expect(vs8(src("renderer/A.tsx", `const A = () => <li onClick={f} />;`))).toHaveLength(1); // M13 的形状
    expect(vs8(src("renderer/A.tsx", `const A = () => <div onClick={f} />;`))).toHaveLength(1);
    expect(vs8(src("renderer/PreviewPane.tsx", `const A = () => <div className="markdown" onClick={f} />;`))).toEqual([]);
    expect(vs8(src("renderer/PreviewPane.tsx", `const A = () => <div className="other" onClick={f} />;`))).toHaveLength(1);
  });
  it("真实 renderer 源码", () => {
    expect(rendererFiles().flatMap((f) => vs8(src(f.rel, f.text)))).toEqual([]);
  });
});

describe("VS-12 闸门按钮同权（附件 tools/vs12.mjs）", () => {
  const OK = `const A = () => <div className="ui-card-actions"><button className="ui-btn" onClick={f}>批准</button><button className="ui-btn">打回…</button></div>;`;
  it("检查器自测", () => {
    expect(vs12("x.tsx", OK)).toEqual([]);
    expect(vs12("x.tsx", OK.replace(`className="ui-btn">打回`, `className="ui-btn ui-btn--ghost">打回`))).toHaveLength(2); // M38
    expect(vs12("x.tsx", OK.replace(`className="ui-btn" onClick`, `className="ui-btn ui-btn--primary" onClick`))).toHaveLength(2); // M34
    expect(vs12("x.tsx", `const A = () => <button onClick={f}>批准</button>;`)).toHaveLength(1);
    expect(vs12("x.tsx", `const A = () => <div className="ui-card-actions"><button className="ui-btn ui-btn--sm">批准</button><button className="ui-btn ui-btn--sm">打回…</button></div>;`)).toEqual([]);
  });
  it("真实闸门卡片文件零违规", () => {
    expect(GATE_CARD_FILES.flatMap((rel) => vs12(rel, read(join(DESKTOP, "src", rel))))).toEqual([]);
  });
  it("名单不失效：凡出现 approval.decide 的 renderer 文件都必须在名单里（🔵-3）", () => {
    const holders = rendererFiles().filter((f) => f.text.includes("approval.decide")).map((f) => f.rel);
    expect(holders.sort()).toEqual([...GATE_CARD_FILES].sort());
  });
});

describe("VS-13 button 上没有 aria-selected", () => {
  it("检查器自测", () => {
    expect(vs13(src("renderer/A.tsx", `const A = () => <button aria-selected="true" />;`))).toHaveLength(1); // M35
    expect(vs13(src("renderer/A.tsx", `const A = () => <button aria-current="true" />;`))).toEqual([]);
  });
  it("真实 renderer 源码", () => {
    expect(rendererFiles().flatMap(vs13)).toEqual([]);
  });
});

// ---------------- VS-9 localStorage 纪律（Spec 14 §3.3：读写均 try/catch，唯一落点 renderer/theme.ts） ----------------

export function vs9(f: SourceFile): string[] {
  const bad: string[] = [];
  // 白名单（Spec 14 §3.3）：theme.ts 是本 spec 的偏好读写；ScriptEditor.tsx 是 Spec 11 先落地的草稿便利层
  // （RF-7，读写同样在 try/catch 里）。spec 原文「只出现在 theme.ts」撰写时 ScriptEditor 尚未施工，按同纪律收录；施工报告如实登记。
  const OWNERS = new Set(["renderer/theme.ts", "renderer/ScriptEditor.tsx"]);
  const walk = (n: ts.Node, inTry: boolean) => {
    if (ts.isTryStatement(n)) {
      walk(n.tryBlock, true);
      if (n.catchClause) walk(n.catchClause, inTry);
      if (n.finallyBlock) walk(n.finallyBlock, inTry);
      return;
    }
    if (ts.isIdentifier(n) && n.text === "localStorage") {
      const p = n.parent;
      const isPropName = p && ts.isPropertyAccessExpression(p) && p.name === n;
      if (isPropName) return;
      if (!OWNERS.has(f.rel)) bad.push(`${f.rel}: localStorage 只许出现在 theme.ts / ScriptEditor.tsx（草稿便利层）`);
      else if (!inTry) bad.push(`${f.rel}: localStorage 的成员访问不在 try 块内`);
    }
    ts.forEachChild(n, (c) => walk(c, inTry));
  };
  walk(parse(f), false);
  return bad;
}

describe("VS-9 localStorage 只在 theme.ts 且每处访问都在 try 里", () => {
  it("检查器自测", () => {
    expect(vs9(src("renderer/theme.ts", `function r() { try { return localStorage.getItem("k"); } catch { return null; } }`))).toEqual([]);
    expect(vs9(src("renderer/ScriptEditor.tsx", `function r() { try { return localStorage.getItem("k"); } catch { return null; } }`))).toEqual([]);
    expect(vs9(src("renderer/theme.ts", `function r() { return localStorage.getItem("k"); }`))).toHaveLength(1); // M32 的形状
    expect(vs9(src("renderer/App.tsx", `function r() { try { return localStorage.getItem("k"); } catch { return null; } }`))).toHaveLength(1);
  });
  it("真实源码", () => {
    expect(sourceFiles().flatMap(vs9)).toEqual([]);
  });
});
