// D39 布局实测探针 + 方案原型（附件，不属常规 e2e；只在人要复现证据时手动跑）：未打包构建 × 临时夹具仓 × 4 种视口 × 关键状态，截图 + 几何量测。
// 不用任何键盘输入（发送一律 fill + 点击「发送」按钮）；夹具全部在系统临时目录，绝不指向真实 data/。
import { mkdirSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test } from "../../../../desktop/node_modules/@playwright/test";
import type { Page } from "../../../../desktop/node_modules/@playwright/test";
import { fingerprintOf, launchSession, pendingObj, sessionFixture, stubQuit, writeStore } from "../../../../desktop/e2e/sessionFixtures";
import { py } from "../../../../desktop/tests/helpers";
import { fixtureWrite, sessionKey, sessionScript } from "../../../../desktop/tests/fixtures/session";

const OUT = join(__dirname, "../../../../desktop/out/d39-shots"); // 已被 .gitignore 排除
const SIZES: [number, number][] = [
  [1280, 800],
  [1440, 900],
  [1470, 923], // 人 2026-09-27 截图的内容区（2940×1912 物理像素 ÷2，扣除顶部 45/1301 标题区）
  [1728, 1117],
];

const LONG_OBS =
  "退出码 1：python -m pipeline.clips /private/var/folders/8f/_04djfns4_l70r2r7pgy4ghm0000gn/T/ava-desktop-repo-Hw3ak8/data/episodes/SESS-A/02-script.md\n" +
  "Traceback (most recent call last):\n  File \"/private/var/folders/8f/_04djfns4_l70r2r7pgy4ghm0000gn/T/ava-desktop-repo-Hw3ak8/pipeline/clips.py\", line 412, in resolve_segment\n" +
  "KeyError: '集' 字段缺失（第 3 段）";
const ANSWER =
  "我看了 04-clips.json，第 3 段的「集」字段缺失，导致排片时回退到全季检索。建议先在 02-script.md 第 3 段补上 `集: S01E07`，然后重跑 `python -m pipeline.clips SESS-A`。另外第 7 段的查询写的是画面构图（「夕阳下两人背影」），字幕索引检索不到，按 AGENTS.md 应改写成台词语义。";

const READY = (ep: string, over: Record<string, unknown> = {}) => ({
  op: "emit",
  frame: { t: "ready", episode: ep, scope: "pipeline", continue_status: "new", llm: "ok", degrade_reason: null, code_freeze_ok: true, history_count: 0, session_bytes: 0, other_sessions: [], ...over },
});
const TURN = [
  { t: "turn_started", turn_id: "$turn", rid: "$rid" },
  { t: "tool", turn_id: "$turn", phase: "start", index: 0, name: "read_artifact", summary: "SESS-A/04-clips.json", ok: null, observation: null, duplicate: false },
  { t: "tool", turn_id: "$turn", phase: "end", index: 0, name: "read_artifact", summary: "", ok: true, observation: null, duplicate: false },
  { t: "tool", turn_id: "$turn", phase: "start", index: 1, name: "run_pipeline", summary: "clips SESS-A", ok: null, observation: null, duplicate: false },
  { t: "tool", turn_id: "$turn", phase: "end", index: 1, name: "run_pipeline", summary: "", ok: false, observation: LONG_OBS, duplicate: false },
  { t: "assistant", turn_id: "$turn", kind: "answer", text: ANSWER },
  { t: "turn_finished", turn_id: "$turn", stopped: "done", llm_calls: 2, tool_calls: 2, tool_executions: 2, duplicates_rejected: 0, checkpoints: 0, wrapup: "none", duration_s: 3, prompt_chars: 1 },
  { t: "stop_points", items: [], turn_id: "$turn" },
];

