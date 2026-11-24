// 桌面端 v1 主界面（Spec 8 §2.11；Spec 10 PR1 版面重排：产物树移左栏、中栏 = 工序卡 + 待答区）。
// UI 自身零判定：工序一律取 status --json，只呈现确定性事实；不做 LLM 推荐（direction §5）。
// 类名与元素种类按 Spec 14 §3.1/§3.2 的契约（可点击的行一律原生 <button class="ui-row">，VS-8）。
import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import type { ApprovalJson } from "../shared/contracts";
import { isStopType } from "../shared/contracts";
import { foldOf, emptyConvStore, reduceConvs, type ConvAction, type ConvStore } from "./convStore";
import { eventLabel } from "../shared/fold";
import { lastContextReading } from "../shared/convFold";
import type { ConvDelta, ConvKey, ConvSnapshot, CreatedEpisode, EpisodeDelta, EpisodeSnapshot, EpisodesList, EpisodeSummary, Health, ProjectSummary, Provenance, TreeEntry } from "../shared/protocol";
import type { ConvEntry } from "../shared/protocol";
import { STOP_PREVIEW } from "../shared/stopPreview";
import { initialAutoOpen, step, type AutoOpenState } from "./autoOpen";
import { formatRelTime } from "../shared/relTime";
import { filterEpisodes, groupByStep } from "../shared/episodeView";
import { filterProject, plainEpisodes, progressSegments, projectBadges, projectTitle, timeline, volumeRows } from "../shared/projectView";
import { AnswerDock, RequestCards } from "./HumanCards";
import { Composer } from "./Composer";
import { ConversationPane, HandoffStrip } from "./ConversationPane";
import { isScriptDirty, ScriptEditor } from "./ScriptEditor";
import { VoicePanel } from "./VoicePanel";
import { healthAlerts, ModelControls, modelInfo, readyInfo, SessionHeader, SessionNotes, type ModelOpenReq } from "./SessionHeader";
import { FloatingPopover, FloatingTooltip, TooltipGroup } from "./floating";
import { Disclosure, SegmentedBar, useCountBump } from "./motion";
import { CopyButton } from "./copy";
import { Icon } from "./icons";
import { NewEpisodeForm, NewProjectForm, NewVolumeForm } from "./NewEpisodeForm";
import { clampLeft, effectivePreviewW, gridColumns, LEFT_DEFAULT, LEFT_MAX, LEFT_MIN, previewMax, PREVIEW_MIN, type Layout } from "./layout";
import { Splitter } from "./Splitter";
import { SessionList, titleTick } from "./SessionList";
import { PreviewPane, type PreviewTarget } from "./PreviewPane";
import { PreviewSwitcher } from "./PreviewSwitcher";
import { generatingOf } from "../shared/artifactGroups";
import { errText, RpcClient } from "./rpc";
import {
  ACCENTS,
  applyAccent,
  applyTheme,
  readAccent,
  readEpisodeView,
  readLayout,
  readTheme,
  saveAccent,
  saveEpisodeView,
  saveLayout,
  saveTheme,
  type Accent,
  type EpisodeViewPref,
  type Theme,
} from "./theme";
import { approvalsOf, emptyStore, reduce, select, type Action, type EpisodeState, type Store } from "./store";
import { CrashProbe, installTestHooks } from "./testHooks";
import { PaneBoundary } from "./PaneBoundary";
import { JobBar } from "./JobBar";
import { isCheckNotPassed } from "../shared/jobBar";
import { Badge, StatusDot } from "./ui";

const rpc = new RpcClient(window);

/** 熔断后 main 把 renderer 换成致命面板（§2.9）：内容随 URL 查询参数带来，不再连接任何 host */
const FATAL = new URLSearchParams(window.location.search).get("fatal");

/**
 * 门禁 14 已退役（Spec 11 S8-R15，2026-09-26）：人时记账命令（/record-time）落地，
 * 桌面端审阅面可见即计时并落 human_time.json，不再需要「数据源不完整」旁标。
 */

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
  return FATAL !== null ? (
    <FatalPanel text={FATAL} />
  ) : (
    <TooltipGroup>
      <Main />
    </TooltipGroup>
  );
}

