// Spec 14 VE-1 判据原型：渲染后 DOM 的逐元素对比度、非文本边界与字号下限审计。测的是真实渲染结果，不是 token 表。
// 用法：webContents.executeJavaScript(本文件全文) 或 page.evaluate(同)；返回 { n, nonText, fails, small }。
// v0.2（红队 Y2）：合成祖先 opacity；.stale 子树按非活动界面守 3:1；加输入框边界与状态点两类非文本检查。
// v0.3（红队二轮 🟡-1）：只有白名单媒体容器（MEDIA）上的 background-image 才允许跳过，且计入 skippedMedia；
//   其他祖先带 background-image 一律记为 bgImage 失败（否则一层透明渐变就能让整片区域失明）。
(() => {
  const P = (s) => { const m = s.match(/rgba?\(([^)]+)\)/); if (!m) return null; const v = m[1].split(/[ ,/]+/).filter(Boolean).map(Number); return [v[0], v[1], v[2], v.length > 3 ? v[3] : 1]; };
  const over = (t, b) => [0, 1, 2].map((i) => t[i] * t[3] + b[i] * (1 - t[3])).concat(1);
  const mix = (a, b, o) => [0, 1, 2].map((i) => a[i] * o + b[i] * (1 - o)).concat(1);
  const lin = (v) => { v /= 255; return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
  const L = (c) => 0.2126 * lin(c[0]) + 0.7152 * lin(c[1]) + 0.0722 * lin(c[2]);
  const R = (a, b) => { const x = L(a), y = L(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
  const MEDIA = "video, img, .image-wrap, .cover-opt, .video-wrap";
  const rootBg = () => { const c = P(getComputedStyle(document.body).backgroundColor); return c && c[3] >= 1 ? c : [255, 255, 255, 1]; };
  // 从 el 往上找不透明底；途中遇到 opacity < 1 的祖先，记下它（其子树整体按 opacity 混到它背后的底色上）
  function backdrop(el) {
    const layers = [];
    let e = el;
    for (; e; e = e.parentElement) {
      const cs = getComputedStyle(e);
      if (cs.backgroundImage !== "none") return e.matches(MEDIA) || e.closest(MEDIA) ? { media: true } : { bgImage: e.className || e.tagName };
      const c = P(cs.backgroundColor);
      if (c && c[3] > 0) { layers.push(c); if (c[3] >= 1) break; }
    }
    let acc = rootBg();
    for (const c of layers.reverse()) acc = c[3] >= 1 ? c : over(c, acc);
    return { c: acc };
  }
  function opacityChain(el) {
    const ops = [];
    for (let e = el; e; e = e.parentElement) { const o = parseFloat(getComputedStyle(e).opacity); if (o < 1) ops.push({ e, o }); }
    return ops;
  }
  // 前景色 fg 画在 el 上：先在子树内合成，再逐层按祖先 opacity 混到该祖先背后的底色上
  function effective(el, fg) {
    const b = backdrop(el); if (b.media) return { media: true }; if (b.bgImage) return { bgImage: b.bgImage };
    let f = over(fg, b.c), g = b.c;
    for (const { e, o } of opacityChain(el)) { const behind = e.parentElement ? backdrop(e.parentElement) : { c: rootBg() }; if (behind.media) return { media: true }; if (behind.bgImage) return { bgImage: behind.bgImage }; f = mix(f, behind.c, o); g = mix(g, behind.c, o); }
    return { f, g };
  }
  const fails = [], small = [], nonText = [], bgImage = []; let n = 0, skippedMedia = 0;
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const seen = new Set();
  for (let t = walker.nextNode(); t; t = walker.nextNode()) {
    const el = t.parentElement; if (!t.textContent.trim() || seen.has(el)) continue; seen.add(el);
    if (el.closest(".mock-swatch, .mock-cell-title, .mock-note, .mock-video-caption")) continue; // 仅 mock 画布自身的标注
    const r = el.getBoundingClientRect(); if (!r.width || !r.height) continue;
    const cs = getComputedStyle(el); if (cs.visibility === "hidden" || cs.display === "none") continue;
    n++;
    const disabled = !!el.closest(":disabled, [aria-disabled=true]");
    const stale = !!el.closest(".stale");
    const fs = parseFloat(cs.fontSize), bold = parseInt(cs.fontWeight) >= 700;
    const min = stale ? 3 : fs >= 24 || (fs >= 18.66 && bold) ? 3 : 4.5;
    const eff = effective(el, P(cs.color));
    if (eff && eff.media) skippedMedia++;
    else if (eff && eff.bgImage) bgImage.push({ text: t.textContent.trim().slice(0, 30), under: String(eff.bgImage) });
    else if (eff && !disabled) { const ratio = R(eff.f, eff.g); if (ratio < min) fails.push({ text: t.textContent.trim().slice(0, 30), cls: el.className?.baseVal ?? el.className, ratio: +ratio.toFixed(2), min }); }
    const badge = !!el.closest(".ui-badge, .ui-count, .ui-kbd");
    if (fs < 10 || (fs < 12 && !badge)) small.push({ text: t.textContent.trim().slice(0, 20), cls: el.className, fs });
  }
  // 非文本：输入框边界对其所在的底、状态点对其所在的底，≥ 3:1
  for (const el of document.querySelectorAll("input.ui-input, textarea.ui-textarea, .ui-dot")) {
    const r = el.getBoundingClientRect(); if (!r.width || !r.height || el.closest(":disabled")) continue;
    const cs = getComputedStyle(el);
    const isDot = el.classList.contains("ui-dot");
    const ring = isDot && el.classList.contains("ui-dot--unknown");
    const fgc = isDot ? (ring ? P(cs.boxShadow) : P(cs.backgroundColor)) : P(cs.borderTopColor);
    if (!fgc || !el.parentElement) continue;
    const eff = effective(el.parentElement, fgc); if (!eff || eff.media) continue;
    if (eff.bgImage) { bgImage.push({ el: el.className, under: String(eff.bgImage) }); continue; }
    const ratio = R(eff.f, eff.g); n++;
    if (el.closest(".stale")) continue; // 非活动界面：WCAG 1.4.11 例外
    if (ratio < 3) nonText.push({ el: el.className, ratio: +ratio.toFixed(2), min: 3 });
  }
  return { n, fails, small, nonText, bgImage, skippedMedia };
})()