/** 页内量测：各栏几何 + 文本片段级的重叠 / 裁切 / 横向滚动 / 省略。 */
function measure() {
  const R = (r: DOMRect) => ({ x: Math.round(r.left), y: Math.round(r.top), w: Math.round(r.width), h: Math.round(r.height) });
  const q = (s: string) => document.querySelector(s) as HTMLElement | null;
  const box = (s: string) => {
    const e = q(s);
    return e ? { ...R(e.getBoundingClientRect()), cw: e.clientWidth, sw: e.scrollWidth, ch: e.clientHeight, sh: e.scrollHeight } : null;
  };
  const cols: Record<string, unknown> = {};
  for (const [k, s] of Object.entries({
    topbar: ".topbar", main: ".main", left: ".left", center: ".center", preview: ".preview", timeline: ".timeline",
    status: "[data-testid=status]", humanTime: "[data-testid=human-time]", sessionHead: "[data-testid=session-head]",
    stream: "[data-testid=conv-stream]", dock: "[data-testid=decisions]", composer: ".composer", previewHead: "[data-testid=preview-head]",
    editor: "[data-testid=script-editor]", checklist: "[data-testid=review-checklist]",
  })) cols[k] = box(s);

  const label = (el: Element): string => {
    const parts: string[] = [];
    for (let e: Element | null = el; e && e !== document.body && parts.length < 3; e = e.parentElement) {
      const t = e.getAttribute("data-testid");
      const c = (e.getAttribute("class") ?? "").trim().split(/\s+/).filter(Boolean).slice(0, 2).join(".");
      parts.push(`${e.tagName.toLowerCase()}${c ? "." + c : ""}${t ? `[${t}]` : ""}`);
      if (t) break;
    }
    return parts.join(" < ");
  };
  type Frag = { id: number; node: Text; el: HTMLElement; r: DOMRect; vis: { l: number; t: number; r: number; b: number } | null; text: string; lab: string };
  const frags: Frag[] = [];
  const clipped: unknown[] = [];
  const hscroll = new Map<string, unknown>();
  const ellipsis: unknown[] = [];
  const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let n: Node | null;
  let id = 0;
  while ((n = w.nextNode())) {
    const t = n as Text;
    if (!t.textContent || !t.textContent.trim()) continue;
    const el = t.parentElement;
    if (!el || el.closest("script,style,template,[popover]:not(:popover-open)")) continue;
    const cs = getComputedStyle(el);
    if (cs.visibility === "hidden" || el.getClientRects().length === 0) continue;
    const range = document.createRange();
    range.selectNodeContents(t);
    for (const r of [...range.getClientRects()].filter((x) => x.width > 0.5 && x.height > 0.5)) {
      // 逐级与裁剪祖先求交；最近一个非 visible 的越界祖先决定性质：auto/scroll = 可滚动到；hidden/clip = 裁切（不可达）
      let vis = { l: r.left, t: r.top, r: r.right, b: r.bottom };
      let decided = false;
      const ell = getComputedStyle(el).textOverflow === "ellipsis";
      for (let a: HTMLElement | null = el; a; a = a.parentElement) {
        const acs = getComputedStyle(a);
        const ox = acs.overflowX, oy = acs.overflowY;
        if (ox === "visible" && oy === "visible") continue;
        const ab = a.getBoundingClientRect();
        const cl = { l: ab.left + a.clientLeft, t: ab.top + a.clientTop, r: ab.left + a.clientLeft + a.clientWidth, b: ab.top + a.clientTop + a.clientHeight };
        const outX = r.left < cl.l - 0.5 || r.right > cl.r + 0.5;
        const outY = r.top < cl.t - 0.5 || r.bottom > cl.b + 0.5;
        if (!decided && (outX || outY) && !ell) {
          decided = true;
          const scrollable = (outX ? ox : oy) === "auto" || (outX ? ox : oy) === "scroll";
          if (scrollable) hscroll.set(label(a) + (outX ? " [x]" : " [y]"), { clipper: label(a), axis: outX ? "x" : "y", cw: a.clientWidth, sw: a.scrollWidth, ch: a.clientHeight, sh: a.scrollHeight, sample: t.textContent.trim().slice(0, 40) });
          else clipped.push({ kind: outX ? "hidden-x" : "hidden-y", text: t.textContent.trim().slice(0, 40), el: label(el), clipper: label(a), frag: R(r) });
        }
        vis = { l: Math.max(vis.l, cl.l), t: Math.max(vis.t, cl.t), r: Math.min(vis.r, cl.r), b: Math.min(vis.b, cl.b) };
      }
      const ok = vis.r - vis.l > 0.5 && vis.b - vis.t > 0.5;
      frags.push({ id: id, node: t, el, r, vis: ok ? vis : null, text: t.textContent.trim().slice(0, 40), lab: label(el) });
    }
    id++;
    if (getComputedStyle(el).textOverflow === "ellipsis" && el.scrollWidth > el.clientWidth + 1) ellipsis.push({ el: label(el), text: t.textContent.trim().slice(0, 40), cw: el.clientWidth, sw: el.scrollWidth });
  }
  // 文本 × 文本：两段不同文本节点的可见部分相交（面积 > 4 px²）
  const overlaps: unknown[] = [];
  const seen = new Set<string>();
  for (let i = 0; i < frags.length; i++) {
    const a = frags[i];
    if (!a.vis) continue;
    for (let j = i + 1; j < frags.length; j++) {
      const b = frags[j];
      if (!b.vis || a.id === b.id) continue;
      const ix = Math.min(a.vis.r, b.vis.r) - Math.max(a.vis.l, b.vis.l);
      const iy = Math.min(a.vis.b, b.vis.b) - Math.max(a.vis.t, b.vis.t);
      if (ix > 2 && iy > 2) {
        const key = `${a.lab}|${b.lab}`;
        if (seen.has(key)) continue;
        seen.add(key);
        overlaps.push({ a: a.lab, aText: a.text, b: b.lab, bText: b.text, area: Math.round(ix * iy), at: { x: Math.round(Math.max(a.vis.l, b.vis.l)), y: Math.round(Math.max(a.vis.t, b.vis.t)) } });
      }
    }
  }
  // 控件：可见矩形（与全部裁剪祖先求交）；被 hidden 祖先裁掉部分或全部 = 点不到；可见部分再与不属于它的文本求交
  const visRect = (e: HTMLElement) => {
    const r = e.getBoundingClientRect();
    let v = { l: r.left, t: r.top, r: r.right, b: r.bottom };
    let hiddenCut = false;
    for (let a: HTMLElement | null = e.parentElement; a; a = a.parentElement) {
      const acs = getComputedStyle(a);
      if (acs.overflowX === "visible" && acs.overflowY === "visible") continue;
      const ab = a.getBoundingClientRect();
      const cl = { l: ab.left + a.clientLeft, t: ab.top + a.clientTop, r: ab.left + a.clientLeft + a.clientWidth, b: ab.top + a.clientTop + a.clientHeight };
      const hid = acs.overflowX === "hidden" || acs.overflowY === "hidden" || acs.overflowX === "clip" || acs.overflowY === "clip";
      if (hid && (r.bottom > cl.b + 0.5 || r.right > cl.r + 0.5 || r.top < cl.t - 0.5 || r.left < cl.l - 0.5)) hiddenCut = true;
      v = { l: Math.max(v.l, cl.l), t: Math.max(v.t, cl.t), r: Math.min(v.r, cl.r), b: Math.min(v.b, cl.b) };
    }
    const area = Math.max(0, v.r - v.l) * Math.max(0, v.b - v.t);
    return { v, frac: r.width * r.height > 0 ? area / (r.width * r.height) : 1, hiddenCut };
  };
  const ctrls = [...document.querySelectorAll("button, input:not([type=file]), textarea, select")].filter((e) => (e as HTMLElement).getClientRects().length > 0) as HTMLElement[];
  const ctrlOverlaps: unknown[] = [];
  const ctrlClipped: unknown[] = [];
  for (const c of ctrls) {
    const { v, frac, hiddenCut } = visRect(c);
    if (hiddenCut && frac < 0.999) ctrlClipped.push({ ctrl: label(c), text: (c.textContent ?? (c as HTMLInputElement).placeholder ?? "").trim().slice(0, 20), visibleFrac: Math.round(frac * 100) / 100 });
    if (frac <= 0) continue;
    for (const f of frags) {
      if (!f.vis || c.contains(f.el)) continue;
      const ix = Math.min(v.r, f.vis.r) - Math.max(v.l, f.vis.l);
      const iy = Math.min(v.b, f.vis.b) - Math.max(v.t, f.vis.t);
      if (ix > 2 && iy > 2) ctrlOverlaps.push({ ctrl: label(c), text: f.text, textEl: f.lab, area: Math.round(ix * iy) });
    }
  }
  return { viewport: { w: innerWidth, h: innerHeight }, cols, overlaps, ctrlOverlaps, ctrlClipped, clipped, hscroll: [...hscroll.values()], ellipsis, nFrags: frags.length };
}


