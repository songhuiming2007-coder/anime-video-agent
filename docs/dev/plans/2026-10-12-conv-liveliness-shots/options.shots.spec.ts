// D80 PR-B：R2 工具行 / R3 运行动感 / R4 回合折叠 三套方案的真实窗口截图（未打包构建 × 临时夹具仓，绝不指向真实 data/）。
// 真实 app + 真实 core + 假模型跑三轮真实工具调用；「现状」不改 DOM，A / B / C 只把工具类行（tool / log / card / injection）
// 换成原型 DOM，用户消息、答复、页脚、停机点行一律是真实渲染。选型用，人选定后按 spec 施工，本脚本不进产品代码。
//   A 平铺（照 ZCode）：强扫光（强调色、2s）；折叠行是一行灰字；展开后过程挂一条细竖线
//   B 卡片收纳：每轮过程装进浅底框，折叠后框收成一行；弱扫光（前景灰阶、4s）
//   C 时间线竖轨：过程挂在竖轨上、状态用圆点；文字不扫光，只有运行中的圆点呼吸
// 注意：PR-C 落地后「现状」列就是方案 A 的真实界面（原型注入假定的是旧 DOM，A/B/C 列会与新界面叠在一起）；
// 选型时的现状见 git 历史里的本脚本与当时的产物。落地后的真实截图用同目录 impl.shots.spec.ts。
// 时长说明：假模型瞬时返回，回合与工具的真实耗时都 < 1s；折叠行的「已工作 …」与个别工具耗时是示意值（DEMO_*），截图说明里写明。
// 动图：扫光是动画，静图看不出强弱。逐帧暂停全部动画并设 currentTime，确定性取帧，再用 ffmpeg 合成 gif。
// 用法（在 desktop/ 下）：
//   PYTHONDONTWRITEBYTECODE=1 npx playwright test -c ../docs/dev/plans/2026-10-12-conv-liveliness-shots/shots.config.ts -g "PR-B"
// 产物：desktop/out/d80-shots/pr-b/（index.html 是对照页）
import { execFileSync } from "node:child_process";
import { mkdirSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test } from "../../../../desktop/node_modules/@playwright/test";
import type { Page } from "../../../../desktop/node_modules/@playwright/test";
import { assistant, startFakeLlm, toolCalls } from "../../../../desktop/e2e/fakeLlm";
import { convSnapshot, launchSession, realCoreFixture, stubQuit } from "../../../../desktop/e2e/sessionFixtures";
import { foldConv } from "../../../../desktop/src/shared/convFold";
import type { ConvRow, ToolRow } from "../../../../desktop/src/shared/convFold";
import type { ConvEntry } from "../../../../desktop/src/shared/protocol";

const OUT = join(__dirname, "../../../../desktop/out/d80-shots/pr-b");
const EP = "第12期-命运石之门人物志";
const KEY = `ep:${EP}`;
const SCHEMES = ["now", "A", "B", "C"] as const;
type Scheme = (typeof SCHEMES)[number];
const SCHEME_LABEL: Record<Scheme, string> = { now: "现状", A: "A 平铺（照 ZCode）", B: "B 卡片收纳", C: "C 时间线竖轨" };

// bash 的 description 目前不进 tool 帧（spec R2 ① 才加），原型按命令查回模型给的 description
const DESC: Record<string, string> = {
  "ls -la": "列出期目录",
  "wc -m 02-script.md": "统计稿件字数",
  "head -3 01-topic.md": "看选题开头",
  "ls _agent": "列出留底目录",
  "cat 旧稿-v1.md": "读取旧稿",
  "head -5 02-script.md": "读稿件开头",
  "sleep 30": "等待机检作业",
};
const DEMO_TURN = ["1分12秒", "38 秒"]; // 示意：已结束回合的「已工作 …」
const DEMO_RUNNING_SECS = 14; // 示意：运行中回合的「工作中 N 秒」
const DEMO_TOOL_MS: Record<string, number> = { "wc -m 02-script.md": 3200 }; // 示意：演示 ≥1s 才显示耗时

const CATEGORY: Record<string, string> = {
  bash: "终端", read_artifact: "读取", read_status: "读取", list_episodes: "读取",
  write_episode_file: "写入", write_note: "写入", write_memory: "写入", cover_edit: "写入",
  run_pipeline: "流水线", web_search: "搜索", search_notes: "搜索",
  web_fetch: "网页", crawl: "网页", browser: "网页", acquire_propose: "素材",
};

