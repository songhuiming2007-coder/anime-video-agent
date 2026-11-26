// Spec 10 §2.3 / §4.3：对话流（foldConv 的行）。纯展示，不含任何答复/发送类方法名（TG-4′/TG-10/TG-16）。
// D46 修订（人 2026-10-08）：助手回复（answer/wrapup）渲染 Markdown，渲染器关掉链接与图片（ChatMarkdown，H-3 前提仍成立）；
// 卡片、本地说明、工具原文仍是纯文本。失败调用的 observation 逐字、不截断，但默认收起。
// D80（人选方案 A，2026-10-12）：工具行「图标 类别词 主文本 次文本 … 状态词 ›」、只有状态词带语义色；连续 bash 折成终端组；
// 一轮过程折成「已工作 … · N 个工具」，答复留在外面；运行中类别词扫光；新到的行淡入。只改怎么显示，行数据一条不删。
import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { cardParts, cardPhrase, type ConvRow, type ToolRow } from "../shared/convFold";
import {
  durText,
  firstLine,
  foldLabel,
  groupDefaultOpen,
  handoffTickets,
  isToolFail,
  mainText,
  statusWord,
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
import { Collapsible, Disclosure, prefersReducedMotion, usePresence } from "./motion";
import { ChatMarkdown } from "./PreviewPane";

/** 离底部不超过这么多像素就算「人在看最新」：新行到来时跟到底；人往上翻了就不打扰（D39 S2） */
const STICK_PX = 48;
/** 往上翻超过这么多像素时浮出「回到底部」胶囊 */
const JUMP_PX = 240;

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

function rowAnimKey(r: ConvRow, index: number): string {
  if (r.k === "tool") return `tool:${r.id}`;
  if (r.k === "card") return `card:${r.requestId}`;
  return `${r.k}:${index}:${r.at}`;
}

function tailSignature(rows: readonly ConvRow[]): string {
  const n = rows.length;
  if (n === 0) return "0";
  const last = rows[n - 1];
  switch (last.k) {
    case "assistant":
      return `${n}:a:${last.text.length}:${last.streaming ? 1 : 0}`;
    case "tool":
      return `${n}:t:${last.id}:${String(last.ok)}:${last.logs.length}`;
    case "card":
      return `${n}:c:${last.requestId}:${last.status}`;
    case "user":
      return `${n}:u:${last.text.length}`;
    case "notice":
      return `${n}:n:${last.code}:${last.text.length}`;
    default:
      return `${n}:${last.k}:${last.at}`;
  }
}

/**
 * D82（人选 C）：输入框上方的工单提示条。回合跑完默认折叠（D80），工单那条工具行随之收起；
 * 提示条在折叠之外，本会话开过的每张工单一条，一步复制交接文本。只取本会话的工具行（帧里的 handoff），不读工单文件。
 */
export function HandoffStrip({ rows }: { rows: ConvRow[] }) {
  const [dismissed, setDismissed] = useState<ReadonlySet<string>>(() => new Set());
  const tickets = handoffTickets(rows).filter((t) => !dismissed.has(`t:${t.id}`));
  // D159：启动期的工单提醒（notice：ticket_fixed）也归到这里——它本来就是说工单的事，不该在对话流里占行
  const notes = rows.filter(
    (r): r is Extract<ConvRow, { k: "notice" }> =>
      r.k === "notice" && r.code === "ticket_fixed" && !dismissed.has(`n:${r.at}:${r.text}`),
  );
  const hasItems = tickets.length > 0 || notes.length > 0;
  const dismiss = (key: string) =>
    setDismissed((prev) => {
      const next = new Set(prev);
      next.add(key);
      return next;
    });
  return (
    <Disclosure open={hasItems} keepLastContent>
      {hasItems && (
        <div className="conv-handoff" data-testid="handoff-strip">
          {notes.map((n) => {
            const key = `n:${n.at}:${n.text}`;
            return (
              <div key={key} className="conv-handoff-row ui-card-enter" data-testid="handoff-note">
                <Icon name={n.level === "error" ? "alert" : "info"} size="sm" />
                <span className="conv-handoff-text">{n.text}</span>
                <button
                  type="button"
                  className="ui-btn ui-btn--ghost ui-btn--sm ui-btn--icon"
                  aria-label="关闭提醒"
                  onClick={() => dismiss(key)}
                >
                  <Icon name="close" size="sm" />
                </button>
              </div>
            );
          })}
          {tickets.map((t) => {
            const key = `t:${t.id}`;
            return (
              <div key={t.id} className="conv-handoff-row ui-card-enter" data-ticket={t.id}>
                <span>本会话开了工单</span>
                <span className="conv-handoff-id">{t.id}</span>
                <span>（待修）</span>
                <span className="spacer" />
                <CopyButton text={t.handoff} label="复制给 coding agent" testId="handoff-strip-copy" />
                <button
                  type="button"
                  className="ui-btn ui-btn--ghost ui-btn--sm ui-btn--icon"
                  aria-label="隐藏该工单提示"
                  onClick={() => dismiss(key)}
                >
                  <Icon name="close" size="sm" />
                </button>
              </div>
            );
          })}
        </div>
      )}
    </Disclosure>
  );
}

/**
 * running：回合在跑；awaiting：有待人答复的卡（这时该看的是待答区，末尾不放指示行）。
 * 执行中的工具行只在所属回合运行时扫光：回合已结束却没收到结束帧的行（中断、进程退出）是「已停止」。
 * `cards`：D80 R6（人选方案 C）待答的请求卡，挂在对话流末尾（由 App 传入——本组件不含任何答复方法，TG-4′）。
 */
export function ConversationPane({ rows, lead, cards, running = false, awaiting = false }: { rows: ConvRow[]; lead?: ReactNode; cards?: ReactNode; running?: boolean; awaiting?: boolean }) {
  const ref = useRef<HTMLDivElement | null>(null);
  const stick = useRef(true);
  const jumpingRef = useRef(false);
  const jumpTimerRef = useRef<number | null>(null);
  const prevSigRef = useRef(tailSignature(rows));
  const [showJump, setShowJump] = useState(false);
  const [hasUnread, setHasUnread] = useState(false);
  // D80（R3 ④）：只有本次挂载之后才到达的行播入场；历史恢复、切会话重挂时已在的行不播（App 以 convKey 为 key 重挂）
  const mountedAt = useRef(Date.now());
  // 已完成首帧播入场的可折叠子行不再在重新展开 Disclosure 时重复播放滑入动画
  const playedInnerRef = useRef<Set<string>>(new Set());
  const [opened, setOpened] = useState<Readonly<Record<string, boolean>>>({});
  const jump = usePresence(showJump, 140);

  const cancelJump = (restorePill = false) => {
    if (!jumpingRef.current) return;
    jumpingRef.current = false;
    if (jumpTimerRef.current !== null) {
      window.clearTimeout(jumpTimerRef.current);
      jumpTimerRef.current = null;
    }
    if (restorePill) {
      const el = ref.current;
      if (el && el.scrollHeight - el.scrollTop - el.clientHeight > JUMP_PX) {
        setShowJump(true);
      }
    }
  };

  const finishJump = () => {
    if (!jumpingRef.current) return;
    jumpingRef.current = false;
    if (jumpTimerRef.current !== null) {
      window.clearTimeout(jumpTimerRef.current);
      jumpTimerRef.current = null;
    }
    const el = ref.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
    stick.current = true;
    setHasUnread(false);
    setShowJump(false);
  };

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const onScrollEnd = () => finishJump();
    el.addEventListener("scrollend", onScrollEnd);
    return () => {
      el.removeEventListener("scrollend", onScrollEnd);
      if (jumpTimerRef.current !== null) {
        window.clearTimeout(jumpTimerRef.current);
        jumpTimerRef.current = null;
      }
    };
  }, []);

  // 仅在对话内容或状态变化时跟到底（不随回到底部按钮的滚动状态重跑）；有卡片待答时不跟，由卡片自行入视野
  useLayoutEffect(() => {
    const el = ref.current;
    if (el && stick.current && !awaiting && !jumpingRef.current) el.scrollTop = el.scrollHeight;
  }, [rows, cards, running, awaiting]);

  const sig = tailSignature(rows);
  useEffect(() => {
    if (sig !== prevSigRef.current && rows.length > 0 && !stick.current) {
      setHasUnread(true);
    }
    prevSigRef.current = sig;
  }, [sig, rows.length]);

  const markPlayed = useCallback((key: string) => {
    playedInnerRef.current.add(key);
  }, []);

  // 缓存消息树：滚动切出「回到底部」胶囊时跳过整条长对话（100+ 行 Markdown）的无谓重渲染
  const body = useMemo(() => {
    const opens: Opens = {
      get: (key, dflt) => opened[key] ?? dflt,
      toggle: (key, current) => setOpened((o) => ({ ...o, [key]: !current })),
    };
    const fresh = (r: ConvRow, key?: string) => {
      if (("hist" in r && r.hist) || r.at < mountedAt.current) return false;
      if (key !== undefined && playedInnerRef.current.has(key)) return false;
      return true;
    };
    const segments = turnSegments(rows, running);
    const showActivity = running && !awaiting;
    const activityPlaced = segments.some((s) => s.k === "turn" && s.running);
    return (
      <>
        {segments.map((seg) =>
          seg.k === "loose" ? (
            <RowView key={seg.index} rows={rows} index={seg.index} running={running} fresh={fresh} markPlayed={markPlayed} opens={opens} />
          ) : (
            <Turn key={seg.key} seg={seg} rows={rows} fresh={fresh} markPlayed={markPlayed} opens={opens} activity={showActivity && seg.running ? <Activity rows={rows} /> : null} />
          ),
        )}
        {showActivity && !activityPlaced && <Activity rows={rows} />}
      </>
    );
  }, [rows, running, awaiting, opened, markPlayed]);

  return (
    <div className="conv-stream-wrap">
      <div
        className="conv-stream"
        data-testid="conv-stream"
        ref={ref}
        onWheel={() => cancelJump(true)}
        onPointerDown={() => cancelJump(true)}
        onTouchStart={() => cancelJump(true)}
        onKeyDown={(e) => {
          if (e.key === "ArrowUp" || e.key === "PageUp" || e.key === "Home") cancelJump(true);
        }}
        onScroll={(e) => {
          const el = e.currentTarget;
          const gap = el.scrollHeight - el.scrollTop - el.clientHeight;
          const atBottom = gap <= STICK_PX;
          if (atBottom) {
            cancelJump();
            stick.current = true;
            setHasUnread(false);
            setShowJump(false);
          } else if (!jumpingRef.current) {
            stick.current = false;
            setShowJump(gap > JUMP_PX);
          }
        }}
      >
        {lead}
        {body}
        {cards}
      </div>
      {jump.mounted && (
        <div ref={jump.ref} className="conv-jump-wrap" data-status={jump.status}>
          <button
            type="button"
            className="ui-btn ui-btn--sm conv-jump-btn"
            data-testid="conv-jump-bottom"
            data-unread={hasUnread ? "1" : "0"}
            onClick={() => {
              const el = ref.current;
              setShowJump(false);
              setHasUnread(false);
              if (!el) return;
              if (prefersReducedMotion()) {
                cancelJump();
                stick.current = true;
                el.scrollTop = el.scrollHeight;
              } else {
                jumpingRef.current = true;
                if (jumpTimerRef.current !== null) window.clearTimeout(jumpTimerRef.current);
                jumpTimerRef.current = window.setTimeout(finishJump, 1200);
                el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
              }
            }}
          >
            <Icon name="arrow-down" size="sm" />
            {hasUnread ? "有新消息 · 回到底部" : "回到底部"}
          </button>
        </div>
      )}
    </div>
  );
}

