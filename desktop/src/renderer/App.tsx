// 桌面端 v1 主界面（Spec 8 §2.11；Spec 10 PR1 版面重排：产物树移左栏、中栏 = 工序卡 + 待答区）。
// UI 自身零判定：工序一律取 status --json，只呈现确定性事实；不做 LLM 推荐（direction §5）。
// 类名与元素种类按 Spec 14 §3.1/§3.2 的契约（可点击的行一律原生 <button class="ui-row">，VS-8）。
import { useCallback, useEffect, useReducer, useRef, useState, type ReactNode } from "react";
import type { ApprovalJson } from "../shared/contracts";
import { isStopType } from "../shared/contracts";
import { foldOf, emptyConvStore, reduceConvs, type ConvAction, type ConvStore } from "./convStore";
import { eventLabel } from "../shared/fold";
import { lastContextReading } from "../shared/convFold";
import type { ConvDelta, ConvKey, ConvSnapshot, CreatedEpisode, EpisodeDelta, EpisodeSnapshot, EpisodesList, EpisodeSummary, Health, SessionRow, ShotsEntry, TreeEntry } from "../shared/protocol";
import type { ConvEntry } from "../shared/protocol";
import { previewKind } from "../shared/previewKind";
import { STOP_PREVIEW } from "../shared/stopPreview";
import { initialAutoOpen, step, type AutoOpenState } from "./autoOpen";
import { formatRelTime } from "../shared/relTime";
import { filterEpisodes, groupByStep } from "../shared/episodeView";
import { AnswerDock } from "./HumanCards";
import { Composer } from "./Composer";
import { ConversationPane } from "./ConversationPane";
import { CoverImport } from "./CoverImport";
import { HumanTimeReadout } from "./HumanTimeReadout";
import { confirmDiscardDirty, ScriptEditor } from "./ScriptEditor";
import { VoicePanel } from "./VoicePanel";
import { readyInfo, SessionHeader, SessionNotes } from "./SessionHeader";
import { Icon, type IconName } from "./icons";
import { NewEpisodeForm } from "./NewEpisodeForm";
import { clampLeft, effectivePreviewW, gridColumns, LEFT_DEFAULT, LEFT_MAX, LEFT_MIN, previewMax, PREVIEW_MIN, type Layout } from "./layout";
import { Splitter } from "./Splitter";
import { SessionList } from "./SessionList";
import { PreviewPane, type PreviewTarget } from "./PreviewPane";
import { PreviewSwitcher } from "./PreviewSwitcher";
import { generatingOf } from "../shared/artifactGroups";
import { errText, RpcClient } from "./rpc";
import { applyTheme, readEpisodeView, readLayout, readTheme, saveEpisodeView, saveLayout, saveTheme, type EpisodeViewPref, type Theme } from "./theme";
import { approvalsOf, emptyStore, reduce, select, type Action, type EpisodeState, type Store } from "./store";
import { installTestHooks } from "./testHooks";
import { Badge, StateView, StatusDot } from "./ui";

const rpc = new RpcClient(window);

/** 熔断后 main 把 renderer 换成致命面板（§2.9）：内容随 URL 查询参数带来，不再连接任何 host */
const FATAL = new URLSearchParams(window.location.search).get("fatal");

/**
 * 门禁 14 已退役（Spec 11 S8-R15，2026-09-26）：人时记账命令（/record-time）落地，
 * 桌面端审阅面可见即计时并落 human_time.json，不再需要「数据源不完整」旁标。
 */

/** 产物树行图标（按扩展名分发，与 PreviewPane 同一份判据） */
const KIND_ICON: Record<string, IconName> = { video: "film", audio: "wave", image: "image", html: "file" };

function storeReducer(s: Store, a: Action): Store {
  return reduce(s, a).store;
}

function convReducer(s: ConvStore, a: ConvAction): ConvStore {
  return reduceConvs(s, a).store;
}

/** 从会话条目里取出已在界面提醒过的记忆待确认（notice{code:"memory_unconfirmed"}）。 */
function hasMemoryAsk(entries: readonly ConvEntry[]): boolean {
  return entries.some((e) => e.k === "frame" && e.frame.t === "notice" && e.frame.code === "memory_unconfirmed");
}

export function App() {
  return FATAL !== null ? <FatalPanel text={FATAL} /> : <Main />;
}

function FatalPanel({ text }: { text: string }) {
  return (
    <div className="fatal" data-testid="fatal">
      <h2>ava-host 反复崩溃，已停止重启</h2>
      <p>界面不再连接任何后台进程。排查后请退出并重新打开 app。</p>
      <pre data-testid="fatal-detail">{text}</pre>
    </div>
  );
}