/** 真实 Event 序列化（pipeline.jobs.Event）写一期 events.jsonl：两个 job（一成一败带 stderr 尾）+ 停机点待审 + 3 条人时记录 */
function writeEvents(root: string, ep: string): void {
  const lines = py(
    `import json, sys
from pipeline.jobs import Event
ep = sys.argv[1]
evs = [
 ("job_created", {"job_id": "job_c1", "command": "clips " + ep}),
 ("job_started", {"job_id": "job_c1", "pid": 999991}),
 ("job_finished", {"job_id": "job_c1", "status": "succeeded", "returncode": 0, "duration_s": 41.2}),
 ("job_created", {"job_id": "job_r1", "command": "render " + ep + " --preset final --out data/episodes/" + ep + "/05-final.mp4"}),
 ("job_started", {"job_id": "job_r1", "pid": 999992}),
 ("job_finished", {"job_id": "job_r1", "status": "failed", "returncode": 1, "duration_s": 3.9, "stderr_tail": "ffmpeg: [Parsed_subtitles_0] fontselect: (Hiragino Sans GB, 400, 0) -> /System/Library/Fonts/Hiragino Sans GB.ttc\\nError while filtering: Cannot allocate memory\\nConversion failed!"}),
 ("approval_requested", {"approval_id": "appr_x", "stop": "05"}),
 ("human_time_recorded", {"stop": "02.5", "minutes": 12.4}),
 ("human_time_recorded", {"stop": "03.5", "minutes": 31.07}),
 ("human_time_recorded", {"stop": "05", "minutes": 4.2}),
]
for i, (t, p) in enumerate(evs):
    e = Event(event_id=f"evt_17900000000{i:02d}_abcd", timestamp="2026-09-27T12:51:%02d.131064Z" % i, episode=ep, type=t, payload=p)
    sys.stdout.write(e.to_jsonl_line())`,
    [ep],
  );
  fixtureWrite(root, `data/episodes/${ep}/events.jsonl`, lines);
}

