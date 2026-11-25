// Spec 10 §2.6 / §2.8 / §4.3：会话头部。scope、LLM 状态、素材模式、确认记忆、结束会话、建期…（D45：继续会话移到侧栏会话列表）。
// 全仓唯一出现 "conv.command"、"conv.end"、"conv.resume" 的文件（TG-10：调用点只在原生元素的 onClick 里）。
// D91：输入框下的模型 / 推理按钮（ModelControls）也因此写在这里，由 App 塞进 Composer 的工具栏。
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import type { AppPrefs, AuthStatusResult, ConvEntry, ConvKey, ConvPhase, CreatedEpisode, Health, ModelSourceInfo } from "../shared/protocol";
import { contextText, type ContextReading } from "../shared/convFold";
import { CopyButton } from "./copy";
import { FloatingPopover, FloatingTooltip } from "./floating";
import { Disclosure, SegmentedBar, useCountBump } from "./motion";
import { HumanTimeReadout } from "./HumanTimeReadout";
import { LoginModal } from "./LoginModal";
import { NewEpisodeForm } from "./NewEpisodeForm";
import { errText, type RpcClient } from "./rpc";
import type { EpisodeState } from "./store";
import { Icon } from "./icons";
import { StateView, StatusDot } from "./ui";

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

/** D41：读数的悬停说明——精炼 3 句说明口径与压缩手段 */
export const CONTEXT_READOUT_TITLE =
  "当前上下文占用（含提示词、工具与历史消息），斜杠后为模型标称窗口；服务商未返回 token 数时按正文字数估算（带「约」）。" +
  "觉得太长可在输入框发 /compact 压缩早期对话，或从左侧点「＋ 新会话」轻装继续（进度都在文件里）。";

export function healthAlerts(health: Health | null): string[] {
  if (!health) return [];
  const alerts: string[] = [];
  if (health.codeFreeze.ok === false) alerts.push(`代码写保护提醒：${health.codeFreeze.detail}`);
  const p = health.buildProvenance;
  if (p.kind === "dirty") alerts.push("界面构建含未提交改动");
  else if (p.kind === "stale") alerts.push(`界面未按当前源码重建（构建于 ${p.gitHead.slice(0, 8)}）`);
  else if (p.kind === "incomparable") alerts.push("无法比对界面构建版本（构建提交不在本仓库）");
  else if (p.kind === "unknown") alerts.push(`无法比对界面构建版本：${p.message}`);
  return alerts;
}

