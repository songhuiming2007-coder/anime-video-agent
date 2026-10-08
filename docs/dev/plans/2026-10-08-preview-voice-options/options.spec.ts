// D49 / D50 界面原型（附件，不属常规 e2e；只在人要复现选型证据时手动跑）。
// 未打包构建 × 临时夹具仓（绝不指向真实 data/）× 1440×900 / 1280×800。原型是运行时注入的 DOM + 样式，
// 只用现有设计系统的类（ui-btn / ui-seg / ui-row / ui-popover / ui-badge / ui-input / ui-section）与 token，不是实现。
import { writeFileSync } from "node:fs";
import { join } from "node:path";
import { test } from "../../../../desktop/node_modules/@playwright/test";
import { launch } from "../../../../desktop/e2e/fixtures";
import { cleanup } from "../../../../desktop/tests/helpers";
import { EP025, EP035, OUT, fixture, openEp, shoot } from "./shared";

type V = "49A" | "49A-open" | "49B" | "49C" | "50A" | "50B" | "50C";

const SEGS = [
  { l: "1", d: "0:03", t: "搜查官死后，二十区的血腥暂时平息。妹妹搬来和她同住，她学着像个姐姐那样帮妹妹修剪头发，在店里和同事斗嘴争吵。" },
  { l: "2", d: "0:04", t: "看着妹妹捡回来的受伤雏鸟，她站在窗边出神。当年父亲也曾带着他们姐弟救过这样一只小鸟，叮嘱她作为姐姐要好好教导弟弟。" },
  { l: "3", d: "0:05", t: "为了守住高中生的普通日常，她强忍着生理上的排斥，硬生生把朋友送来的便当咽进胃里。即使吃人类食物会损伤身体，她也不愿放弃那一点温度。" },
  { l: "4", d: "0:06", t: "但这层勉强维系的宁静很快被暴风雨撕碎。激进组织为了追查一个失踪的人，一路搜到二十区。踹开咖啡店大门的，竟然就是她朝思暮想的弟弟。" },
  { l: "5", d: "0:07", t: "几年未见的重逢没有半分温情。弟弟当众将她按在地上，嘲笑她和人类混在一起过家家太软弱，甚至唾骂死去的父亲是个懦夫。" },
  { l: "6", d: "0:08", t: "至亲的践踏与失去同伴的无力，像耳光抽在她脸上。咖啡店构筑的避风港一夜崩塌，她第一次意识到，自己什么都保护不了。" },
];

