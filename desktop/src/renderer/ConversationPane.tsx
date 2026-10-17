// Spec 10 §2.3 / §4.3：对话流（foldConv 的行）。纯展示，不含任何答复/发送类方法名（TG-4′/TG-10/TG-16）。
// D46 修订（人 2026-10-08）：助手回复（answer/wrapup）渲染 Markdown，渲染器关掉链接与图片（ChatMarkdown，H-3 前提仍成立）；
// 卡片、本地说明、工具原文仍是纯文本。失败调用的 observation 逐字、不截断，但默认收起。
// D80（人选方案 A，2026-10-12）：工具行「图标 类别词 主文本 次文本 … 状态词 ›」、只有状态词带语义色；连续 bash 折成终端组；
// 一轮过程折成「已工作 … · N 个工具」，答复留在外面；运行中类别词扫光；新到的行淡入。只改怎么显示，行数据一条不删。
import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { cardParts, cardPhrase, type ConvRow, type ToolRow } from "../shared/convFold";
import {
  durText,
  firstLine,
  foldLabel,
  groupDefaultOpen,
  handoffTickets,
  mainText,
  STATUS_WORD,
  subText,
  toolCategory,
  toolStatus,
  turnDefaultOpen,
  turnSegments,
  type ProcItem,
  type Segment,
} from "../shared/toolView";
import { CopyButton } from "./copy";
import { Icon, type IconName } from "./icons";
import { ChatMarkdown } from "./PreviewPane";
/** 离底部不超过这么多像素就算「人在看最新」：新行到来时跟到底；人往上翻了就不打扰（D39 S2） */
const STICK_PX = 48;

/**
 * 收起后卸载内容的兜底时长：比 `--dur-slow`（300ms）多 100ms。正常由 transitionend 触发卸载；
 * 减弱动效下过渡时长被归零（ui.css 全局规则），transitionend 不触发，只能靠它
 */
const COLLAPSE_UNMOUNT_MS = 400;

const CATEGORY_ICON: Readonly<Record<string, IconName>> = {
  终端: "terminal",
  读取: "file",
  写入: "pen",
  流水线: "stack",
  搜索: "search",
  网页: "globe",
  素材: "image",
};

type Opens = { get: (key: string, dflt: boolean) => boolean; toggle: (key: string, current: boolean) => void };

/**
 * running：回合在跑；awaiting：有待人答复的卡（这时该看的是待答区，末尾不放指示行）。
 * 执行中的工具行只在所属回合运行时扫光：回合已结束却没收到结束帧的行（中断、进程退出）是「已停止」。
 */
/** `cards`：D80 R6（人选方案 C）待答的请求卡，挂在对话流末尾（由 App 传入——本组件不含任何答复方法，TG-4′） */
/**
 * D82（人选 C）：输入框上方的工单提示条。回合跑完默认折叠（D80），工单那条工具行随之收起；
 * 提示条在折叠之外，本会话开过的每张工单一条，一步复制交接文本。只取本会话的工具行（帧里的 handoff），不读工单文件。
 */
export function HandoffStrip({ rows }: { rows: ConvRow[] }) {
  const tickets = handoffTickets(rows);
  if (tickets.length === 0) return null;
  return (
    <div className="conv-handoff" data-testid="handoff-strip">
      {tickets.map((t) => (
        <div key={t.id} className="conv-handoff-row" data-ticket={t.id}>
          <span>本会话开了工单</span>
          <span className="conv-handoff-id">{t.id}</span>
          <span>（待修）</span>
          <span className="spacer" />
          <CopyButton text={t.handoff} label="复制给 coding agent" testId="handoff-strip-copy" />
        </div>
      ))}
    </div>
  );
}