export function SessionHeader({
  rpc,
  convKey,
  phase,
  pendingScope = null,
  info,
  memoryAsk,
  isIdea,
  context,
  onCreated,
  onEnded,
  onOpenLogin,
  onCompact,
  title,
  ep,
  leftOpen,
  leftAttention = false,
  onToggleLeft,
  health,
  linked = true,
  healthSurfaced = true,
  canRefresh,
  refreshing = false,
  onRefresh,
  showHealth,
  onToggleHealth,
  utilityRight,
}: {
  rpc: RpcClient;
  convKey: ConvKey;
  phase: ConvPhase;
  pendingScope?: "asset" | "auto" | null;
  info: ReadyInfo;
  memoryAsk: boolean;
  isIdea: boolean;
  /** D41 / D56 / D64：最近一次回合结束时的上下文读数（`lastContextReading`）；继续会话先显示上次测得值；null = 新会话还没有回合结束过，不显示 */
  context: ContextReading | null;
  onCreated: (created: CreatedEpisode) => void;
  onEnded: () => void;
  onOpenLogin?: () => void;
  onCompact?: () => void;
  title: string;
  ep?: EpisodeState;
  leftOpen: boolean;
  leftAttention?: boolean;
  onToggleLeft: () => void;
  health: Health | null;
  linked?: boolean;
  healthSurfaced?: boolean;
  canRefresh: boolean;
  refreshing?: boolean;
  onRefresh: () => void;
  showHealth: boolean;
  onToggleHealth: () => void;
  utilityRight?: ReactNode;
}) {
  const live = phase === "starting" || phase === "idle" || phase === "running" || phase === "ending";
  const running = phase === "running";
  const asset = live ? info.scope === "asset" : pendingScope === "asset";
  const scopePending = !live && pendingScope === "asset";
  const scopeLabel =
    info.scope === "asset" ? "素材模式" : info.scope === "pipeline" ? "流水线模式" : info.scope === "creative" ? "创作模式" : info.scope === "project" ? "企划模式" : null;
  const contextReadout = context === null ? null : contextText(context);
  const [statusOpen, setStatusOpen] = useState(false);
  const [endArmed, setEndArmed] = useState(false);
  const [headBusy, setHeadBusy] = useState<"scope" | "mem" | "end" | null>(null);
  const [headErr, setHeadErr] = useState<string | null>(null);
  const convKeyRef = useRef(convKey);
  convKeyRef.current = convKey;
  useEffect(() => {
    setStatusOpen(false);
    setEndArmed(false);
    setHeadErr(null);
    setHeadBusy(null);
  }, [ep?.epKey, convKey]);
  useEffect(() => {
    if (!running || !live) setEndArmed(false);
  }, [running, live]);
  useEffect(() => {
    if (!endArmed) return;
    const t = setTimeout(() => setEndArmed(false), 5000);
    return () => clearTimeout(t);
  }, [endArmed]);
  const epStatus = ep && !isIdea ? ep.status : null;
  const statusVal = epStatus?.ok ? epStatus.value : null;
  const detailsId = ep ? `status-details-${ep.epKey}` : undefined;
  const alerts = healthAlerts(health);
  const healthDanger = !linked || health?.codeFreeze.ok === false || health?.buildProvenance.kind === "dirty";
  const ctxPct = context?.tokens !== null && context?.tokens !== undefined && context.ctxWindow ? Math.min(100, Math.round((context.tokens / context.ctxWindow) * 100)) : null;
  return (
    <>
      <div className="session-head" data-testid="session-head" data-conv={convKey} data-phase={phase}>
        {!leftOpen && (
          <FloatingTooltip
            label={<><span>{leftAttention ? "展开侧栏（有后台待答）" : "展开侧栏"}</span><kbd className="ui-kbd">⌘B</kbd></>}
            renderTrigger={(trigger) => (
              <button
                type="button"
                ref={trigger.ref}
                {...trigger.props}
                className={leftAttention ? "ui-btn ui-btn--ghost ui-btn--sm ui-btn--icon head-left-dot" : "ui-btn ui-btn--ghost ui-btn--sm ui-btn--icon"}
                aria-label="侧栏"
                aria-pressed={false}
                aria-controls="ava-left"
                onClick={onToggleLeft}
                data-testid="toggle-left"
              >
                <Icon name="panel-left" size="sm" />
              </button>
            )}
          />
        )}
        <strong className="head-title">{title}</strong>
        {statusVal !== null && (
          <>
            <span className="head-sep" aria-hidden="true">·</span>
            <span className="head-step" data-testid="current-step">{statusVal.current_step}</span>
            {statusVal.advisories.length > 0 && (
              <button
                type="button"
                className="ui-badge ui-badge--warn head-badge-btn"
                aria-expanded={statusOpen}
                aria-controls={detailsId}
                onClick={() => setStatusOpen((o) => !o)}
                data-testid="status-advisory-count"
              >
                {statusVal.advisories.length} 条提示
              </button>
            )}
            <button
              type="button"
              className="ui-btn ui-btn--ghost ui-btn--sm"
              aria-expanded={statusOpen}
              aria-controls={detailsId}
              onClick={() => setStatusOpen((o) => !o)}
              data-testid="status-toggle"
            >
              详情
              <span className="ui-chev" data-open={statusOpen ? "1" : "0"}>
                <Icon name="chevron-right" size="sm" />
              </span>
            </button>
          </>
        )}
        {scopeLabel !== null && !asset && (
          <span className="ui-badge" data-testid="session-scope">
            {scopeLabel}
          </span>
        )}
        {info.otherSessions > 0 && (
          <span className="muted" data-testid="other-sessions">
            该期还有 {info.otherSessions} 个会话
          </span>
        )}
        {info.llm === "degraded" && (
          onOpenLogin ? (
            <button
              type="button"
              className="ui-btn ui-btn--ghost ui-btn--sm warn"
              data-testid="llm-degraded-mark"
              onClick={onOpenLogin}
              title="点击配置模型服务商登录"
            >
              模型未就绪 · 去登录
            </button>
          ) : (
            <span className="warn" data-testid="llm-degraded-mark">
              模型未就绪
            </span>
          )
        )}
        {running && <span className="ui-spinner" data-testid="session-running" />}
        <span className="spacer" />
        {contextReadout !== null && (
          <FloatingPopover
            placement="bottom-end"
            role="dialog"
            className="ui-popover context-pop"
            renderTrigger={(trigger) => (
              <FloatingTooltip
                label={CONTEXT_READOUT_TITLE}
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
                    className="ui-badge session-head-readout"
                    data-testid="context-readout"
                  >
                    {contextReadout}
                  </button>
                )}
              />
            )}
            renderContent={(close) => (
              <>
                <div className="ui-popover-title">上下文占用</div>
                <div className="side-head-row">
                  <strong>{contextReadout}</strong>
                  {ctxPct !== null && <span className="ui-badge">{ctxPct}%</span>}
                </div>
                <div className="context-pop-desc">{CONTEXT_READOUT_TITLE}</div>
                {onCompact && live && !running && (
                  <button
                    type="button"
                    className="ui-btn ui-btn--sm"
                    onClick={() => {
                      close();
                      onCompact();
                    }}
                  >
                    压缩上下文（/compact）
                  </button>
                )}
              </>
            )}
          />
        )}
        {/* D81：企划会话没有素材模式（企划级只做规划、跨卷审查、总装）；未启动会话也可预选素材模式 */}
        {!isIdea && !convKey.startsWith("proj:") && (
          <FloatingTooltip
            label={scopePending ? "已预选素材模式，发第一条消息时自动进入（再点可取消）" : asset ? "当前处于素材模式，点击切回自动模式" : "切换到素材整理与索引模式（未启动会话也可先预选）"}
            renderTrigger={(trigger) => (
              <button
                type="button"
                ref={trigger.ref}
                {...trigger.props}
                className="ui-btn ui-btn--ghost ui-btn--sm"
                data-testid="scope-toggle"
                data-pending={scopePending ? "1" : undefined}
                aria-pressed={asset}
                disabled={running || phase === "ending" || headBusy !== null}
                onClick={(e) => {
                  if (!e.nativeEvent.isTrusted) return;
                  const targetKey = convKey;
                  setHeadBusy("scope");
                  setHeadErr(null);
                  void rpc.call("conv.command", { convKey: targetKey, name: "scope", arg: asset ? "auto" : "asset" }).then(
                    () => {
                      if (convKeyRef.current === targetKey) setHeadBusy(null);
                    },
                    (err) => {
                      if (convKeyRef.current !== targetKey) return;
                      setHeadBusy(null);
                      setHeadErr(errText(err));
                    },
                  );
                }}
              >
                {headBusy === "scope" && <span className="ui-spinner" />}
                素材模式
                {scopePending && <span className="ui-badge" data-testid="scope-pending">待生效</span>}
              </button>
            )}
          />
        )}
        {memoryAsk && (
          <button
            type="button"
            className="ui-btn ui-btn--ghost ui-btn--sm"
            data-testid="memory-ack-open"
            disabled={running || headBusy !== null}
            onClick={(e) => {
              if (!e.nativeEvent.isTrusted) return;
              const targetKey = convKey;
              setHeadBusy("mem");
              setHeadErr(null);
              void rpc.call("conv.command", { convKey: targetKey, name: "memory_ack" }).then(
                () => {
                  if (convKeyRef.current === targetKey) setHeadBusy(null);
                },
                (err) => {
                  if (convKeyRef.current !== targetKey) return;
                  setHeadBusy(null);
                  setHeadErr(errText(err));
                },
              );
            }}
          >
            {headBusy === "mem" && <span className="ui-spinner" />}
            确认记忆…
          </button>
        )}
        {isIdea && <NewEpisodeForm rpc={rpc} onCreated={onCreated} asPopover defaultFromIdea hasIdeaSession />}
        {!isIdea && live && (
          running && endArmed ? (
            <span className="head-end-confirm ui-confirm-enter">
              <button
                type="button"
                className="ui-btn ui-btn--danger ui-btn--sm"
                data-testid="session-end"
                disabled={headBusy !== null}
                onClick={(e) => {
                  if (!e.nativeEvent.isTrusted) return;
                  const targetKey = convKey;
                  setEndArmed(false);
                  setHeadBusy("end");
                  setHeadErr(null);
                  void rpc.call("conv.end", { convKey: targetKey }).then(
                    () => {
                      if (convKeyRef.current !== targetKey) return;
                      setHeadBusy(null);
                      onEnded();
                    },
                    (err) => {
                      if (convKeyRef.current !== targetKey) return;
                      setHeadBusy(null);
                      setHeadErr(errText(err));
                      onEnded();
                    },
                  );
                }}
              >
                {headBusy === "end" && <span className="ui-spinner" />}
                确认结束
              </button>
              <button
                type="button"
                className="ui-btn ui-btn--ghost ui-btn--sm"
                onClick={() => setEndArmed(false)}
              >
                取消
              </button>
            </span>
          ) : (
            <button
              type="button"
              className="ui-btn ui-btn--ghost ui-btn--danger-hover ui-btn--sm"
              data-testid="session-end"
              disabled={phase === "ending" || headBusy !== null}
              onClick={(e) => {
                if (!e.nativeEvent.isTrusted) return;
                if (running) {
                  setEndArmed(true);
                  return;
                }
                const targetKey = convKey;
                setHeadBusy("end");
                setHeadErr(null);
                void rpc.call("conv.end", { convKey: targetKey }).then(
                  () => {
                    if (convKeyRef.current !== targetKey) return;
                    setHeadBusy(null);
                    onEnded();
                  },
                  (err) => {
                    if (convKeyRef.current !== targetKey) return;
                    setHeadBusy(null);
                    setHeadErr(errText(err));
                    onEnded();
                  },
                );
              }}
            >
              {(phase === "ending" || headBusy === "end") && <span className="ui-spinner" />}
              {phase === "ending" ? "正在结束…" : "结束会话"}
            </button>
          )
        )}
        {health && !health.isPackaged && (
          <span className="ui-badge ui-badge--warn" data-testid="dev-build">
            DEV
          </span>
        )}
        {canRefresh && (
          <FloatingTooltip
            label={<><span>刷新</span><kbd className="ui-kbd">⌘R</kbd></>}
            renderTrigger={(trigger) => (
              <button
                type="button"
                ref={trigger.ref}
                {...trigger.props}
                className="ui-btn ui-btn--ghost ui-btn--sm ui-btn--icon"
                aria-label="刷新"
                aria-busy={refreshing || undefined}
                onClick={onRefresh}
                data-testid="refresh"
              >
                <Icon name="refresh" size="sm" className={refreshing ? "ui-spin-icon" : undefined} />
              </button>
            )}
          />
        )}
        {healthSurfaced && (
          <FloatingTooltip
            label={alerts.length > 0 ? `诊断与环境（${alerts.join("；")}）` : !linked ? "诊断与环境（后台连接断开）" : "诊断与环境"}
            renderTrigger={(trigger) => (
              <button
                type="button"
                ref={trigger.ref}
                {...trigger.props}
                className={alerts.length > 0 || !linked ? "ui-btn ui-btn--ghost ui-btn--sm" : "ui-btn ui-btn--ghost ui-btn--sm ui-btn--icon"}
                aria-label="诊断与环境"
                aria-pressed={showHealth}
                onClick={onToggleHealth}
                data-testid="health-toggle"
              >
                <Icon name="pulse" size="sm" />
                {(alerts.length > 0 || !linked) && <StatusDot tone={healthDanger ? "danger" : "warn"} />}
              </button>
            )}
          />
        )}
        {utilityRight}
      </div>
      {headErr && (
        <div className="head-substrip warn ui-error-shake" role="alert">
          <span>{headErr}</span>
          <button type="button" className="ui-btn ui-btn--ghost ui-btn--sm ui-btn--icon" aria-label="关闭提示" onClick={() => setHeadErr(null)}>
            <Icon name="close" size="sm" />
          </button>
        </div>
      )}
      {epStatus && !epStatus.ok && (
        <StateView kind="error" title={`工序读取失败（${epStatus.code}）：${epStatus.message}`} />
      )}
      {ep && statusVal !== null && (
        <Disclosure open={statusOpen} keepMounted>
          <div className="status-details head-substrip" id={detailsId} data-testid="status-details">
            {statusVal.next_command && (
              <div className="head-cmd-row">
                <code className="ui-code cmd">{statusVal.next_command}</code>
                <CopyButton text={statusVal.next_command} testId="status-copy-cmd" />
              </div>
            )}
            {statusVal.block_reason && <div className="muted">{statusVal.block_reason}</div>}
            <div>下一步：{statusVal.next_action}</div>
            {statusVal.advisories.length > 0 && (
              <ul className="advisories">
                {statusVal.advisories.map((a) => (
                  <li key={a}>
                    <Icon name="alert" size="sm" />
                    {a}
                  </li>
                ))}
              </ul>
            )}
            <HumanTimeReadout key={ep.epKey} epKey={ep.epKey} rpc={rpc} version={ep.events.length} />
          </div>
        </Disclosure>
      )}
    </>
  );
}