function FatalPanel({ text }: { text: string }) {
  return (
    <div className="fatal" data-testid="fatal">
      <h2>后台服务连续异常退出，已停止自动重启</h2>
      <p>当前窗口已断开连接。请复制下方诊断信息排查后，完全退出并重新打开 AVA。</p>
      <div className="ui-card-actions">
        <CopyButton text={text} label="复制诊断信息" testId="fatal-copy" />
      </div>
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
  const [modelOpenReq, setModelOpenReq] = useState<ModelOpenReq | null>(null); // D91：输入框裸 /model、/thinking → 展开工具栏下拉
  /** Spec 18 §3.3 ④：选题讨论刚被带进哪一期（选题视图在新会话起来之前显示「已带入 <期名>」） */
  const [ideaCarriedTo, setIdeaCarriedTo] = useState<string | null>(null);
  const [active, setActive] = useState<string | null>(null);
  const activeRef = useRef<string | null>(null);
  activeRef.current = active;
  /** D81：正在企划会话里的企划（projKey）；与 active（某卷 / 普通期）、showIdea 三者互斥——打开任一处即清另两处 */
  const [activeProject, setActiveProject] = useState<string | null>(null);
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
  const [paneDragging, setPaneDragging] = useState(false);
  const [paneAnimating, setPaneAnimating] = useState(false);
  const paneAnimTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pulsePaneAnim = useCallback(() => {
    setPaneAnimating(true);
    if (paneAnimTimer.current !== null) clearTimeout(paneAnimTimer.current);
    paneAnimTimer.current = setTimeout(() => {
      paneAnimTimer.current = null;
      setPaneAnimating(false);
    }, 185);
  }, []);
  useEffect(() => () => { if (paneAnimTimer.current !== null) clearTimeout(paneAnimTimer.current); }, []);
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
    pulsePaneAnim();
    setLayout((l) => ({ ...l, previewOpen: open }));
    if (open) setPreviewUnseen(false);
  }, [pulsePaneAnim]);
  const [pendingNav, setPendingNav] = useState<(() => void) | null>(null);
  const guardDirty = useCallback((fn: () => void) => {
    if (isScriptDirty()) {
      setPendingNav(() => fn);
      return;
    }
    fn();
  }, []);

  const loadHealth = useCallback(() => rpc.call<Health>("app.health").then(setHealth, (e) => setError(errText(e))), []);

  const convKey: ConvKey = showIdea ? "idea" : activeProject !== null ? `proj:${activeProject}` : active ? `ep:${active}` : "idea";
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
          for (const p of l.projects) if (p.conv?.live) keys.push(`proj:${p.projKey}`);
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

  const openDirect = useCallback(async (epKey: string) => {
    setError(null);
    setPreview(null);
    setPreviewUnseen(false);
    setShowIdea(false);
    setActiveProject(null);
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

  const open = useCallback((epKey: string) => {
    guardDirty(() => void openDirect(epKey));
  }, [guardDirty, openDirect]);

  /** D81：进企划会话。不激活任何期（中栏不显示工序卡，显示企划头部）；预览清空同切期 */
  const openProject = useCallback((projKey: string) => {
    guardDirty(() => {
      setError(null);
      setPreview(null);
      setPreviewUnseen(false);
      setShowIdea(false);
      setActive(null);
      setActiveProject(projKey);
    });
  }, [guardDirty]);

  /** 建期成功（两处「＋ 新建一期」及企划下「＋ 新建卷」共用）：经 guardDirty 保护当前未保存文稿 */
  const onCreatedEp = useCallback((created: CreatedEpisode) => {
    guardDirty(() => {
      setJustCreated(created);
      setIdeaCarriedTo(created.migrated ? created.epKey : null);
      setShowIdea(false);
      void openDirect(created.epKey);
    });
  }, [guardDirty, openDirect]);

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

  const changeRepoDirect = useCallback(async () => {
    setError(null);
    try {
      const r = await rpc.call<{ changed: boolean; repoRoot: string | null; problem: string | null }>("app.requestRepoRootChange");
      if (r.problem) setError(`仓库未切换：${r.problem}`);
      if (!r.changed) return;
      setActive(null);
      setActiveProject(null);
      setPreview(null);
      dispatch({ type: "reset" });
      dispatchConv({ type: "reset-convs" });
      autoRef.current = initialAutoOpen;
      setStrip(null);
      void loadHealth();
      setList(await rpc.call<EpisodesList>("episodes.list"));
    } catch (e) {
      setError(errText(e));
    }
  }, [loadHealth]);

  const changeRepo = useCallback(() => {
    guardDirty(() => void changeRepoDirect());
  }, [guardDirty, changeRepoDirect]);

  const ep = select(store, active);
  // D71：时间线展开状态提到这里——作业条的「看进度」要从预览区之外把它展开（切期即收起，同原先按期重挂载）
  const [timelineOpen, setTimelineOpen] = useState(false);
  useEffect(() => setTimelineOpen(false), [active]);
  const showProgress = useCallback(() => {
    setPreviewOpen(true);
    setTimelineOpen(true);
    requestAnimationFrame(() => document.querySelector("[data-testid=timeline]")?.scrollIntoView({ block: "nearest" }));
  }, [setPreviewOpen]);
  const conv = convs.convs[convKey];
  const folded = foldOf(conv);
  const rows = folded.rows;

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

  /** 「人不空闲」的第二半：预览区里有媒体在播，或文稿编辑器有未保存改动（避免自动呼出静默卸载未保存文稿） */
  const previewBusy = () =>
    isScriptDirty() ||
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
    applyAuto(step(autoRef.current, { kind: "pendings", items }, { mediaPlaying: previewBusy() }));
  }, [ep, applyAuto]);

  /** R4：人从产物树 / 画廊点开文件（此后不再自动替换，只出「已就绪」条） */
  const pickHuman = useCallback((t: PreviewTarget) => {
    const switchingAwayFromScript =
      preview?.kind === "file" &&
      preview.root === "episodes" &&
      activeRef.current !== null &&
      preview.rel === `${activeRef.current}/02-script.md` &&
      !(t.kind === "file" && t.root === "episodes" && t.rel === `${activeRef.current}/02-script.md`);
    const go = () => {
      autoRef.current = step(autoRef.current, { kind: "human" }, { mediaPlaying: false }).state;
      setStrip(autoRef.current.strip);
      setPreview(t);
      // 人自己点开文件 = 要看它：展开预览（与停机点自动呼出不同，这不是「抢」）
      setPreviewOpen(true);
    };
    if (switchingAwayFromScript) guardDirty(go);
    else go();
  }, [guardDirty, preview, setPreviewOpen]);

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
        if (e.k === "frame" && e.frame.t === "turn_started") applyAuto(step(autoRef.current, { kind: "turn_started", turnId: String(e.frame.turn_id) }, { mediaPlaying: previewBusy() }));
        if (e.k === "settled") {
          const epKey = key.startsWith("ep:") ? key.slice(3) : null;
          const items = epKey ? approvalsOf(select(storeRef.current, epKey)).filter((a) => a.status === "pending" && isStopType(a.type)) : [];
          applyAuto(step(autoRef.current, { kind: "settled", turnId: e.turnId, items }, { mediaPlaying: previewBusy() }));
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
  const editorTarget = !showIdea && active !== null && preview?.kind === "file" && preview.root === "episodes" && preview.rel === `${active}/02-script.md`;
  // 03-audio 目标有两个来源：自动呼出（STOP_PREVIEW 的 rel 不带期名前缀）与产物树行（带期名前缀）
  // 顺听面板装载成功的那一期（null = 未成功 / 出错）：只有成功时才收起纯播放队列
  const [voiceOk, setVoiceOk] = useState<string | null>(null);
  const onVoiceReady = useCallback((ok: boolean) => setVoiceOk(ok ? activeRef.current : null), []);
  const voiceTarget = !showIdea && active !== null && preview?.kind === "dir" && preview.root === "episodes" && (preview.rel === "03-audio" || preview.rel === `${active}/03-audio`);
  const activeProjObj = activeProject !== null ? list?.projects.find((x) => x.projKey === activeProject) ?? null : null;
  const headerTitle = showIdea ? "选题" : activeProjObj ? projectTitle(activeProjObj) : activeProject ?? ep?.epKey ?? active ?? "AVA";
  const toggleLeft = useCallback(() => {
    pulsePaneAnim();
    setLayout((l) => ({ ...l, leftOpen: !l.leftOpen }));
  }, [pulsePaneAnim]);
  const alerts = healthAlerts(health);
  const healthSurfaced = alerts.length > 0 || !linked || showHealth;

  // 全局高频快捷键：⌘B 侧栏、⌘J 预览、⌘K 搜期名、⌘R 刷新当前期
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.isComposing || e.altKey || (!e.metaKey && !e.ctrlKey)) return;
      if (document.querySelector(".ui-modal-backdrop")) return;
      const k = e.key.toLowerCase();
      if (k === "b" && !e.shiftKey) {
        e.preventDefault();
        toggleLeft();
      } else if (k === "j" && !e.shiftKey) {
        e.preventDefault();
        setPreviewOpen(!layoutRef.current.previewOpen);
      } else if (k === "k" && !e.shiftKey) {
        e.preventDefault();
        if (!layoutRef.current.leftOpen) {
          pulsePaneAnim();
          setLayout((l) => ({ ...l, leftOpen: true }));
        }
        requestAnimationFrame(() => {
          const inp = document.querySelector<HTMLInputElement>('[data-testid="ep-search"]');
          inp?.focus();
          inp?.select();
        });
      } else if (k === "r" && !e.shiftKey && activeRef.current) {
        e.preventDefault();
        void refresh();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [pulsePaneAnim, refresh, setPreviewOpen, toggleLeft]);

  return (
    <div className="app">
      {health && health.reach !== "ok" && (
        <div className="banner banner-red ui-card-enter" data-testid="reach-banner">
          <Icon name="alert" size="sm" />
          <span>{health.reach}：{health.reachDetail}</span>
        </div>
      )}
      {ep && <JobBar jobs={ep.jobs} onShowProgress={showProgress} />}
      <Disclosure open={showHealth && health !== null}>
        {health && <HealthPanel health={health} diags={diags} linked={linked} onChangeRepo={changeRepo} onClose={() => setShowHealth(false)} />}
      </Disclosure>
      {error && (
        <div className="banner banner-red ui-card-enter" data-testid="error" role="alert">
          <Icon name="alert" size="sm" />
          <span>{error}</span>
          <span className="spacer" />
          <button type="button" className="ui-btn ui-btn--ghost ui-btn--sm ui-btn--icon" aria-label="关闭错误提示" onClick={() => setError(null)} data-testid="error-dismiss">
            <Icon name="close" size="sm" />
          </button>
        </div>
      )}
      <div
        className={`main ${reachOk ? "" : "stale"}`}
        data-left={layout.leftOpen ? "1" : "0"}
        data-dragging={paneDragging ? "1" : "0"}
        data-animating={paneAnimating && !paneDragging ? "1" : "0"}
        ref={mainRef}
        style={{ gridTemplateColumns: gridColumns(mainW, layout) }}
      >
        <nav className="left" id="ava-left" hidden={!layout.leftOpen}>
          {layout.leftOpen && (
            <div className="side-topbar">
              <span className="spacer" />
              <FloatingTooltip
                label={<><span>收起侧栏</span><kbd className="ui-kbd">⌘B</kbd></>}
                renderTrigger={(trigger) => (
                  <button
                    type="button"
                    ref={trigger.ref}
                    {...trigger.props}
                    className="ui-btn ui-btn--ghost ui-btn--sm ui-btn--icon"
                    aria-label="侧栏"
                    aria-pressed={true}
                    aria-controls="ava-left"
                    onClick={toggleLeft}
                    data-testid="toggle-left"
                  >
                    <Icon name="panel-left" size="sm" />
                  </button>
                )}
              />
            </div>
          )}
          <NewEpisodeForm rpc={rpc} onCreated={onCreatedEp} />
          <EpisodeList
            list={list}
            active={active}
            activeProject={activeProject}
            showIdea={showIdea}
            onOpen={open}
            onOpenProject={openProject}
            onCreatedVolume={onCreatedEp}
            onIdea={() =>
              guardDirty(() => {
                setShowIdea(true);
                setActive(null);
                setActiveProject(null);
                setPreview(null);
                setPreviewUnseen(false);
                setJustCreated(null);
              })
            }
            stale={!reachOk}
            convPhase={conv?.phase ?? "none"}
            titles={titleTick(conv?.entries ?? [])}
            openCards={conv?.open.length ?? 0}
            onConvChanged={() => fetchConvRef.current(convKey, true)}
          />
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
            onReset={() => { pulsePaneAnim(); setLayout((l) => ({ ...l, leftW: LEFT_DEFAULT })); }}
            onDraggingChange={setPaneDragging}
            testId="split-left"
          />
        )}
        <section className="center conv-shell" data-testid="center" data-ep={showIdea ? "" : active ?? ""} data-proj={showIdea ? "" : activeProject ?? ""} data-conv={convKey} data-loading={loading > 0 ? "1" : "0"}>
          <SessionHeader
            rpc={rpc}
            convKey={convKey}
            phase={conv?.phase ?? "none"}
            pendingScope={conv?.pending?.scope ?? null}
            info={readyInfo(conv?.entries ?? [])}
            memoryAsk={hasMemoryAsk(conv?.entries ?? [])}
            isIdea={convKey === "idea"}
            context={lastContextReading(conv?.entries ?? [])}
            onCreated={onCreatedEp}
            onEnded={() => fetchConvRef.current(convKey, true)}
            title={headerTitle}
            ep={ep}
            leftOpen={layout.leftOpen}
            onToggleLeft={toggleLeft}
            health={health}
            healthSurfaced={healthSurfaced}
            canRefresh={!!active}
            refreshing={loading > 0}
            onRefresh={refresh}
            showHealth={showHealth}
            onToggleHealth={() => setShowHealth((v) => !v)}
            utilityRight={
              <>
                <ThemeMenu
                  onChangeRepo={changeRepo}
                  showHealthEntry={!healthSurfaced}
                  showHealth={showHealth}
                  onToggleHealth={() => setShowHealth((v) => !v)}
                />
                <FloatingTooltip
                  label={<><span>{layout.previewOpen ? "收起预览" : "展开预览"}</span><kbd className="ui-kbd">⌘J</kbd></>}
                  renderTrigger={(trigger) => (
                    <button
                      type="button"
                      ref={trigger.ref}
                      {...trigger.props}
                      className="ui-btn ui-btn--ghost ui-btn--sm ui-btn--icon"
                      aria-label="预览"
                      aria-pressed={layout.previewOpen}
                      aria-controls="ava-preview"
                      onClick={() => setPreviewOpen(!layout.previewOpen)}
                      data-testid="toggle-preview"
                    >
                      <Icon name="panel-right" size="sm" />
                    </button>
                  )}
                />
              </>
            }
          />
          {/* S8-R23：与 .main.stale 同一条件（不引入新判定），与 reach-banner 分工不同——横幅说原因，这里标出哪些区域是旧数据 */}
          {!reachOk && (
            <div className="stale-note">
              <span className="ui-badge" data-testid="stale-mark">
                陈旧
              </span>
            </div>
          )}
          {!showIdea && activeProject !== null && <ProjectHeader p={activeProjObj} projKey={activeProject} />}
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
              cards={
                <RequestCards
                  conv={conv}
                  rpc={rpc}
                  onEditScript={
                    active !== null && !showIdea
                      ? () => {
                          if (convKeyRef.current !== convKey) return;
                          pickHuman({ kind: "file", root: "episodes", rel: `${active}/02-script.md`, size: null });
                        }
                      : undefined
                  }
                />
              }
            />
            <AnswerDock ep={ep && !showIdea ? ep : undefined} health={health} rpc={rpc} />
            <HandoffStrip rows={rows} />
            <Composer
              rpc={rpc}
              convKey={convKey}
              running={conv?.phase === "running"}
              turnId={conv?.turnId ?? null}
              queued={folded.queued}
              returned={folded.returned}
              toolbar={<ModelControls rpc={rpc} convKey={convKey} phase={conv?.phase ?? "none"} info={modelInfo(conv?.entries ?? [])} openReq={modelOpenReq} pending={conv?.pending ?? null} />}
              onSlash={(cmd) => {
                if (cmd === "/login") {
                  setModelOpenReq((r) => ({ which: "login", n: (r?.n ?? 0) + 1 }));
                  return true;
                }
                // D147/D149：none（存 pending）与 running（排队到回合收尾）也展开下拉；只有 starting/ending/exited 才照常发给 core
                if (conv?.phase !== "idle" && conv?.phase !== "none" && conv?.phase !== "running") return false;
                setModelOpenReq((r) => ({ which: cmd === "/model" ? "model" : "effort", n: (r?.n ?? 0) + 1 }));
                return true;
              }}
              disabled={health?.reach !== "ok"}
              placeholder={convKey === "idea" ? "描述本期想做什么（Enter 发送，Shift+Enter 换行）" : convKey.startsWith("proj:") ? "和 ava 定总纲与卷表、跨卷审查、总装（Enter 发送，Shift+Enter 换行）" : "告诉 ava 要做什么（Enter 发送，Shift+Enter 换行）"}
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
            onReset={() => { pulsePaneAnim(); setLayout((l) => ({ ...l, previewW: null })); }}
            onDraggingChange={setPaneDragging}
            testId="split-preview"
          />
        )}
        {!layout.previewOpen && <PreviewRail target={preview} attention={previewUnseen || strip !== null} onOpen={() => setPreviewOpen(true)} />}
        <section className="preview" id="ava-preview" hidden={!layout.previewOpen} data-auto-open-approval-id={autoOpened.id ?? ""} data-auto-open-count={autoOpened.count}>
          {/* D49-A S4 + D169：预览区顶部的一站式文件切换器 + 镜头画廊 + 封面导入 */}
          <PreviewSwitcher
            epKey={ep && !showIdea && active !== null ? active : null}
            rpc={rpc}
            current={preview}
            running={ep ? ep.jobs.filter((j) => j.state === "running" || j.state === "pending").map((j) => j.command) : []}
            treeBump={treeBump}
            reachOk={reachOk}
            onPick={pickHuman}
            onCoverImported={() => setTreeBump((n) => n + 1)}
          />
          {strip && (
            <button
              className="strip ui-card-enter"
              data-testid="auto-open-strip"
              onClick={() => {
                const go = () => applyAuto(step(autoRef.current, { kind: "strip" }, { mediaPlaying: false }));
                if (strip.type !== "02.5") guardDirty(go);
                else go();
              }}
            >
              <Icon name="info" size="sm" />
              停机点 {strip.type} 已就绪 · 查看
            </button>
          )}
          <PaneBoundary resetKey={`${active ?? "-"}|${preview?.kind ?? ""}|${preview?.rel ?? ""}`}>
            <CrashProbe isPackaged={health?.isPackaged ?? true} />
            {active !== null && editorTarget ? (
              <ScriptEditor
                key={`script:${active}`}
                epKey={active}
                rpc={rpc}
                pendingSwitch={pendingNav !== null}
                onResolveSwitch={(action) => {
                  const fn = pendingNav;
                  setPendingNav(null);
                  if (action === "proceed" && fn) fn();
                  else if (action === "save-failed") setPreviewOpen(true);
                }}
              />
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
                {!(voiceTarget && voiceOk === active) && (
                  <PreviewPane
                    key={active ?? "-"}
                    target={preview}
                    hasEpisode={Boolean(ep && !showIdea && active !== null)}
                    head={!(ep && !showIdea && active !== null)}
                    onPickFile={(dirRel, fileName) =>
                      pickHuman({
                        kind: "file",
                        root: "episodes",
                        rel: active ? (dirRel.startsWith(`${active}/`) ? `${dirRel}/${fileName}` : `${active}/${dirRel}/${fileName}`) : `${dirRel}/${fileName}`,
                        size: null,
                      })
                    }
                  />
                )}
              </>
            )}
          </PaneBoundary>
          {ep && <Timeline key={ep.epKey} ep={ep} open={timelineOpen} onToggle={() => setTimelineOpen((o) => !o)} />}
        </section>
      </div>
    </div>
  );
}