export function ConversationPane({ rows, lead, cards, running = false, awaiting = false }: { rows: ConvRow[]; lead?: ReactNode; cards?: ReactNode; running?: boolean; awaiting?: boolean }) {
  const ref = useRef<HTMLDivElement | null>(null);
  const stick = useRef(true);
  // D80（R3 ④）：只有本次挂载之后才到达的行播入场；历史恢复、切会话重挂时已在的行不播（App 以 convKey 为 key 重挂）
  const mountedAt = useRef(Date.now());
  const [opened, setOpened] = useState<Readonly<Record<string, boolean>>>({});
  const opens: Opens = {
    get: (key, dflt) => opened[key] ?? dflt,
    toggle: (key, current) => setOpened((o) => ({ ...o, [key]: !current })),
  };
  // 每次渲染后（新帧、卡片进出、窗口变化引起的重排）若人在底部就跟到底；App 以 convKey 为 key 重挂，切会话即从底部开始。
  // D80 R6：有卡片待答时不跟——卡片自己负责进视野（比可见区高时让卡面文本顶到上沿）；运行中每秒的重渲染若照跟到底，
  // 会把刚滚好的卡头又滚走（滚动事件异步，stick 标记来不及更新）。回合停在等人答，这期间本就没有新行
  useLayoutEffect(() => {
    const el = ref.current;
    if (el && stick.current && !awaiting) el.scrollTop = el.scrollHeight;
  });
  const fresh = (r: ConvRow) => !("hist" in r && r.hist) && r.at >= mountedAt.current;
  const segments = turnSegments(rows, running);
  const showActivity = running && !awaiting;
  const activityPlaced = segments.some((s) => s.k === "turn" && s.running);
  return (
    <div
      className="conv-stream"
      data-testid="conv-stream"
      ref={ref}
      onScroll={(e) => {
        const el = e.currentTarget;
        stick.current = el.scrollHeight - el.scrollTop - el.clientHeight <= STICK_PX;
      }}
    >
      {lead}
      {segments.map((seg) =>
        seg.k === "loose" ? (
          <RowView key={seg.index} rows={rows} index={seg.index} running={running} fresh={fresh} opens={opens} />
        ) : (
          <Turn key={seg.key} seg={seg} rows={rows} fresh={fresh} opens={opens} activity={showActivity && seg.running ? <Activity rows={rows} /> : null} />
        ),
      )}
      {showActivity && !activityPlaced && <Activity rows={rows} />}
      {cards}
    </div>
  );
}

/** 每秒走一次的时钟：只在 active 时开计时器 */
function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [active]);
  return active ? now : Date.now();
}

/**
 * 高度过渡（R2 ③ / R4 ④）：grid-template-rows 0fr ↔ 1fr，纯 CSS；收起时等过渡结束再卸载内容，避免高度塌陷闪烁。
 * 初次渲染就是展开的不播过渡。
 */
function Collapse({ open, children }: { open: boolean; children: () => ReactNode }) {
  const [mounted, setMounted] = useState(open);
  const [shown, setShown] = useState(open);
  useEffect(() => {
    if (open) {
      setMounted(true);
      const raf = requestAnimationFrame(() => setShown(true));
      return () => cancelAnimationFrame(raf);
    }
    setShown(false);
    const t = setTimeout(() => setMounted(false), COLLAPSE_UNMOUNT_MS);
    return () => clearTimeout(t);
  }, [open]);
  return (
    <div
      className="conv-collapse"
      data-open={shown ? "1" : "0"}
      onTransitionEnd={(e) => {
        if (e.target === e.currentTarget && !open) setMounted(false);
      }}
    >
      <div className="conv-collapse-inner">{mounted ? children() : null}</div>
    </div>
  );
}