function applyProto(arg: { v: V; segs: typeof SEGS; ep: string }) {
  const { v, segs, ep } = arg;
  const q = (s: string) => document.querySelector(s) as HTMLElement;
  const mk = (tag: string, cls: string, html = "") => {
    const e = document.createElement(tag);
    e.className = cls;
    e.innerHTML = html;
    return e;
  };
  const css = `
  .p-head { display: flex; align-items: center; gap: var(--space-2); min-height: 44px; padding: 0 var(--space-3); border-bottom: 1px solid var(--border); flex: none; }
  .p-switch { display: inline-flex; align-items: center; gap: var(--space-1); max-width: 260px; font-weight: var(--weight-semibold); }
  .p-switch .p-tt { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .p-sp { flex: 1; }
  .p-sync { color: var(--fg-muted); font-size: var(--text-sm); white-space: nowrap; }
  .p-body { flex: 1; min-height: 0; overflow: auto; padding: var(--space-3) var(--space-4); }
  .p-src { font-family: var(--font-mono); font-size: var(--text-sm); line-height: 1.7; white-space: pre-wrap; }
  .p-src .ln { color: var(--fg-subtle); display: inline-block; width: 2.2em; user-select: none; }
  .p-src .h { color: var(--accent-strong); font-weight: var(--weight-semibold); }
  .p-md h3 { margin: var(--space-3) 0 var(--space-1); font-size: var(--text-base); }
  .p-md p { margin: 0 0 var(--space-2); line-height: var(--leading-normal); }
  .p-md .meta { color: var(--fg-muted); font-size: var(--text-sm); }
  .p-pop { position: absolute; top: 44px; left: var(--space-3); width: 340px; z-index: 5; display: flex; flex-direction: column; gap: 2px; }
  .p-grp { padding: var(--space-1) var(--space-2) 2px; color: var(--fg-muted); font-size: var(--text-xs); font-weight: var(--weight-semibold); }
  .p-pop .ui-row { min-height: 30px; gap: var(--space-2); }
  .p-pop .ui-row .p-r { margin-left: auto; color: var(--fg-muted); font-size: var(--text-xs); }
  .p-pop .p-dim { color: var(--fg-subtle); }
  .p-cols { display: grid; gap: 0; flex: 1; min-height: 0; }
  .p-cols > * { min-height: 0; overflow: auto; padding: var(--space-3) var(--space-4); border-right: 1px solid var(--border); }
  .p-cols > *:last-child { border-right: 0; }
  .p-tips ol { margin: 0; padding-left: 1.2em; font-size: var(--text-sm); line-height: var(--leading-normal); color: var(--fg-muted); }
  .p-tipbar { display: flex; align-items: center; gap: var(--space-2); padding: var(--space-1) var(--space-4); border-bottom: 1px solid var(--border); font-size: var(--text-sm); color: var(--fg-muted); }
  .p-overlay { position: fixed; z-index: 20; display: flex; flex-direction: column; background: var(--bg-panel); border: 1px solid var(--border); border-radius: var(--radius-xl); }
  .p-mini { display: flex; flex-direction: column; height: 100%; }
  .p-chatbody { display: flex; flex-direction: column; gap: var(--space-3); }
  .p-head .ui-seg > button, .p-fix .ui-seg > button { white-space: nowrap; }
  .p-comp { margin: var(--space-3); padding: var(--space-2) var(--space-3); border: 1px solid var(--border-strong); border-radius: var(--radius-xl); color: var(--fg-subtle); font-size: var(--text-sm); min-height: 56px; }
  .p-seg { display: grid; grid-template-columns: 28px minmax(0,1fr); gap: var(--space-2); padding: var(--space-2) var(--space-3); border-bottom: 1px solid var(--border); }
  .p-seg[aria-current="true"] { background: var(--bg-selected); }
  .p-seg .p-segh { display: flex; align-items: center; gap: var(--space-2); font-size: var(--text-sm); color: var(--fg-muted); }
  .p-seg .p-segt { line-height: var(--leading-normal); margin-top: 2px; }
  .p-mark { background: var(--warn-surface); box-shadow: inset 0 -2px 0 var(--warn); border-radius: 2px; }
  .p-sel { background: var(--bg-selected); box-shadow: 0 0 0 1px var(--accent-strong); border-radius: 2px; }
  .p-fix { position: relative; margin: var(--space-2) 0 0; padding: var(--space-2) var(--space-3); display: grid; grid-template-columns: auto 1fr; gap: var(--space-2) var(--space-3); align-items: center; font-size: var(--text-sm); }
  .p-fix .ok { color: var(--ok); }
  .p-foot { flex: none; display: flex; align-items: center; gap: var(--space-2); padding: var(--space-2) var(--space-3); border-top: 1px solid var(--border); background: var(--bg-subtle); font-size: var(--text-sm); }
  .p-bigt { font-size: var(--text-lg); line-height: 1.9; padding: var(--space-4) var(--space-6); }
  .p-steps { display: flex; gap: 4px; padding: var(--space-2) var(--space-4); }
  .p-steps span { flex: 1; height: 6px; border-radius: var(--radius-pill); background: var(--border); }
  .p-steps span.ok { background: var(--ok); } .p-steps span.bad { background: var(--warn); } .p-steps span.cur { background: var(--accent-strong); }
  .p-card { flex: none; border: 1px solid var(--border); border-radius: var(--radius-xl); background: var(--bg-panel); overflow: hidden; display: flex; flex-direction: column; }
  .p-cols2 { grid-template-columns: 1fr 1fr; } .p-cols3 { grid-template-columns: 1.1fr 1fr 0.55fr; }
  .p-w120 { width: 120px; } .p-flush { padding: 0; } .p-padx { padding: var(--space-3) var(--space-6) 0; } .p-wfull { width: 100%; }
  .p-src .p-line { display: grid; grid-template-columns: 2.4em minmax(0, 1fr); }
  .p-rate { display: flex; align-items: center; gap: var(--space-2); flex-wrap: wrap; font-size: var(--text-sm); }
  `;
  const sheet = new CSSStyleSheet();
  sheet.replaceSync(css);
  document.adoptedStyleSheets = [...document.adoptedStyleSheets, sheet];

  const preview = q("#ava-preview");
  const clearPreview = () => {
    for (const c of [...preview.children]) if (!(c as HTMLElement).dataset.testid?.startsWith("timeline")) (c as HTMLElement).style.display = "none";
  };
  const timeline = preview.querySelector("[data-testid=timeline]");
  const putPreview = (el: HTMLElement) => {
    clearPreview();
    if (timeline) preview.insertBefore(el, timeline);
    else preview.appendChild(el);
    el.style.flex = "1";
    el.style.minHeight = "0";
    el.style.display = "flex";
    el.style.flexDirection = "column";
    el.style.position = "relative";
  };

  const script = segs
    .map((s, i) => {
      const L = (n: number, body: string) => `<div class="p-line"><span class="ln">${n}</span><span>${body}</span></div>`;
      return L(i * 8 + 1, `<span class="h">## 段落 ${s.l}</span>`) + L(i * 8 + 2, "") + L(i * 8 + 3, `配音：${s.t}`) + L(i * 8 + 4, "") +
        L(i * 8 + 5, "画面：") + L(i * 8 + 6, `  锚点: S01E0${5 + (i % 4)} 0${3 + i}:1${i}`) + L(i * 8 + 7, "");
    })
    .join("");
  const rendered = segs.map((s, i) => `<h3>段落 ${s.l}</h3><p>${s.t}</p><p class="meta">锚点 S01E0${5 + (i % 4)} 0${3 + i}:1${i}</p>`).join("");
  const tips = `<div class="ui-section">人审要点</div><ol><li>编辑判断立不立得住：反方立场像不像真人在说话</li><li>事实核验：台词出处、说话人、关键集号与数字是否真实</li><li>去模型味：删去油腻拔高、翻案腔与套路排比</li><li>人物：对照机检 INFO 行逐段扫一眼</li><li>说话人断言抽帧核对</li></ol>`;
  const switcher = (name: string, tag: string) =>
    `<button class="ui-btn ui-btn--ghost ui-btn--sm p-switch" aria-haspopup="listbox"><span class="p-tt">${name}</span> <span class="ui-badge">${tag}</span> ▾</button>`;

  if (v === "49A" || v === "49A-open") {
    // A：编辑器留在预览栏。标题即文件切换器；窄时「源码 / 渲染 / 要点」三选一，宽时源码与渲染并排（截图为窄态）
    const box = mk("div", "p-mini");
    box.innerHTML =
      `<div class="p-head">${switcher("02-script.md", "定稿")}<span class="p-sp"></span>` +
      `<span class="ui-seg"><button aria-pressed="true">源码</button><button>渲染</button><button>要点</button></span>` +
      `<button class="ui-btn ui-btn--sm">保存</button><button class="ui-btn ui-btn--sm">封板</button></div>` +
      `<div class="p-tipbar">已与磁盘同步 · agent 改稿后自动刷新（你有未保存改动时改为提示，不覆盖）</div>` +
      `<div class="p-body p-src">${script}</div>`;
    putPreview(box);
    if (v === "49A-open") {
      const pop = mk("div", "ui-popover p-pop");
      pop.innerHTML =
        `<div class="ui-popover-title">本期文件 · ${ep.slice(11)}</div>` +
        `<div class="p-grp">稿件</div>` +
        `<button class="ui-row">01-topic.md</button>` +
        `<button class="ui-row">02-script.draft.md<span class="p-r">草稿 · 已冻结</span></button>` +
        `<button class="ui-row" aria-current="true">02-script.md<span class="p-r">✎ 可在此编辑</span></button>` +
        `<div class="p-grp">配音</div>` +
        `<button class="ui-row">03-audio<span class="p-r"><span class="ui-badge ui-badge--wait">生成中 4/6</span></span></button>` +
        `<div class="p-grp">排片与成片</div>` +
        `<button class="ui-row p-dim" disabled>04-clips.json<span class="p-r">未生成</span></button>` +
        `<button class="ui-row p-dim" disabled>05-final.mp4<span class="p-r">未生成</span></button>` +
        `<div class="p-grp">其他</div>` +
        `<button class="ui-row">events.jsonl</button><button class="ui-row">_agent/ …</button>`;
      box.appendChild(pop);
    }
  } else if (v === "49B") {
    // B：编辑 02-script.md 时编辑器占中栏（源码 | 渲染并排，要点收成一行可展开），对话挪到右栏
    const center = q("[data-testid=center]");
    for (const c of [...center.children]) (c as HTMLElement).style.display = "none";
    const ed = mk("div", "p-mini");
    ed.innerHTML =
      `<div class="p-head">${switcher("02-script.md", "定稿")}<span class="p-sp"></span><span class="p-sync">已与磁盘同步</span>` +
      `<button class="ui-btn ui-btn--sm">保存</button><button class="ui-btn ui-btn--sm">封板</button><button class="ui-btn ui-btn--ghost ui-btn--sm">退出编辑</button></div>` +
      `<div class="p-tipbar">▸ 人审要点（5 条） · 编辑判断 / 事实核验 / 去模型味 / 人物 / 说话人</div>` +
      `<div class="p-cols p-cols2"><div class="p-src">${script}</div><div class="p-md">${rendered}</div></div>`;
    center.appendChild(ed);
    const chat = mk("div", "p-mini");
    chat.innerHTML =
      `<div class="p-head"><span class="ui-row-title">对话</span><span class="ui-badge">编辑时移到这里</span></div>` +
      `<div class="p-body p-chatbody"><div class="conv-user">段落 3 改成当面咽下去</div><div class="conv-assistant">已改段落 3（卡上有 diff），机检 1541 字。</div></div>` +
      `<div class="p-comp">告诉 ava 要做什么…</div>`;
    putPreview(chat);
  } else if (v === "49C") {
    // C：全宽专注编辑：盖住中栏与预览栏，三栏（源码 | 渲染 | 要点）都够宽；Esc 回到对话
    const c = q("[data-testid=center]").getBoundingClientRect();
    const p = preview.getBoundingClientRect();
    const ov = mk("div", "p-overlay");
    Object.assign(ov.style, { left: `${c.left}px`, top: `${c.top}px`, right: `${window.innerWidth - p.right}px`, bottom: `${window.innerHeight - p.bottom}px` });
    ov.innerHTML =
      `<div class="p-head">${switcher("02-script.md", "定稿")}<span class="p-sync">已与磁盘同步</span><span class="p-sp"></span>` +
      `<button class="ui-btn ui-btn--sm">保存</button><button class="ui-btn ui-btn--sm">封板</button><button class="ui-btn ui-btn--ghost ui-btn--sm">返回对话 <span class="ui-kbd">Esc</span></button></div>` +
      `<div class="p-cols p-cols3"><div class="p-src">${script}</div><div class="p-md">${rendered}</div><div class="p-tips">${tips}</div></div>`;
    document.body.appendChild(ov);
  } else {
    // ---- D50：03.5 顺听与纠错。三案共同：去掉旧音频队列（只留一套播放器）；停机卡的终端打点行改为卡内打点 ----
    const rate = `<div class="p-rate">顺听打点：音色 <span class="ui-seg"><button>1</button><button>2</button><button>3</button><button aria-pressed="true">4</button><button>5</button></span>` +
      ` 韵律 <span class="ui-seg"><button>1</button><button>2</button><button aria-pressed="true">3</button><button>4</button><button>5</button></span>` +
      ` 错读 <span class="ui-seg"><button>1</button><button>2</button><button>3</button><button aria-pressed="true">4</button><button>5</button></span></div>`;
    const card = q("[data-testid=decision][data-stop='03.5']");
    if (card) {
      for (const m of [...card.querySelectorAll(".muted")]) if ((m as HTMLElement).textContent?.includes("终端")) (m as HTMLElement).style.display = "none";
      card.querySelector(".ui-card-actions")?.insertAdjacentElement("beforebegin", mk("div", "", rate));
    }
    const segText = (s: (typeof segs)[number], mark?: string, sel?: string) => {
      let t = s.t;
      if (mark) t = t.replace(mark, `<span class="p-mark">${mark}</span>`);
      if (sel) t = t.replace(sel, `<span class="p-sel">${sel}</span>`);
      return t;
    };
    const fixBox = (word: string, py: string, segLabel: string) =>
      `<div class="ui-popover p-fix">` +
      `<span>「${word}」应读</span><span><input class="ui-input p-w120" value="${py}"> <span class="ok">✓ 读音核对通过</span></span>` +
      `<span>改法</span><span class="ui-seg"><button aria-pressed="true">同音字</button><button>拼音直注</button></span>` +
      `<span>同音字</span><span><input class="ui-input p-w120" value="姐递"> <span class="muted">jie3 di4</span></span>` +
      `<span>范围</span><span class="ui-seg"><button aria-pressed="true">本段 ${segLabel}</button><button>本期</button><button>全局</button></span>` +
      `<span></span><span><button class="ui-btn ui-btn--sm ui-btn--primary">记下</button> <button class="ui-btn ui-btn--ghost ui-btn--sm">取消</button> <span class="muted">记下后段落标黄，攒够一起重配</span></span>` +
      `</div>`;
    const foot = `<div class="p-foot"><span class="ui-badge ui-badge--warn">待重配 2 段</span><span>段 2「姐弟」· 段 5「懦夫」</span><span class="p-sp"></span>` +
      `<button class="ui-btn ui-btn--sm">只重配这 2 段（本地）</button></div>`;
    const head = `<div class="p-head">${switcher("03-audio", "6 段 · 0:33")}<span class="muted">qwen3_tts · 本地</span><span class="p-sp"></span>` +
      `<button class="ui-btn ui-btn--sm">▶ 顺序播放</button><button class="ui-btn ui-btn--ghost ui-btn--sm">停</button></div>`;

    if (v === "50A") {
      // A：预览区一张段落表，选中文字即弹「这里读错了」小卡；底部常驻「待重配」条
      const box = mk("div", "p-mini");
      box.innerHTML = head +
        `<div class="p-body p-flush">` +
        segs.map((s, i) =>
          `<div class="p-seg" ${i === 1 ? 'aria-current="true"' : ""}><button class="ui-btn ui-btn--sm ui-btn--icon">▶</button><div>` +
          `<div class="p-segh">段 ${s.l} · ${s.d}${i === 4 ? ' <span class="ui-badge ui-badge--warn">1 处待重配</span>' : ""}${i === 1 ? ' <span class="ui-badge ui-badge--accent">播放中</span>' : ""}</div>` +
          `<div class="p-segt">${segText(s, i === 4 ? "懦夫" : undefined, i === 1 ? "姐弟" : undefined)}</div>` +
          (i === 1 ? fixBox("姐弟", "jie3 di4", s.l) : "") +
          `</div></div>`).join("") +
        `</div>` + foot;
      putPreview(box);
    } else if (v === "50B") {
      // B：在对话里顺听：agent 配完把段落表作为一条消息发出来，就地播放、就地标错；预览区只放波形与文件
      const stream = q("[data-testid=conv-stream]");
      const msg = mk("div", "p-card");
      msg.innerHTML = `<div class="p-head"><span class="ui-row-title">配音完成 · 6 段 · 0:33</span><span class="muted">qwen3_tts · 本地</span><span class="p-sp"></span><button class="ui-btn ui-btn--sm">▶ 顺序播放</button></div>` +
        segs.slice(0, 4).map((s, i) =>
          `<div class="p-seg" ${i === 1 ? 'aria-current="true"' : ""}><button class="ui-btn ui-btn--sm ui-btn--icon">▶</button><div><div class="p-segh">段 ${s.l} · ${s.d}</div>` +
          `<div class="p-segt">${segText(s, undefined, i === 1 ? "姐弟" : undefined)}</div>${i === 1 ? fixBox("姐弟", "jie3 di4", s.l) : ""}</div></div>`).join("") +
        `<div class="p-seg"><span></span><span class="muted">… 还有 2 段 · 展开</span></div>` + foot;
      stream.appendChild(msg);
      stream.scrollTop = stream.scrollHeight;
      const side = mk("div", "p-mini");
      side.innerHTML = `<div class="p-head">${switcher("03-audio", "6 段")}</div><div class="p-body p-chatbody"><div class="audio-wrap"><audio controls class="p-wfull"></audio></div><div class="muted">段 2 · seg-02.wav · 0:04</div></div>`;
      putPreview(side);
    } else {
      // C：逐段顺听：一次只看一段，大字、上一段/下一段、「这段没问题」一键过；顶部进度条标出听过、有错的段
      const box = mk("div", "p-mini");
      const s = segs[1];
      box.innerHTML = head +
        `<div class="p-steps"><span class="ok"></span><span class="cur"></span><span></span><span></span><span class="bad"></span><span></span></div>` +
        `<div class="p-body p-flush"><div class="p-segh p-padx">段 2 / 6 · 0:04 · 播放中</div>` +
        `<div class="p-bigt">${segText(s, undefined, "姐弟")}</div><div class="p-padx">${fixBox("姐弟", "jie3 di4", s.l)}</div></div>` +
        `<div class="p-foot"><button class="ui-btn ui-btn--sm">← 上一段</button><button class="ui-btn ui-btn--sm">↻ 重听</button>` +
        `<button class="ui-btn ui-btn--sm ui-btn--primary">这段没问题 <span class="ui-kbd">Space</span></button><button class="ui-btn ui-btn--sm">下一段 →</button>` +
        `<span class="p-sp"></span><span class="ui-badge ui-badge--warn">待重配 2 段</span><button class="ui-btn ui-btn--sm">只重配</button></div>`;
      putPreview(box);
    }
  }
}