// ---------------- 顶栏与横幅 ----------------

function provenanceDetail(p: Provenance): string {
  switch (p.kind) {
    case "dev":
      return "dev";
    case "consistent":
      return `consistent（${p.gitHead.slice(0, 8)}）`;
    case "dirty":
      return `dirty（未提交的 desktop/ 改动，基线 ${p.gitHead.slice(0, 8)}）`;
    case "stale":
      return `stale（未按当前源码重建，构建于 ${p.gitHead.slice(0, 8)}）`;
    case "incomparable":
      return `incomparable（构建提交 ${p.gitHead.slice(0, 8)} 不在本仓库）`;
    case "unknown":
      return `unknown：${p.message}`;
  }
}

function HealthPanel({ health, diags, linked, onChangeRepo, onClose }: { health: Health; diags: string[]; linked: boolean; onChangeRepo: () => void; onClose: () => void }) {
  const rows: [string, string, string][] = [
    ["host 连接", "后台连接", linked ? "在线" : "断开"],
    ["repoRoot", "仓库目录", health.repoRoot ?? `未就绪：${health.repoRootProblem ?? ""}`],
    ["HEAD", "代码版本", health.repoHead ? health.repoHead.slice(0, 12) : "—"],
    ["Python", "Python 解释器", health.python ?? "—"],
    ["approval 能力", "审批模块", health.capabilities.approvals ? "正常" : `受限：${health.capabilities.approvalsDetail}`],
    ["纳秒无损解析", "高精度文件校验", health.losslessJson ? "正常" : "不支持（决策条只读）"],
    ["core Code Freeze", "代码写保护", health.codeFreeze.detail],
    ["UI 构建溯源", "界面构建状态", provenanceDetail(health.buildProvenance)],
    ["数据可达性", "数据目录状态", `${health.reach}${health.reachDetail ? `：${health.reachDetail}` : ""}`],
  ];
  return (
    <div className="health" data-testid="health-panel">
      <div className="health-head">
        <strong>诊断与环境</strong>
        <span className="spacer" />
        <button type="button" className="ui-btn ui-btn--ghost ui-btn--sm" data-testid="health-change-repo" onClick={onChangeRepo}>
          <Icon name="repo" size="sm" />
          切换仓库…
        </button>
        <button type="button" className="ui-btn ui-btn--ghost ui-btn--sm ui-btn--icon" aria-label="收起诊断面板" onClick={onClose}>
          <Icon name="close" size="sm" />
        </button>
      </div>
      <table>
        <tbody>
          {rows.map(([rowKey, label, v]) => (
            <tr key={rowKey} data-row={rowKey}>
              <th>{label}</th>
              <td>{v}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {[...health.diagnostics, ...diags].length > 0 && <pre className="diag">{[...health.diagnostics, ...diags].join("\n")}</pre>}
    </div>
  );
}

/** 偏好设置浮层（Floating UI）：主题三档 + 6 套强调色 + 切换仓库 + 常态诊断入口；Esc 关闭且焦点回到触发按钮 */
function ThemeMenu({
  onChangeRepo,
  showHealthEntry,
  showHealth,
  onToggleHealth,
}: {
  onChangeRepo: () => void;
  showHealthEntry: boolean;
  showHealth: boolean;
  onToggleHealth: () => void;
}) {
  const [theme, setTheme] = useState<Theme>(() => readTheme());
  const [accent, setAccent] = useState<Accent>(() => readAccent());
  const choose = (t: Theme) => {
    setTheme(t);
    applyTheme(t, true);
    saveTheme(t);
  };
  const chooseAccent = (a: Accent) => {
    setAccent(a);
    applyAccent(a, true);
    saveAccent(a);
  };
  const OPTS: [Theme, string][] = [
    ["system", "跟随系统"],
    ["light", "浅色"],
    ["dark", "深色"],
  ];
  return (
    <FloatingPopover
      placement="bottom-end"
      role="dialog"
      initialFocus={0}
      className="ui-popover theme-pop"
      testId="theme-menu"
      renderTrigger={(trigger) => (
        <FloatingTooltip
          label="偏好设置"
          disabled={trigger.open}
          renderTrigger={(tip) => (
            <button
              type="button"
              ref={(node) => {
                trigger.ref(node);
                tip.ref(node);
              }}
              {...tip.props}
              {...trigger.props}
              className="ui-btn ui-btn--ghost ui-btn--sm ui-btn--icon"
              aria-label="偏好设置"
              data-testid="appearance"
            >
              <Icon name="appearance" size="sm" />
            </button>
          )}
        />
      )}
      renderContent={(close) => (
        <>
          <div className="ui-popover-title">外观</div>
          <SegmentedBar activeKey={theme} label="外观主题">
            {OPTS.map(([v, label]) => (
              <button key={v} type="button" aria-pressed={theme === v} onClick={() => choose(v)}>
                {label}
              </button>
            ))}
          </SegmentedBar>
          <div className="ui-popover-title">强调色</div>
          <div className="theme-swatches" role="group" aria-label="强调色">
            {ACCENTS.map((a) => (
              <button
                key={a.id}
                type="button"
                className="theme-swatch"
                data-swatch={a.id}
                data-testid={`accent-${a.id}`}
                aria-label={a.label}
                aria-pressed={accent === a.id}
                title={a.label}
                onClick={() => chooseAccent(a.id)}
              />
            ))}
          </div>
          <div className="theme-pop-foot">
            <button
              type="button"
              className="ui-row"
              data-testid="change-repo"
              onClick={() => {
                close();
                onChangeRepo();
              }}
            >
              <Icon name="repo" size="sm" />
              <span className="ui-row-title">切换仓库…</span>
            </button>
            {showHealthEntry && (
              <button
                type="button"
                className="ui-row"
                data-testid="health-toggle"
                aria-pressed={showHealth}
                onClick={() => {
                  close();
                  onToggleHealth();
                }}
              >
                <Icon name="pulse" size="sm" />
                <span className="ui-row-title">诊断与环境</span>
              </button>
            )}
          </div>
        </>
      )}
    />
  );
}

// ---------------- 预览收起后的窄条（D39 S2） ----------------

/** 整条是一个按钮：点哪儿都展开。停机点自动呼出（或「已就绪」条）发生在收起期间时亮呼吸脉冲圆点，不自动展开、不动焦点 */
function PreviewRail({ target, attention, onOpen }: { target: PreviewTarget | null; attention: boolean; onOpen: () => void }) {
  const name = target ? target.rel.split("/").pop() : null;
  return (
    <FloatingTooltip
      placement="left"
      label={<><span>{attention ? "展开预览（停机点已就绪）" : "展开预览"}</span><kbd className="ui-kbd">⌘J</kbd></>}
      renderTrigger={(trigger) => (
        <button
          ref={trigger.ref}
          {...trigger.props}
          className="preview-rail"
          aria-controls="ava-preview"
          aria-expanded={false}
          aria-label={attention ? "展开预览（停机点已就绪）" : "展开预览"}
          onClick={onOpen}
          data-testid="preview-rail"
          data-attention={attention ? "1" : "0"}
        >
          <Icon name="chevron-left" size="sm" />
          {attention && <span className="preview-rail-dot" aria-hidden="true" />}
          <span className="preview-rail-label">预览{name ? ` · ${name}` : ""}</span>
        </button>
      )}
    />
  );
}

// ---------------- 期列表 ----------------

/** 侧栏头部（D4）：搜索框 + 视图切换 + 期总数；视图偏好与主题同一份 localStorage 纪律（§3.3） */
function EpisodeList({ list, active, activeProject, showIdea, onOpen, onOpenProject, onCreatedVolume, onIdea, stale, convPhase, titles, openCards, onConvChanged }: { list: EpisodesList | null; active: string | null; activeProject: string | null; showIdea: boolean; onOpen: (k: string) => void; onOpenProject: (projKey: string) => void; onCreatedVolume: (created: CreatedEpisode) => void; onIdea: () => void; stale: boolean; convPhase: ConvSnapshot["phase"]; titles: number; openCards: number; onConvChanged: () => void }) {
  const idea = list?.idea ?? null;
  const [query, setQuery] = useState("");
  const [view, setView] = useState<EpisodeViewPref>(() => readEpisodeView());
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  /** D81：企划卡展开状态（人点过的才记；没点过的按「含当前卷 / 当前企划」决定） */
  const [projOpen, setProjOpen] = useState<Record<string, boolean>>({});
  /** D158：期行会话子列表的本地收起态（方案②：再点当前期 = 收起）。纯显示，不结束会话；换期自动复位。 */
  const [sessCollapsed, setSessCollapsed] = useState<Record<string, boolean>>({});
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
  // D81（人 2026-10-13 选方案 B「分组卡片」）：企划成员不进平铺列表，归到企划卡里；搜索命中卷名时企划连带显示、只列命中的卷
  const projects = list ? list.projects.map((p) => filterProject(p, query)).filter((p): p is ProjectSummary => p !== null) : [];
  const plain = list ? plainEpisodes(eps, list.projects) : [];
  // D45：当前期下面展开它的会话子列表（人 2026-10-08 选方案 B）
  const row = (e: EpisodeSummary, label: string = e.epKey) => {
    const sessOpen = !showIdea && e.epKey === active && !stale && !sessCollapsed[e.epKey];
    return (
      <div key={e.epKey}>
        {epRow(e, label)}
        <Disclosure open={sessOpen}>
          <SessionList rpc={rpc} convKey={`ep:${e.epKey}` as ConvKey} phase={convPhase} titles={titles} openCards={openCards} active={sessOpen} onChanged={onConvChanged} />
        </Disclosure>
      </div>
    );
  };
  const epRow = (e: EpisodeSummary, label: string) => {
    const isActive = !showIdea && e.epKey === active;
    return (
    <button
      className="ui-row ep"
      aria-current={isActive ? "true" : undefined}
      aria-expanded={isActive && !sessCollapsed[e.epKey] ? "true" : undefined}
      onClick={() => {
        if (isActive) setSessCollapsed((c) => ({ ...c, [e.epKey]: !c[e.epKey] })); // D158 ②：再点当前期只收/展会话子列表
        else { setSessCollapsed((c) => ({ ...c, [e.epKey]: false })); onOpen(e.epKey); }
      }}
      title={label !== e.epKey ? e.epKey : undefined}
      data-testid="episode"
      data-ep={e.epKey}
    >
      <span className="ep-line">
        <span className="ep-name">{label}</span>
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
        {e.currentStep === null && <StatusDot tone="unknown" />}
        <span className="ep-step-text">{e.currentStep ?? "…"}</span>
      </span>
    </button>
    );
  };
  const isOpen = (p: ProjectSummary): boolean =>
    query.trim() !== "" || (projOpen[p.projKey] ?? (activeProject === p.projKey || p.members.some((m) => m.epKey === active)));
  const card = (p: ProjectSummary) => {
    const opened = isOpen(p);
    const b = projectBadges(p);
    const segs = progressSegments(p);
    const sessCur = !showIdea && activeProject === p.projKey;
    return (
      <div key={`proj:${p.projKey}`} className="proj-card" data-testid="project" data-proj={p.projKey}>
        <div className="proj-head">
          <button className="proj-toggle" aria-expanded={opened} onClick={() => setProjOpen((o) => ({ ...o, [p.projKey]: !opened }))} title={p.projKey} data-testid="project-toggle">
            <span className="ui-chev" data-open={opened ? "1" : "0"}>
              <Icon name="chevron-right" size="sm" />
            </span>
            <span className="proj-name">{projectTitle(p)}</span>
            {!p.hasProjectFile && (
              <span className="ui-badge" data-testid="project-unplanned">
                未立项
              </span>
            )}
          </button>
          <button className="proj-sess" aria-current={sessCur ? "true" : undefined} onClick={() => onOpenProject(p.projKey)} data-testid="project-session">
            企划会话
          </button>
        </div>
        {(b.running || b.cards > 0 || b.stops > 0) && (
          <div className="proj-sub">
            {b.running && (
              <span className="ui-badge ui-badge--wait" data-testid="conv-running">
                运行中
              </span>
            )}
            {b.cards > 0 && (
              <span className="ui-badge ui-badge--wait" data-testid="project-cards">
                {b.cards} 张卡待答
              </span>
            )}
            {b.stops > 0 && (
              <span className="muted" data-testid="project-stops">
                待处理 {b.stops}
              </span>
            )}
          </div>
        )}
        {segs.length > 0 ? (
          <div className="proj-bar" title="每卷一段：绿 = 待发布(09)、橙 = 待人处理、蓝 = 进行中" data-testid="project-bar">
            {segs.map((s) => (
              <span key={s.label} className={`proj-seg seg-${s.stage}`} title={s.title} data-stage={s.stage} />
            ))}
          </div>
        ) : (
          <div className="proj-empty muted">还没有卷</div>
        )}
        <Disclosure open={sessCur && !stale}>
          <SessionList rpc={rpc} convKey={`proj:${p.projKey}` as ConvKey} phase={convPhase} titles={titles} openCards={openCards} active={sessCur && !stale} onChanged={onConvChanged} />
        </Disclosure>
        <Disclosure open={opened}>
          <div className="proj-vols" data-testid="project-volumes">
            {volumeRows(p).map((r) => row(r.e, r.label))}
            <NewVolumeForm rpc={rpc} target={{ projKey: p.projKey }} onCreated={onCreatedVolume} />
          </div>
        </Disclosure>
      </div>
    );
  };
  /** 「按工序」（方案 B 散开）：各卷与普通期统一按 待你处理 / 进行中 / 待发布 / 状态未取到 分组 */
  const stepItems = [
    ...plain.map((e) => ({ e, label: e.epKey, isBlocked: e.isBlocked, currentStep: e.currentStep })),
    ...projects.flatMap((p) => volumeRows(p).map((r) => ({ e: r.e, label: `${projectTitle(p)} · ${r.label}`, isBlocked: r.e.isBlocked, currentStep: r.e.currentStep }))),
  ];
  const visibleCount = stepItems.length;
  const countBump = useCountBump(visibleCount);
  const nothing = plain.length === 0 && projects.length === 0;
  return (
    <div className="episodes" data-testid="episodes">
      <div className="side-head">
        <div className={`ui-search${query ? " ui-search--clearable" : ""}`}>
          <Icon name="search" size="sm" />
          <input
            className="ui-input"
            placeholder="搜索期名或企划（⌘K）"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape" && query !== "") {
                e.preventDefault();
                setQuery("");
              }
            }}
            data-testid="ep-search"
          />
          {query !== "" && (
            <button
              type="button"
              className="ui-btn ui-btn--ghost ui-btn--sm ui-btn--icon ui-search-clear"
              aria-label="清空搜索"
              onClick={() => setQuery("")}
              data-testid="ep-search-clear"
            >
              <Icon name="close" size="sm" />
            </button>
          )}
        </div>
        <div className="side-head-row">
          <SegmentedBar activeKey={view} label="期列表视图">
            <button type="button" aria-pressed={view === "time"} onClick={() => chooseView("time")}>
              按时间
            </button>
            <button type="button" aria-pressed={view === "step"} onClick={() => chooseView("step")}>
              按工序
            </button>
          </SegmentedBar>
          <span className="spacer" />
          {stale && (
            <span className="ui-badge" data-testid="stale-mark">
              陈旧
            </span>
          )}
          {list && (
            <span className="ui-count" data-bump={countBump ? "1" : undefined}>
              {visibleCount}
            </span>
          )}
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
      {/* D58：选题会话与期会话同一个会话列表（看 / 进 / 新开 / 删），选中「选题」时展开 */}
      <Disclosure open={showIdea && !stale}>
        <SessionList rpc={rpc} convKey="idea" phase={convPhase} titles={titles} openCards={openCards} active={showIdea && !stale} onChanged={onConvChanged} />
      </Disclosure>
      <NewProjectForm rpc={rpc} onCreated={onOpenProject} />
      {!list && (
        <div className="ui-skeleton-stack" aria-label="加载中">
          <div className="ui-skeleton ui-skeleton--lg" />
          <div className="ui-skeleton ui-skeleton--md" />
          <div className="ui-skeleton ui-skeleton--sm" />
        </div>
      )}
      {list && query.trim() !== "" && nothing && <div className="muted">没有匹配的期</div>}
      {view === "time" && timeline(plain, projects).map((i) => (i.kind === "episode" ? row(i.e) : card(i.p)))}
      {view === "step" &&
        groupByStep(stepItems).map((g) => (
          <div key={g.key}>
            <button className="ui-section" aria-expanded={!collapsed[g.key]} onClick={() => setCollapsed((c) => ({ ...c, [g.key]: !c[g.key] }))}>
              <span className="ui-chev" data-open={!collapsed[g.key] ? "1" : "0"}>
                <Icon name="chevron-right" size="sm" />
              </span>
              {g.label}
              <span className="ui-count">{g.items.length}</span>
            </button>
            <Disclosure open={!collapsed[g.key]}>
              <div>{g.items.map((x) => row(x.e, x.label))}</div>
            </Disclosure>
          </div>
        ))}
      {list && list.hiddenUnderscore > 0 && <div className="muted">另有 {list.hiddenUnderscore} 个隐藏目录（_ 开头）</div>}
    </div>
  );
}

