// Spec 10 §2.6 / §2.8 / §4.3：会话头部。scope、LLM 状态、素材模式、确认记忆、结束会话、建期…（D45：继续会话移到侧栏会话列表）。
// 全仓唯一出现 "conv.command"、"conv.end"、"conv.resume" 的文件（TG-10：调用点只在原生元素的 onClick 里）。
// D91：输入框下的模型 / 推理按钮（ModelControls）也因此写在这里，由 App 塞进 Composer 的工具栏。
import { useEffect, useRef, useState } from "react";
import type { ConvEntry, ConvKey, ConvPhase, CreatedEpisode } from "../shared/protocol";
import { contextText, type ContextReading } from "../shared/convFold";
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

/** D41：读数的悬停说明——如实标口径，并给出现成的绕法（D62 起多一条：/compact 压缩） */
export const CONTEXT_READOUT_TITLE =
  "最近一次请求模型时的输入 token 数，由模型服务商返回，含系统提示、工具定义与全部历史，就是上下文窗口的实际占用；" +
  "斜杠后是当前模型的标称上下文窗口（config/agent/compact.json 的窗口表，表里没有该模型就不显示）。" +
  "服务商没给 token 数时退回显示消息正文的字数（带「约」）。" +
  "继续会话后、新一轮跑完前显示的是上次测得的值（带「上次」）。" +
  "嫌长可以在对话框发 /compact，把较早的对话压成一条摘要、保留近期原文；" +
  "或点侧栏本期下面的「＋ 新会话」从空白上下文开始：期的进度在产物里，不靠对话记忆。";