/** 每秒走一次的时钟：只在叶节点启用，避免 1Hz tick 触发整段工具列表重渲染 */
function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [active]);
  return active ? now : Date.now();
}

function TurnLabel({ seg }: { seg: Extract<Segment, { k: "turn" }> }) {
  const now = useNow(seg.running);
  return <span className={seg.running ? "conv-shimmer" : undefined}>{foldLabel(seg, now)}</span>;
}

function ElapsedSecs({ since }: { since: number }) {
  const now = useNow(true);
  const secs = Math.max(0, Math.floor((now - since) / 1000));
  return <span className="conv-activity-secs">· {secs} 秒</span>;
}

function Turn({
  seg,
  rows,
  fresh,
  markPlayed,
  opens,
  activity,
}: {
  seg: Extract<Segment, { k: "turn" }>;
  rows: ConvRow[];
  fresh: (r: ConvRow, key?: string) => boolean;
  markPlayed: (key: string) => void;
  opens: Opens;
  activity: ReactNode;
}) {
  const open = opens.get(seg.key, turnDefaultOpen(seg));
  const hasBlock = seg.process.length > 0;
  return (
    <>
      <RowView rows={rows} index={seg.user} running={seg.running} fresh={fresh} markPlayed={markPlayed} opens={opens} />
      {hasBlock && (
        <div className={`conv-turn${fresh(rows[seg.user]) ? " conv-enter" : ""}`} data-testid="conv-turn" data-outcome={seg.outcome} data-open={open ? "1" : "0"}>
          <button type="button" className="conv-fold" data-testid="turn-fold" aria-expanded={open} onClick={() => opens.toggle(seg.key, open)}>
            <TurnLabel seg={seg} />
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
          <Disclosure open={open}>
            {() => (
              <div className="conv-turn-body">
                {seg.process.map((it) => (
                  <ProcView key={it.k === "row" ? it.index : it.key} item={it} rows={rows} turnRunning={seg.running} fresh={fresh} markPlayed={markPlayed} opens={opens} scope={seg.key} />
                ))}
              </div>
            )}
          </Disclosure>
        </div>
      )}
      {seg.tail.map((i) => (
        <RowView key={i} rows={rows} index={i} running={seg.running} fresh={fresh} markPlayed={markPlayed} opens={opens} />
      ))}
      {activity}
    </>
  );
}