async function shoot(page: Page, state: string): Promise<void> {
  mkdirSync(OUT, { recursive: true });
  for (const [w, h] of SIZES) {
    await page.setViewportSize({ width: w, height: h });
    await page.mouse.move(0, 0);
    await page.waitForTimeout(400);
    const name = `${state}-${w}x${h}`;
    await page.screenshot({ path: join(OUT, `${name}.png`) });
    const m = await page.evaluate(measure);
    writeFileSync(join(OUT, `${name}.json`), JSON.stringify(m, null, 1));
  }
}

async function openEp(page: Page, ep: string): Promise<void> {
  await page.locator("[data-testid=episode]", { hasText: ep }).first().click();
  await expect(page.getByTestId("center")).toHaveAttribute("data-ep", ep);
}
async function sendClick(page: Page, text: string): Promise<void> {
  await page.getByTestId("composer-input").fill(text);
  await page.getByTestId("composer-send").click();
}

test("A：空态 / 有会话+停机点卡 / 02.5 编辑器在预览区", async () => {
  const fx = sessionFixture(["SESS-A", "SESS-B-这是一个很长的期名用来观察侧栏截断"]);
  const repo = fx.repo;
  sessionKey(repo, "sk-ava-test-key");
  writeStore(repo, "SESS-A", [pendingObj("appr_05a", "05", "2026-09-25T10:00:02Z", "04-clips.json", fingerprintOf(repo, "SESS-A", "04-clips.json"))]);
  sessionScript(repo, "SESS-A", [READY("SESS-A"), { op: "serve", on_turn: TURN }]);
  writeEvents(repo.root, "SESS-A");
  const L = await launchSession(repo, [], { previewOpen: false }); // 看真实默认态
  try {
    await L.page.waitForSelector("[data-testid=episode]");
    await shoot(L.page, "s1-empty");
    await openEp(L.page, "SESS-A");
    await L.page.getByTestId("decision").first().waitFor();
    await sendClick(L.page, "帮我看看 05 为什么排片不对，顺便把第 3 段的集字段补上");
    await L.page.locator("[data-testid=conv-row][data-kind=answer]").first().waitFor();
    await L.page.locator("[data-testid=conv-row][data-kind=footer]").first().waitFor();
    await shoot(L.page, "s2-session-card");
    await L.page.locator("[data-testid=tree-row][data-rel='02-script.md']").click();
    await L.page.getByTestId("script-editor").waitFor();
    await shoot(L.page, "s2e-editor");
  } finally {
    await stubQuit(L, "quit").catch(() => undefined);
    await L.app.close().catch(() => undefined);
    fx.cleanup();
  }
});