function Turn({ seg, rows, fresh, opens, activity }: { seg: Extract<Segment, { k: "turn" }>; rows: ConvRow[]; fresh: (r: ConvRow) => boolean; opens: Opens; activity: ReactNode }) {
  const now = useNow(seg.running);
  const open = opens.get(seg.key, turnDefaultOpen(seg));
  const hasBlock = seg.process.length > 0 || activity !== null;
  return (
    <>
      <RowView rows={rows} index={seg.user} running={seg.running} fresh={fresh} opens={opens} />
      {hasBlock && (
        <div className={`conv-turn${fresh(rows[seg.user]) ? " conv-enter" : ""}`} data-testid="conv-turn" data-outcome={seg.outcome} data-open={open ? "1" : "0"}>
          <button type="button" className="conv-fold" data-testid="turn-fold" aria-expanded={open} onClick={() => opens.toggle(seg.key, open)}>
            <span className={seg.running ? "conv-shimmer" : undefined}>{foldLabel(seg, now)}</span>
            {seg.tools > 0 && (
              <span className="conv-fold-meta">
                · {seg.tools} 个工具
                {seg.fails > 0 && <span className="conv-fold-fail"> · {seg.fails} 个失败</span>}
              </span>
            )}
            <span className="conv-chev">
              <Icon name="chevron-right" size="sm" />
            </span>
          </button>
          <Collapse open={open}>
            {() => (
              <div className="conv-turn-body">
                {seg.process.map((it) => (
                  <ProcView key={it.k === "row" ? it.index : it.key} item={it} rows={rows} turnRunning={seg.running} fresh={fresh} opens={opens} scope={seg.key} />
                ))}
                {activity}
              </div>
            )}
          </Collapse>
        </div>
      )}
      {seg.tail.map((i) => (
        <RowView key={i} rows={rows} index={i} running={seg.running} fresh={fresh} opens={opens} />
      ))}
    </>
  );
}

function ProcView({ item, rows, turnRunning, fresh, opens, scope }: { item: ProcItem; rows: ConvRow[]; turnRunning: boolean; fresh: (r: ConvRow) => boolean; opens: Opens; scope: string }) {
  if (item.k === "row") return <RowView rows={rows} index={item.index} running={turnRunning} fresh={fresh} opens={opens} />;
  const tools = item.indices.map((i) => rows[i]).filter((r): r is ToolRow => r.k === "tool");
  const key = `${scope}/${item.key}`;
  const open = opens.get(key, groupDefaultOpen(rows, item.indices, turnRunning));
  const fails = tools.filter((t) => t.ok === false).length;
  const runningNow = turnRunning && tools.some((t) => t.ok === null);
  const st = runningNow ? "run" : fails > 0 ? "fail" : "ok";
  return (
    <div className={`conv-group${fresh(tools[0]) ? " conv-enter" : ""}`} data-testid="conv-group" data-open={open ? "1" : "0"} data-status={st}>
      <div className="conv-tool">
        <button type="button" className="conv-tool-head" data-testid="group-toggle" aria-expanded={open} onClick={() => opens.toggle(key, open)}>
          <Icon name="terminal" size="sm" />
          <span className="conv-tool-cat">终端</span>
          <span className="conv-tool-main">{tools.length} 个命令</span>
        </button>
        <span className="conv-tool-status" data-st={st}>
          {runningNow ? "执行中" : fails > 0 ? `${fails} 个失败` : "全部成功"}
        </span>
        <Chevron open={open} onToggle={() => opens.toggle(key, open)} />
      </div>
      <Collapse open={open}>
        {() => (
          <div className="conv-group-body">
            {item.indices.map((i) => (
              <RowView key={i} rows={rows} index={i} running={turnRunning} fresh={fresh} opens={opens} inGroup />
            ))}
          </div>
        )}
      </Collapse>
    </div>
  );
}

/** 行尾的展开箭头：鼠标点它与点摘要同效；键盘与读屏走摘要按钮（它带 aria-expanded），这里不进 Tab 序 */
function Chevron({ open, onToggle }: { open: boolean; onToggle: () => void }) {
  return (
    <button type="button" className="conv-chev conv-chev-btn" data-open={open ? "1" : "0"} tabIndex={-1} aria-hidden="true" onClick={onToggle}>
      <Icon name="chevron-right" size="sm" />
    </button>
  );
}