type PTool = { id: string; cat: string; icon: string; main: string; sub: string; status: "ok" | "fail" | "run"; dur: string; command: string; output: string; observation: string; internal: string };
type PItem = { kind: "tool"; tool: PTool } | { kind: "group"; id: string; tools: PTool[]; fails: number };
type PTurn = { items: PItem[]; done: boolean; tools: number; fails: number; label: string };

function ptool(r: ToolRow, id: string): PTool {
  const cmd = r.command ?? "";
  const status = r.ok === null ? "run" : r.ok ? "ok" : "fail";
  const ms = DEMO_TOOL_MS[cmd] ?? r.durationMs ?? 0;
  return {
    id, cat: CATEGORY[r.name] ?? r.name,
    icon: r.name === "bash" ? "term" : CATEGORY[r.name] === "读取" ? "file" : CATEGORY[r.name] === "写入" ? "pen" : CATEGORY[r.name] === "网页" ? "globe" : CATEGORY[r.name] === "搜索" ? "search" : "stack",
    main: r.name === "bash" ? (DESC[cmd] ?? r.summary) : r.name === "read_status" ? "期状态" : r.summary,
    sub: r.name === "bash" ? cmd : "",
    status, dur: status !== "run" && ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : "",
    command: cmd, output: r.logs.join("\n"), observation: r.observation ?? "", internal: r.name,
  };
}

/** 回合 = 一条 user 行起、到该回合 footer 止；连续 ≥2 条 bash 折成一组（spec R2 ⑦ / R4 ①） */
function turnsOf(rows: ConvRow[]): PTurn[] {
  const turns: { tools: ToolRow[]; done: boolean }[] = [];
  for (const r of rows) {
    if (r.k === "user") turns.push({ tools: [], done: false });
    else if (r.k === "tool" && turns.length) turns[turns.length - 1].tools.push(r);
    else if (r.k === "footer" && turns.length) turns[turns.length - 1].done = true;
  }
  let demo = 0;
  return turns.map((t, ti) => {
    const items: PItem[] = [];
    const ts = t.tools.map((r, i) => ptool(r, `t${ti}-${i}`));
    for (let i = 0; i < ts.length; ) {
      let j = i;
      while (j < ts.length && ts[j].internal === "bash") j++;
      if (j - i >= 2) {
        const g = ts.slice(i, j);
        items.push({ kind: "group", id: `g${ti}-${i}`, tools: g, fails: g.filter((x) => x.status === "fail").length });
        i = j;
      } else {
        items.push({ kind: "tool", tool: ts[i] });
        i++;
      }
    }
    const fails = ts.filter((x) => x.status === "fail").length;
    return { items, done: t.done, tools: ts.length, fails, label: t.done ? `已工作 ${DEMO_TURN[demo++] ?? "1 秒"}` : `工作中 ${DEMO_RUNNING_SECS} 秒` };
  });
}

