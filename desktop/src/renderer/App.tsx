// 桌面端 v1 主界面（Spec 8 §2.11；Spec 10 PR1 版面重排：产物树移左栏、中栏 = 工序卡 + 待答区）。
// UI 自身零判定：工序一律取 status --json，只呈现确定性事实；不做 LLM 推荐（direction §5）。
// 类名与元素种类按 Spec 14 §3.1/§3.2 的契约（可点击的行一律原生 <button class="ui-row">，VS-8）。
import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import type { ApprovalJson } from "../shared/contracts";
import { isStopType } from "../shared/contracts";
import { eventLabel } from "../shared/fold";
import type { EpisodeDelta, EpisodeSnapshot, EpisodesList, Health, ShotsEntry, TreeEntry } from "../shared/protocol";
import { previewKind } from "../shared/previewKind";
import { STOP_PREVIEW } from "../shared/stopPreview";
import { initialAutoOpen, step, type AutoOpenState } from "./autoOpen";
import { AnswerDock } from "./HumanCards";
import { Icon, type IconName } from "./icons";
import { NewEpisodeForm } from "./NewEpisodeForm";
import { PreviewPane, type PreviewTarget } from "./PreviewPane";
import { errText, RpcClient } from "./rpc";
import { approvalsOf, emptyStore, reduce, select, type Action, type EpisodeState, type Store } from "./store";
import { installTestHooks } from "./testHooks";
import { Badge, StateView, StatusDot } from "./ui";

const rpc = new RpcClient(window);

/** 熔断后 main 把 renderer 换成致命面板（§2.9）：内容随 URL 查询参数带来，不再连接任何 host */
const FATAL = new URLSearchParams(window.location.search).get("fatal");

/**
 * 人时类 advisory（pipeline/status.py 人时观测行的两种原文开头）旁标数据源不完整（门禁 14，红队 M8）：
 * 桌面端审阅不写 human_time.json，判据 4——不把「不知道」当成合格。
 */
const HUMAN_TIME_ADVISORY = /^(人类耗时|human_time\.json)/;

/** 产物树行图标（按扩展名分发，与 PreviewPane 同一份判据） */
const KIND_ICON: Record<string, IconName> = { video: "film", audio: "wave", image: "image", html: "file" };