function ProcView({
  item,
  rows,
  turnRunning,
  fresh,
  markPlayed,
  opens,
  scope,
}: {
  item: ProcItem;
  rows: ConvRow[];
  turnRunning: boolean;
  fresh: (r: ConvRow, key?: string) => boolean;
  markPlayed: (key: string) => void;
  opens: Opens;
  scope: string;
}) {
  if (item.k === "row") return <RowView rows={rows} index={item.index} running={turnRunning} fresh={fresh} markPlayed={markPlayed} opens={opens} />;
  const tools = item.indices.map((i) => rows[i]).filter((r): r is ToolRow => r.k === "tool");
  const key = `${scope}/${item.key}`;
  const open = opens.get(key, groupDefaultOpen(rows, item.indices, turnRunning));
  const fails = tools.filter(isToolFail).length;
  const runningNow = turnRunning && tools.some((t) => t.ok === null);
  const st = runningNow ? "run" : fails > 0 ? "fail" : "ok";
  const grpAnimKey = `grp:${key}`;
  const isFresh = tools.length > 0 && fresh(tools[0], grpAnimKey);
  useEffect(() => {
    if (isFresh) markPlayed(grpAnimKey);
  }, [isFresh, grpAnimKey, markPlayed]);
  return (
    <div className={`conv-group${isFresh ? " conv-enter" : ""}`} data-testid="conv-group" data-open={open ? "1" : "0"} data-status={st}>
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
      <Disclosure open={open}>
        {() => (
          <div className="conv-group-body">
            {item.indices.map((i) => (
              <RowView key={i} rows={rows} index={i} running={turnRunning} fresh={fresh} markPlayed={markPlayed} opens={opens} inGroup />
            ))}
          </div>
        )}
      </Disclosure>
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

const ToolLine = memo(function ToolLine({
  row,
  turnRunning,
  fresh,
  animKey,
  markPlayed,
  open,
  onToggle,
  inGroup,
}: {
  row: ToolRow;
  turnRunning: boolean;
  fresh: boolean;
  animKey: string;
  markPlayed: (key: string) => void;
  open: boolean;
  onToggle: () => void;
  inGroup: boolean;
}) {
  // 等 240ms 入场动效播完后再记入 playedInnerRef，避免流式输出 ~50ms 后的下一帧重渲染把 .conv-enter 半途撤掉
  useEffect(() => {
    if (!fresh) return;
    const t = window.setTimeout(() => markPlayed(animKey), 280);
    return () => {
      window.clearTimeout(t);
    };
  }, [fresh, animKey, markPlayed]);
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
        <button type="button" className="conv-tool-head" data-testid="tool-toggle" aria-expanded={open} onClick={onToggle}>
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
          {statusWord(row, status)}
        </span>
        <Chevron open={open} onToggle={onToggle} />
      </div>
      <Disclosure open={open}>
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
      </Disclosure>
    </div>
  );
});

/** D46 → D80（R3 ②）：回合运行中的指示行。回复流式或整段到达期间靠扫光 + 独立秒表叶组件说明「还在动」。 */
function Activity({ rows }: { rows: ConvRow[] }) {
  const last = rows.length > 0 ? rows[rows.length - 1] : null;
  const fallbackSinceRef = useRef(Date.now());
  // 已结束的工具行：at 是开始时刻，结束时刻 = at + durationMs（否则工具耗时会被算进「正在思考」）
  const since = last ? (last.k === "tool" && last.durationMs !== null ? last.at + last.durationMs : last.at) : fallbackSinceRef.current;
  const toolRunning = last?.k === "tool" && last.ok === null;
  const streaming = last?.k === "assistant" && last.streaming === true;
  const label = toolRunning ? "正在执行" : streaming ? "正在输出" : "正在思考";
  return (
    <div className="conv-activity" data-testid="conv-activity" data-what={toolRunning ? "tool" : "model"} role="status">
      <span className="conv-shimmer">{label}</span>
      <ElapsedSecs since={since} />
    </div>
  );
}

function RowView({
  rows,
  index,
  running,
  fresh,
  markPlayed,
  opens,
  inGroup = false,
}: {
  rows: ConvRow[];
  index: number;
  running: boolean;
  fresh: (r: ConvRow, key?: string) => boolean;
  markPlayed: (key: string) => void;
  opens: Opens;
  inGroup?: boolean;
}) {
  const row = rows[index];
  if (row.k === "tool") {
    const key = `tool:${row.id}`;
    const open = opens.get(key, false);
    const animKey = rowAnimKey(row, index);
    return (
      <ToolLine
        row={row}
        turnRunning={running}
        fresh={fresh(row, animKey)}
        animKey={animKey}
        markPlayed={markPlayed}
        open={open}
        onToggle={() => opens.toggle(key, open)}
        inGroup={inGroup}
      />
    );
  }
  const cardOpen =
    row.k === "card" && row.status !== "open" && row.detail !== undefined
      ? opens.get(`card:${row.requestId}`, row.status === "rejected" && row.text.includes("我已参考你的建议"))
      : false;
  const toggleCard =
    row.k === "card" && row.status !== "open" && row.detail !== undefined
      ? () => opens.toggle(`card:${row.requestId}`, cardOpen)
      : undefined;
  const animKey = rowAnimKey(row, index);
  return <Row row={row} fresh={fresh(row, animKey)} animKey={animKey} markPlayed={markPlayed} cardOpen={cardOpen} onToggleCard={toggleCard} />;
}

/** D80 R6.3：卡片结局的状态色——待批准用等待色、被拒用危险色，其余中性（语义色只给真实状态） */
const CARD_TONE: Readonly<Record<string, string>> = { open: "wait", approved: "ok-muted", rejected: "fail", voided: "ok-muted" };

/** D80 R5：回合末遥测默认只显示短摘要；ⓘ 展开完整原文（字段一个不删，仍可选中复制） */
const Footer = memo(function Footer({ text, brief, claims, enter }: { text: string; brief: string; claims: readonly string[]; enter: string }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      {claims.length > 0 && (
        // D105（人选 B）：回复下方单独一条警示，列出没有工具记录支撑的核查措辞；只提示，不拦截
        <div className={`conv-notice conv-notice--warn${enter}`} data-testid="unbacked-claims" role="note">
          <Icon name="alert" size="sm" />
          <span>本回合没有调用任何工具。回复里说的「{claims.join("」「")}」没有本回合的工具记录：指的是前几轮的查证就往上对照，否则按未核实看待。</span>
        </div>
      )}
      <div className={`conv-foot${enter}`} data-testid="conv-row" data-kind="footer">
        <div className="conv-foot-line">
          <span data-testid="footer-brief">{brief}</span>
          <button type="button" className="conv-chev-btn conv-foot-more" data-testid="footer-more" aria-expanded={open} aria-label="完整遥测" title={text} onClick={() => setOpen((o) => !o)}>
            <Icon name="info" size="sm" />
          </button>
        </div>
        <Disclosure open={open}>{() => <div data-testid="footer-full">{text}</div>}</Disclosure>
      </div>
    </>
  );
});