/** 在页面里执行：清掉上一套原型，按 scheme 注入（自包含，不引用外部符号） */
function apply(doc: Document, a: { scheme: string; turns: PTurn[]; open: { turns: number[]; groups: string[]; details: string[] }; hover: string | null }): void {
  doc.querySelectorAll(".pb-root").forEach((n) => n.remove());
  // 渲染端 CSP 是 style-src 'self'：内联 <style> 会被拦，改用 constructable stylesheet（CSSOM，不受该指令约束）
  doc.adoptedStyleSheets = doc.adoptedStyleSheets.filter((sh) => !(sh as unknown as { pb?: boolean }).pb);
  doc.querySelectorAll(".pb-hidden").forEach((n) => n.classList.remove("pb-hidden"));
  const stream = doc.querySelector<HTMLElement>("[data-testid=conv-stream]")!;
  stream.removeAttribute("data-pb");
  doc.querySelectorAll<HTMLDetailsElement>("[data-testid=conv-observation]").forEach((d) => (d.open = false));
  if (a.scheme === "now") return;
  stream.setAttribute("data-pb", a.scheme);
  const SVG: Record<string, string> = {
    term: '<rect x="1.75" y="2.75" width="12.5" height="10.5" rx="1.5"/><path d="m4.5 6 2 2-2 2M8.5 10.5h3"/>',
    file: '<path d="M4 1.75h5.25L12.5 5v9.25H4z"/><path d="M9 1.75V5.25h3.5"/>',
    pen: '<path d="m10.5 2.5 3 3-8 8H2.5v-3z"/>',
    globe: '<circle cx="8" cy="8" r="6"/><path d="M2 8h12M8 2c2.2 2.2 2.2 9.8 0 12M8 2c-2.2 2.2-2.2 9.8 0 12"/>',
    search: '<circle cx="7" cy="7" r="4.5"/><path d="m10.5 10.5 3.5 3.5"/>',
    stack: '<path d="M2 4.5h12M2 8h12M2 11.5h12"/>',
    chev: '<path d="M6 3.5 10.5 8 6 12.5"/>',
    copy: '<rect x="5.25" y="5.25" width="8.5" height="8.5" rx="1.5"/><path d="M10.75 5.25v-2a1 1 0 0 0-1-1h-6.5a1 1 0 0 0-1 1v6.5a1 1 0 0 0 1 1h2"/>',
    check: '<path d="m3 8.5 3 3 7-7"/>',
    alert: '<path d="M8 1.75 14.5 13.5h-13z"/><path d="M8 6v3.5M8 11.25v.5"/>',
  };
  const svg = (n: string) => `<svg class="pb-svg" width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${SVG[n]}</svg>`;
  const esc = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const ST: Record<string, string> = { ok: "已执行", fail: "执行失败", run: "执行中" };
  const C = a.scheme === "C";

  const detail = (t: PTool) => `
    <div class="pb-detail">
      ${t.command ? `<div class="pb-cmd"><code>${esc(t.command)}</code><span class="pb-copy">${svg("copy")}</span></div>` : `<div class="pb-lbl">${esc(t.internal)}</div>`}
      ${t.output ? `<div class="pb-lbl">输出</div><pre class="pb-pre">${esc(t.output)}</pre>` : ""}
      ${t.observation ? `<div class="pb-lbl">模型看到的原文</div><pre class="pb-pre">${esc(t.observation)}</pre>` : ""}
    </div>`;
  const toolRow = (t: PTool, inGroup = false) => {
    const open = a.open.details.includes(t.id);
    const lead = C ? `<span class="pb-dot" data-st="${t.status}"></span>` : `<span class="pb-ico">${svg(t.icon)}</span>`;
    const st = C && t.status === "ok" ? "" : `<span class="pb-st" data-st="${t.status}">${ST[t.status]}</span>`;
    return `<div class="pb-tool${a.hover === t.id ? " pb-hover" : ""}" data-st="${t.status}" data-open="${open ? 1 : 0}" title="${esc(t.internal)}">
      <div class="pb-row">${lead}${inGroup ? "" : `<span class="pb-cat${t.status === "run" && !C ? " pb-shim" : ""}">${esc(t.cat)}</span>`}<span class="pb-main${inGroup && t.status === "run" && !C ? " pb-shim" : ""}">${esc(t.main)}</span>${t.sub && t.sub !== t.main ? `<span class="pb-sub">${esc(t.sub)}</span>` : ""}<span class="pb-sp"></span>${t.command && !open ? `<span class="pb-copy">${svg("copy")}</span>` : ""}${t.dur ? `<span class="pb-dur">${t.dur}</span>` : ""}${st}<span class="pb-chev">${svg("chev")}</span></div>
      ${open ? detail(t) : ""}
    </div>`;
  };
  const item = (it: PItem) => {
    if (it.kind === "tool") return toolRow(it.tool);
    const open = a.open.groups.includes(it.id);
    const running = it.tools.some((t) => t.status === "run");
    const st = running ? "run" : it.fails ? "fail" : "ok";
    const word = running ? "执行中" : it.fails ? `${it.fails} 个失败` : "全部成功";
    const lead = C ? `<span class="pb-dot" data-st="${st}"></span>` : `<span class="pb-ico">${svg("term")}</span>`;
    return `<div class="pb-group" data-open="${open ? 1 : 0}">
      <div class="pb-row">${lead}<span class="pb-cat">终端</span><span class="pb-main">${it.tools.length} 个命令</span><span class="pb-sp"></span>${C && st === "ok" ? "" : `<span class="pb-st" data-st="${st}">${word}</span>`}<span class="pb-chev">${svg("chev")}</span></div>
      ${open ? `<div class="pb-children">${it.tools.map((t) => toolRow(t, true)).join("")}</div>` : ""}
    </div>`;
  };
  const turnHtml = (t: PTurn, i: number) => {
    const open = !t.done || t.fails > 0 || a.open.turns.includes(i); // R4.3：有失败的回合不自动折叠
    const dots = C ? `<span class="pb-dots">${t.items.flatMap((it) => (it.kind === "tool" ? [it.tool] : it.tools)).map((x) => `<i data-st="${x.status}"></i>`).join("")}</span>` : "";
    const icon = a.scheme === "B" && t.done ? `<span class="pb-ico" data-st="${t.fails ? "fail" : "ok"}">${svg(t.fails ? "alert" : "check")}</span>` : "";
    const meta = `${t.tools} 个工具${t.fails ? ` · <span class="pb-failmeta">${t.fails} 个失败</span>` : ""}`;
    const act = t.done ? "" : `<div class="pb-act"><span class="${C ? "" : "pb-shim"}">正在执行</span><span class="pb-act-secs">· 3 秒</span></div>`;
    return `<div class="pb-root pb-turn" data-open="${open ? 1 : 0}" data-done="${t.done ? 1 : 0}">
      <div class="pb-fold">${icon}${dots}<span class="pb-fold-label${t.done || C ? "" : " pb-shim"}">${t.label}</span><span class="pb-fold-meta">· ${meta}</span><span class="pb-chev">${svg("chev")}</span></div>
      ${open ? `<div class="pb-body">${t.items.map(item).join("")}${act}</div>` : ""}
    </div>`;
  };

  const css = `
  [data-pb] .pb-hidden, [data-pb] > .conv-activity { display: none !important; }
  .pb-svg { width: 14px; height: 14px; flex: none; display: block; }
  .pb-turn { display: flex; flex-direction: column; font-size: var(--text-sm); }
  .pb-fold { display: flex; align-items: center; gap: var(--space-2); color: var(--fg-muted); cursor: pointer; min-height: 24px; }
  .pb-fold-meta { color: var(--fg-subtle); }
  .pb-failmeta { color: var(--danger); }
  .pb-chev { color: var(--fg-subtle); display: inline-flex; transition: transform var(--dur-base) var(--ease); }
  [data-open="1"] > .pb-fold .pb-chev, [data-open="1"] > .pb-row .pb-chev { transform: rotate(90deg); }
  .pb-row { display: flex; align-items: center; gap: var(--space-2); min-height: 26px; min-width: 0; }
  .pb-ico { color: var(--fg-muted); display: inline-flex; }
  .pb-cat { color: var(--fg); font-weight: var(--weight-medium); flex: none; }
  .pb-main { color: var(--fg-muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; min-width: 0; }
  .pb-sub { color: var(--fg-subtle); font-family: var(--font-mono); font-size: var(--text-xs); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; min-width: 0; flex: 1 1 0; }
  .pb-sp { flex: 1 1 0; }
  .pb-sub + .pb-sp { flex: 0 0 0; }
  .pb-dur { color: var(--fg-subtle); font-size: var(--text-xs); font-variant-numeric: tabular-nums; }
  .pb-st { font-size: var(--text-xs); color: var(--fg-subtle); flex: none; }
  .pb-st[data-st="fail"] { color: var(--danger); text-decoration: underline dashed; text-underline-offset: 3px; }
  .pb-st[data-st="run"] { color: var(--fg); }
  .pb-copy { color: var(--fg-subtle); display: inline-flex; opacity: 0; }
  .pb-hover > .pb-row { background: var(--bg-hover); border-radius: var(--radius-sm); }
  .pb-hover .pb-copy { opacity: 1; }
  .pb-detail { display: flex; flex-direction: column; gap: var(--space-1); margin: var(--space-1) 0 var(--space-2) 22px; }
  .pb-cmd { display: flex; align-items: flex-start; gap: var(--space-2); padding: var(--space-2) var(--space-3); background: var(--bg-subtle); border: 1px solid var(--border); border-radius: var(--radius-md); }
  .pb-cmd code { flex: 1; font-family: var(--font-mono); font-size: var(--text-xs); white-space: pre-wrap; overflow-wrap: anywhere; color: var(--fg); }
  .pb-cmd .pb-copy { opacity: 1; }
  .pb-lbl { color: var(--fg-subtle); font-size: var(--text-xs); margin-top: var(--space-1); }
  .pb-pre { margin: 0; padding: var(--space-2) var(--space-3); background: var(--bg-subtle); border-radius: var(--radius-md); font-family: var(--font-mono); font-size: var(--text-xs); white-space: pre-wrap; overflow-wrap: anywhere; color: var(--fg-muted); max-height: 160px; overflow: auto; }
  .pb-act { display: flex; gap: var(--space-2); color: var(--fg-muted); min-height: 26px; align-items: center; }
  .pb-act-secs { color: var(--fg-subtle); font-variant-numeric: tabular-nums; }
  @keyframes pb-shim { from { background-position: 100% 0; } to { background-position: -100% 0; } }
  @keyframes pb-breathe { 0%, 100% { opacity: 1; transform: scale(1); } 50% { opacity: .35; transform: scale(.7); } }

  /* ---- A 平铺（照 ZCode）：强扫光 ---- */
  [data-pb="A"] .pb-body { margin: var(--space-1) 0 0 6px; padding-left: var(--space-3); border-left: 1px solid var(--border); display: flex; flex-direction: column; }
  [data-pb="A"] .pb-children { margin-left: 6px; padding-left: var(--space-3); border-left: 1px solid var(--border); }
  [data-pb="A"] .pb-shim { background: linear-gradient(90deg, var(--fg-muted) 0%, var(--fg-muted) 35%, var(--accent) 50%, var(--fg-muted) 65%, var(--fg-muted) 100%); background-size: 200% 100%; -webkit-background-clip: text; background-clip: text; color: transparent; animation: pb-shim 2s linear infinite; }

  /* ---- B 卡片收纳：弱扫光 ---- */
  [data-pb="B"] .pb-turn { background: var(--bg-subtle); border: 1px solid var(--border); border-radius: var(--radius-lg); padding: var(--space-1) var(--space-3); }
  [data-pb="B"] .pb-fold .pb-ico[data-st="ok"] { color: var(--ok); }
  [data-pb="B"] .pb-fold .pb-ico[data-st="fail"] { color: var(--danger); }
  [data-pb="B"] .pb-body { border-top: 1px solid var(--border); margin-top: var(--space-1); padding-top: var(--space-1); display: flex; flex-direction: column; }
  [data-pb="B"] .pb-group[data-open="1"] { background: var(--bg-card); border: 1px solid var(--border); border-radius: var(--radius-md); padding: 0 var(--space-2); margin: 2px 0; }
  [data-pb="B"] .pb-children { border-top: 1px solid var(--border); }
  [data-pb="B"] .pb-detail { margin-left: 22px; }
  [data-pb="B"] .pb-cmd, [data-pb="B"] .pb-pre { background: var(--bg-card); }
  [data-pb="B"] .pb-shim { background: linear-gradient(90deg, var(--fg-subtle) 0%, var(--fg-subtle) 30%, var(--fg) 50%, var(--fg-subtle) 70%, var(--fg-subtle) 100%); background-size: 200% 100%; -webkit-background-clip: text; background-clip: text; color: transparent; animation: pb-shim 4s linear infinite; }

  /* ---- C 时间线竖轨：无扫光，圆点呼吸 ---- */
  [data-pb="C"] .pb-body, [data-pb="C"] .pb-children { position: relative; margin-left: 4px; padding-left: 18px; display: flex; flex-direction: column; }
  [data-pb="C"] .pb-body::before, [data-pb="C"] .pb-children::before { content: ""; position: absolute; left: 3px; top: 6px; bottom: 6px; width: 1px; background: var(--border-strong); }
  [data-pb="C"] .pb-tool > .pb-row, [data-pb="C"] .pb-group > .pb-row { position: relative; }
  [data-pb="C"] .pb-dot { position: absolute; left: -18px; width: 7px; height: 7px; border-radius: 50%; background: var(--fg-subtle); box-shadow: 0 0 0 3px var(--bg-panel); }
  [data-pb="C"] .pb-dot[data-st="fail"] { background: var(--danger); }
  [data-pb="C"] .pb-dot[data-st="run"] { background: var(--wait); animation: pb-breathe 1.6s ease-in-out infinite; }
  [data-pb="C"] .pb-cat { color: var(--fg-muted); }
  [data-pb="C"] .pb-main { color: var(--fg); }
  [data-pb="C"] .pb-dots { display: inline-flex; gap: 3px; }
  [data-pb="C"] .pb-dots i { width: 5px; height: 5px; border-radius: 50%; background: var(--fg-subtle); }
  [data-pb="C"] .pb-dots i[data-st="fail"] { background: var(--danger); }
  [data-pb="C"] .pb-dots i[data-st="run"] { background: var(--wait); animation: pb-breathe 1.6s ease-in-out infinite; }
  [data-pb="C"] .pb-detail { margin-left: 0; }
  `;
  const sheet = new CSSStyleSheet();
  sheet.replaceSync(css);
  (sheet as unknown as { pb?: boolean }).pb = true;
  doc.adoptedStyleSheets = [...doc.adoptedStyleSheets, sheet];

  // 按真实 DOM 的 user 行切回合：user 行之后、下一条 answer / footer / user 之前的工具类行隐藏，原型插在 user 行之后
  const kids = Array.from(stream.children) as HTMLElement[];
  let ti = -1;
  let anchor: HTMLElement | null = null;
  const PROCESS = new Set(["tool", "log", "card", "injection"]);
  for (const el of kids) {
    const kind = el.getAttribute("data-kind");
    if (kind === "user") {
      ti++;
      anchor = el;
      if (a.turns[ti]) el.insertAdjacentHTML("afterend", turnHtml(a.turns[ti], ti));
    } else if (kind && PROCESS.has(kind) && anchor) el.classList.add("pb-hidden");
    else if (kind === "notice" && el.getAttribute("data-code") === "code_freeze") el.classList.add("pb-hidden");
  }
}