/**
 * 对话流首行的会话说明（D39 C2）：仅当模型真正降级（degraded）时展示原因与钥匙串提示，避免启动途中误报。
 */
export function SessionNotes({ info, keyProblem, onOpenLogin }: { info: ReadyInfo; keyProblem: string | null; onOpenLogin?: () => void }) {
  const showKeyProblem = keyProblem !== null && info.llm === "degraded";
  if (info.llm !== "degraded" && !showKeyProblem) return null;
  return (
    <div className="session-notes ui-card-enter" data-testid="session-notes">
      {info.llm === "degraded" && (
        <div className="session-notes-row">
          <div className="warn" data-testid="llm-degraded">
            模型未就绪：{info.degradeReason ?? "未知原因"}
          </div>
          {onOpenLogin && (
            <button type="button" className="ui-btn ui-btn--sm" onClick={onOpenLogin}>
              配置登录
            </button>
          )}
        </div>
      )}
      {showKeyProblem && (
        <div className="muted" data-testid="key-problem">
          {keyProblem}
        </div>
      )}
    </div>
  );
}

// ---------------- D91：本会话模型与推理强度（照 ZCode 输入框工具栏） ----------------

export interface ModelInfo {
  model: string | null;
  effort: string | null;
  modelSource: "session" | "default";
  effortSource: "session" | "default";
}

/** 最近一帧 model_config（ready 也带同样字段，取后到的）；会话没起过为 null */
export function modelInfo(entries: readonly ConvEntry[]): ModelInfo | null {
  let out: ModelInfo | null = null;
  for (const e of entries) {
    if (e.k !== "frame" || (e.frame.t !== "model_config" && e.frame.t !== "ready")) continue;
    const f = e.frame;
    if (!("model" in f)) continue; // 旧 core 的 ready 不带这几个键
    out = {
      model: typeof f.model === "string" ? f.model : null,
      effort: typeof f.reasoning_effort === "string" ? f.reasoning_effort : null,
      modelSource: f.model_source === "session" ? "session" : "default",
      effortSource: f.effort_source === "session" ? "session" : "default",
    };
  }
  return out;
}

/** 档位 → 中文词（与 pipeline/agent/model_switch.py 的 EFFORT_LABELS 同表） */
export const EFFORT_LABELS: Readonly<Record<string, string>> = {
  none: "关", minimal: "极低", low: "低", medium: "中", high: "高", xhigh: "超高", auto: "自动", max: "最大",
};
export function effortText(effort: string | null): string {
  return effort === null ? "默认" : EFFORT_LABELS[effort] ?? effort;
}

/** D156/D168：每档的描述（照 Pi 档位语义与 token 预算）。 */
export const EFFORT_DESCRIPTIONS: Readonly<Record<string, string>> = {
  none: "不启用推理",
  minimal: "极简推理（~1k token）",
  low: "轻度推理（~2k token）",
  medium: "中等推理（~8k token）",
  high: "深度推理（~16k token）",
  xhigh: "超高推理（~32k token）",
  max: "最大推理（~64k token）",
  auto: "由模型自行决定",
};

/** 档位循环次序（Shift+Tab 按此走；与 model_switch.py 的 EFFORT_ORDER 同序）。 */
export const EFFORT_ORDER: readonly string[] = ["none", "minimal", "low", "medium", "high", "xhigh", "auto", "max"];
export const EFFORT_DEFAULT_LIST: readonly string[] = ["none", "minimal", "low", "medium", "high", "xhigh", "auto"];

/** D168：按模型家族推断客户端兜底推理档位（与服务端 _family_heuristic_efforts 同源，永不锁死）。 */
export function clientFamilyEfforts(model: string | null): string[] {
  if (!model) return [...EFFORT_DEFAULT_LIST];
  const low = model.trim().toLowerCase();
  const bare = low.includes("/") ? low.slice(low.lastIndexOf("/") + 1) : low;
  if (["claude-opus-4-6", "claude-opus-4.6", "claude-opus-5", "claude-sonnet-4-6", "claude-sonnet-4.6", "claude-sonnet-5"].some((p) => bare.includes(p))) {
    return ["none", "minimal", "low", "medium", "high", "xhigh", "max"];
  }
  if (bare.startsWith("claude-") || bare.startsWith("gpt-5") || bare.startsWith("gpt-6") || bare.startsWith("o1") || bare.startsWith("o3") || bare.startsWith("o4") || bare.includes("codex")) {
    return ["none", "minimal", "low", "medium", "high", "xhigh"];
  }
  if (bare.startsWith("gemini-2.5") || bare.startsWith("gemini-3")) {
    return ["none", "minimal", "low", "medium", "high"];
  }
  if (bare.startsWith("deepseek")) {
    return ["none", "low", "medium", "high"];
  }
  return [...EFFORT_DEFAULT_LIST];
}

/**
 * D168：三层前端支持集解析：model_efforts[model] > 服务端 directEfforts > 模型家族启发式兜底。
 * 确保新会话（phase === "none"）或未入 compact.json 探针表的模型也始终可调推理强度。
 */
export function resolveSupportedEfforts(
  model: string | null,
  modelEfforts: Readonly<Record<string, readonly string[]>> | null | undefined,
  directEfforts: readonly string[] | null | undefined,
): string[] {
  if (model && modelEfforts) {
    if (model in modelEfforts) return [...modelEfforts[model]];
    const firstSlash = model.indexOf("/");
    const rest = firstSlash >= 0 ? model.slice(firstSlash + 1) : model;
    if (rest in modelEfforts) return [...modelEfforts[rest]];
    const lastSlash = model.lastIndexOf("/");
    const bare = lastSlash >= 0 ? model.slice(lastSlash + 1) : model;
    if (bare in modelEfforts) return [...modelEfforts[bare]];
  }
  if (directEfforts && directEfforts.length > 0) {
    return [...directEfforts];
  }
  if (directEfforts && directEfforts.length === 0 && model) {
    return [];
  }
  return clientFamilyEfforts(model);
}

/** D168：推理档位映射为 0..5 信号格数（按钮可视化指示）。 */
export function effortBarLevel(effort: string | null): number {
  switch (effort) {
    case "minimal":
      return 1;
    case "low":
      return 2;
    case "medium":
    case "auto":
      return 3;
    case "high":
      return 4;
    case "xhigh":
    case "max":
      return 5;
    default:
      return 0;
  }
}

/**
 * D156：Shift+Tab 循环档位的纯函数——从 efforts 支持集里挑出当前档的下一档。
 * @param efforts 该模型实测支持集（不含 "default"，default 用 null 态表示）；null/空 = 不支持循环（只有默认）。
 * @param current 当前档（null = 默认态）
 * @returns 下一档名；支持集为空/未拉到时回 null（不动）。
 */
export function nextEffort(efforts: readonly string[] | null, current: string | null): string | null {
  if (!efforts || efforts.length === 0) return null;
  // 只循环实测支持集（D155 诚实纪律：没实测的档不轮进去）
  const order = EFFORT_ORDER.filter((lv) => efforts.includes(lv));
  if (order.length === 0) return null;
  const idx = current === null ? -1 : order.indexOf(current);
  return order[(idx + 1) % order.length];
}