function Main() {
  const [health, setHealth] = useState<Health | null>(null);
  const [list, setList] = useState<EpisodesList | null>(null);
  const [store, dispatch] = useReducer(storeReducer, emptyStore);
  const storeRef = useRef(store);
  storeRef.current = store;
  const [convs, dispatchConv] = useReducer(convReducer, emptyConvStore);
  const convsRef = useRef(convs);
  convsRef.current = convs;
  /** 左栏「选题」入口：未选期时中栏即为 idea 视图；不结束任何会话（后台期照常跑） */
  const [showIdea, setShowIdea] = useState(true);
  /** 刚建好的期：对话区首行本地提示（不是消息，H-8） */
  const [justCreated, setJustCreated] = useState<CreatedEpisode | null>(null);
  /** Spec 18 §3.3 ④：选题讨论刚被带进哪一期（选题视图在新会话起来之前显示「已带入 <期名>」） */
  const [ideaCarriedTo, setIdeaCarriedTo] = useState<string | null>(null);
  const [active, setActive] = useState<string | null>(null);
  const activeRef = useRef<string | null>(null);
  activeRef.current = active;
  const [linked, setLinked] = useState(false);
  /** 激活 / 刷新在途（载入含 heal 与 status），供界面与 e2e 判断载入是否结束 */
  const [loading, setLoading] = useState(0);
  const [preview, setPreview] = useState<PreviewTarget | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [diags, setDiags] = useState<string[]>([]);
  const [showHealth, setShowHealth] = useState(false);
  /** 停机点自动呼出状态机（Spec 10 §2.7）；PR1 回合状态恒为「未运行」，回合事件的接入随 PR3 */
  const autoRef = useRef<AutoOpenState>(initialAutoOpen);
  const [strip, setStrip] = useState<ApprovalJson | null>(null);
  const [autoOpened, setAutoOpened] = useState<{ id: string | null; count: number }>({ id: null, count: 0 });
  /** 封面导入成功后重挂产物树（导入人刚放下的图应在树里立刻可见） */
  const [treeBump, setTreeBump] = useState(0);
  /** D39 S2 分栏（方案 B）：预览默认收起；停机点自动呼出时不展开（不抢），只在窄条上亮点 */
  // D39 S3：跨重启记忆（全局一份，theme.ts / layout.ts::parseLayout；坏数据回落默认）。写失败只影响下次启动
  const [layout, setLayout] = useState<Layout>(() => readLayout());
  useEffect(() => {
    saveLayout(layout);
  }, [layout]);
  const layoutRef = useRef(layout);
  layoutRef.current = layout;
  const [previewUnseen, setPreviewUnseen] = useState(false);
  const filesOpen = layout.filesOpen;
  const setFilesOpen = (f: (o: boolean) => boolean) => setLayout((l) => ({ ...l, filesOpen: f(l.filesOpen) }));
  const mainRef = useRef<HTMLDivElement | null>(null);
  const [mainW, setMainW] = useState(() => window.innerWidth);
  useEffect(() => {
    const el = mainRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setMainW(el.clientWidth));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const setPreviewOpen = useCallback((open: boolean) => {
    setLayout((l) => ({ ...l, previewOpen: open }));
    if (open) setPreviewUnseen(false);
  }, []);

  const loadHealth = useCallback(() => rpc.call<Health>("app.health").then(setHealth, (e) => setError(errText(e))), []);

  const convKeyOf = (epKey: string | null): ConvKey => (showIdea || !epKey ? "idea" : `ep:${epKey}`);
  const convKey = convKeyOf(active);
  const convKeyRef = useRef<ConvKey>(convKey);
  convKeyRef.current = convKey;
  /** autoOpen 的 reset 事件只接受 none/idle/running/exited；starting/ending 视为 idle */
  const phaseForAuto = (p: ConvSnapshot["phase"]): "none" | "idle" | "running" | "exited" => (p === "starting" || p === "ending" ? "idle" : p);

  useEffect(() => {
    const offs = [
      rpc.on("episode.snapshot", (p) => dispatch({ type: "snapshot", snap: p.data as EpisodeSnapshot })),
      rpc.on("conv.snapshot", (p) => {
        const snap = p.data as ConvSnapshot;
        dispatchConv({ type: "snapshot", snap });
        if (snap.convKey === convKeyRef.current) applyAuto(step(autoRef.current, { kind: "reset", phase: phaseForAuto(snap.phase), turnId: snap.turnId, settlePending: snap.settlePending }, { mediaPlaying: false }));
      }),
      rpc.on("conv.delta", (p) => {
        const d = p.data as ConvDelta;
        const r = reduceConvs(convsRef.current, { type: "delta", delta: d });
        if (r.resnapshot) void rpc.call<ConvSnapshot>("conv.snapshot", { convKey: r.resnapshot }).then((snap) => dispatchConv({ type: "snapshot", snap }));
        else dispatchConv({ type: "delta", delta: d });
          applyConvEventsRef.current(d.convKey, d.entries);
      }),
      rpc.on("episode.delta", (p) => {
        const delta = p.data as EpisodeDelta;
        const r = reduce(storeRef.current, { type: "delta", delta });
        if (r.resnapshot) {
          void rpc.call<EpisodeSnapshot>("episode.resnapshot", { epKey: r.resnapshot }).then((snap) => dispatch({ type: "snapshot", snap }));
        } else {
          dispatch({ type: "delta", delta });
        }
      }),
      rpc.on("episodes.summary", (p) => setList(p.data as EpisodesList)),
      rpc.on("reach", () => {
        void loadHealth();
        void rpc.call<EpisodesList>("episodes.list").then(setList);
      }),
      rpc.on("diag", (p) => setDiags((d) => [...d.slice(-49), String(p.data)])),
    ];
    rpc.onConnect(() => {
      setLinked(true);
      // 🟡-9：host 重连（或 renderer 重载）后清空全部会话桶——新 host 的 generation 带新 bootId
      dispatchConv({ type: "reset-convs" });
      autoRef.current = step(autoRef.current, { kind: "reset", phase: "none", turnId: null, settlePending: null }, { mediaPlaying: false }).state;
      setStrip(null);
      void loadHealth().then(() => undefined);
      void rpc
        .call<EpisodesList>("episodes.list")
        .then((l) => {
          setList(l);
          // 按新 host 认识的活会话取 snapshot（本 spec 只有「当前视图」与「活会话」两类）
          const keys: ConvKey[] = [convKeyRef.current];
          for (const e of l.episodes) if (e.conv?.live) keys.push(`ep:${e.epKey}`);
          if (l.idea?.live) keys.push("idea");
          const unique = [...new Set(keys)];
          unique.forEach((k) => fetchConvRef.current(k, k === convKeyRef.current));
        }, (e) => setError(errText(e)));
      // 重连后恢复活跃（H1，§2.12）
      const cur = activeRef.current;
      if (cur) void rpc.call<EpisodeSnapshot>("episode.activate", { epKey: cur }).then((snap) => dispatch({ type: "snapshot", snap }), (e) => setError(errText(e)));
    });
    rpc.onDisconnect(() => setLinked(false));
    return () => offs.forEach((f) => f());
  }, [loadHealth]);

  useEffect(() => {
    if (health && !health.isPackaged) installTestHooks(health.isPackaged, rpc);
  }, [health]);

  const open = useCallback(async (epKey: string) => {
    // RF-7：02.5 编辑器有未保存改动时先二次确认（切期即卸载，脏缓冲区会蒸发）
    if (!confirmDiscardDirty()) return;
    setError(null);
    setPreview(null);
    setPreviewUnseen(false);
    setShowIdea(false);
    setActive(epKey);
    setLoading((n) => n + 1);
    try {
      const snap = await rpc.call<EpisodeSnapshot>("episode.activate", { epKey });
      dispatch({ type: "snapshot", snap });
    } catch (e) {
      setError(errText(e));
    } finally {
      setLoading((n) => n - 1);
    }
  }, []);

  /** 建期成功（两处「＋ 新建一期」共用）：记下带入结果给 idea-note 两态与选题视图的「已带入」提示 */
  const onCreatedEp = useCallback((created: CreatedEpisode) => {
    setJustCreated(created);
    setIdeaCarriedTo(created.migrated ? created.epKey : null);
    setShowIdea(false);
    void open(created.epKey);
  }, [open]);

  const refresh = useCallback(async () => {
    if (!active) return;
    setLoading((n) => n + 1);
    try {
      dispatch({ type: "snapshot", snap: await rpc.call<EpisodeSnapshot>("episode.refresh", { epKey: active }) });
      void loadHealth();
    } catch (e) {
      setError(errText(e));
    } finally {
      setLoading((n) => n - 1);
    }
  }, [active, loadHealth]);

  const changeRepo = useCallback(async () => {
    if (!confirmDiscardDirty()) return;
    setError(null);
    try {
      const r = await rpc.call<{ changed: boolean; repoRoot: string | null; problem: string | null }>("app.requestRepoRootChange");
      if (r.problem) setError(`仓库未切换：${r.problem}`);
      if (!r.changed) return;
      setActive(null);
      setPreview(null);
      dispatch({ type: "reset" });
      autoRef.current = initialAutoOpen;
      setStrip(null);
      void loadHealth();
      setList(await rpc.call<EpisodesList>("episodes.list"));
    } catch (e) {
      setError(errText(e));
    }
  }, [loadHealth]);

  const ep = select(store, active);
  const conv = convs.convs[convKey];
  const rows = foldOf(conv).rows;

  // D57：选题视图没有活会话时问 core 有没有可接上的选题对话（重开 app 后靠它显示「继续上次的选题对话」）
  const [ideaLast, setIdeaLast] = useState<string | null>(null);
  const ideaPhase = convKey === "idea" ? (conv?.phase ?? "none") : null;
  const ideaIdle = ideaPhase === "none" || ideaPhase === "exited";
  useEffect(() => {
    setIdeaLast(null);
    if (!ideaIdle) return;
    let alive = true;
    rpc.call<SessionRow[]>("conv.sessions", { convKey: "idea" }).then(
      (rows) => {
        const last = [...rows].reverse().find((r) => r.resumable);
        if (alive) setIdeaLast(last ? last.lastActivity : null);
      },
      () => undefined, // 查不到就不显示入口：发消息照样会接上（core 启动即恒恢复）
    );
    return () => {
      alive = false;
    };
  }, [ideaIdle, rpc]);

  /** 打开停机点的预览目标（沿用 Spec 8 §2.5 的映射；这里只决定「看哪份文件」） */
  const showStopPreview = useCallback((epKey: string, a: ApprovalJson) => {
    if (!isStopType(a.type)) return;
    const t = STOP_PREVIEW[a.type];
    if (a.type === "03.5") {
      void rpc.call<TreeEntry[]>("tree.list", { epKey, relDir: t.rel }).then(
        (es) => setPreview({ kind: "dir", root: t.root, rel: t.rel, entries: es.map((x) => x.name) }),
        () => undefined,
      );
    } else {
      setPreview({ kind: "file", root: t.root, rel: `${epKey}/${t.rel}`, size: null });
    }
  }, []);

  /** 「人不空闲」的第二半：预览区里有媒体在播（另一头是 owner === "human"，由 R4 自己维护） */
  const mediaPlaying = () =>
    [...document.querySelectorAll("audio, video")].some((m) => {
      const el = m as HTMLMediaElement;
      return !el.paused && !el.ended;
    });

  const applyAuto = useCallback(
    (r: { open: ApprovalJson | null; state: AutoOpenState }) => {
      autoRef.current = r.state;
      setStrip(r.state.strip);
      if (r.open && activeRef.current) {
        showStopPreview(activeRef.current, r.open);
        setAutoOpened((s) => ({ id: r.open!.approval_id, count: s.count + 1 }));
        if (!layoutRef.current.previewOpen) setPreviewUnseen(true);
      }
    },
    [showStopPreview],
  );

  /** R1：活跃期对象库出现未见过的 pending。卡片不受此延迟影响，仍照常进待答区（§2.7） */
  useEffect(() => {
    if (!ep) return;
    const items = approvalsOf(ep).filter((a) => a.status === "pending" && isStopType(a.type));
    applyAuto(step(autoRef.current, { kind: "pendings", items }, { mediaPlaying: mediaPlaying() }));
  }, [ep, applyAuto]);

  /** R4：人从产物树 / 画廊点开文件（此后不再自动替换，只出「已就绪」条） */
  const pickHuman = useCallback((t: PreviewTarget) => {
    autoRef.current = step(autoRef.current, { kind: "human" }, { mediaPlaying: false }).state;
    setStrip(autoRef.current.strip);
    setPreview(t);
    // 人自己点开文件 = 要看它：展开预览（与停机点自动呼出不同，这不是「抢」）
    setPreviewOpen(true);
  }, [setPreviewOpen]);

  /** R6：切期 / host 重连 / renderer 重载 → 预览清空、owner 归 none、deferred/strip 清空；`seen` 保留；
   * `awaiting` 从会话 snapshot 的三个顶层字段重建（不扫条目：长回合截头会丢 `turn_started`，四轮 🔵-3）。 */
  const resetAutoFor = useCallback((key: ConvKey) => {
    const c = convsRef.current.convs[key];
    const phase = c?.phase ?? "none";
    autoRef.current = step(autoRef.current, { kind: "reset", phase: phaseForAuto(phase), turnId: c?.turnId ?? null, settlePending: c?.settlePending ?? null }, { mediaPlaying: false }).state;
    setStrip(null);
  }, []);
  useEffect(() => {
    resetAutoFor(convKey);
  }, [convKey, resetAutoFor]);

  /** 会话帧驱动 autoOpen：turn_started（R2a）与 host 结算（R2b）。只对当前视图的会话生效。 */
  const applyConvEvents = useCallback(
    (key: ConvKey, entries: readonly ConvEntry[]) => {
      if (key !== convKeyRef.current) return;
      for (const e of entries) {
        if (e.k === "frame" && e.frame.t === "turn_started") applyAuto(step(autoRef.current, { kind: "turn_started", turnId: String(e.frame.turn_id) }, { mediaPlaying: mediaPlaying() }));
        if (e.k === "settled") {
          const epKey = key.startsWith("ep:") ? key.slice(3) : null;
          const items = epKey ? approvalsOf(select(storeRef.current, epKey)).filter((a) => a.status === "pending" && isStopType(a.type)) : [];
          applyAuto(step(autoRef.current, { kind: "settled", turnId: e.turnId, items }, { mediaPlaying: mediaPlaying() }));
        }
      }
    },
    [applyAuto],
  );
  const applyConvEventsRef = useRef(applyConvEvents);
  applyConvEventsRef.current = applyConvEvents;

  const fetchConv = useCallback(
    (key: ConvKey, resetAuto = false) => {
      void rpc.call<ConvSnapshot>("conv.snapshot", { convKey: key }).then(
        (snap) => {
          dispatchConv({ type: "snapshot", snap });
          if (resetAuto && snap.convKey === convKeyRef.current) applyAuto(step(autoRef.current, { kind: "reset", phase: phaseForAuto(snap.phase), turnId: snap.turnId, settlePending: snap.settlePending }, { mediaPlaying: false }));
        },
        () => undefined,
      );
    },
    [applyAuto],
  );
  const fetchConvRef = useRef(fetchConv);
  fetchConvRef.current = fetchConv;

  const reachOk = health?.reach === "ok";
  /** Spec 11 的深度组件按预览目标长出：02.5 编辑器长在 02-script.md 的 .md 预览旁；03.5 顺听面板替下 03-audio 队列 */
  const editorTarget = preview?.kind === "file" && preview.root === "episodes" && preview.rel === `${active}/02-script.md`;
  // 03-audio 目标有两个来源：自动呼出（STOP_PREVIEW 的 rel 不带期名前缀）与产物树行（带期名前缀）
  // 顺听面板装载成功的那一期（null = 未成功 / 出错）：只有成功时才收起纯播放队列
  const [voiceOk, setVoiceOk] = useState<string | null>(null);
  const onVoiceReady = useCallback((ok: boolean) => setVoiceOk(ok ? activeRef.current : null), []);
  const voiceTarget = preview?.kind === "dir" && preview.root === "episodes" && (preview.rel === "03-audio" || preview.rel === `${active}/03-audio`);

  return (
    <div className="app">
      <TopBar health={health} onToggleHealth={() => setShowHealth((v) => !v)} onRefresh={refresh} canRefresh={!!active} onChangeRepo={changeRepo} layout={layout} onToggleLeft={() => setLayout((l) => ({ ...l, leftOpen: !l.leftOpen }))} onTogglePreview={() => setPreviewOpen(!layout.previewOpen)} />
      {showHealth && health && <HealthPanel health={health} diags={diags} linked={linked} />}
      {error && (
        <div className="banner banner-red" data-testid="error">
          <Icon name="alert" size="sm" />
          {error}
        </div>
      )}
      <div className={`main ${reachOk ? "" : "stale"}`} ref={mainRef} style={{ gridTemplateColumns: gridColumns(mainW, layout) }}>
        <nav className="left" id="ava-left" hidden={!layout.leftOpen}>
          <NewEpisodeForm rpc={rpc} onCreated={onCreatedEp} />
          <EpisodeList
            list={list}
            active={active}
            showIdea={showIdea}
            onOpen={open}
            onIdea={() => { setShowIdea(true); setJustCreated(null); }}
            stale={!reachOk}
            convPhase={conv?.phase ?? "none"}
            onConvChanged={() => fetchConvRef.current(convKey, true)}
          />
          {ep && !showIdea && (
            <div className="files">
              <button className="ui-section" aria-expanded={filesOpen} aria-controls="ava-files" onClick={() => setFilesOpen((o) => !o)} data-testid="files-toggle">
                <Icon name={filesOpen ? "chevron-down" : "chevron-right"} size="sm" />
                本期文件
              </button>
              <div id="ava-files" hidden={!filesOpen}>
                <CoverImport epKey={ep.epKey} rpc={rpc} onImported={() => setTreeBump((n) => n + 1)} />
                <ArtifactTree key={`${ep.epKey}:${treeBump}`} epKey={ep.epKey} onPick={pickHuman} />
              </div>
            </div>
          )}
          <GalleryList reachOk={reachOk} onPick={pickHuman} />
        </nav>
        {layout.leftOpen && (
          <Splitter
            label="调整侧栏宽度"
            controls="ava-left"
            value={layout.leftW}
            min={LEFT_MIN}
            max={LEFT_MAX}
            dir={1}
            onChange={(w) => setLayout((l) => ({ ...l, leftW: clampLeft(w) }))}
            onReset={() => setLayout((l) => ({ ...l, leftW: LEFT_DEFAULT }))}
            testId="split-left"
          />
        )}
        <section className="center conv-shell" data-testid="center" data-ep={showIdea ? "" : active ?? ""} data-conv={convKey} data-loading={loading > 0 ? "1" : "0"}>
          {/* S8-R23：与 .main.stale 同一条件（不引入新判定），与 reach-banner 分工不同——横幅说原因，这里标出哪些区域是旧数据 */}
          {!reachOk && (
            <div className="stale-note">
              <span className="ui-badge" data-testid="stale-mark">
                陈旧
              </span>
            </div>
          )}
          {ep && !showIdea && <StatusCard key={ep.epKey} ep={ep} extra={<HumanTimeReadout key={ep.epKey} epKey={ep.epKey} rpc={rpc} version={ep.events.length} />} />}
          <SessionHeader
            rpc={rpc}
            convKey={convKey}
            phase={conv?.phase ?? "none"}
            info={readyInfo(conv?.entries ?? [])}
            memoryAsk={hasMemoryAsk(conv?.entries ?? [])}
            isIdea={convKey === "idea"}
            context={lastContextReading(conv?.entries ?? [])}
            ideaLastActivity={ideaLast}
            onCreated={onCreatedEp}
            onEnded={() => fetchConvRef.current(convKey, true)}
          />
          <div className="conv">
            {justCreated !== null && justCreated.epKey === active && !showIdea && (
              <div className="conv-note" data-testid="idea-note" data-migrated={justCreated.migrated ? "1" : "0"}>
                {justCreated.migrated ? `已带入选题会话记录（${justCreated.messages} 条消息）` : "选题会话的讨论不会带入本期；需要的要点请在这里重述"}
              </div>
            )}
            {showIdea && ideaCarriedTo !== null && (conv?.phase ?? "none") === "none" && (
              <div className="conv-note" data-testid="idea-carried">
                已带入 {ideaCarriedTo}
              </div>
            )}
            <ConversationPane
              key={convKey}
              rows={rows}
              running={conv?.phase === "running"}
              awaiting={(conv?.open.length ?? 0) > 0}
              lead={<SessionNotes info={readyInfo(conv?.entries ?? [])} keyProblem={conv?.keyProblem ?? null} />}
            />
            <AnswerDock conv={conv} ep={ep && !showIdea ? ep : undefined} health={health} rpc={rpc} />
            <Composer
              rpc={rpc}
              convKey={convKey}
              running={conv?.phase === "running"}
              turnId={conv?.turnId ?? null}
              disabled={health?.reach !== "ok"}
              placeholder={convKey === "idea" ? "描述本期想做什么（Enter 发送，Shift+Enter 换行）" : "告诉 ava 要做什么（Enter 发送，Shift+Enter 换行）"}
            />
          </div>
        </section>
        {layout.previewOpen && (
          <Splitter
            label="调整预览宽度"
            controls="ava-preview"
            value={effectivePreviewW(mainW, layout)}
            min={PREVIEW_MIN}
            max={previewMax(mainW, layout)}
            dir={-1}
            onChange={(w) => setLayout((l) => ({ ...l, previewW: w }))}
            onReset={() => setLayout((l) => ({ ...l, previewW: null }))}
            testId="split-preview"
          />
        )}
        {!layout.previewOpen && <PreviewRail target={preview} attention={previewUnseen || strip !== null} onOpen={() => setPreviewOpen(true)} />}
        <section className="preview" id="ava-preview" hidden={!layout.previewOpen} data-auto-open-approval-id={autoOpened.id ?? ""} data-auto-open-count={autoOpened.count}>
          {/* D49-A S4：预览区顶部的文件切换器——侧栏收起时也能选本期任何产物 */}
          {ep && !showIdea && active !== null && (
            <PreviewSwitcher
              epKey={active}
              rpc={rpc}
              current={preview}
              running={ep.jobs.filter((j) => j.state === "running" || j.state === "pending").map((j) => j.command)}
              onPick={pickHuman}
            />
          )}
          {strip && (
            <button className="strip" data-testid="auto-open-strip" onClick={() => applyAuto(step(autoRef.current, { kind: "strip" }, { mediaPlaying: false }))}>
              <Icon name="info" size="sm" />
              停机点 {strip.type} 已就绪 · 查看
            </button>
          )}
          {active !== null && editorTarget ? (
            <ScriptEditor key={`script:${active}`} epKey={active} rpc={rpc} />
          ) : (
            <>
              {/* D50-A S1：03-audio 只留顺听面板一套播放器；面板取不到数据时退回 Spec 8 的纯播放队列 */}
              {active !== null && voiceTarget && <VoicePanel
                  key={`voice:${active}`}
                  epKey={active}
                  rpc={rpc}
                  onReady={onVoiceReady}
                  live={!!ep && ep.jobs.some((j) => j.state === "running" && generatingOf([j.command]).has("03-audio"))}
                />}
              {!(voiceTarget && voiceOk === active) && <PreviewPane key={active ?? "-"} target={preview} head={!(ep && !showIdea && active !== null)} />}
            </>
          )}
          {ep && <Timeline key={ep.epKey} ep={ep} />}
        </section>
      </div>
    </div>
  );
}

// ---------------- 顶栏与横幅 ----------------

function TopBar({
  health,
  onToggleHealth,
  onRefresh,
  canRefresh,
  onChangeRepo,
  layout,
  onToggleLeft,
  onTogglePreview,
}: {
  health: Health | null;
  onToggleHealth: () => void;
  onRefresh: () => void;
  canRefresh: boolean;
  onChangeRepo: () => void;
  layout: Layout;
  onToggleLeft: () => void;
  onTogglePreview: () => void;
}) {
  const p = health?.buildProvenance;
  return (
    <header className="topbar">
      <div className="topbar-row">
        <span className="wordmark">AVA</span>
        {health && !health.isPackaged && (
          <span className="ui-badge ui-badge--warn dev-mark" data-testid="dev-build">
            DEV BUILD
          </span>
        )}
        <span className="repo" data-testid="repo-root">
          <Icon name="repo" size="sm" />
          {health?.repoRoot ?? health?.repoRootProblem ?? "…"}
          {health?.repoHead && <code> @{health.repoHead.slice(0, 8)}</code>}
        </span>
        <span className="spacer" />
        {/* D39 S2：两栏的收起 / 展开开关（macOS 工具栏惯例）；收起的栏仍挂在 DOM 里，搜索词、树展开状态、未保存的编辑都不丢 */}
        <button className="ui-btn ui-btn--ghost ui-btn--sm" aria-pressed={layout.leftOpen} aria-controls="ava-left" onClick={onToggleLeft} data-testid="toggle-left">
          侧栏
        </button>
        <button className="ui-btn ui-btn--ghost ui-btn--sm" aria-pressed={layout.previewOpen} aria-controls="ava-preview" onClick={onTogglePreview} data-testid="toggle-preview">
          预览
        </button>
        <button className="ui-btn ui-btn--ghost ui-btn--sm" onClick={onChangeRepo} data-testid="change-repo">
          切换仓库…
        </button>
        <button className="ui-btn ui-btn--ghost ui-btn--sm" onClick={onRefresh} disabled={!canRefresh} data-testid="refresh">
          <Icon name="refresh" size="sm" />
          刷新
        </button>
        <button className="ui-btn ui-btn--ghost ui-btn--sm" onClick={onToggleHealth} data-testid="health-toggle">
          <Icon name="pulse" size="sm" />
          健康
        </button>
        <ThemeMenu />
      </div>
      {health && health.reach !== "ok" && (
        <div className="banner banner-red" data-testid="reach-banner">
          <Icon name="alert" size="sm" />
          {health.reach}：{health.reachDetail}
        </div>
      )}
      {health && health.codeFreeze.ok === false && (
        <div className="banner banner-yellow">
          <Icon name="alert" size="sm" />
          core Code Freeze：{health.codeFreeze.detail}
        </div>
      )}
      {p?.kind === "dirty" && (
        <div className="banner banner-red">
          <Icon name="alert" size="sm" />
          UI 构建自未提交的 desktop/ 改动
        </div>
      )}
      {p?.kind === "stale" && (
        <div className="banner banner-yellow">
          <Icon name="alert" size="sm" />
          UI 未按当前源码重建（构建于 {p.gitHead.slice(0, 8)}）
        </div>
      )}
      {p?.kind === "incomparable" && (
        <div className="banner banner-grey">
          <Icon name="info" size="sm" />
          无法比对 UI 构建版本（构建提交不在本仓库）
        </div>
      )}
      {p?.kind === "unknown" && (
        <div className="banner banner-grey">
          <Icon name="info" size="sm" />
          无法比对 UI 构建版本：{p.message}
        </div>
      )}
    </header>
  );
}

function HealthPanel({ health, diags, linked }: { health: Health; diags: string[]; linked: boolean }) {
  const rows: [string, string][] = [
    ["host 连接", linked ? "在线" : "断开"],
    ["repoRoot", health.repoRoot ?? `未就绪：${health.repoRootProblem ?? ""}`],
    ["HEAD", health.repoHead ?? "—"],
    ["Python", health.python ?? "—"],
    ["approval 能力", health.capabilities.approvals ? "在场" : `缺席：${health.capabilities.approvalsDetail}`],
    ["纳秒无损解析", health.losslessJson ? "ok" : "不支持（决策条只读）"],
    ["core Code Freeze", health.codeFreeze.detail],
    ["UI 构建溯源", health.buildProvenance.kind],
    ["数据可达性", `${health.reach}${health.reachDetail ? `：${health.reachDetail}` : ""}`],
  ];
  return (
    <div className="health" data-testid="health-panel">
      <table>
        <tbody>
          {rows.map(([k, v]) => (
            <tr key={k} data-row={k}>
              <th>{k}</th>
              <td>{v}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {[...health.diagnostics, ...diags].length > 0 && <pre className="diag">{[...health.diagnostics, ...diags].join("\n")}</pre>}
    </div>
  );
}

/** 外观浮层（D3）：原生 popover API，Esc 关闭且焦点回到触发按钮（E8）；主题三档存 localStorage（§3.3） */
function ThemeMenu() {
  const [theme, setTheme] = useState<Theme>(() => readTheme());
  const choose = (t: Theme) => {
    setTheme(t);
    applyTheme(t);
    saveTheme(t);
  };
  const OPTS: [Theme, string][] = [
    ["system", "跟随系统"],
    ["light", "浅色"],
    ["dark", "深色"],
  ];
  return (
    <>
      <button className="ui-btn ui-btn--ghost ui-btn--sm ui-btn--icon" aria-label="外观" popoverTarget="theme-menu" data-testid="appearance">
        <Icon name="appearance" size="sm" />
      </button>
      <div className="ui-popover" id="theme-menu" popover="" data-testid="theme-menu">
        <div className="ui-popover-title">外观</div>
        <div className="ui-seg">
          {OPTS.map(([v, label]) => (
            <button key={v} aria-pressed={theme === v} onClick={() => choose(v)}>
              {label}
            </button>
          ))}
        </div>
      </div>
    </>
  );
}

// ---------------- 预览收起后的窄条（D39 S2） ----------------

/** 整条是一个按钮：点哪儿都展开。停机点自动呼出（或「已就绪」条）发生在收起期间时亮等待色圆点，不自动展开、不动焦点 */
function PreviewRail({ target, attention, onOpen }: { target: PreviewTarget | null; attention: boolean; onOpen: () => void }) {
  const name = target ? target.rel.split("/").pop() : null;
  return (
    <button className="preview-rail" aria-controls="ava-preview" aria-expanded={false} aria-label={attention ? "展开预览（停机点已就绪）" : "展开预览"} onClick={onOpen} data-testid="preview-rail" data-attention={attention ? "1" : "0"}>
      <Icon name="chevron-right" size="sm" />
      {attention && <span className="ui-dot ui-dot--wait" aria-hidden="true" />}
      <span className="preview-rail-label">预览{name ? ` · ${name}` : ""}</span>
    </button>
  );
}

// ---------------- 期列表 ----------------

/** 侧栏头部（D4）：搜索框 + 视图切换 + 期总数；视图偏好与主题同一份 localStorage 纪律（§3.3） */
function EpisodeList({ list, active, showIdea, onOpen, onIdea, stale, convPhase, onConvChanged }: { list: EpisodesList | null; active: string | null; showIdea: boolean; onOpen: (k: string) => void; onIdea: () => void; stale: boolean; convPhase: ConvSnapshot["phase"]; onConvChanged: () => void }) {
  const idea = list?.idea ?? null;
  const [query, setQuery] = useState("");
  const [view, setView] = useState<EpisodeViewPref>(() => readEpisodeView());
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  /** 相对时间每 60 s 与回到前台时重算（RF-11） */
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const tick = () => setNow(Date.now());
    const t = setInterval(tick, 60_000);
    document.addEventListener("visibilitychange", tick);
    return () => {
      clearInterval(t);
      document.removeEventListener("visibilitychange", tick);
    };
  }, []);
  const chooseView = (v: EpisodeViewPref) => {
    setView(v);
    saveEpisodeView(v);
  };
  const eps = list ? filterEpisodes(list.episodes, query) : [];
  // D45：当前期下面展开它的会话子列表（人 2026-10-08 选方案 B）
  const row = (e: EpisodeSummary) => (
    <div key={e.epKey}>
      {epRow(e)}
      {!showIdea && e.epKey === active && !stale && <SessionList rpc={rpc} convKey={`ep:${e.epKey}` as ConvKey} phase={convPhase} onChanged={onConvChanged} />}
    </div>
  );
  const epRow = (e: EpisodeSummary) => (
    <button
      className="ui-row ep"
      aria-current={!showIdea && e.epKey === active ? "true" : undefined}
      onClick={() => onOpen(e.epKey)}
      data-testid="episode"
    >
      <span className="ep-line">
        <span className="ep-name">{e.epKey}</span>
        <span className="ep-time" title="期目录顶层最近变动">
          {formatRelTime(e.mtimeMs, now)}
        </span>
      </span>
      <span className="ep-step">
        {e.conv?.running && (
          <span className="ui-badge ui-badge--wait" data-testid="conv-running">
            运行中
          </span>
        )}
        {e.conv && e.conv.openRequests > 0 && (
          <span className="ui-badge ui-badge--wait" data-testid="conv-open">
            {e.conv.openRequests} 张卡待答
          </span>
        )}
        {e.isBlocked && <span className="ui-badge stop-mark">停机</span>}
        {e.currentStep === null && <StatusDot tone="unknown" />}
        <span className="ep-step-text">{e.currentStep ?? "…"}</span>
      </span>
    </button>
  );
  return (
    <div className="episodes" data-testid="episodes">
      <div className="side-head">
        <label className="ui-search">
          <Icon name="search" size="sm" />
          <input className="ui-input" placeholder="搜索期名" value={query} onChange={(e) => setQuery(e.target.value)} data-testid="ep-search" />
        </label>
        <div className="side-head-row">
          <div className="ui-seg">
            <button aria-pressed={view === "time"} onClick={() => chooseView("time")}>
              按时间
            </button>
            <button aria-pressed={view === "step"} onClick={() => chooseView("step")}>
              按工序
            </button>
          </div>
          <span className="spacer" />
          {stale && (
            <span className="ui-badge" data-testid="stale-mark">
              陈旧
            </span>
          )}
          {list && <span className="ui-count">{eps.length}</span>}
        </div>
      </div>
      <button className="ui-row ep" aria-current={showIdea ? "true" : undefined} onClick={onIdea} data-testid="idea">
        <span className="ep-line">
          <span className="ep-name">选题</span>
        </span>
        <span className="ep-step">
          {idea?.running && (
            <span className="ui-badge ui-badge--wait" data-testid="conv-running">
              运行中
            </span>
          )}
          {idea !== null && idea.openRequests > 0 && (
            <span className="ui-badge ui-badge--wait" data-testid="conv-open">
              {idea.openRequests} 张卡待答
            </span>
          )}
        </span>
      </button>
      {!list && <div className="muted">加载中…</div>}
      {list && query.trim() !== "" && eps.length === 0 && <div className="muted">没有匹配的期</div>}
      {view === "time" && eps.map(row)}
      {view === "step" &&
        groupByStep(eps).map((g) => (
          <div key={g.key}>
            <button className="ui-section" aria-expanded={!collapsed[g.key]} onClick={() => setCollapsed((c) => ({ ...c, [g.key]: !c[g.key] }))}>
              <Icon name={collapsed[g.key] ? "chevron-right" : "chevron-down"} size="sm" />
              {g.label}
              <span className="ui-count">{g.items.length}</span>
            </button>
            {!collapsed[g.key] && g.items.map(row)}
          </div>
        ))}
      {list && list.hiddenUnderscore > 0 && <div className="muted">另有 {list.hiddenUnderscore} 个 _ 前缀目录未显示</div>}
    </div>
  );
}

// ---------------- 镜头画廊（data/library/shots 顶层 html） ----------------

function GalleryList({ reachOk, onPick }: { reachOk: boolean; onPick: (t: PreviewTarget) => void }) {
  const [items, setItems] = useState<ShotsEntry[] | null>(null);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (open && reachOk) rpc.call<ShotsEntry[]>("shots.list").then(setItems, () => setItems([]));
  }, [open, reachOk]);
  return (
    <div className="galleries">
      <button className="ui-section" onClick={() => setOpen(!open)} data-testid="galleries-toggle" aria-expanded={open}>
        <Icon name={open ? "chevron-down" : "chevron-right"} size="sm" />
        镜头画廊
        {items && <span className="ui-count">{items.length}</span>}
      </button>
      {open && items?.length === 0 && <div className="muted">没有画廊</div>}
      {open &&
        items?.map((g) => (
          <button
            key={g.name}
            className="ui-row"
            data-testid="gallery"
            data-name={g.name}
            onClick={() => onPick({ kind: "file", root: "shots", rel: g.name, size: g.size })}
          >
            <Icon name="file" size="sm" />
            <span className="ui-row-title">{g.name}</span>
          </button>
        ))}
    </div>
  );
}

