// D45：侧栏里当前期下面的会话子列表（人 2026-10-08 选方案 B）。
// 进入 = conv.enter（先结束空闲活会话，再 --continue <sid>）；「＋ 新会话」= conv.fresh；删除 = conv.delete（host 先弹原生确认框，移进回收站）。
// 列表只在：换期、会话阶段变化（起停、回合结束）、自己做完操作后重取；不轮询。
import { useCallback, useEffect, useState } from "react";
import type { ConvKey, ConvSnapshot, SessionRow } from "../shared/protocol";
import { formatRelTime } from "../shared/relTime";
import { errText, type RpcClient } from "./rpc";

export function SessionList({ rpc, convKey, phase, onChanged }: { rpc: RpcClient; convKey: ConvKey; phase: ConvSnapshot["phase"]; onChanged: () => void }) {
  const [rows, setRows] = useState<SessionRow[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const reload = useCallback(() => {
    rpc.call<SessionRow[]>("conv.sessions", { convKey }).then(
      // 最近活动在上（core 按文件顺序给，最旧在前；ISO 时间串字典序即时间序）
      (r) => setRows([...r].sort((a, b) => (a.lastActivity < b.lastActivity ? 1 : a.lastActivity > b.lastActivity ? -1 : 0))),
      (err) => setError(errText(err)),
    );
  }, [rpc, convKey]);
  useEffect(() => {
    setError(null);
    reload();
  }, [reload, phase]);

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
        const title = r.firstUser || "（没有用户消息）";
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
              <span className="ui-row-title">{title}</span>
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
    </div>
  );
}
