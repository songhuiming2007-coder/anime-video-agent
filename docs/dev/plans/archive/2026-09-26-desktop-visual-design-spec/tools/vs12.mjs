// Spec 14 VS-12 判据原型（v0.3，红队二轮 🟡-3）：闸门卡片的按钮「同权」，不只是「不是主按钮」。
// 用法（在 desktop/ 下，借用其 node_modules 里的 typescript）：node <本文件> [src/renderer/DecisionBar.tsx …]；无参数只跑自测。
// 规则：闸门卡片文件（DecisionBar.tsx / HumanCards.tsx）里
//   ① 每个 <button> 的 className 必须是字符串字面量（动态写法读不到，一律判违规），且含 ui-btn；
//   ② 不许出现 ui-btn--primary / ui-btn--ghost（视觉加强或减弱都是引导）；
//   ③ 同一个动作区（className 含 ui-card-actions 或 decision-actions 的元素）内，各按钮的变体类集合（ui-btn--*，尺寸类 --sm/--lg 除外）必须完全相同。
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const ts = createRequire(join(process.cwd(), "package.json"))("typescript");
const SIZE = new Set(["ui-btn--sm", "ui-btn--lg"]);

function tagName(n) { return (ts.isJsxElement(n) ? n.openingElement : n).tagName.getText(); }
function attrs(n) { return (ts.isJsxElement(n) ? n.openingElement : n).attributes.properties; }
function classOf(n) {
  const a = attrs(n).find((p) => ts.isJsxAttribute(p) && p.name.getText() === "className");
  if (!a) return { missing: true };
  if (a.initializer && ts.isStringLiteral(a.initializer)) return { text: a.initializer.text };
  return { dynamic: true };
}
const variants = (cls) => new Set(cls.split(/\s+/).filter((c) => c.startsWith("ui-btn--") && !SIZE.has(c)));

export function vs12(rel, text) {
  const sf = ts.createSourceFile(rel, text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const bad = [];
  const isEl = (n) => ts.isJsxElement(n) || ts.isJsxSelfClosingElement(n);
  const visit = (n, fn) => { fn(n); ts.forEachChild(n, (c) => visit(c, fn)); };
  visit(sf, (n) => {
    if (!isEl(n)) return;
    if (tagName(n) === "button") {
      const c = classOf(n);
      if (c.missing || c.dynamic || !/\bui-btn\b/.test(c.text)) bad.push(`${rel}: <button> 的 className 须为含 ui-btn 的字符串字面量`);
      else if (/ui-btn--(primary|ghost)\b/.test(c.text)) bad.push(`${rel}: 闸门按钮带 ${c.text.match(/ui-btn--(primary|ghost)/)[0]}`);
    }
    const own = classOf(n);
    if (own.text && /\b(ui-card-actions|decision-actions)\b/.test(own.text)) {
      const sets = [];
      visit(n, (m) => { if (m !== n && isEl(m) && tagName(m) === "button") { const c = classOf(m); if (c.text) sets.push([...variants(c.text)].sort().join(" ")); } });
      if (new Set(sets).size > 1) bad.push(`${rel}: 同一动作区按钮变体不一致 ${JSON.stringify(sets)}`);
    }
  });
  return bad;
}

function selfTest() {
  const eq = (a, b, m) => { if (a !== b) throw new Error(`自测失败：${m}（${a} ≠ ${b}）`); };
  const ok = `const A = () => <div className="ui-card-actions"><button className="ui-btn" onClick={f}>批准</button><button className="ui-btn">打回…</button></div>;`;
  eq(vs12("x.tsx", ok).length, 0, "同权正例");
  const sm = `const A = () => <div className="ui-card-actions"><button className="ui-btn ui-btn--sm">批准</button><button className="ui-btn ui-btn--sm">打回…</button></div>;`;
  eq(vs12("x.tsx", sm).length, 0, "尺寸类不算变体");
  const ghost = ok.replace(`className="ui-btn">打回…`, `className="ui-btn ui-btn--ghost">打回…`);
  eq(vs12("x.tsx", ghost).length, 2, "打回… 改 ghost：带 ghost + 变体不一致");
  const primary = ok.replace(`className="ui-btn" onClick`, `className="ui-btn ui-btn--primary" onClick`);
  eq(vs12("x.tsx", primary).length, 2, "批准改 primary：带 primary + 变体不一致");
  const dyn = ok.replace(`className="ui-btn">打回…`, "className={cls}>打回…");
  eq(vs12("x.tsx", dyn).length, 1, "动态 className");
  const missing = `const A = () => <button onClick={f}>批准</button>;`;
  eq(vs12("x.tsx", missing).length, 1, "缺 className（现状 DecisionBar 的形态）");
  return "selfTest ok";
}

console.log(selfTest());
for (const f of process.argv.slice(2)) { const r = vs12(f, readFileSync(f, "utf-8")); console.log(f, r.length, r.slice(0, 3)); }