// ---------------- 工序卡（status --json 原文） ----------------

/**
 * D39 C1/C5：工序卡默认一行（工序 + 停机 + 命令），停机原因、下一步、advisories、人时收进「详情」。
 * 收起时详情仍在 DOM 里（`hidden`），文案一字不改；有 advisories 时「详情」旁标条数，警告不被静默藏掉。
 */
function StatusCard({ ep, extra }: { ep: EpisodeState; extra?: ReactNode }) {
  const [open, setOpen] = useState(false);
  const s = ep.status;
  // 门禁 5：文案与 HEAD 逐字相同（不为换皮改可见字符串），StateView 的形态只承载这一条原文
  if (!s.ok) return <StateView kind="error" title={`工序读取失败（${s.code}）：${s.message}`} />;
  const v = s.value;
  const detailsId = `status-details-${ep.epKey}`;
  return (
    <div className="status" role="group" aria-label="当前工序" data-testid="status">
      <div className="status-step">
        <strong data-testid="current-step">{v.current_step}</strong>
        {v.is_blocked && <span className="ui-badge stop-mark">停机</span>}
        <span className="spacer" />
        {v.advisories.length > 0 && (
          <span className="ui-badge ui-badge--warn" data-testid="status-advisory-count">
            {v.advisories.length} 条提示
          </span>
        )}
        <button className="ui-btn ui-btn--ghost ui-btn--sm" aria-expanded={open} aria-controls={detailsId} onClick={() => setOpen((o) => !o)} data-testid="status-toggle">
          详情
          <Icon name={open ? "chevron-down" : "chevron-right"} size="sm" />
        </button>
      </div>
      {v.next_command && <code className="ui-code cmd">{v.next_command}</code>}
      <div className="status-details" id={detailsId} hidden={!open} data-testid="status-details">
        {v.block_reason && <div className="muted">{v.block_reason}</div>}
        <div>下一步：{v.next_action}</div>
        {v.advisories.length > 0 && (
          <ul className="advisories">
            {v.advisories.map((a) => (
              <li key={a}>
                <Icon name="alert" size="sm" />
                {a}
              </li>
            ))}
          </ul>
        )}
        {extra}
      </div>
    </div>
  );
}

