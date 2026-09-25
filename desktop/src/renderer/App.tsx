// 桌面端 v1 主界面（Spec 8 §2.11）：期列表 + 产物树 + PreviewPane + 只读事件时间线 + 健康面板。
// UI 自身零判定：工序一律取 status --json，只呈现确定性事实；不做 LLM 推荐（direction §5）。
// Approval 决策条属 PR4。
import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import type { EventJson } from "../shared/contracts";
import { isStopType } from "../shared/contracts";
import type { EpisodeDelta, EpisodeSnapshot, EpisodesList, Health, ShotsEntry, TreeEntry } from "../shared/protocol";
import { STOP_PREVIEW } from "../shared/stopPreview";
import { PreviewPane, type PreviewTarget } from "./PreviewPane";
import { RpcClient, RpcFailure } from "./rpc";
import { approvalsOf, emptyStore, reduce, select, type Action, type EpisodeState, type Store } from "./store";
import { installTestHooks } from "./testHooks";

const rpc = new RpcClient(window);

function storeReducer(s: Store, a: Action): Store {
  return reduce(s, a).store;
}

function errText(e: unknown): string {
  return e instanceof RpcFailure ? `${e.error.code}：${e.error.message}` : e instanceof Error ? e.message : String(e);
}

export function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [list, setList] = useState<EpisodesList | null>(null);
  const [store, dispatch] = useReducer(storeReducer, emptyStore);
  const storeRef = useRef(store);
  storeRef.current = store;
  const [active, setActive] = useState<string | null>(null);
  const [preview, setPreview] = useState<PreviewTarget | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [diags, setDiags] = useState<string[]>([]);
  const [showHealth, setShowHealth] = useState(false);
  const seenApprovals = useRef(new Set<string>());

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
      void loadHealth().then(() => undefined);
      void rpc.call<EpisodesList>("episodes.list").then(setList, (e) => setError(errText(e)));
    });
    return () => offs.forEach((f) => f());
  }, [loadHealth]);

  useEffect(() => {
    if (health && !health.isPackaged) installTestHooks(health.isPackaged, rpc);
  }, [health]);

  const open = useCallback(async (epKey: string) => {
    setError(null);
    setPreview(null);
    setActive(epKey);
    try {
      const snap = await rpc.call<EpisodeSnapshot>("episode.activate", { epKey });
      dispatch({ type: "snapshot", snap });
    } catch (e) {
      setError(errText(e));
    }
  }, []);

  const refresh = useCallback(async () => {
    if (!active) return;
    try {
      dispatch({ type: "snapshot", snap: await rpc.call<EpisodeSnapshot>("episode.refresh", { epKey: active }) });
      void loadHealth();
    } catch (e) {
      setError(errText(e));
    }
  }, [active, loadHealth]);

  const ep = select(store, active);

  // 停机点自动呼出：只作用于活跃期，且只在出现新的 pending approvalId 时触发一次（§2.5）
  useEffect(() => {
    if (!ep) return;
    for (const a of approvalsOf(ep)) {
      if (a.status !== "pending" || seenApprovals.current.has(a.approval_id)) continue;
      seenApprovals.current.add(a.approval_id);
      if (!isStopType(a.type)) continue;
      const t = STOP_PREVIEW[a.type];
      if (a.type === "03.5") {
        void rpc.call<TreeEntry[]>("tree.list", { epKey: ep.epKey, relDir: t.rel }).then(
          (es) => setPreview({ kind: "dir", root: t.root, rel: t.rel, entries: es.map((x) => x.name) }),
          () => undefined,
        );
      } else {
        setPreview({ kind: "file", root: t.root, rel: `${ep.epKey}/${t.rel}`, size: null });
      }
    }
  }, [ep]);

  const reachOk = health?.reach === "ok";

  return (
    <div className="app">
      <TopBar health={health} onToggleHealth={() => setShowHealth((v) => !v)} onRefresh={refresh} canRefresh={!!active} />
      {showHealth && health && <HealthPanel health={health} diags={diags} />}
      {error && (
        <div className="banner banner-red" data-testid="error">
          {error}
        </div>
      )}
      <div className={`main ${reachOk ? "" : "stale"}`}>
        <nav className="left">
          <EpisodeList list={list} active={active} onOpen={open} />
          <GalleryList reachOk={reachOk} onPick={setPreview} />
        </nav>
        <section className="center">
          {ep ? (
            <>
              <StatusCard ep={ep} />
              <ArtifactTree key={ep.epKey} epKey={ep.epKey} onPick={setPreview} />
            </>
          ) : (
            <div className="empty">选择一期</div>
          )}
        </section>
        <section className="preview">
          <PreviewPane key={active ?? "-"} target={preview} />
        </section>
      </div>
      {ep && <Timeline ep={ep} />}
    </div>
  );
}