// ---------------- D81：企划会话的中栏头部（只展示相对顶部会话头的增量信息：合集/未立项/卷表/提示） ----------------

function ProjectHeader({ p, projKey }: { p: ProjectSummary | null; projKey: string }) {
  const m = p?.meta ?? null;
  const rows = p ? volumeRows(p) : [];
  const hasMeta = Boolean((p && !p.hasProjectFile) || m?.collection || m?.error || rows.length > 0 || (m?.hints.length ?? 0) > 0);
  if (!hasMeta) return null;
  return (
    <div className="status proj-status" role="group" aria-label="企划信息" data-testid="project-head" data-proj={projKey}>
      {((p && !p.hasProjectFile) || m?.collection) && (
        <div className="status-step">
          {p && !p.hasProjectFile && <span className="ui-badge">未立项</span>}
          {m?.collection && <span className="proj-status-coll">合集 · {m.collection}</span>}
        </div>
      )}
      {p && !p.hasProjectFile && <div className="muted">还没有 00-project.md：在这里和 ava 定总纲与卷表，写入前会弹卡给你看差异。</div>}
      {m?.error && (
        <div className="error" data-testid="project-meta-error">
          卷表解析失败：{m.error}
        </div>
      )}
      {rows.length > 0 && (
        <ul className="proj-status-vols" data-testid="project-head-volumes">
          {rows.map((r) => (
            <li key={r.e.epKey} title={r.e.epKey}>
              <span className="proj-status-vol">{r.label}</span>
              {r.e.isBlocked && <span className="ui-badge ui-badge--warn">待处理</span>}
              <span className="muted">{r.e.currentStep ?? "…"}</span>
            </li>
          ))}
        </ul>
      )}
      {m?.hints.map((h) => (
        <div className="muted" key={h} data-testid="project-hint">
          <Icon name="info" size="sm" />
          {h}
        </div>
      ))}
    </div>
  );
}