function storeReducer(s: Store, a: Action): Store {
  return reduce(s, a).store;
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

  const loadHealth = useCallback(() => rpc.call<Health>("app.health").then(setHealth, (e) => setError(errText(e))), []);

  useEffect(() => {
    const offs = [
      rpc.on("episode.snapshot", (p) => dispatch({ type: "snapshot", snap: p.data as EpisodeSnapshot })),
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
      // R6：host 重连（或 renderer 重载）后预览清空、owner 归 none；`seen` 保留，旧的自动呼出不会被重放
      autoRef.current = step(autoRef.current, { kind: "reset", phase: "none", turnId: null, settlePending: null }, { mediaPlaying: false }).state;
      setStrip(null);
      void loadHealth().then(() => undefined);
      void rpc.call<EpisodesList>("episodes.list").then(setList, (e) => setError(errText(e)));
      // 重连后恢复活跃（H1，§2.12）：host 重启后订阅全部丢失，一切从磁盘重新 snapshot（§2.9）
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
    setError(null);
    setPreview(null);
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
  }, []);

  /** R6：切期或 host 重连 → 预览清空、owner 归 none、deferred/strip 清空；`seen` 保留。PR1 的 snapshot 字段是常量 */
  const resetAuto = useCallback(() => {
    autoRef.current = step(autoRef.current, { kind: "reset", phase: "none", turnId: null, settlePending: null }, { mediaPlaying: false }).state;
    setStrip(null);
  }, []);
  useEffect(() => {
    resetAuto();
  }, [active, resetAuto]);

  const reachOk = health?.reach === "ok";

  return (
    <div className="app">
      <TopBar health={health} onToggleHealth={() => setShowHealth((v) => !v)} onRefresh={refresh} canRefresh={!!active} onChangeRepo={changeRepo} />
      {showHealth && health && <HealthPanel health={health} diags={diags} linked={linked} />}
      {error && (
        <div className="banner banner-red" data-testid="error">
          <Icon name="alert" size="sm" />
          {error}
        </div>
      )}
      <div className={`main ${reachOk ? "" : "stale"}`}>
        <nav className="left">
          <NewEpisodeForm rpc={rpc} onCreated={open} />
          <EpisodeList list={list} active={active} onOpen={open} />
          {ep && <ArtifactTree key={ep.epKey} epKey={ep.epKey} onPick={pickHuman} />}
          <GalleryList reachOk={reachOk} onPick={pickHuman} />
        </nav>
        <section className="center" data-testid="center" data-ep={active ?? ""} data-loading={loading > 0 ? "1" : "0"}>
          {ep ? (
            <>
              <StatusCard ep={ep} />
              <AnswerDock key={ep.epKey} ep={ep} health={health} rpc={rpc} />
            </>
          ) : (
            <StateView kind="empty" title="选择一期" />
          )}
        </section>
        <section className="preview" data-auto-open-approval-id={autoOpened.id ?? ""} data-auto-open-count={autoOpened.count}>
          {strip && (
            <button className="strip" data-testid="auto-open-strip" onClick={() => applyAuto(step(autoRef.current, { kind: "strip" }, { mediaPlaying: false }))}>
              <Icon name="info" size="sm" />
              停机点 {strip.type} 已就绪 · 查看
            </button>
          )}
          <PreviewPane key={active ?? "-"} target={preview} />
        </section>
      </div>
      {ep && <Timeline ep={ep} />}
    </div>
  );
}

// ---------------- 顶栏与横幅 ----------------

function TopBar({ health, onToggleHealth, onRefresh, canRefresh, onChangeRepo }: { health: Health | null; onToggleHealth: () => void; onRefresh: () => void; canRefresh: boolean; onChangeRepo: () => void }) {
  const p = health?.buildProvenance;
  return (
    <header className="topbar">
      <div className="topbar-row">
        <span className="wordmark">ava</span>
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

// ---------------- 期列表 ----------------

function EpisodeList({ list, active, onOpen }: { list: EpisodesList | null; active: string | null; onOpen: (k: string) => void }) {
  return (
    <div className="episodes" data-testid="episodes">
      {!list && <div className="muted">加载中…</div>}
      {list?.episodes.map((e) => (
        <button
          key={e.epKey}
          className="ui-row ep"
          aria-current={e.epKey === active ? "true" : undefined}
          onClick={() => onOpen(e.epKey)}
          data-testid="episode"
        >
          <span className="ep-line">
            <span className="ep-name">{e.epKey}</span>
          </span>
          <span className="ep-step">
            {e.isBlocked && <span className="ui-badge stop-mark">停机</span>}
            {e.currentStep === null && <StatusDot tone="unknown" />}
            <span className="ep-step-text">{e.currentStep ?? "…"}</span>
          </span>
        </button>
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

function StatusCard({ ep }: { ep: EpisodeState }) {
  const s = ep.status;
  // 门禁 5：文案与 HEAD 逐字相同（不为换皮改可见字符串），StateView 的形态只承载这一条原文
  if (!s.ok) return <StateView kind="error" title={`工序读取失败（${s.code}）：${s.message}`} />;
  const v = s.value;
  return (
    <div className="status" data-testid="status">
      <span className="status-label">当前工序</span>
      <div className="status-step">
        <strong data-testid="current-step">{v.current_step}</strong>
        {v.is_blocked && <span className="ui-badge stop-mark">停机</span>}
      </div>
      {v.block_reason && <div className="muted">{v.block_reason}</div>}
      <div>下一步：{v.next_action}</div>
      {v.next_command && <code className="ui-code cmd">{v.next_command}</code>}
      {v.advisories.length > 0 && (
        <ul className="advisories">
          {v.advisories.map((a) => (
            <li key={a}>
              <Icon name="alert" size="sm" />
              {a}
              {HUMAN_TIME_ADVISORY.test(a) && (
                <span className="muted" data-testid="human-time-incomplete">
                  （数据源不完整：桌面端审阅不计入）
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
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

function Timeline({ ep }: { ep: EpisodeState }) {
  const nonJob = ep.events.filter((e) => e.kind.startsWith("approval_") || e.kind === "unknown" || e.kind === "human_time_recorded");
  return (
    <footer className="timeline" data-testid="timeline">
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
    </footer>
  );
}