test("B：人时 + 钥匙串降级提示 / 素材模式", async () => {
  const fx = sessionFixture(["SESS-A", "SESS-C"]);
  const repo = fx.repo;
  // 无钥匙串条目（脚本退 44）→ host 给出 keyProblem；会话按 Spec 9 降级
  rmSync(join(repo.baseDir, "key"), { force: true });
  fixtureWrite(repo.root, "data/episodes/SESS-A/human_time.json", JSON.stringify([{ stop: "02.5", minutes: 12.4, source: "desktop" }, { stop: "03.5", minutes: 31.07, source: "terminal" }, { stop: "05", minutes: 4.2, source: "desktop" }, { stop: "09", minutes: 26.58, source: "desktop" }]));
  writeStore(repo, "SESS-A", [pendingObj("appr_05b", "05", "2026-09-25T10:00:02Z", "04-clips.json", fingerprintOf(repo, "SESS-A", "04-clips.json"))]);
  const reason = "钥匙串里没有该密钥；配置指名的环境变量 AVA_TEST_KEY 在本次 spawn 中缺失，已按 Spec 9 如实降级（无模型可用）";
  sessionScript(repo, "SESS-A", [READY("SESS-A", { llm: "degraded", degrade_reason: reason }), { op: "serve", on_turn: TURN }]);
  sessionScript(repo, "SESS-C", [READY("SESS-C", { scope: "asset" }), { op: "serve", on_turn: TURN }]);
  writeEvents(repo.root, "SESS-A");
  writeEvents(repo.root, "SESS-C");
  const L = await launchSession(repo, [], { previewOpen: false }); // 看真实默认态
  try {
    await L.page.waitForSelector("[data-testid=episode]");
    await openEp(L.page, "SESS-A");
    await L.page.getByTestId("decision").first().waitFor();
    await sendClick(L.page, "重排一下");
    await L.page.getByTestId("key-problem").waitFor();
    await L.page.getByTestId("llm-degraded").waitFor();
    await L.page.locator("[data-testid=conv-row][data-kind=footer]").first().waitFor();
    await expect(L.page.getByTestId("human-time")).toContainText("12.4");
    await shoot(L.page, "s3-humantime-keychain");
    // D39 S2：同一状态，人点开预览（顶栏「预览」）
    await L.page.getByTestId("toggle-preview").click();
    await shoot(L.page, "s3o-preview-open");
    await L.page.getByTestId("toggle-preview").click();
    await openEp(L.page, "SESS-C");
    await sendClick(L.page, "去找第 7 集的素材");
    await L.page.locator("[data-testid=conv-row][data-kind=footer]").first().waitFor();
    await expect(L.page.getByTestId("session-scope")).toHaveText("素材模式");
    await shoot(L.page, "s4-asset");
  } finally {
    await stubQuit(L, "quit").catch(() => undefined);
    await L.app.close().catch(() => undefined);
    fx.cleanup();
  }
});

// ---------------- 方案原型（运行时注入：adoptedStyleSheets + 少量 DOM 搬移；不改 desktop/src） ----------------
// 只引用 token 变量（与 Spec 14 VS-3 同纪律），证明方案不需要回退美化。截图用，不是实现。

type Variant = "A" | "A-rails" | "B" | "B-open";

