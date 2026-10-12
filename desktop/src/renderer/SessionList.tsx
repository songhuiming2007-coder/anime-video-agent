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

export function SessionList({ rpc, convKey, phase, titles = 0, onChanged }: { rpc: RpcClient; convKey: ConvKey; phase: ConvSnapshot["phase"]; titles?: number; onChanged: () => void }) {
  const [rows, setRows] = useState<SessionRow[] | null>(null);
  const [editing, setEditing] = useState<{ sid: string; text: string } | null>(null);
  // R1.5：标题换了（兜底 → 生成名 / 改名）的行淡入切换；首次载入不播
  const seen = useRef<Map<string, string>>(new Map());
  const [trash, setTrash] = useState<TrashRow[] | null>(null);
  const [trashOpen, setTrashOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const reload = useCallback(() => {
    rpc.call<SessionRow[]>("conv.sessions", { convKey }).then(
      // 最近活动在上（core 按文件顺序给，最旧在前；ISO 时间串字典序即时间序）
      (r) => setRows([...r].sort((a, b) => (a.lastActivity < b.lastActivity ? 1 : a.lastActivity > b.lastActivity ? -1 : 0))),
      (err) => setError(errText(err)),
    );
    rpc.call<TrashRow[]>("conv.trashList", { convKey }).then(
      (r) => setTrash([...r].sort((a, b) => (a.lastTs < b.lastTs ? 1 : a.lastTs > b.lastTs ? -1 : 0))),
      () => setTrash(null), // 回收站读不到不挡会话列表（错误只在会话区如实显示）
    );
  }, [rpc, convKey]);
  useEffect(() => {
    setError(null);
    reload();
  }, [reload, phase, titles]);

  /** 方法名在调用点写成字面量（TG-16：rpc.call 首参必须是方法闭集里的字面量） */
  const act = (call: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    call().then(
      () => {
        setBusy(false);
        reload();
        onChanged();
      },
      (err) => {
        setBusy(false);
        setError(errText(err));
        reload();
      },
    );
  };

  const running = phase === "running" || phase === "starting" || phase === "ending";
  const off = busy || running;
  const saveTitle = (sid: string, text: string) => {
    const problem = titleProblem(text);
    if (problem !== null) {
      setError(problem);
      return;
    }
    setEditing(null);
    // 改名不结束活会话、回合在跑时也能改（core 侧标题文件自带锁）：只受本列表自己的 busy 约束
    act(() => rpc.call("conv.rename", { convKey, sid, title: text.trim() }));
  };
  const anyLive = rows?.some((r) => r.live) ?? false;
  const now = Date.now();
  return (
    <div className="ep-sessions" data-testid="session-list" aria-busy={busy || undefined}>
      <button
        className="ui-row ep-session"
        data-testid="session-new"
        // 没有哪个会话在跑时，下一条消息本来就开新会话——如实标成当前
        aria-current={!anyLive ? "true" : undefined}
        disabled={off}
        title="结束当前会话，下一条消息从空白上下文开始"
        onClick={(e) => {
          if (!e.nativeEvent.isTrusted) return;
          act(() => rpc.call("conv.fresh", { convKey }));
        }}
      >
        <span className="ui-row-title">＋ 新会话</span>
      </button>
      {rows?.map((r) => {
        const title = r.title || "（没有用户消息）";
        const prev = seen.current.get(r.sid);
        const swapped = prev !== undefined && prev !== title;
        seen.current.set(r.sid, title);
        if (editing?.sid === r.sid) {
          return (
            <div key={r.sid} className="ep-session-wrap" data-testid="session-row" data-sid={r.sid} data-live={r.live ? "1" : "0"}>
              <input
                className="ui-input ep-session-edit"
                data-testid="session-rename-input"
                aria-label="会话标题"
                autoFocus
                value={editing.text}
                onChange={(e) => setEditing({ sid: r.sid, text: e.target.value })}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.nativeEvent.isComposing) {
                    e.preventDefault();
                    if (editing.text.trim() === "" || editing.text.trim() === title) setEditing(null); // 空串 / 没改 = 取消
                    else saveTitle(r.sid, editing.text);
                  } else if (e.key === "Escape") {
                    e.preventDefault();
                    setEditing(null);
                  }
                }}
                onBlur={() => setEditing(null)}
              />
            </div>
          );
        }
        const when = Number.isNaN(Date.parse(r.lastActivity)) ? r.lastActivity : formatRelTime(Date.parse(r.lastActivity), now);
        return (
          <div key={r.sid} className="ep-session-wrap" data-testid="session-row" data-sid={r.sid} data-live={r.live ? "1" : "0"}>
            <button
              className="ui-row ep-session"
              aria-current={r.live ? "true" : undefined}
              data-empty={r.firstUser ? undefined : "1"}
              disabled={off}
              title={`${title}\n${r.messages} 条消息 · ${when}`}
              onClick={(e) => {
                // 不按本地 r.live 拦：列表可能还没跟上刚结束的会话（e2e TX-10 实测吞点击）；「进的就是当前会话」由 host 判（conv.enter 原样返回）
                if (!e.nativeEvent.isTrusted) return;
                act(() => rpc.call("conv.enter", { convKey, sid: r.sid }));
              }}
            >
              <span key={title} className={`ui-row-title${swapped ? " ep-title-swap" : ""}`} data-testid="session-title" data-source={r.titleSource}>
                {title}
              </span>
            </button>
            <button
              className="ui-btn ui-btn--ghost ui-btn--sm ep-session-del"
              data-testid="session-rename"
              aria-label={`改名：${title}`}
              disabled={busy}
              onClick={(e) => {
                if (!e.nativeEvent.isTrusted) return;
                setError(null);
                setEditing({ sid: r.sid, text: r.titleSource === "first_input" ? "" : title });
              }}
            >
              改名
            </button>
            <button
              className="ui-btn ui-btn--ghost ui-btn--sm ep-session-del"
              data-testid="session-delete"
              aria-label={`删除会话：${title}`}
              disabled={off}
              onClick={(e) => {
                if (!e.nativeEvent.isTrusted) return;
                act(() => rpc.call("conv.delete", { convKey, sid: r.sid }));
              }}
            >
              删除
            </button>
          </div>
        );
      })}
      {error && (
        <div className="warn ep-session-err" data-testid="session-error">
          {error}
        </div>
      )}
      {trash && trash.length > 0 && (
        <div className="ep-trash" data-testid="trash-group">
          <button
            className="ui-row ep-session"
            data-testid="trash-toggle"
            aria-expanded={trashOpen}
            onClick={(e) => {
              if (!e.nativeEvent.isTrusted) return;
              setTrashOpen((v) => !v);
            }}
          >
            <span className="ui-row-title">{trashOpen ? "▾" : "▸"} 回收站（{trash.length}）</span>
          </button>
          {trashOpen &&
            trash.map((t) => {
              const when = Number.isNaN(Date.parse(t.lastTs)) ? t.lastTs : formatRelTime(Date.parse(t.lastTs), now);
              // 同 sid 多文件（手动搬回再删即再造）：行内显示时间戳后缀区分（红队发现 6）
              const dup = t.sid !== null && trash.filter((x) => x.sid === t.sid).length > 1;
              const label = t.empty
                ? "空文件"
                : t.parseError
                  ? "无法解析"
                  : `${t.title ?? (t.sid ? `${t.sid.slice(0, 8)}…` : "未知会话")} · ${t.messageCount} 条${t.partial ? "（记录不全）" : ""}${when ? ` · ${when}` : ""}${dup ? ` · ${t.file.replace(/\.jsonl$/, "").slice(17)}` : ""}`;
              return (
                <div key={t.file} className="ep-session-wrap" data-testid="trash-row" data-file={t.file}>
                  <span className="ui-row ep-session ep-trash-row" title={`${t.file}\n${formatTrashSize(t.bytes)}`}>
                    <span className="ui-row-title">{label}</span>
                  </span>
                  <button
                    className="ui-btn ui-btn--ghost ui-btn--sm ep-session-del"
                    data-testid="trash-purge"
                    aria-label={`彻底删除：${t.file}`}
                    disabled={off}
                    onClick={(e) => {
                      if (!e.nativeEvent.isTrusted) return;
                      act(() => rpc.call("conv.purgeTrash", { convKey, file: t.file }));
                    }}
                  >
                    彻底删除
                  </button>
                </div>
              );
            })}
        </div>
      )}
    </div>
  );
}