export function SessionHeader({
  rpc,
  convKey,
  phase,
  info,
  memoryAsk,
  isIdea,
  context,
  onCreated,
  onEnded,
}: {
  rpc: RpcClient;
  convKey: ConvKey;
  phase: ConvPhase;
  info: ReadyInfo;
  memoryAsk: boolean;
  isIdea: boolean;
  /** D41 / D56 / D64：最近一次回合结束时的上下文读数（`lastContextReading`）；继续会话先显示上次测得值；null = 新会话还没有回合结束过，不显示 */
  context: ContextReading | null;
  onCreated: (created: CreatedEpisode) => void;
  onEnded: () => void;
}) {
  const live = phase === "starting" || phase === "idle" || phase === "running" || phase === "ending";
  const running = phase === "running";
  const asset = info.scope === "asset";
  const contextReadout = context === null ? null : contextText(context);
  // N32：无活会话时 scope 命令无处可发——点击不发任何 RPC，只给一行可读提示（换期即清）
  const [needSession, setNeedSession] = useState(false);
  useEffect(() => setNeedSession(false), [convKey]);
  return (
    <div className="session-head" data-testid="session-head" data-conv={convKey} data-phase={phase}>
      <span className="ui-badge" data-testid="session-scope">
        {info.scope === "asset" ? "素材模式" : info.scope === "pipeline" ? "流水线模式" : info.scope === "creative" ? "创作模式" : info.scope === "project" ? "企划模式" : "—"}
      </span>
      {/* D64 余项②（2026-10-09 人选方案 B）：读数做成徽章、紧跟模式徽章，正文色——灰字夹在「LLM 已连接」后面不显眼 */}
      {contextReadout !== null && (
        <span className="ui-badge session-head-readout" data-testid="context-readout" title={CONTEXT_READOUT_TITLE}>
          {contextReadout}
        </span>
      )}
      {info.llm === "ok" && <span className="ui-dot ui-dot--ok" data-testid="llm-ok" title="LLM 已连接" />}
      {/* D39 C2：头部恒为一行，只留短标；原因全文、钥匙串命令、更早会话进对话流首行（SessionNotes，Spec 10 §2.3「流内一行」） */}
      {info.llm === "degraded" && (
        <span className="warn" data-testid="llm-degraded-mark">
          LLM 未就绪
        </span>
      )}
      <span className="spacer" />
      {/* D81：企划会话没有素材模式（企划级只做规划、跨卷审查、总装） */}
      {!isIdea && !convKey.startsWith("proj:") && (
        <button
          className="ui-btn ui-btn--ghost ui-btn--sm"
          data-testid="scope-toggle"
          aria-pressed={asset}
          disabled={running || phase === "ending"}
          onClick={(e) => {
            if (!e.nativeEvent.isTrusted) return;
            if (!live) {
              setNeedSession(true);
              return;
            }
            void rpc.call("conv.command", { convKey, name: "scope", arg: asset ? "auto" : "asset" }).catch(() => undefined);
          }}
        >
          素材模式
        </button>
      )}
      {!isIdea && needSession && !live && (
        <span className="muted" role="status" data-testid="scope-needs-session">
          没有进行中的会话：先发一条消息（或「继续上次会话」）再切素材模式
        </span>
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
      {/* D45 / D58：「继续上次会话」由侧栏会话子列表取代（期会话与选题会话同一套；点哪个进哪个） */}
      {running && <span className="ui-spinner" data-testid="session-running" />}
      {info.llm === null && <Icon name="info" size="sm" />}
    </div>
  );
}

/**
 * 对话流首行的会话说明（D39 C2；Spec 10 §2.3：`llm: "degraded"` 时流内一行降级说明、`other_sessions` 非空时流首一行）。
 * 文案与原会话头逐字相同；钥匙串命令仍在同一个文本节点里，可一次选中复制（N36）。
 */
export function SessionNotes({ info, keyProblem }: { info: ReadyInfo; keyProblem: string | null }) {
  if (info.llm !== "degraded" && keyProblem === null && info.otherSessions === 0) return null;
  return (
    <div className="session-notes" data-testid="session-notes">
      {info.llm === "degraded" && (
        <div className="warn" data-testid="llm-degraded">
          LLM 未就绪：{info.degradeReason ?? "未知原因"}
        </div>
      )}
      {keyProblem !== null && (
        <div className="muted" data-testid="key-problem">
          {keyProblem}
        </div>
      )}
      {info.otherSessions > 0 && (
        <div className="muted" data-testid="other-sessions">
          该期还有 {info.otherSessions} 个更早的会话{info.otherLatest ? `；最近一个 ${info.otherLatest}` : ""}
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
  none: "关", minimal: "极低", low: "低", medium: "中", high: "高", xhigh: "超高", auto: "自动",
};
export function effortText(effort: string | null): string {
  return effort === null ? "默认" : EFFORT_LABELS[effort] ?? effort;
}

/** D156：每档的描述（照 Pi `LEVEL_DESCRIPTIONS`，把英文档名翻成 ava 语境的读法）。 */
export const EFFORT_DESCRIPTIONS: Readonly<Record<string, string>> = {
  none: "不启用推理",
  minimal: "极简推理（~1k token）",
  low: "轻度推理（~2k token）",
  medium: "中等推理（~8k token）",
  high: "深度推理（~16k token）",
  xhigh: "超高推理（~32k token）",
  max: "最大推理",
  auto: "由模型自行决定",
};

/** 档位循环次序（Shift+Tab 按此走；与 model_switch.py 的 EFFORT_ORDER 同序）。 */
export const EFFORT_ORDER: readonly string[] = ["none", "minimal", "low", "medium", "high", "xhigh", "auto", "max"];

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

function usePopover(): [boolean, (v: boolean) => void, React.RefObject<HTMLDivElement | null>] {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!open) return;
    const away = (ev: MouseEvent) => {
      if (ref.current && !ref.current.contains(ev.target as Node)) setOpen(false);
    };
    const esc = (ev: KeyboardEvent) => {
      if (ev.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", away);
      document.removeEventListener("keydown", esc);
    };
  }, [open]);
  return [open, setOpen, ref];
}

/**
 * 输入框工具栏上的两个按钮：模型（点开列出代理的模型）、推理强度（点开选档位）。只改本会话。
 * 会话还没起（还没发过消息）时显示「—」并提示：先发一条消息，或直接发 /model。回合在跑时置灰。
 */
/** 输入框发裸 `/model`、`/thinking` 时由 App 递进来：n 每次加一，展开对应下拉（只展开、不切换——切换仍要点一行） */
export type ModelOpenReq = { which: "model" | "effort"; n: number };

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

export function ModelControls({ rpc, convKey, phase, info, openReq = null, pending = null }: { rpc: RpcClient; convKey: ConvKey; phase: ConvPhase; info: ModelInfo | null; openReq?: ModelOpenReq | null; pending?: { model?: string; effort?: string } | null }) {
  // D149：running 也可用（core 排队到回合收尾执行，下个回合生效）；D147：none 也可用（存 pending，首条消息带出）。
  const usable = modelCtlUsable(phase);
  const pendingModel = pending?.model ?? null;
  const pendingEffort = pending?.effort ?? null;
  const [modelOpen, setModelOpen, modelRef] = usePopover();
  const [effortOpen, setEffortOpen, effortRef] = usePopover();
  const [models, setModels] = useState<string[] | null>(null);
  const [efforts, setEfforts] = useState<string[] | null>(null); // D154：该模型支持档（core 回子集）；null = 未拉到/不支持
  const [err, setErr] = useState<string | null>(null);
  const [customModel, setCustomModel] = useState("");
  const [warn, setWarn] = useState<string | null>(null);
  const [effortQuery, setEffortQuery] = useState(""); // D156：选择器搜索框
  // D156：Shift+Tab 循环要拿到支持集——usable 时主动拉一次（不等点按钮）。模型变了（info.model）也重拉。
  const infoModel = info?.model ?? null;
  useEffect(() => {
    setModels(null);
    setEfforts(null);
    setErr(null);
    setWarn(null);
    setCustomModel("");
    setModelOpen(false);
    setEffortOpen(false);
  }, [convKey]); // eslint-disable-line react-hooks/exhaustive-deps
  // 取列表走纯读的 conv.models（不在点击纪律内：它不改任何设置）。
  // D153：会话未起（none）时也照发——host 无 s.proc 时降级经 PROBE_MODEL_LIST 直连代理取列表，
  // 进程在时透传 core；代理不可达回 ok:false，popover 显示失败行 + 「重试」（D151）。
  const fetchModels = () => {
    void rpc.call<{ ok?: boolean; text?: string; items?: string[]; efforts?: string[] }>("conv.models", { convKey }).then(
      (res) => {
        setModels(res.ok ? res.items ?? [] : []);
        setEfforts(res.ok ? res.efforts ?? null : null);
        setErr(res.ok ? null : res.text ?? "取模型列表失败");
      },
      (e2) => setErr(String((e2 as Error).message ?? e2)),
    );
  };
  // D156：控件可用就预拉——Shift+Tab 循环与 effort 选择器都吃 efforts，不等点开再发。
  useEffect(() => {
    if (!usable) return;
    fetchModels();
  }, [usable, convKey, infoModel]); // eslint-disable-line react-hooks/exhaustive-deps

  const hint = modelCtlHint(phase);
  const popNote = modelPopNote(phase);
  const after = (r: unknown) => {
    const res = r as { ok?: boolean; text?: string; warn?: string } | null;
    if (res && res.ok === false) { setErr(res.text ?? "切换失败"); setWarn(null); }
    else { setErr(null); setWarn(res?.warn ?? null); }  // D148：不在代理列表的模型切换成功也带回 warn
  };
  // D156：Shift+Tab 循环推理强度（Pi 的行为）。绑 model-controls 容器 onKeyDown（TG-10：conv.command 只能挂在 JSX 事件属性上）。
  // 焦点在控件内（Tab 到按钮 / popover 搜索框）时 Shift+Tab 循环；下一档从实测支持集 +1。
  const currentEffort = pendingEffort ?? info?.effort ?? null;
  const openModel = () => {
    setEffortOpen(false);
    setModelOpen(true);
    fetchModels();
  };
  useEffect(() => {
    if (!openReq || !usable) return;
    if (openReq.which === "model") openModel();
    else {
      setModelOpen(false);
      setEffortQuery("");
      setEffortOpen(true);
    }
  }, [openReq?.n]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <div className="model-controls" data-testid="model-controls" onKeyDown={(e) => {
      if (!e.nativeEvent.isTrusted) return;
      if (e.key !== "Tab" || !e.shiftKey) return;
      const next = nextEffort(efforts, currentEffort);
      if (next === null || next === currentEffort) return;
      e.preventDefault();
      void rpc.call("conv.command", { convKey, name: "thinking", arg: next }).then(after, (e2) => setErr(String((e2 as Error).message ?? e2)));
    }}>
      <div className="model-ctl model-ctl--model" ref={modelRef}>
        <button
          className="ui-btn ui-btn--ghost ui-btn--sm model-ctl-btn"
          data-testid="model-btn"
          data-source={pendingModel !== null ? "session" : info?.modelSource ?? ""}
          aria-expanded={modelOpen}
          disabled={!usable}
          title={hint ?? `${info?.model ?? ""} · ${info?.modelSource === "session" ? "本会话设置的模型（新会话仍用默认）" : "默认模型（config/agent.local.json）"}`}
          onClick={(e) => {
            if (!e.nativeEvent.isTrusted) return;
            if (modelOpen) setModelOpen(false);
            else openModel();
          }}
        >
          <Icon name="chip" size="sm" />
          <span className="model-ctl-label" data-testid="model-label">{pendingModel ?? info?.model ?? "—"}</span>
          {pendingModel !== null && <span className="model-ctl-pending" data-testid="model-pending">待生效</span>}
          <Icon name="chevron-down" size="sm" />
        </button>
        {modelOpen && (
          <div className="ui-popover model-pop" data-testid="model-pop">
            <div className="ui-popover-title">本会话模型</div>
            {popNote !== null && <div className="muted model-pop-note" data-testid="model-pop-note">{popNote}</div>}
            {info !== null && info.modelSource === "default" && info.model !== null && <div className="muted model-pop-note" data-testid="model-pop-default">默认：{info.model}</div>}
            {err !== null && (
              <div className="warn model-pop-err" data-testid="model-pop-err">
                {err}
                <button className="ui-btn ui-btn--ghost ui-btn--sm model-pop-retry" data-testid="model-pop-retry" onClick={(e) => { if (!e.nativeEvent.isTrusted) return; setErr(null); setModels(null); fetchModels(); }}>重试</button>
              </div>
            )}
            {warn !== null && <div className="warn model-pop-warn" data-testid="model-pop-warn">{warn}</div>}
            {models === null && err === null && <div className="muted model-pop-wait">…</div>}
            {models?.map((m) => (
              <button
                key={m}
                className="ui-row model-pop-row"
                aria-current={(pendingModel ?? info?.model) === m ? "true" : undefined}
                data-testid="model-row"
                data-model={m}
                onClick={(e) => {
                  if (!e.nativeEvent.isTrusted) return;
                  setModelOpen(false);
                  void rpc.call("conv.command", { convKey, name: "model", arg: m }).then(after, (e2) => setErr(String((e2 as Error).message ?? e2)));
                }}
              >
                <span className="ui-row-title">{m}</span>
                <span className="model-pop-check">{(pendingModel ?? info?.model) === m ? <Icon name="check" size="sm" /> : null}</span>
              </button>
            ))}
            {/* D148：列表里没有的模型手输——不校验、照样走 conv.command，core 不在列表时回 warn 不拦。
                输入框只收值（onKeyDown 不能发 conv.command，TG-10 纪律）；点「切换」按钮发出。 */}
            {info?.modelSource === "session" && (
              <button
                className="ui-row model-pop-row model-pop-reset"
                data-testid="model-reset"
                onClick={(e) => {
                  if (!e.nativeEvent.isTrusted) return;
                  setModelOpen(false);
                  void rpc.call("conv.command", { convKey, name: "model", arg: "default" }).then(after, (e2) => setErr(String((e2 as Error).message ?? e2)));
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
                placeholder="自定义模型名…"
                value={customModel}
                onChange={(e) => setCustomModel(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.nativeEvent.isComposing && e.keyCode !== 229) setCustomModel((v) => v.trim());
                }}
              />
              <button
                className="ui-btn ui-btn--ghost ui-btn--sm"
                data-testid="model-custom-submit"
                disabled={customModel.trim() === ""}
                onClick={(e) => {
                  if (!e.nativeEvent.isTrusted) return;
                  setModelOpen(false);
                  void rpc.call("conv.command", { convKey, name: "model", arg: customModel.trim() }).then(after, (e2) => setErr(String((e2 as Error).message ?? e2)));
                }}
              >
                切换
              </button>
            </div>
          </div>
        )}
      </div>
      <div className="model-ctl" ref={effortRef}>
        <button
          className="ui-btn ui-btn--ghost ui-btn--sm model-ctl-btn"
          data-testid="effort-btn"
          data-source={pendingEffort !== null ? "session" : info?.effortSource ?? ""}
          aria-expanded={effortOpen}
          disabled={!usable}
          title={hint ?? "推理强度（只影响本会话；「默认」= 不发推理参数，由模型自己定）"}
          onClick={(e) => {
            if (!e.nativeEvent.isTrusted) return;
            setModelOpen(false);
            // D154：effort 下拉也要该模型支持档——只在还没拿过时拉一次
            if (efforts === null && !effortOpen) fetchModels();
            setEffortOpen(!effortOpen);
          }}
        >
          <Icon name="gauge" size="sm" />
          <span data-testid="effort-label">{pendingEffort !== null ? effortText(pendingEffort) : info ? effortText(info.effort) : "—"}</span>
          {pendingEffort !== null && <span className="model-ctl-pending" data-testid="effort-pending">待生效</span>}
          <Icon name="chevron-down" size="sm" />
        </button>
        {effortOpen && (
          <div className="ui-popover model-pop effort-pop" data-testid="effort-pop">
            <div className="ui-popover-title">推理强度</div>
            {popNote !== null && <div className="muted model-pop-note" data-testid="effort-pop-note">{popNote}</div>}
            <div className="muted model-pop-note" data-testid="effort-pop-cycle">Shift+Tab 在会话内循环档位</div>
            {/* D156：独立档位选择器——搜索框过滤（Pi 的 Input+fuzzyFilter）；
                D155：没实测数据的模型不列全档骗人——只有实测支持集才列出各档，否则只剩底部「默认」 */}
            <input
              className="ui-input effort-pop-search"
              data-testid="effort-search"
              placeholder="过滤档位…"
              value={effortQuery}
              onChange={(e) => setEffortQuery(e.target.value)}
            />
            {(efforts ?? [])
              .filter((v) => {
                const q = effortQuery.trim().toLowerCase();
                if (q === "") return true;
                return (
                  v.toLowerCase().includes(q) ||
                  (EFFORT_LABELS[v] ?? "").toLowerCase().includes(q) ||
                  (EFFORT_DESCRIPTIONS[v] ?? "").toLowerCase().includes(q)
                );
              })
              .map((v) => {
                const isCurrent = (pendingEffort ?? info?.effort) === v;
                const isDefault = info !== null && info.effortSource === "default" && info.effort === v;
                return (
                  <button
                    key={v}
                    className="ui-row model-pop-row effort-pop-row"
                    aria-current={isCurrent ? "true" : undefined}
                    data-testid="effort-row"
                    data-effort={v}
                    onClick={(e) => {
                      if (!e.nativeEvent.isTrusted) return;
                      setEffortOpen(false);
                      void rpc.call("conv.command", { convKey, name: "thinking", arg: v }).then(after, (e2) => setErr(String((e2 as Error).message ?? e2)));
                    }}
                  >
                    <span className="ui-row-title">{EFFORT_LABELS[v] ?? v}</span>
                    <span className="effort-pop-desc" data-testid="effort-desc">
                      {EFFORT_DESCRIPTIONS[v] ?? v}
                      {isDefault ? " · 默认" : ""}
                    </span>
                    <span className="model-pop-check">{isCurrent ? <Icon name="check" size="sm" /> : null}</span>
                  </button>
                );
              })}
            <button
              className="ui-row model-pop-row model-pop-reset"
              aria-current={info !== null && info.effort === null ? "true" : undefined}
              data-testid="effort-reset"
              onClick={(e) => {
                if (!e.nativeEvent.isTrusted) return;
                setEffortOpen(false);
                void rpc.call("conv.command", { convKey, name: "thinking", arg: "default" }).then(after, (e2) => setErr(String((e2 as Error).message ?? e2)));
              }}
            >
              <span className="ui-row-title">默认（不发推理参数）</span>
              <span className="effort-pop-desc">{info !== null && info.effort === null ? "当前生效 · 默认" : "由模型自行决定"}</span>
              <span className="model-pop-check">{info !== null && info.effort === null ? <Icon name="check" size="sm" /> : null}</span>
            </button>
          </div>
        )}
      </div>
      {err !== null && !modelOpen && (
        <span className="warn model-ctl-err" data-testid="model-ctl-err" title={err}>
          {err}
        </span>
      )}
    </div>
  );
}
