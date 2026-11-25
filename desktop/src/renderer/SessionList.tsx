// D45：侧栏里当前期下面的会话子列表（人 2026-10-08 选方案 B）。
// 进入 = conv.enter（先结束空闲活会话，再 --continue <sid>）；「＋ 新会话」= conv.fresh；删除 = conv.delete（host 先弹原生确认框，移进回收站）。
// D66：列表底部可折叠分组「回收站（N）」——按文件聚合（键用 file 不用 sid：同名冲突时同一 sid 可对应多个回收站文件）；
// 彻底删除 = conv.purgeTrash，确认框在宿主层（与 conv.delete 同一落位，渲染层不新造组件）。空回收站不渲染分组。
// 列表只在：换期、会话阶段变化（起停、回合结束）、自己做完操作后、收到 session_title 帧（D80）时重取；不轮询。
// D80 R1：行标题 = core 给的 title（生成名 / 人改名，缺省首条消息）；「改名」= conv.rename（行内编辑，回车存、Esc 或失焦取消）。
// 不用双击进编辑：单击本行就是 conv.enter（会结束空闲活会话再接上），双击必然先触发两次单击。
import { useCallback, useEffect, useRef, useState } from "react";
import type { ConvEntry, ConvKey, ConvSnapshot, SessionRow, TrashRow } from "../shared/protocol";
import { formatRelTime } from "../shared/relTime";
import { titleProblem } from "../shared/sessionTitle";
import { Icon } from "./icons";
import { Disclosure } from "./motion";
import { errText, type RpcClient } from "./rpc";

/** D80：本会话键收到过几帧 session_title——变了就重取会话列表（标题在回合结束之后才异步到达，阶段早已变完） */
export function titleTick(entries: readonly ConvEntry[]): number {
  return entries.reduce((n, e) => (e.k === "frame" && e.frame.t === "session_title" ? n + 1 : n), 0);
}