function ToolLine({ row, turnRunning, fresh, opens, inGroup }: { row: ToolRow; turnRunning: boolean; fresh: boolean; opens: Opens; inGroup: boolean }) {
  const key = `tool:${row.id}`;
  const open = opens.get(key, false);
  const status = toolStatus(row, turnRunning);
  const cat = toolCategory(row.name);
  const running = status === "run";
  const lastLog = row.logs.length > 0 ? row.logs[row.logs.length - 1] : "";
  // R3 ③：执行中的流水线作业，次文本换成最新一行作业输出（滚动替换）；core 不发进度，进度条只来回扫光
  const ticker = running && row.name === "run_pipeline" && lastLog.trim() !== "";
  const sub = ticker ? lastLog : subText(row);
  const dur = durText(row.durationMs);
  const fail = status === "fail" ? firstLine(row.observation) : "";
  return (
    <div
      className={`conv-tool-row${fresh ? " conv-enter" : ""}`}
      data-testid="conv-row"
      data-kind="tool"
      data-ok={row.ok === null ? "pending" : row.ok ? "ok" : "fail"}
      data-status={status}
      data-duplicate={row.duplicate ? "1" : "0"}
      data-enter={fresh ? "1" : undefined}
    >
      <div className="conv-tool" title={row.command !== null ? `${row.name}\n${row.command}` : row.name}>
        <button type="button" className="conv-tool-head" data-testid="tool-toggle" aria-expanded={open} onClick={() => opens.toggle(key, open)}>
          <Icon name={CATEGORY_ICON[cat] ?? "pulse"} size="sm" />
          {!inGroup && (
            <span className={`conv-tool-cat${running ? " conv-shimmer" : ""}`} data-testid={running ? "conv-tool-shimmer" : undefined}>
              {cat}
            </span>
          )}
          <span className={`conv-tool-main${inGroup && running ? " conv-shimmer" : ""}`} data-testid={inGroup && running ? "conv-tool-shimmer" : undefined}>
            {mainText(row)}
          </span>
          {sub !== "" && (
            <span key={ticker ? lastLog : "sub"} className={`conv-tool-sub${ticker ? " conv-ticker" : ""}`}>
              {sub}
            </span>
          )}
          {running && row.name === "run_pipeline" && (
            <span className="conv-progress" aria-hidden="true">
              <span />
            </span>
          )}
        </button>
        {row.command !== null && !open && (
          <CopyButton text={row.command} label={<Icon name="copy" size="sm" />} ariaLabel="复制命令" testId="tool-copy" className="conv-tool-copy" />
        )}
        {/* D82（人选 C）：已开工单的工具行行尾「复制给 coding agent」，复制的是帧里的交接文本原文（core 模板生成） */}
        {row.handoff !== null && <CopyButton text={row.handoff} label="复制给 coding agent" testId="tool-handoff" className="conv-tool-handoff" />}
        {dur !== "" && <span className="conv-tool-dur">{dur}</span>}
        {row.duplicate && <span className="ui-badge">重复调用</span>}
        <span className="conv-tool-status" data-st={status} title={fail !== "" ? fail : undefined} data-testid="tool-status">
          {STATUS_WORD[status]}
        </span>
        <Chevron open={open} onToggle={() => opens.toggle(key, open)} />
      </div>
      <Collapse open={open}>
        {() => (
          <div className="conv-tool-detail">
            {row.command !== null ? (
              <div className="conv-tool-cmd">
                <code>{row.command}</code>
                <CopyButton text={row.command} label={<Icon name="copy" size="sm" />} ariaLabel="复制命令" testId="tool-detail-copy" />
              </div>
            ) : (
              <div className="conv-tool-lbl">
                {row.name}
                {row.summary !== "" ? ` · ${row.summary}` : ""}
              </div>
            )}
            {row.logs.length > 0 && (
              <div data-testid="conv-logs">
                <div className="conv-tool-lbl">作业输出（末 {row.logs.length} 行）</div>
                <pre className="conv-tool-out">{row.logs.join("\n")}</pre>
              </div>
            )}
            {row.observation !== null && (
              // 非成功才有：逐字、不截断（S9-R1 判定由 core 给出；host 不解析）
              <div data-testid="conv-observation">
                <div className="conv-tool-lbl">模型看到的原文</div>
                <pre className="conv-obs">{row.observation}</pre>
              </div>
            )}
          </div>
        )}
      </Collapse>
    </div>
  );
}