async function useScheme(p: Page, scheme: Scheme, turns: PTurn[], open: { turns: number[]; groups: string[]; details: string[] }, hover: string | null = null): Promise<void> {
  await p.evaluate(`(${apply.toString()})(document, ${JSON.stringify({ scheme, turns, open, hover })})`);
  if (scheme === "now" && open.details.length) {
    // 现状的「展开细节」= 把失败工具的原生 <details> 打开
    await p.evaluate(() => document.querySelectorAll<HTMLDetailsElement>("[data-ok=fail] [data-testid=conv-observation]").forEach((d) => (d.open = true)));
  }
  await p.getByTestId("conv-stream").evaluate((el) => { el.scrollTop = el.scrollHeight; });
  await p.waitForTimeout(250);
}

async function shotCenter(p: Page, name: string): Promise<void> {
  await p.getByTestId("center").screenshot({ path: join(OUT, `${name}.png`), animations: "allow" });
}

/** 确定性取帧：暂停全部动画并设 currentTime，逐帧截图，ffmpeg 合成 gif */
async function gif(p: Page, name: string, ms = 4000, fps = 15): Promise<void> {
  const dir = join(OUT, `_frames-${name}`);
  rmSync(dir, { recursive: true, force: true });
  mkdirSync(dir, { recursive: true });
  const n = Math.round((ms / 1000) * fps);
  for (let i = 0; i < n; i++) {
    await p.evaluate((t) => document.getAnimations().forEach((an) => { an.pause(); an.currentTime = t; }), (i * 1000) / fps);
    await p.getByTestId("center").screenshot({ path: join(dir, `${String(i).padStart(3, "0")}.png`), animations: "allow" });
  }
  await p.evaluate(() => document.getAnimations().forEach((an) => an.play()));
  execFileSync("ffmpeg", ["-y", "-loglevel", "error", "-framerate", String(fps), "-i", join(dir, "%03d.png"),
    "-vf", "split[s0][s1];[s0]palettegen=max_colors=128[p];[s1][p]paletteuse=dither=none", "-loop", "0", join(OUT, `${name}.gif`)]);
  rmSync(dir, { recursive: true, force: true });
}

