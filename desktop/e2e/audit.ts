// VE-1 的判据本体（Spec 14 §7.1；判据原型是附件 tools/audit.js，逐字移植）。
// 测的是真实渲染出来的 DOM：逐元素对比度（合成祖先 opacity）、非文本边界、字号下限、背景图旁路。
// 作为 page.evaluate 的实参传入，所以必须自包含（无 import、无闭包引用）。
export function domAudit() {
  const P = (s: string) => {
    const m = s.match(/rgba?\(([^)]+)\)/);
    if (!m) return null;
    const v = m[1].split(/[ ,/]+/).filter(Boolean).map(Number);
    return [v[0], v[1], v[2], v.length > 3 ? v[3] : 1];
  };
  const over = (t: number[], b: number[]) => [0, 1, 2].map((i) => t[i] * t[3] + b[i] * (1 - t[3])).concat(1);
  const mix = (a: number[], b: number[], o: number) => [0, 1, 2].map((i) => a[i] * o + b[i] * (1 - o)).concat(1);
  const lin = (v: number) => {
    v /= 255;
    return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
  };
  const L = (c: number[]) => 0.2126 * lin(c[0]) + 0.7152 * lin(c[1]) + 0.0722 * lin(c[2]);
  const R = (a: number[], b: number[]) => {
    const x = L(a);
    const y = L(b);
    return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
  };
  const MEDIA = "video, img, .image-wrap, .cover-opt, .video-wrap";
  const rootBg = () => {
    const c = P(getComputedStyle(document.body).backgroundColor);
    return c && c[3] >= 1 ? c : [255, 255, 255, 1];
  };
  function backdrop(el: Element): { c?: number[]; media?: boolean; bgImage?: string } {
    const layers: number[][] = [];
    for (let e: Element | null = el; e; e = e.parentElement) {
      const cs = getComputedStyle(e);
      if (cs.backgroundImage !== "none") return e.matches(MEDIA) || e.closest(MEDIA) ? { media: true } : { bgImage: (e.className as string) || e.tagName };
      const c = P(cs.backgroundColor);
      if (c && c[3] > 0) {
        layers.push(c);
        if (c[3] >= 1) break;
      }
    }
    let acc = rootBg();
    for (const c of layers.reverse()) acc = c[3] >= 1 ? c : over(c, acc);
    return { c: acc };
  }
  function opacityChain(el: Element) {
    const ops: { e: Element; o: number }[] = [];
    for (let e: Element | null = el; e; e = e.parentElement) {
      const o = parseFloat(getComputedStyle(e).opacity);
      if (o < 1) ops.push({ e, o });
    }
    return ops;
  }
  function effective(el: Element, fg: number[]) {
    const b = backdrop(el);
    if (b.media) return { media: true };
    if (b.bgImage) return { bgImage: b.bgImage };
    let f = over(fg, b.c!);
    let g = b.c!;
    for (const { e, o } of opacityChain(el)) {
      const behind = e.parentElement ? backdrop(e.parentElement) : { c: rootBg() };
      if (behind.media) return { media: true };
      if (behind.bgImage) return { bgImage: behind.bgImage };
      f = mix(f, behind.c!, o);
      g = mix(g, behind.c!, o);
    }
    return { f, g };
  }
  const fails: unknown[] = [];
  const small: unknown[] = [];
  const nonText: unknown[] = [];
  const bgImage: unknown[] = [];
  let n = 0;
  let skippedMedia = 0;
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const seen = new Set<Element>();
  for (let t = walker.nextNode(); t; t = walker.nextNode()) {
    const el = t.parentElement;
    if (!el || !t.textContent!.trim() || seen.has(el)) continue;
    seen.add(el);
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height) continue;
    const cs = getComputedStyle(el);
    if (cs.visibility === "hidden" || cs.display === "none") continue;
    n++;
    const disabled = !!el.closest(":disabled, [aria-disabled=true]");
    const stale = !!el.closest(".stale");
    const fs = parseFloat(cs.fontSize);
    const bold = parseInt(cs.fontWeight) >= 700;
    const min = stale ? 3 : fs >= 24 || (fs >= 18.66 && bold) ? 3 : 4.5;
    const eff = effective(el, P(cs.color) as number[]);
    if (eff && eff.media) skippedMedia++;
    else if (eff && eff.bgImage) bgImage.push({ text: t.textContent!.trim().slice(0, 30), under: String(eff.bgImage) });
    else if (eff && !disabled) {
      const ratio = R(eff.f!, eff.g!);
      if (ratio < min) fails.push({ text: t.textContent!.trim().slice(0, 30), cls: (el.className as string) || el.tagName, ratio: +ratio.toFixed(2), min });
    }
    const badge = !!el.closest(".ui-badge, .ui-count, .ui-kbd");
    if (fs < 10 || (fs < 12 && !badge)) small.push({ text: t.textContent!.trim().slice(0, 20), cls: el.className, fs });
  }
  for (const el of Array.from(document.querySelectorAll("input.ui-input, textarea.ui-textarea, .ui-dot"))) {
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height || el.closest(":disabled")) continue;
    const cs = getComputedStyle(el);
    const isDot = el.classList.contains("ui-dot");
    const ring = isDot && el.classList.contains("ui-dot--unknown");
    const fgc = isDot ? (ring ? P(cs.boxShadow) : P(cs.backgroundColor)) : P(cs.borderTopColor);
    if (!fgc || !el.parentElement) continue;
    const eff = effective(el.parentElement, fgc);
    if (!eff || eff.media) continue;
    if (eff.bgImage) {
      bgImage.push({ el: el.className, under: String(eff.bgImage) });
      continue;
    }
    const ratio = R(eff.f!, eff.g!);
    n++;
    if (el.closest(".stale")) continue;
    if (ratio < 3) nonText.push({ el: el.className, ratio: +ratio.toFixed(2), min: 3 });
  }
  return { n, fails, small, nonText, bgImage, skippedMedia };
}