// ---------------- 事件时间线（有损观测） ----------------

const JOB_TONE: Record<string, "ok" | "danger" | "warn" | "wait" | undefined> = {
  succeeded: "ok",
  failed: "danger",
  blocked: "warn",
  running: "wait",
  pending: undefined,
};

const JOB_STATE_LABEL: Record<string, string> = {
  succeeded: "成功",
  failed: "失败",
  blocked: "已拦截",
  running: "运行中",
  pending: "等待中",
};

/**
 * D39 C4：时间线收成预览列底部的一行摘要（作业 / 失败 / 事件计数），点开才展开（上限 40vh）。
 */
function Timeline({ ep, open, onToggle }: { ep: EpisodeState; open: boolean; onToggle: () => void }) {
  const nonJob = ep.events.filter((e) => e.kind.startsWith("approval_") || e.kind === "unknown" || e.kind === "human_time_recorded" || e.kind.startsWith("script_save"));
  // D71：机检退出码 1 是「稿件没过」，不是作业故障，不计入失败
  const failed = ep.jobs.filter((j) => j.state === "failed" && !isCheckNotPassed(j)).length;
  const bodyId = `timeline-body-${ep.epKey}`;
  return (
    <footer className="timeline" data-testid="timeline">
      <div className="timeline-head">
        <button className="ui-btn ui-btn--ghost ui-btn--sm" aria-expanded={open} aria-controls={bodyId} onClick={onToggle} data-testid="timeline-toggle">
          <span className="ui-chev" data-open={open ? "1" : "0"}>
            <Icon name="chevron-right" size="sm" />
          </span>
          时间线
        </button>
        <span className="ui-badge">作业 {ep.jobs.length}</span>
        {failed > 0 && <span className="ui-badge ui-badge--danger">失败 {failed}</span>}
        {ep.degradedNotices.length > 0 && <span className="ui-badge ui-badge--warn">有遗漏</span>}
        <span className="ui-badge">事件 {nonJob.length}</span>
      </div>
      <Disclosure open={open} keepMounted>
        <div className="timeline-body" id={bodyId}>
          <div className="notice">
            <Icon name="info" size="sm" />
            事件日志可能存在少量延迟或遗漏；当前进度以顶部工序为准
          </div>
          {ep.eventsMeta.truncatedHead && <div className="notice">更早的作业记录未载入</div>}
          {ep.degradedNotices.map((d) => (
            <div key={d.at} className="notice notice--warn" data-testid="degraded">
              事件日志熔断期间漏记 {d.droppedDuringCircuit} 条记录 · {d.at}
            </div>
          ))}
          <ul className="jobs">
            {ep.jobs.map((j) => (
              <li key={j.jobId} className={`job ${j.state}`} data-testid="job">
                <span className="job-state">
                  {isCheckNotPassed(j) ? <Badge tone="warn">未通过</Badge> : <Badge tone={JOB_TONE[j.state]}>{JOB_STATE_LABEL[j.state] ?? j.state}</Badge>}
                </span>
                <code>{j.command ?? j.jobId}</code>
                {j.returncode !== null && j.returncode !== 0 && <span className="muted">退出码 {j.returncode}</span>}
                {j.durationS !== null && <span className="muted">{j.durationS}s</span>}
                {j.noFollowupEvents && <span className="warn">未见后续进度</span>}
                {j.finishedEventMissing && <span className="warn">进程已结束，未收到完成事件</span>}
                {j.message && j.message !== "退出码 0" && <span className="muted">{j.message}</span>}
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
              格式异常记录 {ep.eventsMeta.malformed} 条；归属不一致 {ep.eventsMeta.episodeFieldMismatch} 条
            </div>
          )}
        </div>
      </Disclosure>
    </footer>
  );
}