function applyProto(v: Variant) {
  const q = (s: string) => document.querySelector(s) as HTMLElement;
  const mk = (tag: string, cls: string, html = "") => {
    const e = document.createElement(tag);
    e.className = cls;
    e.innerHTML = html;
    return e;
  };
  const colhead = (t: string) => mk("div", "d39-colhead", `<span class="d39-coltitle">${t}</span><span class="spacer"></span><button class="ui-btn ui-btn--ghost ui-btn--sm ui-btn--icon" aria-label="收起${t}" aria-expanded="true">«</button>`);
  const rail = (label: string, extra = "") => mk("div", "d39-rail", `<button class="ui-btn ui-btn--ghost ui-btn--sm ui-btn--icon" aria-label="展开${label}" aria-expanded="false">»</button><span class="d39-vlabel">${label}</span>${extra}`);
  const split = () => {
    const s = mk("div", "d39-split");
    s.setAttribute("role", "separator");
    s.setAttribute("aria-orientation", "vertical");
    s.tabIndex = 0;
    return s;
  };

  const css = `
  .main { gap: 0; padding: 0 var(--space-1) var(--space-1) 0; }
  .center.conv-shell { overflow: auto; }
  footer.timeline { display: none; }
  /* 工序卡压成一行摘要，详情（next_action / 停机原因 / advisories / 人时）收进「详情」 */
  .status { padding: var(--space-2) var(--space-4); gap: var(--space-1); }
  .status > .status-label, .status > .muted, .status > div:not(.status-step), .status > .advisories { display: none; }
  .status .status-step { font-size: var(--text-base); }
  .status .status-step .d39-more { margin-left: auto; }
  .status .cmd { margin: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  [data-testid=human-time] { display: none; }
  /* 会话头恒为一行：长说明改进对话流首行（Spec 10 §2.3 本就规定降级说明是流内一行） */
  .session-head { flex-wrap: nowrap; height: 40px; max-height: none; overflow: hidden; }
  .session-head > * { flex: none; }
  [data-testid=key-problem] { display: none; }
  .d39-note { display: block; padding: var(--space-2) var(--space-3); border-radius: var(--radius-lg); background: var(--warn-surface); color: var(--fg); font-size: var(--text-sm); overflow-wrap: anywhere; }
  .d39-note code { font-family: var(--font-mono); }
  .conv-stream { min-height: 160px; }
  .dock { max-height: 40%; }
  /* 分栏手柄与折叠形态 */
  .d39-split { cursor: col-resize; position: relative; }
  .d39-split::after { content: ""; position: absolute; left: 3px; top: calc(50% - 16px); width: 2px; height: 32px; border-radius: var(--radius-pill); background: var(--border-strong); }
  .d39-colhead { display: flex; align-items: center; gap: var(--space-2); height: 36px; padding: 0 var(--space-1) 0 var(--space-3); color: var(--fg-muted); font-size: var(--text-sm); font-weight: var(--weight-semibold); }
  .d39-files { min-height: 0; overflow: auto; background: var(--bg-sidebar); padding: 0 var(--space-2) var(--space-2); border-left: 1px solid var(--border); }
  .d39-rail { display: flex; flex-direction: column; align-items: center; gap: var(--space-2); padding-top: var(--space-2); background: var(--bg-sidebar); min-height: 0; }
  .d39-rail.d39-rail--panel { background: var(--bg-panel); border: 1px solid var(--border); border-radius: var(--radius-xl); margin-left: var(--space-1); }
  .d39-vlabel { writing-mode: vertical-rl; color: var(--fg-muted); font-size: var(--text-sm); letter-spacing: 0.08em; }
  .d39-tl { flex: none; display: flex; align-items: center; gap: var(--space-2); height: 32px; padding: 0 var(--space-3); border-top: 1px solid var(--border); color: var(--fg-muted); font-size: var(--text-sm); }
  .preview .d39-tl { margin-top: auto; }
  /* 宽对话列里限制阅读行宽（B 方案预览收起时对话列 > 1000px） */
  .d39-readable .conv-stream > *, .d39-readable .dock > *, .d39-readable .status > *, .d39-readable .session-head { max-width: 880px; }
  .d39-readable .composer { max-width: 856px; }
  `;
  const sheet = new CSSStyleSheet();
  sheet.replaceSync(css);
  document.adoptedStyleSheets = [...document.adoptedStyleSheets, sheet];

  const main = q(".main"), left = q(".left"), center = q(".center"), preview = q(".preview");
  // 工序卡：「详情」按钮
  const step = q(".status-step");
  if (step) step.appendChild(mk("button", "ui-btn ui-btn--ghost ui-btn--sm d39-more", "详情 ▸"));
  // 会话头：降级长文案缩为短标，全文进对话流首行
  const deg = q("[data-testid=llm-degraded]");
  const kp = q("[data-testid=key-problem]");
  const stream = q("[data-testid=conv-stream]");
  if (deg && stream) {
    const full = deg.textContent ?? "";
    deg.textContent = "LLM 未就绪";
    const note = mk("div", "d39-note");
    note.textContent = `${full}${kp ? "\n" + (kp.textContent ?? "") : ""}`;
    note.innerHTML = note.innerHTML.replace(/security add-generic-password[^｜]*/, (m) => `<code>${m}</code>`);
    stream.insertBefore(note, stream.firstChild);
  }
  // 时间线：一行摘要（点开再展开，展开上限 40vh，只占预览列）
  const jobs = document.querySelectorAll("footer.timeline [data-testid=job]").length;
  const failed = document.querySelectorAll("footer.timeline .job.failed").length;
  const evs = document.querySelectorAll("footer.timeline .events li").length;
  const tl = mk("div", "d39-tl", `<span>时间线</span><span class="ui-badge">作业 ${jobs}</span>${failed ? `<span class="ui-badge ui-badge--danger">失败 ${failed}</span>` : ""}<span class="ui-badge">事件 ${evs}</span><span class="spacer"></span><button class="ui-btn ui-btn--ghost ui-btn--sm" aria-expanded="false">展开 ▴</button>`);

  if (v === "A" || v === "A-rails") {
    const files = mk("nav", "d39-files");
    const tree = q("[data-testid=tree]"), cover = q(".cover-import");
    files.appendChild(colhead("文件 · " + (q("[data-testid=current-step]") ? (q(".ui-row.ep[aria-current=true] .ep-name")?.textContent ?? "") : "")));
    if (cover) files.appendChild(cover);
    if (tree) files.appendChild(tree);
    left.insertBefore(colhead("期"), left.firstChild);
    q(".preview-head")?.appendChild(mk("button", "ui-btn ui-btn--ghost ui-btn--sm ui-btn--icon", "»"));
    preview.appendChild(tl);
    if (v === "A") {
      main.replaceChildren(left, split(), files, split(), center, split(), preview);
      sheet.insertRule(`.main { grid-template-columns: 220px 8px 200px 8px minmax(420px, 1fr) 8px 400px; }`);
    } else {
      const rl = rail("期列表"), rf = rail("文件");
      rf.classList.add("d39-rail--files");
      main.replaceChildren(rl, rf, center, split(), preview);
      sheet.insertRule(`.main { grid-template-columns: 40px 40px minmax(420px, 1fr) 8px 400px; }`);
      sheet.insertRule(`.d39-rail--files { border-left: 1px solid var(--border); margin-right: var(--space-1); }`);
    }
  } else {
    // B：左栏 = 期列表 + 本期文件（现状的纵向叠放，但文件段可折叠）；对话列为主；预览默认收成右侧窄条
    const tree = q("[data-testid=tree]");
    if (tree) tree.parentElement?.insertBefore(colhead("本期文件"), q(".cover-import") ?? tree);
    if (v === "B") {
      const badge = `<span class="ui-dot ui-dot--wait" aria-hidden="true"></span>`;
      const rp = rail("预览 · 04-review.html", badge);
      rp.classList.add("d39-rail--panel");
      const rt = mk("div", "d39-vlabel", "时间线 · 失败 1");
      rp.appendChild(rt);
      main.replaceChildren(left, split(), center, rp);
      center.classList.add("d39-readable");
      sheet.insertRule(`.main { grid-template-columns: 240px 8px minmax(480px, 1fr) 44px; }`);
      sheet.insertRule(`.center.d39-readable .conv-stream, .center.d39-readable .dock, .center.d39-readable .status, .center.d39-readable .session-head { padding-left: max(var(--space-4), calc((100% - 880px) / 2)); padding-right: max(var(--space-4), calc((100% - 880px) / 2)); }`);
      sheet.insertRule(`.center.d39-readable .composer { margin-left: max(var(--space-3), calc((100% - 856px) / 2)); margin-right: max(var(--space-3), calc((100% - 856px) / 2)); }`);
    } else {
      q(".preview-head")?.appendChild(mk("button", "ui-btn ui-btn--ghost ui-btn--sm ui-btn--icon", "»"));
      preview.appendChild(tl);
      main.replaceChildren(left, split(), center, split(), preview);
      sheet.insertRule(`.main { grid-template-columns: 240px 8px minmax(420px, 1fr) 8px minmax(360px, 40%); }`);
    }
  }
  center.scrollTop = 0;
}