// ---------------- 产物树 ----------------

function ArtifactTree({ epKey, onPick }: { epKey: string; onPick: (t: PreviewTarget) => void }) {
  return (
    <div className="tree" data-testid="tree">
      <TreeDir epKey={epKey} relDir="" depth={0} onPick={onPick} />
    </div>
  );
}

function TreeDir({ epKey, relDir, depth, onPick }: { epKey: string; relDir: string; depth: number; onPick: (t: PreviewTarget) => void }) {
  const [entries, setEntries] = useState<TreeEntry[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [open, setOpen] = useState<Record<string, boolean>>({});
  useEffect(() => {
    rpc.call<TreeEntry[]>("tree.list", { epKey, relDir }).then(setEntries, (e) => setErr(errText(e)));
  }, [epKey, relDir]);
  if (err) return <StateView kind="inline" title={err} />;
  if (!entries) return <div className="muted">…</div>;
  return (
    <ul className="tree-list">
      {entries.map((e) => (
        <li key={e.rel}>
          <button
            className={`ui-row tree-row ${e.kind}`}
            style={{ paddingLeft: 8 + depth * 14 }}
            aria-expanded={e.kind === "dir" ? !!open[e.rel] : undefined}
            data-testid="tree-row"
            data-rel={e.rel}
            onClick={() => {
              if (e.kind === "dir") {
                setOpen((o) => ({ ...o, [e.rel]: !o[e.rel] }));
                void rpc.call<TreeEntry[]>("tree.list", { epKey, relDir: e.rel }).then(
                  (sub) => onPick({ kind: "dir", root: "episodes", rel: `${epKey}/${e.rel}`, entries: sub.filter((x) => x.kind === "file").map((x) => x.name) }),
                  () => undefined,
                );
              } else if (e.kind === "file") {
                onPick({ kind: "file", root: "episodes", rel: `${epKey}/${e.rel}`, size: e.size });
              }
            }}
          >
            {e.kind === "dir" ? <Icon name={open[e.rel] ? "chevron-down" : "chevron-right"} size="sm" /> : null}
            <Icon name={e.kind === "dir" ? "folder" : (KIND_ICON[previewKind(e.name)] ?? "file")} size="sm" />
            <span className="ui-row-title">{e.name}</span>
          </button>
          {e.kind === "dir" && open[e.rel] && <TreeDir epKey={epKey} relDir={e.rel} depth={depth + 1} onPick={onPick} />}
        </li>
      ))}
    </ul>
  );
}

// ---------------- 事件时间线（有损观测） ----------------

// 标签文案在 shared/fold.ts（TV-7 / S8-R9 的判据本体；此处只渲染）

const JOB_TONE: Record<string, "ok" | "danger" | "warn" | "wait" | undefined> = {
  succeeded: "ok",
  failed: "danger",
  blocked: "warn",
  running: "wait",
  pending: undefined,
};

/**
 * D39 C4：时间线收成预览列底部的一行摘要（作业 / 失败 / 事件计数），点开才展开（上限 40vh）。
 * 收起时正文仍在 DOM 里（`hidden`），「观测层有损」声明原样留在正文顶部。
 */
function Timeline({ ep }: { ep: EpisodeState }) {
  const [open, setOpen] = useState(false);
  const nonJob = ep.events.filter((e) => e.kind.startsWith("approval_") || e.kind === "unknown" || e.kind === "human_time_recorded");
  const failed = ep.jobs.filter((j) => j.state === "failed").length;
  const bodyId = `timeline-body-${ep.epKey}`;
  return (
    <footer className="timeline" data-testid="timeline">
      <div className="timeline-head">
        <button className="ui-btn ui-btn--ghost ui-btn--sm" aria-expanded={open} aria-controls={bodyId} onClick={() => setOpen((o) => !o)} data-testid="timeline-toggle">
          <Icon name={open ? "chevron-down" : "chevron-right"} size="sm" />
          时间线
        </button>
        <span className="ui-badge">作业 {ep.jobs.length}</span>
        {failed > 0 && <span className="ui-badge ui-badge--danger">失败 {failed}</span>}
        {ep.degradedNotices.length > 0 && <span className="ui-badge ui-badge--warn">丢事件</span>}
        <span className="ui-badge">事件 {nonJob.length}</span>
      </div>
      <div className="timeline-body" id={bodyId} hidden={!open}>
        <div className="notice">
          <Icon name="info" size="sm" />
          观测层有损（Spec 2 §2.3），轨迹可能不完整；工序以 status 为准
        </div>
        {ep.eventsMeta.truncatedHead && <div className="notice">更早的 job 未载入</div>}
        {ep.degradedNotices.map((d) => (
          <div key={d.at} className="notice notice--warn" data-testid="degraded">
            某进程在熔断期丢了 {d.droppedDuringCircuit} 条事件（不限于本期）· {d.at}
          </div>
        ))}
        <ul className="jobs">
          {ep.jobs.map((j) => (
            <li key={j.jobId} className={`job ${j.state}`} data-testid="job">
              <span className="job-state">
                <Badge tone={JOB_TONE[j.state]}>{j.state}</Badge>
              </span>
              <code>{j.command ?? j.jobId}</code>
              {j.returncode !== null && <span className="muted">rc={j.returncode}</span>}
              {j.durationS !== null && <span className="muted">{j.durationS}s</span>}
              {j.noFollowupEvents && <span className="warn">未见后续事件</span>}
              {j.finishedEventMissing && <span className="warn">进程已不在，未收到 job_finished</span>}
              {j.message && <span className="muted">{j.message}</span>}
              {j.state === "failed" && j.stderrTail && <pre className="stderr">{j.stderrTail}</pre>}
            </li>
          ))}
        </ul>
        {nonJob.length > 0 && (
          <ul className="events">
            {nonJob.map((e) => (
              <li key={e.event_id}>
                <span className="muted">{e.timestamp}</span> {eventLabel(e)}
              </li>
            ))}
          </ul>
        )}
        {(ep.eventsMeta.malformed > 0 || ep.eventsMeta.episodeFieldMismatch > 0) && (
          <div className="muted">
            损坏行 {ep.eventsMeta.malformed}；episode 字段与文件位置不一致 {ep.eventsMeta.episodeFieldMismatch}
          </div>
        )}
      </div>
    </footer>
  );
}