/** D46 → D80（R3 ②）：回合运行中的指示行。回复整段到达，模型生成期间别无动静，靠扫光 + 每秒走的秒数说明「还在动」。
 *  秒数从最后一行到达起算；系统「减少动态效果」只停扫光，秒数照走。 */
function Activity({ rows }: { rows: ConvRow[] }) {
  const last = rows.length > 0 ? rows[rows.length - 1] : null;
  // 已结束的工具行：at 是开始时刻，结束时刻 = at + durationMs（否则工具耗时会被算进「正在思考」）
  const since = last ? (last.k === "tool" && last.durationMs !== null ? last.at + last.durationMs : last.at) : Date.now();
  const now = useNow(true);
  const toolRunning = last?.k === "tool" && last.ok === null;
  const secs = Math.max(0, Math.floor((now - since) / 1000));
  return (
    <div className="conv-activity" data-testid="conv-activity" data-what={toolRunning ? "tool" : "model"} role="status">
      <span className="conv-shimmer">{toolRunning ? "正在执行" : "正在思考"}</span>
      <span className="conv-activity-secs">· {secs} 秒</span>
    </div>
  );
}

function RowView({ rows, index, running, fresh, opens, inGroup = false }: { rows: ConvRow[]; index: number; running: boolean; fresh: (r: ConvRow) => boolean; opens: Opens; inGroup?: boolean }) {
  const row = rows[index];
  if (row.k === "tool") return <ToolLine row={row} turnRunning={running} fresh={fresh(row)} opens={opens} inGroup={inGroup} />;
  return <Row row={row} fresh={fresh(row)} />;
}

/** D80 R6.3：卡片结局的状态色——待批准用等待色、被拒用危险色，其余中性（语义色只给真实状态） */
const CARD_TONE: Readonly<Record<string, string>> = { open: "wait", approved: "ok-muted", rejected: "fail", voided: "ok-muted" };

/** D80 R5：回合末遥测默认只显示短摘要；ⓘ 展开完整原文（字段一个不删，仍可选中复制） */
function Footer({ text, brief, claims }: { text: string; brief: string; claims: readonly string[] }) {
  const [open, setOpen] = useState(false);
  return (
    <>
    {claims.length > 0 && (
      // D105（人选 B）：回复下方单独一条警示，列出没有工具记录支撑的核查措辞；只提示，不拦截
      <div className="conv-notice conv-notice--warn" data-testid="unbacked-claims" role="note">
        <Icon name="alert" size="sm" />
        <span>本回合没有调用任何工具。回复里说的「{claims.join("」「")}」没有本回合的工具记录：指的是前几轮的查证就往上对照，否则按未核实看待。</span>
      </div>
    )}
    <div className="conv-foot" data-testid="conv-row" data-kind="footer">
      <div className="conv-foot-line">
        <span data-testid="footer-brief">{brief}</span>
        <button type="button" className="conv-chev-btn conv-foot-more" data-testid="footer-more" aria-expanded={open} aria-label="完整遥测" title={text} onClick={() => setOpen((o) => !o)}>
          <Icon name="info" size="sm" />
        </button>
      </div>
      {open && <div data-testid="footer-full">{text}</div>}
    </div>
    </>
  );
}