/** 输入框工具栏上的两个按钮：模型（点开列出代理的模型）、推理强度（点开选档位）。只改本会话。
 * 会话还没起（还没发过消息）时显示「—」并提示：先发一条消息，或直接发 /model。回合在跑时置灰。
 */
/** 输入框发裸 `/model`、`/thinking`、`/login` 时由 App 递进来：n 每次加一，展开对应浮层 */
export type ModelOpenReq = { which: "model" | "effort" | "login"; n: number };

/** D149/D147：按钮是否可用。可用 = none（存 pending）/ idle / running（core 排队）；
 * 禁 = starting（proc 未 ready）/ ending（收尾）/ exited（进程没了）。 */
export function modelCtlUsable(phase: ConvPhase): boolean {
  return phase === "none" || phase === "idle" || phase === "running";
}

/** 悬停提示：running 时如实说生效时机（core 回合收尾才执行 override）；none 时说 pending 语义；其余 undefined（用默认 title） */
export function modelCtlHint(phase: ConvPhase): string | undefined {
  if (phase === "running") return "当前正在跑，切换在下个回合生效";
  if (phase === "none") return "会话未启动：选择后存下，发第一条消息时生效";
  return undefined;
}

/** popover 顶部的生效提示行：running / none 各一行，idle 不显示（立即生效不误导） */
export function modelPopNote(phase: ConvPhase): string | null {
  if (phase === "running") return "切换在本回合结束后、下个回合生效";
  if (phase === "none") return "会话未启动：选择存下，发第一条消息时生效";
  return null;
}

const CHANNEL_LABELS: Readonly<Record<string, string>> = {
  devin: "Devin",
  antigravity: "Antigravity",
  opencode: "OpenCode",
  deepseek: "DeepSeek",
  openai: "OpenAI",
  anthropic: "Anthropic",
  google: "Google",
  cognition: "Cognition",
  moonshot: "Moonshot",
  nvidia: "NVIDIA",
};

// 与 pipeline/agent/auth.py _format_channel_label 保持同口径（旧 core 无 model_sources 时的前端回退）
function formatChannelLabel(raw: string): string {
  const trimmed = raw.trim();
  const mapped = CHANNEL_LABELS[trimmed.toLowerCase()];
  if (mapped) return mapped;
  if (/^[a-z]+$/.test(trimmed) && trimmed.length <= 24) {
    return trimmed[0].toUpperCase() + trimmed.slice(1);
  }
  return trimmed;
}

export function sourceKindLabel(kind: string): string {
  switch (kind) {
    case "oauth":
    case "oauth_pkce":
    case "oauth_device":
      return "OAuth";
    case "api_key":
      return "API Key";
    case "env":
      return "环境变量";
    case "proxy":
      return "代理";
    case "config":
      return "配置";
    default:
      return kind;
  }
}

export function shortSourceLabel(meta: ModelSourceInfo): string {
  if (meta.source_id.startsWith("default:") && meta.channel) {
    return `${meta.channel} · 代理`;
  }
  if (meta.source_id === "cliproxyapi") {
    return meta.channel ? `CLIProxy · ${meta.channel}` : "CLIProxyAPI";
  }
  if (meta.source_id === "ollama") return "Ollama";
  if (meta.source_id === "lmstudio") return "LM Studio";
  if (meta.source_id === "default") return meta.source_name.startsWith("默认代理") ? "默认代理" : "默认配置";
  return meta.source_name;
}

export interface ResolvedModelSource {
  id: string;
  label: string;
  shortLabel: string;
  kindLabel: string;
  displayModel: string;
  channel?: string;
  ownedBy?: string;
  endpoint?: string;
}

export interface ModelEntryItem {
  id: string;
  displayModel: string;
  sourceBadge: string;
  ownedBy?: string;
  endpoint?: string;
  searchText: string;
}

export interface ModelSourceGroup {
  id: string;
  label: string;
  shortLabel: string;
  kindLabel: string;
  endpoint?: string;
  items: ModelEntryItem[];
}

function matchesActiveModelSource(active: ModelSourceInfo, trimmed: string): boolean {
  if (active.source_id === "default" || active.source_id.startsWith("default:")) {
    return active.wire_model === trimmed;
  }
  return Boolean(active.wire_model && `${active.source_id}/${active.wire_model}` === trimmed);
}

export function resolveModelSource(
  model: string | null,
  modelSources: Readonly<Record<string, ModelSourceInfo>> | null | undefined,
  authStatus: AuthStatusResult | null | undefined,
  activeModelSource?: ModelSourceInfo | null,
): ResolvedModelSource | null {
  if (!model || model.trim() === "") return null;
  const trimmed = model.trim();
  const direct =
    modelSources?.[trimmed] ??
    (activeModelSource && matchesActiveModelSource(activeModelSource, trimmed)
      ? activeModelSource
      : undefined);
  if (direct) {
    const shortLabel = shortSourceLabel(direct);
    return {
      id: direct.source_id,
      label: direct.source_name,
      shortLabel,
      kindLabel: sourceKindLabel(direct.source_kind),
      displayModel: direct.display_model || trimmed,
      ...(direct.channel ? { channel: direct.channel } : {}),
      ...(direct.owned_by ? { ownedBy: direct.owned_by } : {}),
      ...(direct.endpoint ? { endpoint: direct.endpoint } : {}),
    };
  }
  const slash = trimmed.indexOf("/");
  if (slash > 0) {
    const pid = trimmed.slice(0, slash);
    const rest = trimmed.slice(slash + 1).trim();
    const providers = authStatus?.providers ?? [];
    const matched = providers.find((p) => p.connected && (p.id === pid || (p.aliases ?? []).includes(pid)));
    if (matched && rest !== "" && matched.models.includes(rest)) {
      const meta: ModelSourceInfo = {
        source_id: matched.id,
        source_name: matched.name,
        source_kind: matched.auth_type || "api_key",
        wire_model: rest,
        display_model: rest,
      };
      return {
        id: matched.id,
        label: matched.name,
        shortLabel: shortSourceLabel(meta),
        kindLabel: sourceKindLabel(meta.source_kind),
        displayModel: rest,
      };
    }
    const ch = formatChannelLabel(pid);
    return {
      id: `default:${pid.toLowerCase()}`,
      label: `${ch} (默认代理)`,
      shortLabel: `${ch} · 代理`,
      kindLabel: "代理",
      displayModel: rest || trimmed,
      channel: ch,
    };
  }
  return {
    id: "default",
    label: "默认配置",
    shortLabel: "默认配置",
    kindLabel: "配置",
    displayModel: trimmed,
  };
}

export function groupModelsBySource(
  models: readonly string[] | null,
  modelSources: Readonly<Record<string, ModelSourceInfo>> | null | undefined,
  authStatus: AuthStatusResult | null | undefined,
  query: string,
  sourceFilter: string | null,
): { allGroups: ModelSourceGroup[]; filteredGroups: ModelSourceGroup[] } {
  if (!models || models.length === 0) return { allGroups: [], filteredGroups: [] };
  const byGroup = new Map<string, ModelSourceGroup>();
  for (const m of models) {
    const src = resolveModelSource(m, modelSources, authStatus) ?? {
      id: "default",
      label: "默认配置",
      shortLabel: "默认配置",
      kindLabel: "配置",
      displayModel: m,
    };
    let grp = byGroup.get(src.id);
    if (!grp) {
      grp = {
        id: src.id,
        label: src.label,
        shortLabel: src.shortLabel,
        kindLabel: src.kindLabel,
        ...(src.endpoint ? { endpoint: src.endpoint } : {}),
        items: [],
      };
      byGroup.set(src.id, grp);
    }
    const searchText = [
      m,
      src.displayModel,
      src.label,
      src.shortLabel,
      src.id,
      src.kindLabel,
      src.channel ?? "",
      src.ownedBy ?? "",
      src.endpoint ?? "",
    ]
      .join(" ")
      .toLowerCase();
    grp.items.push({
      id: m,
      displayModel: src.displayModel,
      sourceBadge: src.shortLabel,
      ...(src.ownedBy ? { ownedBy: src.ownedBy } : {}),
      ...(src.endpoint ? { endpoint: src.endpoint } : {}),
      searchText,
    });
  }
  const providerOrder = new Map<string, number>();
  (authStatus?.providers ?? []).forEach((p, idx) => providerOrder.set(p.id, idx));
  const rankOf = (g: ModelSourceGroup): [number, number, string] => {
    const pIdx = providerOrder.get(g.id);
    if (pIdx !== undefined) return [0, pIdx, g.label];
    if (g.id.startsWith("default:")) return [1, 0, g.label];
    if (g.id === "default") return [2, 0, g.label];
    return [3, 0, g.label];
  };
  const allGroups = [...byGroup.values()].sort((a, b) => {
    const ra = rankOf(a);
    const rb = rankOf(b);
    return ra[0] - rb[0] || ra[1] - rb[1] || ra[2].localeCompare(rb[2]);
  });
  const tokens = query
    .trim()
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean);
  const effectiveFilter =
    sourceFilter && allGroups.some((grp) => grp.id === sourceFilter) ? sourceFilter : null;
  const filteredGroups: ModelSourceGroup[] = [];
  for (const grp of allGroups) {
    if (effectiveFilter && grp.id !== effectiveFilter) continue;
    const items =
      tokens.length === 0
        ? grp.items
        : grp.items.filter((item) => tokens.every((tok) => item.searchText.includes(tok)));
    if (items.length > 0) {
      filteredGroups.push({ ...grp, items });
    }
  }
  return { allGroups, filteredGroups };
}