/** 非工具行。D80：只有实时到达的行挂入场标记（R3 ④）；播完后记入 playedInnerRef，避免折叠重展时重播 */
const Row = memo(function Row({
  row,
  fresh,
  animKey,
  markPlayed,
  cardOpen,
  onToggleCard,
}: {
  row: Exclude<ConvRow, ToolRow>;
  fresh: boolean;
  animKey: string;
  markPlayed: (key: string) => void;
  cardOpen: boolean;
  onToggleCard?: () => void;
}) {
  useEffect(() => {
    if (!fresh) return;
    const t = window.setTimeout(() => markPlayed(animKey), 280);
    return () => window.clearTimeout(t);
  }, [fresh, animKey, markPlayed]);
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
        <Collapsible
          className={`conv-note${enter}`}
          testId="conv-row"
          dataKind="injection"
          summary={`系统注入（${row.text.length} 字：提示词、规程或记忆，模型当时看到的原文）`}
        >
          {() => <pre className="conv-obs">{row.text}</pre>}
        </Collapsible>
      );
    case "log":
      return (
        <Collapsible
          className={`conv-note${enter}`}
          testId="conv-row"
          dataKind="log"
          summary={`作业输出（末 ${row.lines.length} 行）`}
        >
          {() => <pre className="conv-obs">{row.lines.join("\n")}</pre>}
        </Collapsible>
      );
    case "card": {
      // D80 R6.3：与工具行同结构（方案 A）——类别词是卡类，主文本是工具名，状态词是卡的结局（被拒附反馈原文、作废附原因）
      const { kind, tool } = cardParts(row.label);
      const phrase = cardPhrase(row);
      if (row.status !== "open" && row.detail !== undefined && onToggleCard) {
        return (
          <div className={`conv-tool-row conv-card${enter}`} data-testid="conv-row" data-kind="card" data-request-id={row.requestId} data-status={row.status}>
            <div className="conv-tool" title={row.text}>
              <button type="button" className="conv-tool-head" data-testid="card-toggle" aria-expanded={cardOpen} onClick={onToggleCard}>
                <Icon name="alert" size="sm" />
                <span className="conv-tool-cat">{kind}</span>
                {tool !== "" && <span className="conv-tool-main">{tool}</span>}
              </button>
              <span className="conv-tool-status conv-card-phrase" data-st={CARD_TONE[row.status]}>
                {phrase}
              </span>
              <Chevron open={cardOpen} onToggle={onToggleCard} />
            </div>
            <Disclosure open={cardOpen}>
              {() => (
                <div className="conv-tool-detail">
                  <pre className="conv-tool-out" data-testid="card-detail-text">{row.detail}</pre>
                </div>
              )}
            </Disclosure>
          </div>
        );
      }
      return (
        <div className={`conv-tool-row conv-card${enter}`} data-testid="conv-row" data-kind="card" data-request-id={row.requestId} data-status={row.status}>
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
    case "notice":
      // D159：工单提醒归工单提示条（HandoffStrip），不再进对话流
      if (row.code === "ticket_fixed") return null;
      return (
        <div
          className={`conv-notice conv-notice--${row.level}${enter}`}
          data-testid="conv-row"
          data-kind="notice"
          data-code={row.code}
          data-level={row.level}
          title={row.level === "info" ? row.code : undefined}
        >
          <Icon name={row.level === "error" ? "alert" : "info"} size="sm" />
          {row.level !== "info" && <code>{row.code}</code>}
          <span>{row.text}</span>
        </div>
      );
    case "footer":
      return <Footer text={row.text} brief={row.brief} claims={row.claims} enter={enter} />;
    case "historySeparator":
      // 去掉底层记录文件名，同时遵守红线 3 / TG-14
      return (
        <div className="conv-foot" data-testid="conv-row" data-kind="history-separator">
          以下为恢复的历史记录（不含当时的工具耗时与过程输出）
        </div>
      );
    case "exited": {
      const abnormal = row.code !== 0;
      return (
        <div className={enter ? enter.trim() : undefined} data-testid="conv-row" data-kind="exited">
          {abnormal ? (
            <div className="conv-notice conv-notice--error">
              <Icon name="alert" size="sm" />
              <span>会话异常退出（{row.code === null ? `信号 ${row.signal ?? "?"}` : `退出码 ${row.code}`}）</span>
            </div>
          ) : (
            <div className="conv-note">会话已结束</div>
          )}
          {row.stderrTail.trim() !== "" && (
            <Collapsible className="conv-note" summary="错误输出详情">
              {() => <pre className="conv-obs">{row.stderrTail}</pre>}
            </Collapsible>
          )}
        </div>
      );
    }
    case "framesLost":
      return (
        <div className={`conv-notice conv-notice--warn${enter}`} data-testid="conv-row" data-kind="frames-lost">
          <Icon name="alert" size="sm" />
          此处丢失 {row.count} 帧（{row.reason === "oversize" ? "超过单帧上限" : "无法解析"}）
        </div>
      );
  }
});
