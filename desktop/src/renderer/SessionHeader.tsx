// Spec 10 §2.6 / §2.8 / §4.3：会话头部。scope、LLM 状态、素材模式、确认记忆、结束会话、继续上次会话、建期…。
// 全仓唯一出现 "conv.command"、"conv.end"、"conv.resume" 的文件（TG-10：调用点只在原生元素的 onClick 里）。
import type { ConvEntry, ConvKey, ConvPhase } from "../shared/protocol";
import { NewEpisodeForm } from "./NewEpisodeForm";
import type { RpcClient } from "./rpc";
import { Icon } from "./icons";

export interface ReadyInfo {
  scope: string | null;
  llm: "ok" | "degraded" | null;
  degradeReason: string | null;
  otherSessions: number;
  otherLatest: string | null;
}

export function readyInfo(entries: readonly ConvEntry[]): ReadyInfo {
  let out: ReadyInfo = { scope: null, llm: null, degradeReason: null, otherSessions: 0, otherLatest: null };
  for (const e of entries) {
    if (e.k !== "frame" || e.frame.t !== "ready") continue;
    const f = e.frame;
    const others = Array.isArray(f.other_sessions) ? (f.other_sessions as { sid?: string }[]) : [];
    const last = others.length > 0 ? others[others.length - 1].sid : null;
    out = {
      scope: String(f.scope),
      llm: f.llm === "ok" ? "ok" : "degraded",
      degradeReason: typeof f.degrade_reason === "string" ? f.degrade_reason : null,
      otherSessions: others.length,
      otherLatest: typeof last === "string" ? last.slice(0, 8) : null,
    };
  }
  return out;
}

export function SessionHeader({
  rpc,
  convKey,
  phase,
  info,
  keyProblem,
  memoryAsk,
  isIdea,
  onCreated,
  onResumed,
  onEnded,
}: {
  rpc: RpcClient;
  convKey: ConvKey;
  phase: ConvPhase;
  info: ReadyInfo;
  keyProblem: string | null;
  memoryAsk: boolean;
  isIdea: boolean;
  onCreated: (epKey: string) => void;
  onResumed: () => void;
  onEnded: () => void;
}) {
  const live = phase === "starting" || phase === "idle" || phase === "running" || phase === "ending";
  const running = phase === "running";
  const asset = info.scope === "asset";
  return (
    <div className="session-head" data-testid="session-head" data-conv={convKey} data-phase={phase}>
      <span className="ui-badge" data-testid="session-scope">
        {info.scope === "asset" ? "素材模式" : info.scope === "pipeline" ? "流水线模式" : info.scope === "creative" ? "创作模式" : "—"}
      </span>
      {info.llm === "ok" && (
        <>
          <span className="ui-dot ui-dot--ok" data-testid="llm-ok" />
          <span className="muted">LLM 已连接</span>
        </>
      )}
      {info.llm === "degraded" && (
        <span className="warn" data-testid="llm-degraded">
          LLM 未就绪：{info.degradeReason ?? "未知原因"}
        </span>
      )}
      {keyProblem !== null && (
        <span className="muted" data-testid="key-problem">
          {keyProblem}
        </span>
      )}
      {info.otherSessions > 0 && (
        <span className="muted" data-testid="other-sessions">
          该期还有 {info.otherSessions} 个更早的会话{info.otherLatest ? `；最近一个 ${info.otherLatest}` : ""}
        </span>
      )}
      <span className="spacer" />
      {!isIdea && (
        <button
          className="ui-btn ui-btn--ghost ui-btn--sm"
          data-testid="scope-toggle"
          aria-pressed={asset}
          disabled={running || phase === "ending"}
          onClick={(e) => {
            if (!e.nativeEvent.isTrusted) return;
            void rpc.call("conv.command", { convKey, name: "scope", arg: asset ? "auto" : "asset" }).catch(() => undefined);
          }}
        >
          素材模式
        </button>
      )}
      {memoryAsk && (
        <button
          className="ui-btn ui-btn--ghost ui-btn--sm"
          data-testid="memory-ack-open"
          disabled={running}
          onClick={(e) => {
            if (!e.nativeEvent.isTrusted) return;
            void rpc.call("conv.command", { convKey, name: "memory_ack" }).catch(() => undefined);
          }}
        >
          确认记忆…
        </button>
      )}
      {isIdea && <NewEpisodeForm rpc={rpc} onCreated={onCreated} />}
      {!isIdea && live && (
        <button
          className="ui-btn ui-btn--ghost ui-btn--sm"
          data-testid="session-end"
          disabled={phase === "ending"}
          onClick={(e) => {
            if (!e.nativeEvent.isTrusted) return;
            void rpc.call("conv.end", { convKey }).then(() => onEnded(), () => onEnded());
          }}
        >
          结束会话
        </button>
      )}
      {!isIdea && !live && (
        <button
          className="ui-btn ui-btn--ghost ui-btn--sm"
          data-testid="session-resume"
          onClick={(e) => {
            if (!e.nativeEvent.isTrusted) return;
            void rpc.call("conv.resume", { convKey }).then(() => onResumed(), () => undefined);
          }}
        >
          继续上次会话
        </button>
      )}
      {running && <span className="ui-spinner" data-testid="session-running" />}
      {info.llm === null && <Icon name="info" size="sm" />}
    </div>
  );
}