/** 回收站文件大小：KB/MB 一位小数（D66 §四）。 */
export function formatTrashSize(bytes: number): string {
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

/** D80 R7：`openCards` = 活会话的待答卡数（只有活会话能有卡，口径同期行的「N 张卡待答」） */
export function SessionList({
  rpc,
  convKey,
  phase,
  titles = 0,
  openCards = 0,
  active = true,
  now,
  onChanged,
}: {
  rpc: RpcClient;
  convKey: ConvKey;
  phase: ConvSnapshot["phase"];
  titles?: number;
  openCards?: number;
  active?: boolean;
  now?: number;
  onChanged: () => void;
}) {
  const [rows, setRows] = useState<SessionRow[] | null>(null);
  const [editing, setEditing] = useState<{ sid: string; text: string; origTitle: string } | null>(null);
  const renameCancelledRef = useRef(false);
  // R1.5：标题换了（兜底 → 生成名 / 改名）的行淡入切换；首次载入不播（副作用更新在 useEffect 中，避免渲染期 mutate ref）
  const seen = useRef<Map<string, string>>(new Map());
  const reloadGenRef = useRef(0);
  const [trash, setTrash] = useState<TrashRow[] | null>(null);
  const [trashOpen, setTrashOpen] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [errSeq, setErrSeq] = useState(0);
  // A4：空闲会话一键移入回收站后 5 秒行内撤销条
  const [undoItem, setUndoItem] = useState<{ file: string; label: string } | null>(null);
  const undoTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clearUndo = useCallback(() => {
    if (undoTimerRef.current !== null) {
      clearTimeout(undoTimerRef.current);
      undoTimerRef.current = null;
    }
    setUndoItem(null);
  }, []);

  useEffect(() => {
    clearUndo();
    seen.current = new Map();
    setRows(null);
    setTrash(null);
    setTrashOpen(false);
  }, [convKey, clearUndo]);

  useEffect(() => () => {
    if (undoTimerRef.current !== null) clearTimeout(undoTimerRef.current);
  }, []);

  const reload = useCallback(() => {
    const gen = (reloadGenRef.current += 1);
    rpc.call<SessionRow[]>("conv.sessions", { convKey }).then(
      // 最近活动在上（core 按文件顺序给，最旧在前；ISO 时间串字典序即时间序）
      (r) => {
        if (gen !== reloadGenRef.current) return;
        setRows([...r].sort((a, b) => (a.lastActivity < b.lastActivity ? 1 : a.lastActivity > b.lastActivity ? -1 : 0)));
      },
      (err) => {
        if (gen !== reloadGenRef.current) return;
        setError(errText(err));
        setErrSeq((n) => n + 1);
      },
    );
    rpc.call<TrashRow[]>("conv.trashList", { convKey }).then(
      (r) => {
        if (gen !== reloadGenRef.current) return;
        const sorted = [...r].sort((a, b) => (a.lastTs < b.lastTs ? 1 : a.lastTs > b.lastTs ? -1 : 0));
        setTrash(sorted);
        if (sorted.length === 0) setTrashOpen(false);
      },
      () => {
        if (gen !== reloadGenRef.current) return;
        setTrash(null);
      },
    );
  }, [rpc, convKey]);
  useEffect(() => {
    // 退场中的旧期 SessionList（active === false）不响应新期的全局 phase/titles 变动，避免多发 2 次 Python RPC
    if (!active) return;
    setError(null);
    reload();
  }, [active, reload, phase, titles]);

  useEffect(() => {
    if (!rows) return;
    for (const r of rows) {
      seen.current.set(r.sid, r.title || "（没有用户消息）");
    }
  }, [rows]);

  const busy = busyId !== null;

  /** 方法名在调用点写成字面量（TG-16 / TG-10：rpc.call 直接落在原生按钮的 onClick 里） */
  const act = <T,>(id: string, call: Promise<T>, onOk?: (res: T) => void) => {
    setBusyId(id);
    setError(null);
    call.then(
      (res) => {
        setBusyId(null);
        onOk?.(res);
        reload();
        onChanged();
      },
      (err) => {
        setBusyId(null);
        setError(errText(err));
        setErrSeq((n) => n + 1);
        reload();
      },
    );
  };

  const running = phase === "running" || phase === "starting" || phase === "ending";
  // D157：回合在跑不再锁住会话管理——切段/开新段都不杀活进程（host 侧已改）。只剩本列表自己的操作在途。
  const off = busy;
  const saveTitle = (sid: string, text: string): boolean => {
    const problem = titleProblem(text);
    if (problem !== null) {
      setError(problem);
      setErrSeq((n) => n + 1);
      return false;
    }
    setEditing(null);
    // 改名不结束活会话、回合在跑时也能改（core 侧标题文件自带锁）：只受本列表自己的 busy 约束
    act(sid, rpc.call("conv.rename", { convKey, sid, title: text.trim() }));
    return true;
  };
  const anyLive = rows?.some((r) => r.live) ?? false;
  const nowMs = now ?? Date.now();
  return (
    <div className="ep-sessions" data-testid={active ? "session-list" : undefined} aria-hidden={!active || undefined} aria-busy={busy || undefined}>
      <button
        type="button"
        className="ui-row ep-session"
        data-testid="session-new"
        // 仅在列表载入完成且确实没有活会话时标当前，避免首次载入闪烁
        aria-current={rows !== null && !anyLive ? "true" : undefined}
        disabled={off}
        title="新建一段空白上下文会话（当前会话继续留在后台）"
        onClick={(e) => {
          if (!e.nativeEvent.isTrusted) return;
          act("new", rpc.call("conv.fresh", { convKey }));
        }}
      >
        {busyId === "new" ? <span className="ui-spinner" /> : <Icon name="plus" size="sm" />}
        <span className="ui-row-title">新会话</span>
      </button>
      {rows === null && error === null && (
        <div className="ui-skeleton-stack ep-sessions-wait" aria-label="正在加载会话列表">
          <div className="ui-skeleton ui-skeleton--md" />
          <div className="ui-skeleton ui-skeleton--sm" />
        </div>
      )}
      {rows?.map((r) => {
        const title = r.title || "（没有用户消息）";
        const prev = seen.current.get(r.sid);
        const swapped = prev !== undefined && prev !== title;
        const rowBusy = busyId === r.sid;
        if (editing?.sid === r.sid) {
          const commitRename = (): boolean => {
            const trimmed = editing.text.trim();
            if (trimmed === "" || trimmed === editing.origTitle) {
              setEditing(null);
              return true;
            }
            return saveTitle(r.sid, editing.text);
          };
          return (
            <div key={r.sid} className="ep-session-wrap" data-testid="session-row" data-sid={r.sid} data-live={r.live ? "1" : "0"}>
              <input
                className="ui-input ep-session-edit"
                data-testid="session-rename-input"
                aria-label="会话标题"
                placeholder="输入新名称（1–20 字）"
                autoFocus
                value={editing.text}
                onChange={(e) => {
                  setEditing({ sid: r.sid, text: e.target.value, origTitle: editing.origTitle });
                  if (error) setError(null);
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.nativeEvent.isComposing && e.keyCode !== 229) {
                    e.preventDefault();
                    if (commitRename()) renameCancelledRef.current = true;
                  } else if (e.key === "Escape") {
                    e.preventDefault();
                    renameCancelledRef.current = true;
                    setEditing(null);
                    setError(null);
                  }
                }}
                onBlur={() => {
                  if (renameCancelledRef.current) {
                    renameCancelledRef.current = false;
                    return;
                  }
                  commitRename();
                }}
              />
            </div>
          );
        }
        const when = Number.isNaN(Date.parse(r.lastActivity)) ? r.lastActivity : formatRelTime(Date.parse(r.lastActivity), nowMs);
        const rowRunning = (r.live && running) || r.bgRunning === true;
        const rowOpenCards = r.live ? openCards : r.bgOpenCards ?? 0;
        return (
          <div key={r.sid} className="ep-session-wrap" data-testid="session-row" data-sid={r.sid} data-live={r.live ? "1" : "0"}>
            <button
              type="button"
              className="ui-row ep-session"
              aria-current={r.live ? "true" : undefined}
              data-empty={r.firstUser ? undefined : "1"}
              disabled={off}
              title={`${title}\n${r.messages} 条消息 · ${when}`}
              onClick={(e) => {
                // 不按本地 r.live 拦：列表可能还没跟上刚结束的会话（e2e TX-10 实测吞点击）；「进的就是当前会话」由 host 判（conv.enter 原样返回）
                if (!e.nativeEvent.isTrusted) return;
                act(r.sid, rpc.call("conv.enter", { convKey, sid: r.sid }));
              }}
            >
              {rowBusy && <span className="ui-spinner" />}
              {/* R7.2：正在跑的会话标题扫光（含后台并存运行段）；有待答卡用期行同口径的徽标 */}
              <span
                key={title}
                className={`ui-row-title${swapped ? " ep-title-swap" : ""}${rowRunning ? " conv-shimmer" : ""}`}
                data-testid="session-title"
                data-source={r.titleSource}
                data-running={rowRunning ? "1" : undefined}
              >
                {title}
              </span>
              {rowOpenCards > 0 && (
                <span className="ui-badge ui-badge--wait" data-testid="session-open">
                  待答
                </span>
              )}
            </button>
            {/* R7.1：相对时间与悬停操作按钮叠在同一格淡入淡出，标题宽度零跳动 */}
            <span className="ep-session-trail">
              <span className="ep-session-when" data-testid="session-when">
                {when}
              </span>
              <span className="ep-session-actions">
                <button
                  type="button"
                  className="ui-btn ui-btn--ghost ui-btn--sm ui-btn--icon ep-session-del"
                  data-testid="session-rename"
                  aria-label={`改名：${title}`}
                  title="改名"
                  disabled={busy}
                  onClick={(e) => {
                    if (!e.nativeEvent.isTrusted) return;
                    setError(null);
                    renameCancelledRef.current = false;
                    setEditing({ sid: r.sid, text: r.titleSource === "first_input" ? "" : title, origTitle: title });
                  }}
                >
                  <Icon name="pen" size="sm" />
                </button>
                <button
                  type="button"
                  className="ui-btn ui-btn--ghost ui-btn--sm ui-btn--icon ui-btn--danger-hover ep-session-del"
                  data-testid="session-delete"
                  aria-label={`移到回收站：${title}`}
                  title={rowRunning ? "停止并移到回收站" : "移到回收站"}
                  disabled={off}
                  onClick={(e) => {
                    if (!e.nativeEvent.isTrusted) return;
                    clearUndo();
                    act(
                      r.sid,
                      rpc.call<{ deleted: boolean; moved: number; file?: string }>("conv.delete", { convKey, sid: r.sid }),
                      (res) => {
                        if (res.deleted && res.file) {
                          setUndoItem({ file: res.file, label: title });
                          undoTimerRef.current = setTimeout(() => {
                            undoTimerRef.current = null;
                            setUndoItem(null);
                          }, 5000);
                        }
                      },
                    );
                  }}
                >
                  <Icon name="trash" size="sm" />
                </button>
              </span>
            </span>
          </div>
        );
      })}
      <Disclosure open={undoItem !== null} keepLastContent>
        {undoItem && (
          <div className="ep-trash-undo ui-card-enter" data-testid="session-undo">
            <span className="ep-trash-undo-text" title={undoItem.label}>已移入回收站</span>
            <button
              type="button"
              className="ui-btn ui-btn--ghost ui-btn--sm"
              data-testid="session-undo-btn"
              disabled={off}
              onClick={(e) => {
                if (!e.nativeEvent.isTrusted) return;
                const file = undoItem.file;
                clearUndo();
                act(file, rpc.call("conv.restoreTrash", { convKey, file }));
              }}
            >
              <Icon name="undo" size="sm" />
              撤销
            </button>
            <button
              type="button"
              className="ui-btn ui-btn--ghost ui-btn--sm ui-btn--icon"
              aria-label="关闭撤销提示"
              onClick={clearUndo}
            >
              <Icon name="close" size="sm" />
            </button>
          </div>
        )}
      </Disclosure>
      {error && (
        <div key={errSeq} className="warn ep-session-err ui-error-shake" data-testid="session-error">
          {error}
        </div>
      )}
      {trash && trash.length > 0 && (
        <div className="ep-trash" data-testid="trash-group">
          <button
            type="button"
            className="ui-row ep-session"
            data-testid="trash-toggle"
            aria-expanded={trashOpen}
            onClick={(e) => {
              if (!e.nativeEvent.isTrusted) return;
              setTrashOpen((v) => !v);
            }}
          >
            <span className="ui-chev" data-open={trashOpen ? "1" : "0"}>
              <Icon name="chevron-right" size="sm" />
            </span>
            <span className="ui-row-title">回收站（{trash.length}）</span>
          </button>
          <Disclosure open={trashOpen}>
            <div>
              {trash.map((t) => {
                const when = Number.isNaN(Date.parse(t.lastTs)) ? t.lastTs : formatRelTime(Date.parse(t.lastTs), nowMs);
                // 同 sid 多文件（手动搬回再删即再造）：行内显示时间戳后缀区分（红队发现 6）
                const dup = t.sid !== null && trash.filter((x) => x.sid === t.sid).length > 1;
                const displayName = t.title ?? (t.sid ? `${t.sid.slice(0, 8)}…` : "未知会话");
                const label = t.empty
                  ? "空文件"
                  : t.parseError
                    ? "无法解析"
                    : `${displayName} · ${t.messageCount} 条${t.partial ? "（记录不全）" : ""}${when ? ` · ${when}` : ""}${dup ? ` · ${t.file.replace(/\.jsonl$/, "").slice(17)}` : ""}`;
                const restorable = !t.empty && !t.parseError;
                const tBusy = busyId === t.file;
                return (
                  <div key={t.file} className="ep-session-wrap ep-trash-wrap" data-testid="trash-row" data-file={t.file}>
                    <span className="ui-row ep-session ep-trash-row" title={`${displayName} · ${formatTrashSize(t.bytes)}`}>
                      {tBusy && <span className="ui-spinner" />}
                      <span className="ui-row-title">{label}</span>
                    </span>
                    <span className="ep-trash-actions">
                      {restorable && (
                        <button
                          type="button"
                          className="ui-btn ui-btn--ghost ui-btn--sm ui-btn--icon ep-session-del"
                          data-testid="trash-restore"
                          aria-label={`还原：${displayName}`}
                          title="还原回会话列表"
                          disabled={off}
                          onClick={(e) => {
                            if (!e.nativeEvent.isTrusted) return;
                            act(t.file, rpc.call("conv.restoreTrash", { convKey, file: t.file }));
                          }}
                        >
                          <Icon name="undo" size="sm" />
                        </button>
                      )}
                      <button
                        type="button"
                        className="ui-btn ui-btn--ghost ui-btn--sm ui-btn--icon ui-btn--danger-hover ep-session-del"
                        data-testid="trash-purge"
                        aria-label={`彻底删除：${displayName}`}
                        title="彻底删除（不可恢复）"
                        disabled={off}
                        onClick={(e) => {
                          if (!e.nativeEvent.isTrusted) return;
                          act(t.file, rpc.call("conv.purgeTrash", { convKey, file: t.file }));
                        }}
                      >
                        <Icon name="trash" size="sm" />
                      </button>
                    </span>
                  </div>
                );
              })}
            </div>
          </Disclosure>
        </div>
      )}
    </div>
  );
}