// ---------------- 顶栏与横幅 ----------------

function TopBar({ health, onToggleHealth, onRefresh, canRefresh }: { health: Health | null; onToggleHealth: () => void; onRefresh: () => void; canRefresh: boolean }) {
  const p = health?.buildProvenance;
  return (
    <header className="topbar">
      <div className="topbar-row">
        <strong>ava</strong>
        {health && !health.isPackaged && <span className="dev-mark" data-testid="dev-build">DEV BUILD</span>}
        <span className="repo" data-testid="repo-root">
          {health?.repoRoot ?? health?.repoRootProblem ?? "…"}
          {health?.repoHead && <code> @{health.repoHead.slice(0, 8)}</code>}
        </span>
        <span className="spacer" />
        <button onClick={onRefresh} disabled={!canRefresh}>刷新</button>
        <button onClick={onToggleHealth} data-testid="health-toggle">健康</button>
      </div>
      {health && health.reach !== "ok" && (
        <div className="banner banner-red" data-testid="reach-banner">
          {health.reach}：{health.reachDetail}
        </div>
      )}
      {health && health.codeFreeze.ok === false && <div className="banner banner-yellow">core Code Freeze：{health.codeFreeze.detail}</div>}
      {p?.kind === "dirty" && <div className="banner banner-red">UI 构建自未提交的 desktop/ 改动</div>}
      {p?.kind === "stale" && <div className="banner banner-yellow">UI 未按当前源码重建（构建于 {p.gitHead.slice(0, 8)}）</div>}
      {p?.kind === "incomparable" && <div className="banner banner-grey">无法比对 UI 构建版本（构建提交不在本仓库）</div>}
      {p?.kind === "unknown" && <div className="banner banner-grey">无法比对 UI 构建版本：{p.message}</div>}
    </header>
  );
}

function HealthPanel({ health, diags }: { health: Health; diags: string[] }) {
  const rows: [string, string][] = [
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
            <tr key={k}>
              <th>{k}</th>
              <td>{v}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {[...health.diagnostics, ...diags].length > 0 && (
        <pre className="diag">{[...health.diagnostics, ...diags].join("\n")}</pre>
      )}
    </div>
  );
}

// ---------------- 期列表 ----------------