/** 底线量测：注入元素有没有出视口（不用来证明好用）。 */
function overflowCheck() {
  const out: unknown[] = [];
  for (const p of [...document.querySelectorAll(".p-mini, .p-overlay, .p-pop, .p-fix, .p-foot, .p-card")] as HTMLElement[]) {
    const r = p.getBoundingClientRect();
    if (r.width === 0) continue;
    if (r.right > window.innerWidth + 0.5 || r.left < -0.5) out.push({ kind: "横向出视口", cls: p.className, r: [r.left, r.right].map(Math.round) });
    if (p.scrollWidth > p.clientWidth + 1) out.push({ kind: "内部横向溢出", cls: p.className, sw: p.scrollWidth, cw: p.clientWidth });
  }
  return out;
}

test("D49 / D50 原型：同一夹具，各方案两个尺寸", async () => {
  const repo = fixture();
  const report: Record<string, unknown> = {};
  try {
    for (const v of ["49A", "49A-open", "49B", "49C", "50A", "50B", "50C"] as V[]) {
      const L = await launch(repo);
      try {
        await L.page.waitForSelector("[data-testid=episode]");
        const ep = v.startsWith("49") ? EP025 : EP035;
        await openEp(L.page, ep);
        await L.page.waitForSelector(v.startsWith("49") ? "[data-testid=script-editor]" : "[data-testid=voice-panel]", { timeout: 20_000 }).catch(() => undefined);
        if (v === "50A" || v === "50C" || v === "49B" || v === "49C") {
          // 做视频时的常态是收起侧栏（D49 ④）
          await L.page.getByTestId("toggle-left").click();
        }
        await L.page.setViewportSize({ width: 1440, height: 900 });
        await L.page.evaluate(applyProto, { v, segs: SEGS, ep });
        await shoot(L.page, `proto-${v}`);
        report[v] = await L.page.evaluate(overflowCheck);
      } finally {
        await L.app.close().catch(() => undefined);
      }
    }
  } finally {
    writeFileSync(join(OUT, "report.json"), JSON.stringify(report, null, 1));
    cleanup(repo);
  }
});