/** 非工具行。D80：只有实时到达的用户消息与答复挂入场标记（R3 ④）；工具行在 ToolLine */
function Row({ row, fresh }: { row: Exclude<ConvRow, ToolRow>; fresh: boolean }) {
  const enter = fresh ? " conv-enter" : "";
  switch (row.k) {
    case "user":
      return (
        <div className={`conv-user${enter}`} data-testid="conv-row" data-kind="user" data-enter={fresh ? "1" : undefined}>
          {row.text}
        </div>
      );
    case "assistant": {
      const label = row.label ?? (row.kind === "wrapup" ? "收尾总结" : row.kind === "local_note" ? "本地说明（未进入会话历史）" : null);
      return (
        <div
          className={`${row.kind === "answer" ? "conv-assistant conv-md" : row.kind === "wrapup" ? "conv-note conv-md" : "conv-note"}${enter}`}
          data-testid="conv-row"
          data-kind={row.kind}
          data-enter={fresh ? "1" : undefined}
        >
          {label && <span className="conv-label" data-testid="conv-label">{label}</span>}
          {row.kind === "local_note" ? row.text : <ChatMarkdown text={row.text} />}
        </div>
      );
    }
    case "injection":
      return (
        <details data-testid="conv-row" data-kind="injection">
          <summary className="conv-note">系统注入（{row.text.length} 字：提示词、规程或记忆，模型当时看到的原文）</summary>
          <pre className="conv-obs">{row.text}</pre>
        </details>
      );
    case "log":
      return (
        <details data-testid="conv-row" data-kind="log">
          <summary className="conv-note">作业输出（末 {row.lines.length} 行）</summary>
          <pre className="conv-obs">{row.lines.join("\n")}</pre>
        </details>
      );
    case "card": {
      // D80 R6.3：与工具行同结构（方案 A）——类别词是卡类，主文本是工具名，状态词是卡的结局（被拒附反馈原文、作废附原因）
      const { kind, tool } = cardParts(row.label);
      const phrase = cardPhrase(row);
      return (
        <div className="conv-tool-row conv-card" data-testid="conv-row" data-kind="card" data-request-id={row.requestId} data-status={row.status}>
          <div className="conv-tool" title={row.text}>
            <span className="conv-tool-lead">
              <Icon name="alert" size="sm" />
              <span className="conv-tool-cat">{kind}</span>
              {tool !== "" && <span className="conv-tool-main">{tool}</span>}
            </span>
            <span className="conv-tool-status conv-card-phrase" data-st={CARD_TONE[row.status]}>
              {phrase}
            </span>
          </div>
        </div>
      );
    }
    case "stop":
      return (
        <div className="conv-note" data-testid="conv-row" data-kind="stop">
          {row.text}
        </div>
      );
    case "notice":
      return (
        <div className={`conv-notice conv-notice--${row.level}`} data-testid="conv-row" data-kind="notice" data-code={row.code} data-level={row.level}>
          <Icon name={row.level === "error" ? "alert" : "info"} size="sm" />
          <code>{row.code}</code>
          <span>{row.text}</span>
        </div>
      );
    case "footer":
      return <Footer text={row.text} brief={row.brief} claims={row.claims} />;
    case "historySeparator":
      // 文案与 Spec 10 §2.3 一致；红线 3 / TG-14 禁止 desktop/src 出现「会话记录文件名」字面量，故拆开拼接
      return (
        <div className="conv-foot" data-testid="conv-row" data-kind="history-separator">
          {"以下为恢复的历史，重建自 " + "session" + ".jsonl" + "：只含消息，不含当时的工具耗时与作业输出"}
        </div>
      );
    case "exited":
      return (
        <div data-testid="conv-row" data-kind="exited">
          <div className="conv-note">
            会话已结束（{row.code === null ? `信号 ${row.signal ?? "?"}` : `退出码 ${row.code}`}）
          </div>
          {row.stderrTail.trim() !== "" && (
            <details>
              <summary className="conv-note">stderr 尾部（诊断，从不解析）</summary>
              <pre className="conv-obs">{row.stderrTail}</pre>
            </details>
          )}
        </div>
      );
    case "framesLost":
      return (
        <div className="conv-notice conv-notice--warn" data-testid="conv-row" data-kind="frames-lost">
          <Icon name="alert" size="sm" />
          此处丢失 {row.count} 帧（{row.reason === "oversize" ? "超过单帧上限" : "无法解析"}）
        </div>
      );
  }
}