function EpisodeList({ list, active, onOpen }: { list: EpisodesList | null; active: string | null; onOpen: (k: string) => void }) {
  return (
    <div className="episodes" data-testid="episodes">
      {!list && <div className="muted">加载中…</div>}
      {list?.episodes.map((e) => (
        <div key={e.epKey} className={`ep ${e.epKey === active ? "active" : ""}`} onClick={() => onOpen(e.epKey)} data-testid="episode">
          <div className="ep-name">{e.epKey}</div>
          <div className="ep-step">
            {e.isBlocked && <span className="stop-mark">停机</span>}
            {e.currentStep ?? "…"}
          </div>
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
      <div className="section-title" onClick={() => setOpen(!open)} data-testid="galleries-toggle">
        {open ? "▾" : "▸"} 镜头画廊
      </div>
      {open && items?.length === 0 && <div className="muted">没有画廊</div>}
      {open &&
        items?.map((g) => (
          <div key={g.name} className="tree-row" data-testid="gallery" data-name={g.name} onClick={() => onPick({ kind: "file", root: "shots", rel: g.name, size: g.size })}>
            {g.name}
          </div>
        ))}
    </div>
  );
}

// ---------------- 工序卡（status --json 原文） ----------------

function StatusCard({ ep }: { ep: EpisodeState }) {
  const s = ep.status;
  if (!s.ok) return <div className="status error">工序读取失败（{s.code}）：{s.message}</div>;
  const v = s.value;
  return (
    <div className="status" data-testid="status">
      <div>
        <strong data-testid="current-step">{v.current_step}</strong>
        {v.is_blocked && <span className="stop-mark">停机</span>}
      </div>
      {v.block_reason && <div className="muted">{v.block_reason}</div>}
      <div>下一步：{v.next_action}</div>
      {v.next_command && <code className="cmd">{v.next_command}</code>}
      {v.advisories.length > 0 && (
        <ul className="advisories">
          {v.advisories.map((a) => (
            <li key={a}>{a}</li>
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
  if (err) return <div className="error">{err}</div>;
  if (!entries) return <div className="muted">…</div>;
  return (
    <ul className="tree-list">
      {entries.map((e) => (
        <li key={e.rel}>
          <div
            className={`tree-row ${e.kind}`}
            style={{ paddingLeft: depth * 14 }}
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
            {e.kind === "dir" ? (open[e.rel] ? "▾ " : "▸ ") : ""}
            {e.name}
          </div>
          {e.kind === "dir" && open[e.rel] && <TreeDir epKey={epKey} relDir={e.rel} depth={depth + 1} onPick={onPick} />}
        </li>
      ))}
    </ul>
  );
}

// ---------------- 事件时间线（有损观测） ----------------

function eventLabel(e: EventJson): string {
  const p = e.payload;
  if (e.kind === "approval_resolved") {
    if (typeof p.approval_id !== "string") return `命令卡拒执（非停机点）：${String(p.command ?? "")}`;
    if (p.confirms === "artifact") return `确认已对齐的批准：${String(p.stop ?? "")}`;
    return `停机点决策：${String(p.stop ?? "")} → ${String(p.decision ?? "")}`;
  }
  if (e.kind === "approval_requested") return `停机点待审：${String(p.stop ?? "")}`;
  if (e.kind === "unknown") return `未知事件 ${e.type}`;
  return `${e.kind}${typeof p.job_id === "string" ? ` ${p.job_id}` : ""}`;
}

function Timeline({ ep }: { ep: EpisodeState }) {
  const nonJob = ep.events.filter((e) => e.kind.startsWith("approval_") || e.kind === "unknown" || e.kind === "human_time_recorded");
  return (
    <footer className="timeline" data-testid="timeline">
      <div className="notice">观测层有损（Spec 2 §2.3），轨迹可能不完整；工序以 status 为准</div>
      {ep.eventsMeta.truncatedHead && <div className="notice">更早的 job 未载入</div>}
      {ep.degradedNotices.map((d) => (
        <div key={d.at} className="notice" data-testid="degraded">
          某进程在熔断期丢了 {d.droppedDuringCircuit} 条事件（不限于本期）· {d.at}
        </div>
      ))}
      <ul className="jobs">
        {ep.jobs.map((j) => (
          <li key={j.jobId} className={`job ${j.state}`} data-testid="job">
            <span className="job-state">{j.state}</span> {j.command ?? j.jobId}
            {j.returncode !== null && <span className="muted"> rc={j.returncode}</span>}
            {j.durationS !== null && <span className="muted"> {j.durationS}s</span>}
            {j.noFollowupEvents && <span className="warn"> 未见后续事件</span>}
            {j.finishedEventMissing && <span className="warn"> 进程已不在，未收到 job_finished</span>}
            {j.message && <div className="muted">{j.message}</div>}
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