// 选型已完成（2026-09-29 人选 B）；原型是按 S1 之前的 DOM 打的补丁，S1 起结构已变，保留代码作证据、不再运行
test.skip("方案原型：同一状态（有会话 + 05 停机点卡 + 人时 + 钥匙串降级）", async () => {
  const fx = sessionFixture(["SESS-A", "SESS-C"]);
  const repo = fx.repo;
  rmSync(join(repo.baseDir, "key"), { force: true });
  fixtureWrite(repo.root, "data/episodes/SESS-A/human_time.json", JSON.stringify([{ stop: "02.5", minutes: 12.4, source: "desktop" }, { stop: "03.5", minutes: 31.07, source: "terminal" }, { stop: "05", minutes: 4.2, source: "desktop" }, { stop: "09", minutes: 26.58, source: "desktop" }]));
  writeStore(repo, "SESS-A", [pendingObj("appr_05b", "05", "2026-09-25T10:00:02Z", "04-clips.json", fingerprintOf(repo, "SESS-A", "04-clips.json"))]);
  const reason = "钥匙串里没有该密钥；配置指名的环境变量 AVA_TEST_KEY 在本次 spawn 中缺失，已按 Spec 9 如实降级（无模型可用）";
  sessionScript(repo, "SESS-A", [READY("SESS-A", { llm: "degraded", degrade_reason: reason }), { op: "serve", on_turn: TURN }]);
  writeEvents(repo.root, "SESS-A");
  const variants: Variant[] = ["A", "A-rails", "B", "B-open"];
  for (const v of variants) {
    const L = await launchSession(repo, [], { previewOpen: false }); // 看真实默认态
    try {
      await L.page.waitForSelector("[data-testid=episode]");
      await openEp(L.page, "SESS-A");
      await L.page.getByTestId("decision").first().waitFor();
      await sendClick(L.page, "重排一下");
      await L.page.getByTestId("key-problem").waitFor();
      await L.page.locator("[data-testid=conv-row][data-kind=footer]").first().waitFor();
      await L.page.locator("footer.timeline [data-testid=job]").nth(1).waitFor();
      // 记录 HEAD 的「中栏被滚走」事实，再套原型
      const headScrollTop = await L.page.evaluate(() => (document.querySelector(".center") as HTMLElement).scrollTop);
      await L.page.evaluate(applyProto, v);
      mkdirSync(OUT, { recursive: true });
      for (const [w, h] of [[1440, 900], [1280, 800]] as [number, number][]) {
        await L.page.setViewportSize({ width: w, height: h });
        await L.page.mouse.move(0, 0);
        await L.page.waitForTimeout(400);
        await L.page.evaluate(() => { (document.querySelector(".center") as HTMLElement).scrollTop = 0; });
        const name = `proto-${v}-${w}x${h}`;
        await L.page.screenshot({ path: join(OUT, `${name}.png`) });
        const m = await L.page.evaluate(measure);
        const colsW = await L.page.evaluate(() => [...(document.querySelector(".main") as HTMLElement).children].map((e) => ({ cls: (e.getAttribute("class") ?? "").split(" ").slice(0, 2).join("."), w: Math.round(e.getBoundingClientRect().width) })));
        writeFileSync(join(OUT, `${name}.json`), JSON.stringify({ ...m, colsW, headScrollTopBeforeProto: headScrollTop }, null, 1));
      }
    } finally {
      await stubQuit(L, "quit").catch(() => undefined);
      await L.app.close().catch(() => undefined);
    }
  }
  fx.cleanup();
});
