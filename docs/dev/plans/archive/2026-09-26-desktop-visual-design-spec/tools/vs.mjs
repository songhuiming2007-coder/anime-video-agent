// Spec 14 VS-3/4/5/6/11 判据原型（CSS 层静态守卫）。用法：node vs.mjs <ui.css> <style.css>；无参数时只跑检查器自测。
// 施工时移植为 desktop/tests/static/visual.test.ts 的对应用例。TSX 层守卫（VS-7/8/10/12/13）依赖真实组件，施工时写。
import { readFileSync } from "node:fs";

/** 极简 CSS 解析：去注释；顶层规则与一层 @media 内的规则；@keyframes 整块跳过选择器检查。 */
export function parseCss(css) {
  css = css.replace(/\/\*[\s\S]*?\*\//g, "");
  const rules = [];
  let i = 0;
  const readBlock = (start) => { let d = 0; for (let j = start; j < css.length; j++) { if (css[j] === "{") d++; else if (css[j] === "}") { d--; if (d === 0) return j; } } throw new Error("unbalanced"); };
  while (i < css.length) {
    const open = css.indexOf("{", i); if (open < 0) break;
    const head = css.slice(i, open).trim(); const close = readBlock(open);
    const inner = css.slice(open + 1, close);
    if (head.startsWith("@media")) {
      for (const r of parseCss(inner)) rules.push({ ...r, media: head });
    } else if (head.startsWith("@keyframes")) {
      rules.push({ selector: head, decls: [], keyframes: true });
    } else {
      const decls = inner.split(";").map((d) => d.trim()).filter(Boolean).map((d) => { const k = d.indexOf(":"); return { prop: d.slice(0, k).trim(), value: d.slice(k + 1).trim() }; });
      rules.push({ selector: head, decls });
    }
    i = close + 1;
  }
  return rules;
}

const NAMED = /\b(white|black|red|green|blue|gray|grey|yellow|orange|purple|pink|silver|navy)\b/i;
export function vs3(rules) { // 声明值里的颜色字面量
  const bad = [];
  for (const r of rules) for (const { prop, value } of r.decls) {
    if (prop.startsWith("--")) { bad.push(`${r.selector} 定义了自定义属性 ${prop}（token 只许在 tokens.css 定义，红队二轮 B1）`); continue; }
    if (/#[0-9a-f]{3,8}\b/i.test(value) || /\b(rgba?|hsla?|color-mix|oklch|oklab)\(/i.test(value) || NAMED.test(value)) bad.push(`${r.selector} { ${prop}: ${value} }`);
  }
  return bad;
}
export function vs4(rules) { // 字号纪律
  const bad = [];
  for (const r of rules) for (const { prop, value } of r.decls) {
    if (prop === "font-size" && !/^(var\(--text-(xs|sm|base|lg|xl)\)|inherit)$/.test(value)) bad.push(`${r.selector} font-size: ${value}`);
    if (prop === "font" && /\d+px/.test(value)) bad.push(`${r.selector} font: ${value}`);
    if (/var\(--text-xs\)/.test(value) && !/ui-badge|ui-count|ui-kbd/.test(r.selector)) bad.push(`${r.selector} 用了 --text-xs`);
  }
  return bad;
}
const OUTLINE_NONE_OK = [":focus:not(:focus-visible)", ".composer textarea:focus-visible"];
export function vs5(rules) { // 焦点
  const bad = [];
  const fv = rules.filter((r) => r.selector === ":focus-visible");
  if (fv.length !== 1) bad.push(`:focus-visible 规则应恰为 1 条，实为 ${fv.length}`);
  else {
    const d = Object.fromEntries(fv[0].decls.map((x) => [x.prop, x.value]));
    if (d.outline !== "2px solid var(--focus-ring)") bad.push(`outline 应为 2px solid var(--focus-ring)，实为 ${d.outline}`);
    if (d["outline-offset"] !== "2px") bad.push(`outline-offset 应为 2px，实为 ${d["outline-offset"]}`);
  }
  for (const r of rules) for (const { prop, value } of r.decls) {
    if (!prop.startsWith("outline")) continue;
    if (r.selector === ":focus-visible") continue;
    const ok = prop === "outline" && /^(none|0)$/.test(value) && r.selector.split(",").every((s) => OUTLINE_NONE_OK.includes(s.trim()));
    if (!ok) bad.push(`${r.selector} ${prop}: ${value}（outline* 只许在全局 :focus-visible 与两条豁免里，红队二轮 B1）`);
  }
  return bad;
}
export function vs6(rules) { // 减弱动效
  const rm = rules.filter((r) => r.media && /prefers-reduced-motion:\s*reduce/.test(r.media));
  const decls = rm.flatMap((r) => r.decls.map((d) => `${d.prop}:${d.value.replace(/\s*!important/, "")}`));
  const ok = decls.includes("transition-duration:0s") && decls.includes("animation-duration:0s");
  return ok ? [] : ["缺 prefers-reduced-motion 归零块"];
}
const UI_SCOPE = /^(\.ui-|\.conv|\.dock|\.composer|\.session-head|\.toolbar|\.editor-split|\.checklist|\.seg-row|\.cover-)/;
const UI_GLOBALS = [":focus-visible", ":focus:not(:focus-visible)", "*, *::before, *::after"];
export function vs11(rules) { // ui.css 作用域：PR1 落地时旧界面零变化
  const bad = [];
  for (const r of rules) {
    if (r.keyframes) continue;
    const globalOk = UI_GLOBALS.includes(r.selector) && (r.selector !== "*, *::before, *::after" || /prefers-reduced-motion/.test(r.media ?? ""));
    if (globalOk) continue;
    for (const s of r.selector.split(",")) if (!UI_SCOPE.test(s.trim())) bad.push(`ui.css 越出作用域：${s.trim()}`);
  }
  return bad;
}

// ---- 检查器自测（正反例；施工时照搬为 vitest 的「检查器自测」用例） ----
function selfTest() {
  const t = (css) => parseCss(css);
  const eq = (a, b, m) => { if (a !== b) throw new Error(`自测失败：${m}（${a} ≠ ${b}）`); };
  eq(vs3(t(".a { white-space: nowrap; color: var(--fg); background: transparent; }")).length, 0, "vs3 white-space 与 transparent 放行");
  eq(vs3(t(".a { color: #333; } .b { border: 1px solid white; } .c { background: rgba(0,0,0,.1); }")).length, 3, "vs3 三类字面量");
  eq(vs3(t(".banner-red { color: var(--danger); }")).length, 0, "vs3 选择器里的 red 不算");
  eq(vs3(t(".ep { --fg-muted: #999999; } .x { --gap: 4px; }")).length, 2, "vs3 tokens.css 以外的自定义属性（B1 ①）");
  eq(vs4(t(".a { font-size: var(--text-sm); } .ui-badge { font-size: var(--text-xs); }")).length, 0, "vs4 正例");
  eq(vs4(t(".a { font-size: 13px; } .b { font: 12px ui-monospace; } .dev-mark { font-size: var(--text-xs); }")).length, 3, "vs4 反例");
  eq(vs5(t(":focus-visible { outline: 2px solid var(--focus-ring); outline-offset: 2px; } :focus:not(:focus-visible) { outline: none; } .composer textarea:focus-visible { outline: none; }")).length, 0, "vs5 正例");
  eq(vs5(t(":focus-visible { outline: 2px solid var(--focus-ring); } .x:focus-visible { outline: none; }")).length, 2, "vs5 缺 offset + 越权 none");
  eq(vs5(t(":focus-visible { outline: 2px solid var(--focus-ring); outline-offset: 2px; } .ui-btn--primary:focus-visible { outline-color: var(--accent); } .b:focus-visible { outline-offset: -2px; }")).length, 2, "vs5 单组件覆盖焦点环（B1 ②）");
  eq(vs6(t("@media (prefers-reduced-motion: reduce) { *, *::before, *::after { transition-duration: 0s !important; animation-duration: 0s !important; } }")).length, 0, "vs6 正例");
  eq(vs6(t(".a { transition-duration: 0s; }")).length, 1, "vs6 反例");
  eq(vs11(t(".ui-btn { color: var(--fg); } :focus-visible { outline: 0; } .decision { color: var(--fg); } body { margin: 0; }")).length, 2, "vs11 反例两条");
  return "selfTest ok";
}

console.log(selfTest());
if (process.argv[3]) {
  const ui = parseCss(readFileSync(process.argv[2], "utf-8")), st = parseCss(readFileSync(process.argv[3], "utf-8"));
  const all = [...ui, ...st];
  const res = { vs3: vs3(all), vs4: vs4(all), vs5: vs5(all), vs6: vs6(all), vs11: vs11(ui) };
  for (const [k, v] of Object.entries(res)) console.log(k, v.length, v.slice(0, 3));
}