export function ModelControls({ rpc, convKey, phase, info, openReq = null, pending = null }: { rpc: RpcClient; convKey: ConvKey; phase: ConvPhase; info: ModelInfo | null; openReq?: ModelOpenReq | null; pending?: { model?: string; effort?: string } | null }) {
  // D149：running 也可用（core 排队到回合收尾执行，下个回合生效）；D147：none 也可用（存 pending，首条消息带出）。
  const usable = modelCtlUsable(phase);
  const rawPendingModel = pending?.model ?? null;
  const rawPendingEffort = pending?.effort ?? null;
  const modelExplicitDefault = rawPendingModel === "default";
  const effortExplicitDefault = rawPendingEffort === "default";
  const pendingModel = modelExplicitDefault ? null : rawPendingModel;
  const pendingEffort = effortExplicitDefault ? null : rawPendingEffort;
  const [modelOpen, setModelOpen] = useState(false);
  const [effortOpen, setEffortOpen] = useState(false);
  const [loginOpen, setLoginOpen] = useState(false);
  const [authStatus, setAuthStatus] = useState<AuthStatusResult | null>(null);
  const [models, setModels] = useState<string[] | null>(null);
  const [efforts, setEfforts] = useState<string[] | null>(null); // D154/D168：该模型支持档（core 回子集）
  const [effortsForModel, setEffortsForModel] = useState<string | null>(null);
  const [modelEfforts, setModelEfforts] = useState<Record<string, string[]> | null>(null);
  const [modelSources, setModelSources] = useState<Record<string, ModelSourceInfo> | null>(null);
  const [activeModelSource, setActiveModelSource] = useState<ModelSourceInfo | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [customModel, setCustomModel] = useState("");
  const [modelQuery, setModelQuery] = useState("");
  const [modelKbdIdx, setModelKbdIdx] = useState(0);
  const [kbdNavActive, setKbdNavActive] = useState(false);
  const [sourceFilter, setSourceFilter] = useState<string | null>(null);
  const [warn, setWarn] = useState<string | null>(null);
  const [hoverEffort, setHoverEffort] = useState<string | null>(null);
  // A2：用户显式通过「设为默认」持久化的跨会话默认配置
  const [prefs, setPrefs] = useState<AppPrefs>({ defaultModel: null, defaultEffort: null });
  const [prefNotice, setPrefNotice] = useState<string | null>(null);
  const fetchGenRef = useRef(0);
  const searchRef = useRef<HTMLInputElement | null>(null);
  const listRef = useRef<HTMLDivElement | null>(null);
  // A2：未启动的新会话若未显式覆写，展示即将生效的用户自定义默认
  const savedDefaultModel = phase === "none" && !modelExplicitDefault && info === null ? prefs.defaultModel : null;
  const savedDefaultEffort = phase === "none" && !effortExplicitDefault && info === null ? prefs.defaultEffort : null;
  const activeModel = pendingModel ?? info?.model ?? savedDefaultModel;
  const currentEffort = pendingEffort ?? info?.effort ?? savedDefaultEffort;
  const activeSource = useMemo(
    () => resolveModelSource(activeModel, modelSources, authStatus, activeModelSource),
    [activeModel, modelSources, authStatus, activeModelSource],
  );
  const { allGroups: modelGroups, filteredGroups: visibleModelGroups } = useMemo(
    () => groupModelsBySource(models, modelSources, authStatus, modelQuery, sourceFilter),
    [models, modelSources, authStatus, modelQuery, sourceFilter],
  );
  const flatModels = useMemo(() => visibleModelGroups.flatMap((g) => g.items), [visibleModelGroups]);
  const loginCountBump = useCountBump(authStatus?.connected_count ?? 0);
  useEffect(() => {
    setModelKbdIdx(0);
    setKbdNavActive(modelQuery !== "");
  }, [modelQuery, sourceFilter, modelOpen]);
  useEffect(() => {
    setModelKbdIdx((i) => (flatModels.length === 0 ? 0 : Math.min(i, flatModels.length - 1)));
  }, [flatModels.length]);
  useEffect(() => {
    if (!modelOpen || !kbdNavActive) return;
    const item = flatModels[modelKbdIdx];
    if (!item) return;
    const btn = listRef.current?.querySelector(`[data-model="${CSS.escape(item.id)}"]`);
    if (btn instanceof HTMLElement) btn.scrollIntoView({ block: "nearest" });
  }, [modelOpen, kbdNavActive, modelKbdIdx, flatModels]);
  useEffect(() => {
    if (sourceFilter !== null && models !== null && !modelGroups.some((g) => g.id === sourceFilter)) {
      setSourceFilter(null);
    }
  }, [sourceFilter, models, modelGroups]);
  useEffect(() => {
    if (!prefNotice) return;
    const t = setTimeout(() => setPrefNotice(null), 2600);
    return () => clearTimeout(t);
  }, [prefNotice]);
  const scopedDirectEfforts = efforts !== null && (effortsForModel === activeModel || effortsForModel === null) ? efforts : null;
  const supportedEfforts = resolveSupportedEfforts(activeModel, modelEfforts, scopedDirectEfforts);
  const meterLevel = effortBarLevel(currentEffort);
  useEffect(() => {
    fetchGenRef.current += 1;
    setModels(null);
    setEfforts(null);
    setEffortsForModel(null);
    setModelEfforts(null);
    setModelSources(null);
    setActiveModelSource(null);
    setErr(null);
    setWarn(null);
    setCustomModel("");
    setModelQuery("");
    setSourceFilter(null);
    setModelOpen(false);
    setEffortOpen(false);
  }, [convKey]); // eslint-disable-line react-hooks/exhaustive-deps
  // 取列表走纯读的 conv.models（不在点击纪律内：它不改任何设置）。
  // D153/D168：会话未起（none）时经 PROBE_MODEL_LIST 取全量模型与 model_efforts 映射；
  // 进程启动从 none→starting→idle 时不再重复往 stdin 自动灌入 {t:"command", name:"models"}（修复 N32）。
  const fetchModels = () => {
    const gen = (fetchGenRef.current += 1);
    const targetModel = activeModel;
    void rpc
      .call<{
        ok?: boolean;
        text?: string;
        items?: string[];
        efforts?: string[];
        model_efforts?: Record<string, string[]>;
        model_sources?: Record<string, ModelSourceInfo>;
        active_model_source?: ModelSourceInfo;
      }>("conv.models", { convKey })
      .then(
        (res) => {
          if (gen !== fetchGenRef.current) return;
          setModels(res.ok ? res.items ?? [] : []);
          setEfforts(res.ok ? res.efforts ?? null : null);
          setEffortsForModel(res.ok ? targetModel : null);
          if (res.ok && res.model_efforts) setModelEfforts(res.model_efforts);
          setModelSources(res.ok ? res.model_sources ?? null : null);
          setActiveModelSource(res.ok ? res.active_model_source ?? null : null);
          setErr(res.ok ? null : res.text ?? "取模型列表失败");
        },
        (e2) => {
          if (gen !== fetchGenRef.current) return;
          setErr(errText(e2));
        },
      );
  };
  // D168：仅在会话未启动（phase === "none"）时按 convKey 预拉一次（走外带 PROBE_MODEL_LIST，不写 stdin）。
  useEffect(() => {
    if (!usable || phase !== "none") return;
    fetchModels();
  }, [usable, convKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const hint = modelCtlHint(phase);
  const popNote = modelPopNote(phase);
  const after = (r: unknown) => {
    const res = r as { ok?: boolean; text?: string; warn?: string } | null;
    if (res && res.ok === false) { setErr(res.text ?? "切换失败"); setWarn(null); }
    else { setErr(null); setWarn(res?.warn ?? null); }  // D148：不在代理列表的模型切换成功也带回 warn
  };
  const openModel = () => {
    setEffortOpen(false);
    setModelQuery("");
    setSourceFilter(null);
    setModelOpen(true);
    fetchModels();
  };
  useEffect(() => {
    void rpc.call<AuthStatusResult>("auth.status", {}).then(setAuthStatus, () => {});
    void rpc.call<AppPrefs>("app.prefs", {}).then(setPrefs, () => {});
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!openReq) return;
    if (openReq.which === "login") {
      setModelOpen(false);
      setEffortOpen(false);
      setLoginOpen(true);
      return;
    }
    if (!usable) return;
    if (openReq.which === "model") openModel();
    else {
      setModelOpen(false);
      setHoverEffort(null);
      if (models === null || (activeModel !== null && !(activeModel in (modelEfforts ?? {})) && effortsForModel !== activeModel)) {
        fetchModels();
      }
      setEffortOpen(true);
    }
  }, [openReq?.n]); // eslint-disable-line react-hooks/exhaustive-deps
  const modelTooltip =
    hint ??
    `${activeModel ?? "默认模型"}${activeSource ? ` · 来源：${activeSource.label}` : ""} · ${
      info?.modelSource === "session" || pendingModel !== null ? "本会话生效" : savedDefaultModel !== null ? "新会话默认" : "默认"
    }`;
  const effortTooltip = hint ?? "推理强度（本会话生效，Shift+Tab 循环）";
  const providerCount = authStatus?.providers.length ?? 0;
  return (
    <>
    <div className="model-controls" data-testid="model-controls" onKeyDown={(e) => {
      if (!e.nativeEvent.isTrusted) return;
      if (!usable || e.key !== "Tab" || !e.shiftKey) return;
      // 仅在推理强度按钮或分段选择器内拦截 Shift+Tab 循环，避免劫持其他按钮/弹层的反向焦点移动
      const tgt = e.target as HTMLElement | null;
      if (!tgt?.closest("[data-testid=effort-btn], [data-testid=effort-seg-bar]")) return;
      const next = nextEffort(supportedEfforts, currentEffort);
      if (next === null || next === currentEffort) return;
      e.preventDefault();
      void rpc.call("conv.command", { convKey, name: "thinking", arg: next }).then(after, (e2) => setErr(errText(e2)));
    }}>
      <div className="model-ctl model-ctl--model">
        <FloatingPopover
          open={modelOpen}
          onOpenChange={(next) => {
            if (!usable) {
              setModelOpen(false);
              return;
            }
            if (next) openModel();
            else setModelOpen(false);
          }}
          placement="top-start"
          initialFocus={searchRef}
          className="ui-popover model-pop model-pop--models"
          testId="model-pop"
          renderTrigger={(trigger) => (
            <button
              type="button"
              ref={trigger.ref}
              {...trigger.props}
              className="ui-btn ui-btn--ghost ui-btn--sm model-ctl-btn"
              data-testid="model-btn"
              data-source={pendingModel !== null ? "session" : savedDefaultModel !== null ? "default" : info?.modelSource ?? ""}
              aria-expanded={modelOpen}
              disabled={!usable}
              title={modelTooltip}
            >
              <Icon name="chip" size="sm" />
              {activeSource && activeSource.id !== "default" && (
                <span className="ui-badge model-ctl-source" data-testid="model-source-badge">
                  {activeSource.shortLabel}
                </span>
              )}
              <span className="model-ctl-label" data-testid="model-label">{activeModel ?? "—"}</span>
              {pendingModel !== null && <span className="ui-badge" data-testid="model-pending">待生效</span>}
              <span className="ui-chev ui-chev--down" data-open={modelOpen ? "1" : "0"}>
                <Icon name="chevron-down" size="sm" />
              </span>
            </button>
          )}
          renderContent={() => (
            <>
              <div className="model-pop-head">
                <div className="ui-popover-title">本会话模型</div>
                <button
                  type="button"
                  className="ui-btn ui-btn--ghost ui-btn--sm model-pop-login"
                  data-testid="model-pop-login"
                  onClick={(e) => {
                    if (!e.nativeEvent.isTrusted) return;
                    setModelOpen(false);
                    setLoginOpen(true);
                  }}
                >
                  <Icon name="globe" size="sm" />
                  服务商登录
                </button>
              </div>
              {popNote !== null && <div className="muted model-pop-note" data-testid="model-pop-note">{popNote}</div>}
              {info !== null && info.modelSource === "default" && info.model !== null && (
                <div className="muted model-pop-note" data-testid="model-pop-default">
                  默认：{info.model}
                  {activeSource && info.modelSource === "default" ? ` · ${activeSource.label}` : ""}
                </div>
              )}
              {prefs.defaultModel !== null && (
                <div className="muted model-pop-note" data-testid="model-pop-user-default">
                  已设新会话默认：{prefs.defaultModel}
                </div>
              )}
              {err !== null && (
                <div className="warn model-pop-err" data-testid="model-pop-err">
                  {err}
                  <button type="button" className="ui-btn ui-btn--ghost ui-btn--sm model-pop-retry" data-testid="model-pop-retry" onClick={(e) => { if (!e.nativeEvent.isTrusted) return; setErr(null); setModels(null); fetchModels(); }}>重试</button>
                </div>
              )}
              {warn !== null && <div className="warn model-pop-warn" data-testid="model-pop-warn">{warn}</div>}
              {models !== null && models.length > 0 && (
                <div className={`ui-search model-pop-search${modelQuery ? " ui-search--clearable" : ""}`}>
                  <Icon name="search" size="sm" />
                  <input
                    ref={searchRef}
                    className="ui-input"
                    data-testid="model-search"
                    placeholder="筛选模型或来源（↑↓ 选择，Enter 切换）…"
                    value={modelQuery}
                    onChange={(e) => setModelQuery(e.target.value)}
                    onKeyDown={(e) => {
                      if (!e.nativeEvent.isTrusted) return;
                      if (e.key === "ArrowDown") {
                        e.preventDefault();
                        setKbdNavActive(true);
                        setModelKbdIdx((i) => (flatModels.length === 0 ? 0 : Math.min(flatModels.length - 1, kbdNavActive ? i + 1 : 0)));
                      } else if (e.key === "ArrowUp") {
                        e.preventDefault();
                        setKbdNavActive(true);
                        setModelKbdIdx((i) => Math.max(0, i - 1));
                      } else if (e.key === "Escape" && modelQuery !== "") {
                        e.preventDefault();
                        e.stopPropagation();
                        setModelQuery("");
                      } else if (e.key === "Enter" && !e.nativeEvent.isComposing && e.keyCode !== 229) {
                        const picked = flatModels[modelKbdIdx];
                        if (!picked) return;
                        e.preventDefault();
                        setModelOpen(false);
                        void rpc.call("conv.command", { convKey, name: "model", arg: picked.id }).then(after, (e2) => setErr(errText(e2)));
                      }
                    }}
                  />
                  {modelQuery !== "" && (
                    <button
                      type="button"
                      className="ui-btn ui-btn--ghost ui-btn--sm ui-btn--icon ui-search-clear"
                      aria-label="清空模型筛选"
                      onClick={() => {
                        setModelQuery("");
                        searchRef.current?.focus();
                      }}
                    >
                      <Icon name="close" size="sm" />
                    </button>
                  )}
                </div>
              )}
              {modelGroups.length > 1 && (
                <div className="model-pop-filters" data-testid="model-source-filters" role="group" aria-label="按来源筛选">
                  <button
                    type="button"
                    className="ui-btn ui-btn--ghost ui-btn--sm model-pop-filter-btn"
                    data-testid="model-source-filter"
                    data-source-id="all"
                    aria-pressed={sourceFilter === null}
                    onClick={() => setSourceFilter(null)}
                  >
                    全部 ({models?.length ?? 0})
                  </button>
                  {modelGroups.map((grp) => (
                    <button
                      key={grp.id}
                      type="button"
                      className="ui-btn ui-btn--ghost ui-btn--sm model-pop-filter-btn"
                      data-testid="model-source-filter"
                      data-source-id={grp.id}
                      aria-pressed={sourceFilter === grp.id}
                      onClick={() => setSourceFilter((prev) => (prev === grp.id ? null : grp.id))}
                    >
                      {grp.shortLabel} ({grp.items.length})
                    </button>
                  ))}
                </div>
              )}
              {models === null && err === null && (
                <div className="ui-skeleton-stack model-pop-wait" aria-label="正在加载模型列表">
                  <div className="ui-skeleton ui-skeleton--lg" />
                  <div className="ui-skeleton ui-skeleton--md" />
                  <div className="ui-skeleton ui-skeleton--sm" />
                </div>
              )}
              {models !== null && (
                <div className="model-pop-list" ref={listRef} data-testid="model-pop-list">
                  {(() => {
                    let rowIdx = 0;
                    return visibleModelGroups.map((grp) => (
                      <div key={grp.id} className="model-pop-group" data-testid="model-group" data-source-id={grp.id}>
                        <div className="model-pop-group-head" data-testid="model-group-head">
                          <span className="model-pop-group-title">{grp.label}</span>
                          <span className="ui-badge">{grp.kindLabel}</span>
                          <span className="ui-count">{grp.items.length}</span>
                        </div>
                        {grp.items.map((item) => {
                          const m = item.id;
                          const idx = rowIdx++;
                          const mEffs = modelEfforts?.[m];
                          const titleParts = [m, `来源：${grp.label}`];
                          if (item.ownedBy && item.ownedBy.toLowerCase() !== grp.id.replace("default:", "")) {
                            titleParts.push(`上游：${item.ownedBy}`);
                          }
                          if (item.endpoint) titleParts.push(`地址：${item.endpoint}`);
                          return (
                            <button
                              key={m}
                              type="button"
                              className="ui-row model-pop-row"
                              aria-current={activeModel === m ? "true" : undefined}
                              data-kbd-active={idx === modelKbdIdx && kbdNavActive ? "1" : undefined}
                              data-testid="model-row"
                              data-model={m}
                              data-source-id={grp.id}
                              title={titleParts.join(" · ")}
                              onClick={(e) => {
                                if (!e.nativeEvent.isTrusted) return;
                                setModelOpen(false);
                                void rpc.call("conv.command", { convKey, name: "model", arg: m }).then(after, (e2) => setErr(errText(e2)));
                              }}
                            >
                              <span className="ui-row-title">{item.displayModel}</span>
                              {mEffs && mEffs.length > 0 && (
                                <span className="model-pop-meta" data-testid="model-row-efforts">{mEffs.length}档</span>
                              )}
                              <span className="model-pop-check">{activeModel === m ? <Icon name="check" size="sm" /> : null}</span>
                            </button>
                          );
                        })}
                      </div>
                    ));
                  })()}
                  {visibleModelGroups.length === 0 && models.length > 0 && (
                    <div className="muted model-pop-empty" data-testid="model-pop-empty">
                      <span>没有匹配的模型</span>
                      {modelQuery.trim() !== "" && (
                        <button
                          type="button"
                          className="ui-btn ui-btn--ghost ui-btn--sm"
                          onClick={(e) => {
                            if (!e.nativeEvent.isTrusted) return;
                            const val = modelQuery.trim();
                            setModelOpen(false);
                            void rpc.call("conv.command", { convKey, name: "model", arg: val }).then(after, (e2) => setErr(errText(e2)));
                          }}
                        >
                          直接切换为「{modelQuery.trim()}」
                        </button>
                      )}
                    </div>
                  )}
                </div>
              )}
              {(info?.modelSource === "session" || pendingModel !== null || savedDefaultModel !== null) && (
                <button
                  type="button"
                  className="ui-row model-pop-row model-pop-reset"
                  data-testid="model-reset"
                  onClick={(e) => {
                    if (!e.nativeEvent.isTrusted) return;
                    setModelOpen(false);
                    void rpc.call("conv.command", { convKey, name: "model", arg: "default" }).then(after, (e2) => setErr(errText(e2)));
                  }}
                >
                  <span className="ui-row-title">恢复默认模型</span>
                  <span className="model-pop-check" />
                </button>
              )}
              <div className="model-pop-custom">
                <input
                  className="ui-input model-pop-input"
                  data-testid="model-custom-input"
                  placeholder="自定义模型名（Enter 切换）…"
                  value={customModel}
                  onChange={(e) => setCustomModel(e.target.value)}
                  onKeyDown={(e) => {
                    if (!e.nativeEvent.isTrusted) return;
                    if (e.key !== "Enter" || e.nativeEvent.isComposing || e.keyCode === 229) return;
                    const val = customModel.trim();
                    if (val === "") return;
                    e.preventDefault();
                    setCustomModel(val);
                    setModelOpen(false);
                    void rpc.call("conv.command", { convKey, name: "model", arg: val }).then(after, (e2) => setErr(errText(e2)));
                  }}
                />
                <button
                  type="button"
                  className="ui-btn ui-btn--ghost ui-btn--sm"
                  data-testid="model-custom-submit"
                  disabled={customModel.trim() === ""}
                  onClick={(e) => {
                    if (!e.nativeEvent.isTrusted) return;
                    setModelOpen(false);
                    void rpc.call("conv.command", { convKey, name: "model", arg: customModel.trim() }).then(after, (e2) => setErr(errText(e2)));
                  }}
                >
                  切换
                </button>
              </div>
              {/* A2：显式「设为默认」——只有人点这里才持久化到 settings.json，对之后的新会话生效 */}
              <div className="model-pop-defaults">
                {activeModel && activeModel !== prefs.defaultModel && (
                  <button
                    type="button"
                    className="ui-row model-pop-row model-pop-reset"
                    data-testid="model-set-default"
                    onClick={(e) => {
                      if (!e.nativeEvent.isTrusted) return;
                      const chosen = activeModel;
                      void rpc.call<AppPrefs>("app.setDefaults", { model: chosen }).then(
                        (next) => {
                          setPrefs(next);
                          setPrefNotice(`已将 ${chosen} 设为新会话默认`);
                        },
                        (e2) => setErr(errText(e2)),
                      );
                    }}
                  >
                    <span className="ui-row-title">将当前模型设为新会话默认</span>
                  </button>
                )}
                {prefs.defaultModel !== null && (
                  <button
                    type="button"
                    className="ui-row model-pop-row model-pop-reset"
                    data-testid="model-clear-default"
                    onClick={(e) => {
                      if (!e.nativeEvent.isTrusted) return;
                      void rpc.call<AppPrefs>("app.setDefaults", { model: "" }).then(
                        (next) => {
                          setPrefs(next);
                          setPrefNotice("已清除自定义默认模型");
                        },
                        (e2) => setErr(errText(e2)),
                      );
                    }}
                  >
                    <span className="ui-row-title">清除自定义默认模型</span>
                  </button>
                )}
                {prefNotice && <div className="muted model-pop-note ui-confirm-enter">{prefNotice}</div>}
              </div>
            </>
          )}
        />
      </div>
      <div className="model-ctl">
        <FloatingPopover
          open={effortOpen}
          onOpenChange={(next) => {
            if (!usable) {
              setEffortOpen(false);
              return;
            }
            if (next) {
              setModelOpen(false);
              if (models === null || (activeModel !== null && !(activeModel in (modelEfforts ?? {})) && effortsForModel !== activeModel)) {
                fetchModels();
              }
              setHoverEffort(null);
              setEffortOpen(true);
            } else {
              setHoverEffort(null);
              setEffortOpen(false);
            }
          }}
          placement="top-start"
          className="ui-popover model-pop effort-pop"
          testId="effort-pop"
          renderTrigger={(trigger) => (
            <FloatingTooltip
              label={effortTooltip}
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
                  className="ui-btn ui-btn--ghost ui-btn--sm model-ctl-btn"
                  data-testid="effort-btn"
                  data-source={pendingEffort !== null ? "session" : savedDefaultEffort !== null ? "default" : info?.effortSource ?? ""}
                  aria-expanded={effortOpen}
                  disabled={!usable}
                >
                  <span className="effort-meter" data-testid="effort-meter" data-level={meterLevel} aria-hidden="true">
                    <span className="effort-meter-bar effort-meter-bar--1" data-active={meterLevel >= 1 ? "1" : "0"} />
                    <span className="effort-meter-bar effort-meter-bar--2" data-active={meterLevel >= 2 ? "1" : "0"} />
                    <span className="effort-meter-bar effort-meter-bar--3" data-active={meterLevel >= 3 ? "1" : "0"} />
                    <span className="effort-meter-bar effort-meter-bar--4" data-active={meterLevel >= 4 ? "1" : "0"} />
                    <span className="effort-meter-bar effort-meter-bar--5" data-active={meterLevel >= 5 ? "1" : "0"} />
                  </span>
                  <span className="model-ctl-label" data-testid="effort-label">{currentEffort !== null ? effortText(currentEffort) : info ? effortText(info.effort) : "默认"}</span>
                  {pendingEffort !== null && <span className="ui-badge" data-testid="effort-pending">待生效</span>}
                  <span className="ui-chev ui-chev--down" data-open={effortOpen ? "1" : "0"}>
                    <Icon name="chevron-down" size="sm" />
                  </span>
                </button>
              )}
            />
          )}
          renderContent={() => {
            const previewKey = hoverEffort ?? currentEffort;
            return (
              <>
                <div className="effort-pop-head">
                  <div className="ui-popover-title">推理强度</div>
                  <span className="muted effort-pop-cur">
                    <kbd className="ui-kbd">Shift+Tab</kbd> 循环
                  </span>
                </div>
                {popNote !== null && <div className="muted model-pop-note" data-testid="effort-pop-note">{popNote}</div>}
                {prefs.defaultEffort !== null && (
                  <div className="muted model-pop-note" data-testid="effort-pop-user-default">
                    已设新会话默认：{effortText(prefs.defaultEffort)}
                  </div>
                )}
                <SegmentedBar activeKey={currentEffort ?? "default"} className="effort-seg-bar" testId="effort-seg-bar" label="推理强度分段选择器">
                  {supportedEfforts.map((v) => {
                    const isCurrent = currentEffort === v;
                    return (
                      <button
                        key={v}
                        type="button"
                        data-testid="effort-seg-btn"
                        data-effort={v}
                        aria-pressed={isCurrent}
                        title={EFFORT_DESCRIPTIONS[v] ?? v}
                        onMouseEnter={() => setHoverEffort(v)}
                        onMouseLeave={() => setHoverEffort(null)}
                        onFocus={() => setHoverEffort(v)}
                        onBlur={() => setHoverEffort(null)}
                        onClick={(e) => {
                          if (!e.nativeEvent.isTrusted) return;
                          setEffortOpen(false);
                          void rpc.call("conv.command", { convKey, name: "thinking", arg: v }).then(after, (e2) => setErr(errText(e2)));
                        }}
                      >
                        {EFFORT_LABELS[v] ?? v}
                      </button>
                    );
                  })}
                </SegmentedBar>
                <div className="effort-pop-detail" data-testid="effort-desc">
                  <span>{previewKey ? `${EFFORT_LABELS[previewKey] ?? previewKey} · ${EFFORT_DESCRIPTIONS[previewKey] ?? previewKey}` : "默认 · 由模型自行决定"}</span>
                </div>
                <button
                  type="button"
                  className="ui-row model-pop-row model-pop-reset"
                  aria-current={currentEffort === null ? "true" : undefined}
                  data-testid="effort-reset"
                  onClick={(e) => {
                    if (!e.nativeEvent.isTrusted) return;
                    setEffortOpen(false);
                    void rpc.call("conv.command", { convKey, name: "thinking", arg: "default" }).then(after, (e2) => setErr(errText(e2)));
                  }}
                >
                  <span className="ui-row-title">跟随模型默认</span>
                  <span className="model-pop-check">{currentEffort === null ? <Icon name="check" size="sm" /> : null}</span>
                </button>
                <div className="model-pop-defaults">
                  {currentEffort !== null && currentEffort !== prefs.defaultEffort && (
                    <button
                      type="button"
                      className="ui-row model-pop-row model-pop-reset"
                      data-testid="effort-set-default"
                      onClick={(e) => {
                        if (!e.nativeEvent.isTrusted) return;
                        const chosen = currentEffort;
                        void rpc.call<AppPrefs>("app.setDefaults", { effort: chosen }).then(
                          (next) => {
                            setPrefs(next);
                            setPrefNotice(`已将「${effortText(chosen)}」设为新会话默认`);
                          },
                          (e2) => setErr(errText(e2)),
                        );
                      }}
                    >
                      <span className="ui-row-title">将当前强度设为新会话默认</span>
                    </button>
                  )}
                  {prefs.defaultEffort !== null && (
                    <button
                      type="button"
                      className="ui-row model-pop-row model-pop-reset"
                      data-testid="effort-clear-default"
                      onClick={(e) => {
                        if (!e.nativeEvent.isTrusted) return;
                        void rpc.call<AppPrefs>("app.setDefaults", { effort: "" }).then(
                          (next) => {
                            setPrefs(next);
                            setPrefNotice("已清除自定义默认推理强度");
                          },
                          (e2) => setErr(errText(e2)),
                        );
                      }}
                    >
                      <span className="ui-row-title">清除自定义默认推理强度</span>
                    </button>
                  )}
                  {prefNotice && <div className="muted model-pop-note ui-confirm-enter">{prefNotice}</div>}
                </div>
              </>
            );
          }}
        />
      </div>
      <div className="model-ctl model-ctl--login">
        <FloatingTooltip
          label={providerCount > 0 ? `模型服务商登录（已支持 ${providerCount} 家服务商）` : "模型服务商登录"}
          renderTrigger={(tip) => (
            <button
              type="button"
              ref={tip.ref}
              {...tip.props}
              className="ui-btn ui-btn--ghost ui-btn--sm model-ctl-btn login-ctl-btn"
              data-testid="login-btn"
              onClick={(e) => {
                if (!e.nativeEvent.isTrusted) return;
                setModelOpen(false);
                setEffortOpen(false);
                setLoginOpen(true);
              }}
            >
              <Icon name="globe" size="sm" />
              <span className="model-ctl-label">登录</span>
              {(authStatus?.connected_count ?? 0) > 0 && (
                <span className="ui-count" data-testid="login-connected-count" data-bump={loginCountBump ? "1" : undefined}>
                  {authStatus?.connected_count}
                </span>
              )}
            </button>
          )}
        />
      </div>
      {err !== null && !modelOpen && (
        <span className="warn model-ctl-err" data-testid="model-ctl-err" title={err}>
          <span>{err}</span>
          <button type="button" className="ui-btn ui-btn--ghost ui-btn--sm ui-btn--icon" aria-label="关闭错误提示" onClick={() => setErr(null)}>
            <Icon name="close" size="sm" />
          </button>
        </span>
      )}
      {err === null && warn !== null && !modelOpen && (
        <span className="warn model-ctl-err" data-testid="model-ctl-warn" title={warn}>
          <span>{warn}</span>
          <button type="button" className="ui-btn ui-btn--ghost ui-btn--sm ui-btn--icon" aria-label="关闭提醒" onClick={() => setWarn(null)}>
            <Icon name="close" size="sm" />
          </button>
        </span>
      )}
    </div>
    <LoginModal
      open={loginOpen}
      rpc={rpc}
      initialStatus={authStatus}
      preferredProvider={activeSource?.id && !activeSource.id.startsWith("default") ? activeSource.id : undefined}
      onClose={() => setLoginOpen(false)}
      onAuthChanged={(next) => {
        setAuthStatus(next);
        if (usable) fetchModels();
      }}
    />
    </>
  );
}