test("PR-B 方案截图：现状 / A / B / C", async () => {
  test.setTimeout(300_000);
  rmSync(OUT, { recursive: true, force: true });
  mkdirSync(OUT, { recursive: true });
  const llm = await startFakeLlm();
  const fx = realCoreFixture(llm.url, [EP]);
  const L = await launchSession(fx.repo, [], { previewOpen: false });
  const p = L.page;
  const send = async (t: string) => { await p.getByTestId("composer-input").fill(t); await p.getByTestId("composer-input").press("Enter"); };
  const turnsDone = (n: number) => expect.poll(async () => (await convSnapshot(L, KEY)).entries.filter((e) => e.k === "frame" && e.frame?.t === "turn_finished").length, { timeout: 30_000 }).toBeGreaterThanOrEqual(n);
  const model = async () => turnsOf(foldConv((await convSnapshot(L, KEY)).entries as unknown as ConvEntry[]).rows);
  const bash = (command: string) => ({ name: "bash", args: { command, description: DESC[command] } });
  try {
    await p.setViewportSize({ width: 1440, height: 900 });
    await p.locator("[data-testid=episode]").first().click();
    // 夹具仓不是干净 git 树，Code Freeze 横幅是夹具伪影，隐藏（D71 同法）
    await p.evaluate(() => document.querySelectorAll<HTMLElement>(".banner").forEach((b) => { if (b.textContent?.includes("Code Freeze")) b.style.display = "none"; }));

    // 回合 1：全部成功 → 方案里默认折叠
    llm.push(
      toolCalls({ name: "read_status", args: {} }),
      toolCalls(bash("ls -la"), bash("wc -m 02-script.md")),
      toolCalls({ name: "read_artifact", args: { path: "01-topic.md" } }),
      assistant("## 当前进度\n\n这期停在 **05 审时间码**，稿件字数正常：\n\n- 总字数在上限内\n- `人物:` 标记齐全\n\n下一步需要你在审看页过一遍时间码。"),
    );
    await send("这期走到哪了？顺便看下字数");
    await turnsDone(1);
    // S0 一轮全部成功：回合 1 自动折叠成一行
    const first = await model();
    for (const s of SCHEMES) { await useScheme(p, s, first, { turns: [], groups: [], details: [] }); await shotCenter(p, `s0-ok-${s}`); }
    // 回合 2：有失败 → 方案里不自动折叠，失败所在的终端组默认展开
    llm.push(
      toolCalls(bash("ls _agent"), bash("cat 旧稿-v1.md"), bash("head -5 02-script.md")),
      assistant("旧稿 `旧稿-v1.md` 不在期目录里，`_agent/` 下也没有留底。如果你记得放在哪，告诉我路径；否则只能拿现在的 `02-script.md` 对比。"),
    );
    await send("把旧稿找出来跟现在的对比一下");
    await turnsDone(2);
    const done = await model();
    const failGroups = done.flatMap((t) => t.items.filter((it) => it.kind === "group" && it.fails > 0).map((it) => it.id));
    const failTool = done.flatMap((t) => t.items.flatMap((it) => (it.kind === "tool" ? [it.tool] : it.tools))).find((x) => x.status === "fail")!.id;

    // S1 完成态：1440 浅色（整窗 + 对话区）、1440 深色、1280 浅色
    for (const s of SCHEMES) {
      await useScheme(p, s, done, { turns: [], groups: failGroups, details: [] });
      await p.screenshot({ path: join(OUT, `s1-window-${s}.png`) });
      await shotCenter(p, `s1-light-${s}`);
    }
    await p.emulateMedia({ colorScheme: "dark" });
    for (const s of SCHEMES) { await useScheme(p, s, done, { turns: [], groups: failGroups, details: [] }); await shotCenter(p, `s1-dark-${s}`); }
    await p.emulateMedia({ colorScheme: "light" });
    await p.setViewportSize({ width: 1280, height: 800 });
    for (const s of SCHEMES) {
      await useScheme(p, s, done, { turns: [], groups: failGroups, details: [] });
      const overflow = await p.getByTestId("conv-stream").evaluate((el) => el.scrollWidth > el.clientWidth);
      expect(overflow, `${s} 在 1280 宽下横向溢出`).toBe(false);
      await shotCenter(p, `s1-1280-${s}`);
    }
    await p.setViewportSize({ width: 1440, height: 900 });

    // S2 展开细节：回合 1 手动展开 + 失败工具展开（命令全文 / 原文）+ 悬停态（复制图标）
    for (const s of SCHEMES) {
      await useScheme(p, s, done, { turns: [0], groups: failGroups, details: [failTool] }, failTool);
      await shotCenter(p, `s2-detail-${s}`);
    }

    // S3 运行中：回合 3 的 bash sleep 在跑
    llm.push(toolCalls({ name: "read_status", args: {} }), toolCalls(bash("sleep 30")), assistant("机检跑完了。"));
    await send("跑一下机检");
    await expect(p.getByTestId("conv-tool-spinner")).toHaveCount(1, { timeout: 20_000 });
    const running = await model();
    for (const s of SCHEMES) {
      await useScheme(p, s, running, { turns: [], groups: failGroups, details: [] });
      await shotCenter(p, `s3-running-${s}`);
      await gif(p, `s3-running-${s}`);
    }

    writeFileSync(join(OUT, "index.html"), indexHtml());
  } finally {
    await stubQuit(L, "quit").catch(() => undefined);
    await L.app.close().catch(() => undefined);
    await llm.close();
    fx.cleanup();
  }
});

