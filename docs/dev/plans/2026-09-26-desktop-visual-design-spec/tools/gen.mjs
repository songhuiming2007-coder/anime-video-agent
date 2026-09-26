// Spec 14 mock 生成器（v0.2）：node gen.mjs → 在上级附件目录写出 mock-*.html。改 mock 改这里，不手改 HTML。
import { writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { icon, ICONS } from "./icons.mjs";
const A = fileURLToPath(new URL("..", import.meta.url)).replace(/\/$/, "");
const CSP = `default-src 'none'; script-src 'self'; style-src 'self'; img-src ava-media: data:; media-src ava-media:; frame-src ava-media:; connect-src ava-media:`;
const page = (title, body) => `<!doctype html>
<!-- Spec 14 mock：${title}。只用 tokens.css + ui.css + style.css（将发货的样式）+ mock.css（画布与媒体占位）。
     CSP 与 desktop/src/renderer/index.html 生产版逐字相同：页面里不许出现 style="" 属性与 <style> 元素。
     看明暗两版：切 macOS 外观，或按 Spec 14 §2.7 的截图命令渲染。 -->
<html lang="zh-CN"><head><meta charset="UTF-8"><meta http-equiv="Content-Security-Policy" content="${CSP}">
<title>ava mock · ${title}</title>
<link rel="stylesheet" href="./tokens.css"><link rel="stylesheet" href="./ui.css"><link rel="stylesheet" href="./style.css"><link rel="stylesheet" href="./mock.css"></head>
<body><div class="mock-frame">${body}</div></body></html>
`;
const I = icon;
const ep = (name, step, { sel = false, stop = false, unknown = false, time = "", hover = false } = {}) => `
<button class="ui-row ep${hover ? " mock-hover" : ""}" ${sel ? 'aria-current="true"' : ""} data-testid="episode">
  <span class="ep-line"><span class="ep-name">${name}</span><span class="ep-time" title="期目录顶层最近变动">${time}</span></span>
  <span class="ep-step">${stop ? '<span class="ui-badge stop-mark">停机</span>' : unknown ? '<span class="ui-dot ui-dot--unknown"></span>' : ""}<span class="ep-step-text">${step}</span></span>
</button>`;
const side = (extraBelow = "") => `
<nav class="left">
  <div class="side-head">
    <label class="ui-search">${I("search", "ui-icon--sm")}<input class="ui-input" placeholder="搜索期名"></label>
    <div class="side-head-row"><div class="ui-seg"><button aria-pressed="true">按时间</button><button>按工序</button></div><span class="spacer"></span><span class="ui-count">5</span></div>
  </div>
  ${ep("罪恶王冠-EP07-集与祈", "05 审时间码", { sel: true, stop: true, time: "3 分钟" })}
  ${ep("东京喰种-人物志-金木研", "03.5 配音顺听 / 04 排片", { time: "12 分钟" })}
  ${ep("命运石之门-EP02-世界线", "02.5 人审改稿", { stop: true, time: "1 小时", hover: true })}
  ${ep("进击的巨人-杂谈-自由的代价", "03 语音合成", { time: "2 小时" })}
  ${ep("CLANNAD-人物志-古河渚", "…", { unknown: true, time: "3 天" })}
  <div class="muted mock-pad">另有 2 个 _ 前缀目录未显示</div>
  ${extraBelow}
  <button class="ui-section">${I("chevron-right", "ui-icon--sm")}镜头画廊</button>
</nav>`;
const topbar = (pop = false) => `
<header class="topbar">
  <div class="topbar-row">
    <span class="wordmark">ava</span><span class="ui-badge ui-badge--warn dev-mark">DEV BUILD</span>
    <span class="repo">${I("repo", "ui-icon--sm")} ~/Documents/code/anime-video-agent-ava <code>@9eb1f97a</code></span>
    <span class="spacer"></span>
    <button class="ui-btn ui-btn--ghost ui-btn--sm">切换仓库…</button>
    <button class="ui-btn ui-btn--ghost ui-btn--sm">${I("refresh", "ui-icon--sm")}刷新</button>
    <button class="ui-btn ui-btn--ghost ui-btn--sm">${I("pulse", "ui-icon--sm")}健康</button>
    <button class="ui-btn ui-btn--ghost ui-btn--sm ui-btn--icon" aria-label="外观" popovertarget="theme-menu">${I("appearance", "ui-icon--sm")}</button>
  </div>
  <div class="banner banner-yellow">${I("alert", "ui-icon--sm")}core Code Freeze：pipeline/ 存在未提交改动或 git 检查异常（Code Freeze WARN）</div>
  ${pop ? `<div class="ui-popover mock-pop" id="theme-menu"><div class="ui-popover-title">外观</div><div class="ui-seg"><button aria-pressed="true">跟随系统</button><button>浅色</button><button>深色</button></div></div>` : ""}
</header>`;
const decision05 = (open = false) => `
<div class="ui-card decision" data-testid="decision">
  <div class="ui-card-head"><span class="ui-card-title">停机点 05 审时间码</span></div>
  <div class="ui-card-sub">待审 · 创建于 2026-09-26T05:26:16.051194Z · appr_1790400376051_75b9</div>
  <ul class="fingerprints"><li><code>04-clips.json</code> 277 字节 · mtime_ns 1790400375238421234</li></ul>
  <div class="muted mock-mt">批准须先显式执行 python -m pipeline.review &lt;期&gt; --approve</div>
  <div class="notice mock-mt">${I("info", "ui-icon--sm")}本次审阅不计人时</div>
  <div class="ui-card-actions"><button class="ui-btn">批准</button><button class="ui-btn" ${open ? 'aria-expanded="true"' : ""}>打回…</button></div>
  ${open ? `<div class="reject-form"><label class="ui-field">哪段<input class="ui-input" placeholder="例如 04-clips.json s07 1:20" value="04-clips.json s03 0:42"></label>
  <label class="ui-field">问题<textarea class="ui-textarea" rows="3">画面是第 2 集的战斗，台词说的是第 1 集的告白，换一个镜头</textarea></label>
  <div><button class="ui-btn">提交打回</button></div></div>` : ""}
</div>`;
const tree = `
<div class="tree mock-pad">
  <ul class="tree-list">
    <li><button class="ui-row tree-row dir">${I("chevron-right", "ui-icon--sm")}${I("folder", "ui-icon--sm")}<span class="ui-row-title">_agent</span></button></li>
    <li><button class="ui-row tree-row dir">${I("chevron-right", "ui-icon--sm")}${I("folder", "ui-icon--sm")}<span class="ui-row-title">03-audio</span></button></li>
    <li><button class="ui-row tree-row">${I("file", "ui-icon--sm")}<span class="ui-row-title">01-topic.md</span></button></li>
    <li><button class="ui-row tree-row" aria-current="true">${I("file", "ui-icon--sm")}<span class="ui-row-title">02-script.md</span></button></li>
    <li><button class="ui-row tree-row mock-hover">${I("file", "ui-icon--sm")}<span class="ui-row-title">04-clips.json</span></button></li>
    <li><button class="ui-row tree-row">${I("file", "ui-icon--sm")}<span class="ui-row-title">04-review.html</span></button></li>
    <li><button class="ui-row tree-row">${I("film", "ui-icon--sm")}<span class="ui-row-title">05-final.mp4</span></button></li>
    <li><button class="ui-row tree-row">${I("image", "ui-icon--sm")}<span class="ui-row-title">07-cover/</span></button></li>
  </ul>
</div>`;
const md = `
<section class="preview">
  <div class="preview-head">${I("file", "ui-icon--sm")}<span class="ui-row-title">罪恶王冠-EP07-集与祈/02-script.md</span><span class="ui-badge">Markdown</span></div>
  <div class="preview-body"><div class="text-wrap"><div class="markdown">
    <h1>集与祈：命运的回响</h1>
    <h2>段落 1</h2><p>配音：那一天，集第一次握住了祈的手。他还不知道，这只手会把他拖进一场没有退路的战争。</p><p>查询：<code>集 握手 祈</code></p>
    <h2>段落 2</h2><p>配音：王之能力从来不是礼物，而是枷锁。每拔出一把虚空，就要亲手剖开一个人的心。</p><p>查询：<code>王之能力 虚空</code></p>
    <h2>段落 3</h2><p>配音：当祈唱起 Euterpe 的时候，整座城市都安静了下来。</p>
  </div></div></div>
</section>`;
const timeline = `
<footer class="timeline" data-testid="timeline">
  <div class="notice">${I("info", "ui-icon--sm")}观测层有损（Spec 2 §2.3），轨迹可能不完整；工序以 status 为准</div>
  <ul class="jobs">
    <li class="job"><span class="job-state"><span class="ui-badge ui-badge--ok">succeeded</span></span><code>python -m pipeline.clips 罪恶王冠-EP07-集与祈</code><span class="muted">rc=0 · 41s</span></li>
    <li class="job failed"><span class="job-state"><span class="ui-badge ui-badge--danger">failed</span></span><code>python -m pipeline.review … --approve</code><span class="muted">rc=1 · 2s</span></li>
  </ul>
  <ul class="events"><li><span class="muted">2026-09-26T05:26:16.056534Z</span>停机点待审：05</li></ul>
</footer>`;

// ---------- 01 工作台（Spec 14 施工后的现有三栏） ----------
writeFileSync(`${A}/mock-01-workbench.html`, page("工作台（现有三栏，Spec 14 施工后）", `
<div class="app">${topbar(true)}
<div class="main">${side()}
<section class="center">
  <div class="status" data-testid="status">
    <span class="status-label">当前工序</span>
    <div class="status-step"><strong data-testid="current-step">05 审时间码</strong><span class="ui-badge stop-mark">停机</span></div>
    <div class="muted">🛑 处于人工停机点 3（05 审时间码）！排片已生成，未获 --approve 批准。</div>
    <div>下一步：严禁直接启动渲染！请在浏览器打开 04-review.html 抽检画面与台词，确认无画外音错配后执行批准：</div>
    <code class="ui-code cmd">python -m pipeline.review /Volumes/Samsung T7/anime-video-data/episodes/罪恶王冠-EP07-集与祈 --approve</code>
    <ul class="advisories"><li>${I("alert", "ui-icon--sm")}人类耗时：本期已记 18.5 分钟<span class="muted">（数据源不完整：桌面端审阅不计入）</span></li></ul>
  </div>
  <div class="decisions" data-testid="decisions">${decision05()}</div>
  ${tree}
</section>
${md}
</div>${timeline}</div>`));

// ---------- 02 对话面板（Spec 10 版面，套 token） ----------
writeFileSync(`${A}/mock-02-conversation.html`, page("对话面板（Spec 10 版面套用 token）", `
<div class="app">${topbar()}
<div class="main">${side()}
<section class="center conv">
  <div class="session-head"><span class="ui-badge">创作模式</span><span class="ui-dot ui-dot--ok"></span><span class="muted">LLM 已连接</span><span class="spacer"></span>
    <div class="ui-seg"><button aria-pressed="true">创作</button><button>素材</button></div><button class="ui-btn ui-btn--ghost ui-btn--sm">结束会话</button></div>
  <div class="conv-stream">
    <div class="conv-user">把第 3 段的查询改成「祈 唱歌 城市」，然后重排 04</div>
    <div class="conv-tool">${I("check", "ui-icon--sm conv-tool-ok")}<code>read_episode_file</code><span>02-script.md</span><span class="spacer"></span><span>0.1s</span></div>
    <div class="conv-tool">${I("close", "ui-icon--sm conv-tool-fail")}<code>run_pipeline</code><span>clips 罪恶王冠-EP07</span><span class="spacer"></span><span>3.4s</span></div>
    <pre class="conv-obs">退出码 1：02-script.md 第 3 段「集」字段缺失，检索掩码无法生效
（observation 原文逐字显示，不截断）</pre>
    <div class="conv-assistant">第 3 段缺了「集」字段，我没法给检索加集内掩码。你希望这段素材限定在第几集？我先把查询改好，写回前会弹卡请你批准。</div>
    <div class="conv-note">[工具卡] write_episode_file · 待答</div>
    <div class="conv-foot">模型调用 2 · 工具 3（执行 3、重复拒绝 0）· 用时 14 s · end_turn</div>
  </div>
  <div class="dock">
    <div class="ui-card"><div class="ui-card-head"><span class="ui-card-title">写入 02-script.md</span><span class="ui-badge ui-badge--wait">待答</span><span class="spacer"></span><span class="ui-card-sub">write_episode_file</span></div>
      <code class="ui-code mock-mt">段落 3 · 查询：王之能力 虚空 → 祈 唱歌 城市</code>
      <div class="ui-card-actions"><button class="ui-btn">批准</button><button class="ui-btn">拒绝</button><input class="ui-input mock-flex1" placeholder="告诉它怎么改（可选）"></div></div>
  </div>
  <div class="composer"><textarea placeholder="告诉 ava 要做什么（Enter 发送，Shift+Enter 换行）"></textarea>
    <div class="composer-row"><span class="muted">罪恶王冠-EP07-集与祈</span><span class="spacer"></span><button class="ui-btn ui-btn--primary ui-btn--sm">发送</button></div></div>
</section>
<section class="preview">
  <div class="preview-head">${I("film", "ui-icon--sm")}<span class="ui-row-title">罪恶王冠-EP07-集与祈/05-final.mp4</span><span class="ui-badge">视频</span></div>
  <div class="preview-body"><div class="video-wrap"><div class="mock-video"></div><div class="mock-video-caption">当祈唱起 Euterpe 的时候</div><div class="timecode">00:01:42.375</div></div>
  <div class="mock-pad"><div class="ui-state ui-state--inline">${I("info", "ui-icon--sm")}停机点 05 已就绪 · <button class="ui-btn ui-btn--sm">查看审片页</button></div></div></div>
</section>
</div></div>`));

// ---------- 03 预览面板各态 ----------
const cell = (t, body) => `<div class="mock-cell"><div class="mock-cell-title">${t}</div><div class="mock-flex1">${body}</div></div>`;
writeFileSync(`${A}/mock-03-preview.html`, page("预览面板各态", `
<div class="mock-grid-3">
${cell("视频 · 时间码浮层", `<div class="video-wrap"><div class="mock-video"></div><div class="timecode">00:03:17.042</div></div>`)}
${cell("音频队列 · 03-audio/（当前段高亮）", `<div class="audio-wrap"><div class="ui-code">▶ ━━━━━━━━○──────  0:02 / 0:05（原生 &lt;audio controls&gt; 随 color-scheme 变色）</div>
<ol class="queue" data-testid="audio-queue"><li><button class="ui-row">seg-01.wav</button></li><li class="current"><button class="ui-row" aria-current="true">seg-02.wav</button></li><li><button class="ui-row">seg-03.wav</button></li><li><button class="ui-row">seg-10.wav</button></li></ol></div>`)}
${cell("图片 · 缩放平移", `<div class="image-wrap"><div class="mock-img"></div></div>`)}
${cell("JSON · 折叠树（数字按源码原文）", `<div class="text-wrap json"><div><button class="j-toggle" aria-expanded="true">▾</button>{</div><div class="j-children">
<div><span class="j-key">"anime"</span>: <span class="j-str">"罪恶王冠"</span></div><div><span class="j-key">"total_duration"</span>: <span class="j-num">312.48</span></div>
<div><span class="j-key">"approved"</span>: <span class="j-lit">false</span></div><div><button class="j-toggle" aria-expanded="false">▸</button><span class="j-key">"segments"</span>: [ …24 项 ]</div>
<div><span class="j-key">"mtime_ns"</span>: <span class="j-num">1790171112636927676</span></div></div><div>}</div></div>`)}
${cell("空态", `<div class="ui-state">${I("file")}<div class="ui-state-title">选择左侧产物以预览</div><div>支持 html / 视频 / 音频 / 图片 / md / json</div></div>`)}
${cell("错误态", `<div class="ui-state ui-state--error">${I("alert")}<div class="ui-state-title">读取失败</div><div class="ui-state-detail">HTTP 404 · ava-media://episodes/罪恶王冠-EP07/02-script.md</div></div>`)}
${cell("加载态（骨架）", `<div class="text-wrap mock-col"><div class="ui-skeleton mock-w260"></div><div class="ui-skeleton"></div><div class="ui-skeleton"></div><div class="ui-skeleton mock-w220"></div><div class="ui-state ui-state--inline"><span class="ui-spinner"></span>读取中…</div></div>`)}
${cell("截断提示 · 纯文本", `<div class="text-wrap"><div class="notice notice--warn">${I("alert", "ui-icon--sm")}文件超过 5 MiB，只显示前 5 MiB 原文</div><pre class="plain mock-mt">2026-09-26 13:02:11 INFO clips: 段 01 命中 S01E07 12:31.4 score 0.71
2026-09-26 13:02:11 INFO clips: 段 02 命中 S01E07 18:02.0 score 0.64
2026-09-26 13:02:12 WARN clips: 段 03 查询阶梯落到「配音」级</pre></div>`)}
${cell("未知类型 · 只显示元数据", `<div class="meta"><div class="meta-title">run.sh</div><div class="muted">该类型只显示元数据</div></div>`)}
</div>`));

// ---------- 04 停机点组件 ----------
writeFileSync(`${A}/mock-04-stop-points.html`, page("停机点组件（02.5 / 03.5 / 05 / 09）", `
<div class="mock-grid-2">
<div class="mock-cell"><div class="mock-cell-title">02.5 · app 内改稿与封板（Spec 11 ScriptEditor）</div>
  <div class="toolbar"><button class="ui-btn ui-btn--primary ui-btn--sm">保存 <span class="ui-kbd">⌘S</span></button><button class="ui-btn ui-btn--sm">机检</button><span class="spacer"></span><span class="notice">有未保存改动，封板对象必须是磁盘上的最新版本</span><button class="ui-btn ui-btn--sm" disabled>封板</button></div>
  <div class="editor-split">
    <div class="mock-col mock-pad"><div class="ui-state ui-state--inline">${I("info", "ui-icon--sm")}示意：真实编辑器是 CodeMirror 6（.cm-* DOM），高亮经 HighlightStyle 引用 --syntax-* 变量（S11-R1）</div>
    <pre class="ui-code">## 段落 1
配音：那一天，集第一次握住了祈的手。
查询：集 握手 祈
集：7</pre></div>
    <div class="text-wrap markdown"><h2>段落 1</h2><p>配音：那一天，集第一次握住了祈的手。</p><p>查询：<code>集 握手 祈</code></p></div>
    <div class="checklist"><strong>人审要点</strong><label>☐ 人物名与集数对得上</label><label>☐ 查询写台词语义，不写构图</label><label>☐ 没有政治议题</label></div>
  </div></div>
<div class="mock-cell"><div class="mock-cell-title">03.5 · 顺听纠错（Spec 11 VoicePanel）</div>
  <div class="toolbar"><button class="ui-btn ui-btn--sm">${I("play", "ui-icon--sm")}顺序播放</button><button class="ui-btn ui-btn--sm">${I("stop", "ui-icon--sm")}停</button><span class="spacer"></span><button class="ui-btn ui-btn--sm">完成并应用补丁</button></div>
  <div class="mock-pad"><div class="notice">${I("info", "ui-icon--sm")}多音字预检：「重」×3（zhòng / chóng）· 「行」×1</div>
  <div class="composer-row mock-mt"><input class="ui-input" placeholder="纠错，例如：段 3 把「虚空」读成 xū kōng" value="段 3 把「虚空」读成 xū kōng"><button class="ui-btn ui-btn--sm">解析</button></div></div>
  <div class="seg-row"><span>01</span><span class="muted">4.21s</span><span class="ui-row-title">那一天，集第一次握住了祈的手。</span><span class="mock-flex"><button class="ui-btn ui-btn--ghost ui-btn--sm ui-btn--icon" aria-label="播放">${I("play", "ui-icon--sm")}</button></span></div>
  <div class="seg-row" aria-current="true"><span>02</span><span class="muted">5.08s</span><span class="ui-row-title">王之能力从来不是礼物，而是枷锁。</span><span class="mock-flex"><span class="ui-spinner"></span></span></div>
  <div class="seg-row"><span>03</span><span class="muted">3.90s</span><span class="ui-row-title">每拔出一把虚空……</span><span class="mock-flex"><span class="ui-badge ui-badge--warn">待应用 #4</span><button class="ui-btn ui-btn--sm">撤回</button><button class="ui-btn ui-btn--sm">回滚</button></span></div>
  <div class="mock-pad"><div class="ui-card"><div class="ui-card-head"><span class="ui-card-title">确认纠错条目</span><span class="ui-badge ui-badge--warn">⚠️ 全局生效</span></div>
  <dl class="ui-kv"><dt>原文</dt><dd>虚空</dd><dt>读音</dt><dd>xū kōng</dd><dt>范围</dt><dd>全部段落</dd></dl>
  <div class="ui-card-actions"><button class="ui-btn ui-btn--sm">确认落盘</button><button class="ui-btn ui-btn--sm">取消</button></div></div></div></div>
<div class="mock-cell"><div class="mock-cell-title">05 · 打回表单展开 + 失败回执</div><div class="mock-pad">${decision05(true)}
  <div class="decision-err mock-mt">停机点 05（appr_1790400376051_75b9）E_STALE：批准文件对应的不是你审阅的版本</div><pre class="tail stderr">EOFError: 已不是审阅时的版本（size 277 ≠ 281）</pre></div></div>
<div class="mock-cell"><div class="mock-cell-title">09 · 封面与标题定稿（Spec 12 两输入）</div><div class="mock-pad">
  <div class="ui-card decision"><div class="ui-card-head"><span class="ui-card-title">停机点 09 人工发布</span></div><div class="ui-card-sub">待审 · 创建于 2026-09-26T06:02:41.118305Z · appr_1790402561118_0c2e</div>
  <div class="ui-dropzone mock-mt" data-dragover="true">${I("image", "ui-icon--sm")}松开以导入到 07-cover/import/</div>
  <div class="ui-card-sub mock-mt">选一张封面</div>
  <div class="cover-grid mock-mt"><button class="cover-opt" aria-pressed="true"><div class="mock-cover-169"></div><span class="ui-badge">1920×1080</span></button><button class="cover-opt"><div class="mock-cover-169b"></div><span class="ui-badge">1920×1080</span></button><button class="cover-opt"><div class="mock-cover-916"></div><span class="ui-badge">1080×1920</span></button></div>
  <label class="ui-field mock-mt">标题<input class="ui-input" value="集与祈：王之能力是礼物还是枷锁？"></label>
  <div class="ui-card-actions"><button class="ui-btn">批准并记录定稿</button><button class="ui-btn">打回…</button></div></div></div></div>
</div>`));

// ---------- 05 组件清单 ----------
const sw = (n) => `<span class="mock-swatch"><span class="mock-chip mock-c-${n}"></span>--${n}</span>`;
writeFileSync(`${A}/mock-05-components.html`, page("组件清单", `
<div class="mock-sheet">
<div class="mock-col">
 <h3>按钮（原生 &lt;button&gt; + class；浅粉主按钮只给非闸门动作，闸门按钮同权）</h3>
 <div class="mock-flex"><button class="ui-btn ui-btn--primary">发送</button><button class="ui-btn">批准</button><button class="ui-btn">打回…</button><button class="ui-btn ui-btn--ghost">结束会话</button><button class="ui-btn ui-btn--primary" disabled>封板</button><button class="ui-btn" disabled>禁用</button><button class="ui-btn ui-btn--sm">小号</button><button class="ui-btn ui-btn--lg ui-btn--primary">大号</button><button class="ui-btn ui-btn--icon" aria-label="刷新">${I("refresh", "ui-icon--sm")}</button></div>
 <h3 class="mock-mt">输入</h3>
 <div class="mock-flex"><label class="ui-search mock-w220">${I("search", "ui-icon--sm")}<input class="ui-input" placeholder="搜索期名"></label><input class="ui-input mock-w220" value="已填写的内容"><div class="ui-seg"><button aria-pressed="true">按时间</button><button>按工序</button></div><div class="ui-seg"><button>跟随系统</button><button aria-pressed="true">浅色</button><button>深色</button></div></div>
 <h3 class="mock-mt">徽标与状态点（颜色永远配文字）</h3>
 <div class="mock-flex"><span class="ui-badge stop-mark">停机</span><span class="ui-badge ui-badge--wait">待答</span><span class="ui-badge ui-badge--ok">succeeded</span><span class="ui-badge ui-badge--warn">待应用 #4</span><span class="ui-badge ui-badge--danger">failed</span><span class="ui-badge">Markdown</span><span class="ui-badge ui-badge--accent">已选</span><span class="ui-count">12</span>
 <span class="mock-swatch"><span class="ui-dot"></span>中性</span><span class="mock-swatch"><span class="ui-dot ui-dot--ok"></span>在线</span><span class="mock-swatch"><span class="ui-dot ui-dot--danger"></span>失败</span><span class="mock-swatch"><span class="ui-dot ui-dot--unknown"></span>未知</span><span class="mock-swatch"><span class="ui-spinner"></span>运行中</span></div>
 <h3 class="mock-mt">拖放区（Spec 12）：常态 / dragover</h3>
 <div class="mock-flex"><div class="ui-dropzone mock-w220">${I("image", "ui-icon--sm")}拖入图片导入</div><div class="ui-dropzone mock-w220" data-dragover="true">${I("image", "ui-icon--sm")}松开以导入</div></div>
 <h3 class="mock-mt">列表行：默认 / 悬停 / 选中；「按工序」分组（停机点 / 非停机点 / 未取到）</h3>
 <div class="mock-col mock-w260"><button class="ui-section" aria-expanded="true">${I("chevron-down", "ui-icon--sm")}停机点<span class="ui-count">2</span></button>${ep("悬停行-示例", "05 审时间码", { stop: true, time: "1 小时", hover: true })}${ep("选中行-示例", "09 人工发布", { sel: true, stop: true, time: "2 天" })}<button class="ui-section" aria-expanded="true">${I("chevron-down", "ui-icon--sm")}非停机点<span class="ui-count">1</span></button>${ep("默认行-示例", "07 自动质检（未通过）", { time: "5 分钟" })}<button class="ui-section" aria-expanded="true">${I("chevron-down", "ui-icon--sm")}未取到<span class="ui-count">1</span></button>${ep("未知行-示例", "…", { unknown: true, time: "3 天" })}</div>
 <h3 class="mock-mt">横幅</h3>
 <div class="mock-col"><div class="banner banner-red">${I("alert", "ui-icon--sm")}data 不可达：/Volumes/Samsung T7 未挂载</div><div class="banner banner-yellow">${I("alert", "ui-icon--sm")}UI 未按当前源码重建（构建于 9eb1f97a）</div><div class="banner banner-grey">${I("info", "ui-icon--sm")}无法比对 UI 构建版本（构建提交不在本仓库）</div></div>
</div>
<div class="mock-col">
 <h3>空态 / 错误态 / 加载态</h3>
 <div class="mock-grid-3 mock-cell"><div class="ui-state">${I("file")}<div class="ui-state-title">选择一期</div></div><div class="ui-state ui-state--error">${I("alert")}<div class="ui-state-title">工序读取失败</div><div class="ui-state-detail">E_CORE：status 退出 1</div></div><div class="ui-state"><span class="ui-spinner"></span><div>加载中…</div></div></div>
 <h3 class="mock-mt">图标（仓库内手绘 · ${Object.keys(ICONS).length} 个 · currentColor）</h3>
 <div class="mock-flex">${Object.keys(ICONS).map((n) => `<span class="mock-swatch">${I(n)}${n}</span>`).join("")}</div>
 <h3 class="mock-mt">字号阶梯（正文 14 · 下限 12 · 徽标 10）</h3>
 <div class="mock-col"><span class="markdown"><strong>18 · 预览 h1</strong></span><span>16 · 工序名 / 预览 h2</span><span>14 · 正文、按钮、列表行</span><span class="muted">12 · 次要信息、元数据、代码块</span><span class="ui-badge mock-w220">10 · 仅徽标 / 计数</span></div>
 <h3 class="mock-mt">色板（本主题）</h3>
 <div class="mock-flex">${["bg", "bg-sidebar", "bg-panel", "bg-card", "bg-selected", "fg", "fg-muted", "fg-subtle", "accent", "accent-strong", "fg-on-accent", "ok", "warn", "danger", "wait", "border-input"].map(sw).join("")}</div>
</div>
</div>`));
// ---------- 06 数据盘不可达（stale，Spec 8 §2.8 置灰；B5 横幅强度） ----------
writeFileSync(`${A}/mock-06-stale.html`, page("数据盘不可达（stale）", `
<div class="app"><header class="topbar"><div class="topbar-row"><span class="wordmark">ava</span><span class="repo">${I("repo", "ui-icon--sm")} ~/Documents/code/anime-video-agent-ava</span><span class="spacer"></span><button class="ui-btn ui-btn--ghost ui-btn--sm">刷新</button></div>
<div class="banner banner-red" data-testid="reach-banner">${I("alert", "ui-icon--sm")}unreachable：data 指向的 /Volumes/Samsung T7/anime-video-data 不存在（外置盘未挂载？）</div>
<div class="banner banner-yellow">${I("alert", "ui-icon--sm")}core Code Freeze：pipeline/ 存在未提交改动或 git 检查异常（Code Freeze WARN）</div></header>
<div class="main stale">${side()}
<section class="center"><div class="status" data-testid="status"><span class="status-label">当前工序</span><div class="status-step"><strong data-testid="current-step">05 审时间码</strong><span class="ui-badge stop-mark">停机</span></div><div>下一步：严禁直接启动渲染！请在浏览器打开 04-review.html 抽检画面与台词，确认无画外音错配后执行批准：</div></div>
<div class="decisions">${decision05()}</div></section>
${md}</div></div>`));
console.log("written");