function indexHtml(): string {
  const row = (title: string, note: string, file: (s: Scheme) => string) => `
  <h2>${title}</h2><p>${note}</p>
  <div class="grid">${SCHEMES.map((s) => `<figure><figcaption>${SCHEME_LABEL[s]}</figcaption><a href="${file(s)}"><img src="${file(s)}" loading="lazy"></a></figure>`).join("")}</div>`;
  return `<!doctype html><meta charset="utf-8"><title>D80 PR-B 方案对照</title>
<style>
  body { font: 14px/1.6 -apple-system, "PingFang SC", sans-serif; margin: 16px 24px; background: #f6f6f4; color: #222; }
  h1 { font-size: 20px; } h2 { font-size: 16px; margin-top: 32px; }
  p { color: #555; max-width: 1100px; }
  .grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }
  figure { margin: 0; } figcaption { font-weight: 600; margin-bottom: 4px; }
  img { width: 100%; border: 1px solid #ddd; border-radius: 6px; background: #fff; }
  ul { color: #444; }
</style>
<h1>D80 PR-B：工具行 / 运行动感 / 回合折叠 方案对照</h1>
<ul>
  <li><b>A 平铺（照 ZCode）</b>：图标 + 类别词 + 主文本 + 状态词 + ›；折叠行是一行灰字；展开后过程挂细竖线；<b>强扫光</b>（强调色，2 秒一周）。</li>
  <li><b>B 卡片收纳</b>：每轮过程装进浅底框，折叠后框收成一行（带 ✓ / ⚠）；终端组展开成内嵌小框；<b>弱扫光</b>（灰阶，4 秒一周，ZCode 原值）。</li>
  <li><b>C 时间线竖轨</b>：过程挂在竖轨上，状态用圆点，成功的不写状态词；折叠行前一串小圆点；<b>文字不扫光</b>，只有运行中的圆点呼吸。</li>
  <li>三套共用：bash 主文本用模型给的用途、命令放次文本；失败只染状态词（虚线下划线 = 悬停看错误首行）；耗时 &lt; 1s 不显示；复制命令悬停才出现。</li>
</ul>
<p>真实 app + 真实 core + 假模型，三轮真实工具调用；原型只替换工具类行，用户消息、答复、页脚、停机点是真实渲染（遥测页脚与停机点重复是 R5，本轮不选）。<b>时长是示意值</b>：假模型瞬时返回，「已工作 1分12秒 / 38 秒」「工作中 14 秒」「3.2s」都是写死的演示数。Code Freeze 横幅与 code_freeze 提示是夹具仓伪影，已隐藏。点图看原尺寸。</p>
${row("⓪ 一轮全部成功 · 1440×900", "回合 1 的 4 个工具全部成功 → 结束时自动折叠成一行「已工作 … · 4 个工具」，答复留在外面。", (s) => `s0-ok-${s}.png`)}
${row("① 有失败的一轮 · 1440×900 浅色", "回合 2 有失败 → 不自动折叠，失败所在的终端组默认展开（组内子行不再重复「终端」）。回合 1 已折叠在上方（滚出视野）。", (s) => `s1-light-${s}.png`)}
${row("② 完成态 · 深色", "同上，系统深色。", (s) => `s1-dark-${s}.png`)}
${row("③ 完成态 · 1280×800", "窄窗口下是否挤、是否横向溢出（脚本已断言不溢出）。", (s) => `s1-1280-${s}.png`)}
${row("④ 展开细节", "手动展开回合 1；失败工具展开（命令全文 → 模型看到的原文），并处于悬停态（复制图标出现）。", (s) => `s2-detail-${s}.png`)}
${row("⑤ 运行中 · 动图", "回合 3 正在跑 sleep：看扫光 / 呼吸的强弱。动图为原尺寸。", (s) => `s3-running-${s}.gif`)}
${row("⑥ 运行中 · 静图", "同一时刻的原尺寸静图。", (s) => `s3-running-${s}.png`)}
${row("整窗 · 1440×900", "放回整窗看比例。", (s) => `s1-window-${s}.png`)}
`;
}
