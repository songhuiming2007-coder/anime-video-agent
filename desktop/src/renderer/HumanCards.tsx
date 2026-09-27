// 人审卡片（Spec 10 §2.4）：`CardFrame` + `StopPointCard`（原 DecisionBar.tsx 搬入，语义不变）+ `AnswerDock`。
// 全仓唯一出现 "approval.decide" 与（PR3 起）"conv.answer" 的文件。
//
// 「显式点击」纪律（Spec 10 §2.4 第 1 层，TG-4′）：答复类方法只许写在 JSX 事件属性（onClick）的值函数里，
// 处理器首句检查第一个形参的 nativeEvent.isTrusted、体内不许有循环与迭代方法、每个处理器至多一处此类调用。
// 只呈现确定性事实（停机点类型、对象创建时间、关联产物指纹、note、审片页时间先后），UI 自身零判定、不做 LLM 推荐（direction §5）。
// 失败时 stdout 与 stderr 尾部都原样显示（补丁段号在 stdout，红队 B3）；不提供任何重试按钮（RF-18）。
import { useEffect, useState, type ReactNode } from "react";
import type { ApprovalJson, StopType } from "../shared/contracts";
import { isStopType } from "../shared/contracts";
import type { OutFrame } from "../shared/convFrames";
import type { ConvKey, Health, RpcError, TreeEntry } from "../shared/protocol";
import type { ConvState } from "./convStore";
import { encodeMediaUrl } from "../shared/mediaUrl";
import type { RpcClient } from "./rpc";
import { HOST_LINK_LOST, RpcFailure, errText } from "./rpc";
import { Icon } from "./icons";
import { approvalsOf, type EpisodeState } from "./store";

const STOP_NAMES: Record<StopType, string> = { "02.5": "人审改稿", "03.5": "配音顺听", "05": "审时间码", "09": "人工发布" };

/** 决策条展示的对象：可 ack 的（PENDING，或 artifact 已对齐、待确认）。判定以 host 为准，这里只决定画不画卡片。 */
function actionable(a: ApprovalJson): boolean {
  return a.status === "pending" || (a.status === "approved" && a.resolved_by === "artifact" && a.confirmed_by === null);
}

/** 卡片外框（Spec 10 §2.8；类名按 Spec 14 §3.2）：Spec 12 的 09 定稿控件、Spec 11 的编辑器都挂在这里的 body 插槽里。 */
export function CardFrame({ className, testId, attrs, title, sub, children }: { className: string; testId: string; attrs: Record<string, string>; title: ReactNode; sub?: ReactNode; children?: ReactNode }) {
  return (
    <div className={`ui-card ${className}`} data-testid={testId} {...attrs}>
      <div className="ui-card-head">
        <span className="ui-card-title">{title}</span>
      </div>
      {sub ? <div className="ui-card-sub">{sub}</div> : null}
      {children}
    </div>
  );
}

/** 最近一次决定的结果挂在决策条上而不是卡片上：成功或陈旧后对象离开可操作态，卡片随 resnapshot 卸载 */
type Outcome = { approvalId: string; stop: StopType } & ({ kind: "ok"; text: string } | { kind: "err"; error: RpcError });

/** 待答区（用户裁决：输入框上方常驻；停机点对象 + Spec 9 的四种请求卡，PR3 起并入）。 */
export function AnswerDock({ ep, conv, health, rpc }: { ep?: EpisodeState; conv?: ConvState; health: Health | null; rpc: RpcClient }) {
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const [busy, setBusy] = useState(false);
  const items = ep ? approvalsOf(ep).filter((a) => actionable(a) && isStopType(a.type)) : [];
  const a = ep?.approvals;
  const requests = conv?.open ?? [];

  const run = async (obj: ApprovalJson, call: Promise<unknown>, okText: string) => {
    const head = { approvalId: obj.approval_id, stop: obj.type as StopType };
    setBusy(true);
    setOutcome(null);
    try {
      await call;
      setOutcome({ ...head, kind: "ok", text: okText });
    } catch (e) {
      const error: RpcError =
        e instanceof RpcFailure
          ? e.error.code === "E_UNREACHABLE" && e.error.message.startsWith(HOST_LINK_LOST)
            ? { ...e.error, message: "上次操作结果未知，已从磁盘重新读取（host 连接中断）" }
            : e.error
          : { code: "E_CORE", message: e instanceof Error ? e.message : String(e) };
      setOutcome({ ...head, kind: "err", error });
    } finally {
      setBusy(false);
    }
  };

  if (a?.state === "unsupported" || (a !== undefined && health && !health.capabilities.approvals)) {
    return (
      <div className="decision readonly" data-testid="decision-readonly">
        <Icon name="info" size="sm" />
        决策条只读：{health?.capabilities.approvalsDetail ?? "approval 能力缺席"}
      </div>
    );
  }
  if (items.length === 0 && requests.length === 0 && a?.state !== "error" && !outcome) return null;
  const disabled = a?.state !== "ok" || health?.reach !== "ok" || busy;
  return (
    <div className="decisions dock" data-testid="decisions">
      {a?.state === "error" && <div className="error">对象库读取失败：{a.message}（以下为上次成功读取的结果，按钮已禁用）</div>}
      {requests.map((req) => (
        <RequestCard key={String(req.request_id)} convKey={conv!.convKey} req={req} rpc={rpc} disabled={busy} />
      ))}
      {items.map((obj) => (
        <StopPointCard key={obj.approval_id} ep={ep!} obj={obj} rpc={rpc} disabled={disabled} run={run} />
      ))}
      {busy && <div className="muted">处理中…</div>}
      {outcome?.kind === "ok" && (
        <div className="decision-ok" data-testid="decision-ok" data-approval-id={outcome.approvalId}>
          <Icon name="check" size="sm" />
          停机点 {outcome.stop}：{outcome.text}（{outcome.approvalId}）
        </div>
      )}
      {outcome?.kind === "err" && (
        <div className="decision-err" data-testid="decision-err" data-code={outcome.error.code} data-approval-id={outcome.approvalId}>
          <div>
            停机点 {outcome.stop}（{outcome.approvalId}）{outcome.error.code}：{outcome.error.message}
          </div>
          {outcome.error.stdoutTail ? (
            <pre className="tail" data-testid="err-stdout">
              {outcome.error.stdoutTail}
            </pre>
          ) : null}
          {outcome.error.stderrTail ? (
            <pre className="tail stderr" data-testid="err-stderr">
              {outcome.error.stderrTail}
            </pre>
          ) : null}
        </div>
      )}
    </div>
  );
}

type Run = (obj: ApprovalJson, call: Promise<unknown>, okText: string) => Promise<void>;

export function StopPointCard({ ep, obj, rpc, disabled, run }: { ep: EpisodeState; obj: ApprovalJson; rpc: RpcClient; disabled: boolean; run: Run }) {
  const stop = obj.type as StopType;
  const [rejecting, setRejecting] = useState(false);
  const [target, setTarget] = useState("");
  const [problem, setProblem] = useState("");
  const [cover, setCover] = useState("");
  const [title, setTitle] = useState("");
  const aligned = obj.status === "approved";
  const off = disabled;

  // 人时：05/09 的决策卡呈现即计时（Spec 11 §2.4；与编辑器/顺听面板同一 per-stop 单区间机）
  useEffect(() => {
    if (stop !== "05" && stop !== "09") return;
    void rpc.call("time.surface", { epKey: ep.epKey, stop, visible: "true" }).catch(() => undefined);
    return () => {
      void rpc.call("time.surface", { epKey: ep.epKey, stop, visible: "false" }).catch(() => undefined);
    };
  }, [rpc, ep.epKey, stop]);

  const base = { epKey: ep.epKey, approvalId: obj.approval_id, stop };
  // 09 定稿（Spec 12 S3-R12）：两个输入；其余停机点不带这两个键（exact-keys 会拒）
  const needsFinalize = stop === "09" && !aligned;
  const ready = !needsFinalize || (cover.trim() !== "" && title.trim() !== "");
  const approveParams = needsFinalize ? { ...base, decision: "approve", cover, title } : { ...base, decision: "approve" };
  return (
    <CardFrame
      className="decision"
      testId="decision"
      attrs={{ "data-stop": stop, "data-approval-id": obj.approval_id }}
      title={`停机点 ${stop} ${STOP_NAMES[stop]}`}
      sub={`${aligned ? "解封物已在终端生成，待你确认" : "待审"} · 创建于 ${obj.created_at} · ${obj.approval_id}`}
    >
      <ul className="fingerprints">
        {obj.artifacts.map((f) => (
          <li key={f.path}>
            <code>{f.path}</code> {f.size < 0 ? "（钉住时缺失）" : `${f.size} 字节 · mtime_ns ${f.mtime_ns}`}
          </li>
        ))}
      </ul>
      {obj.note && <div className="muted">{obj.note}</div>}
      {stop === "05" && <ReviewPageAge epKey={ep.epKey} rpc={rpc} />}
      {needsFinalize && <FinalizeInputs epKey={ep.epKey} rpc={rpc} cover={cover} title={title} onCover={setCover} onTitle={setTitle} />}
      <div className="ui-card-actions">
        <button
          className="ui-btn"
          data-testid="approve"
          disabled={off || !ready}
          onClick={(e) => {
            if (!e.nativeEvent.isTrusted) return;
            void run(obj, rpc.call("approval.decide", approveParams), aligned ? "已确认批准" : "已批准");
          }}
        >
          批准
        </button>
        {stop === "03.5" && <span className="muted">结构化打点（manifest human_review）须在终端 <code>{`python -m pipeline.tts ${ep.epKey} --review`}</code> 完成</span>}
        {!aligned && (
          <button className="ui-btn" data-testid="reject-open" disabled={off} onClick={() => setRejecting((v) => !v)}>
            打回…
          </button>
        )}
      </div>
      {rejecting && !aligned && (
        <div className="reject-form" data-testid="reject-form">
          <label className="ui-field">
            哪段
            <input className="ui-input" data-testid="reject-target" value={target} onChange={(e) => setTarget(e.target.value)} placeholder="例如 04-clips.json s07 1:20" />
          </label>
          <label className="ui-field">
            问题
            <textarea className="ui-textarea" data-testid="reject-problem" value={problem} onChange={(e) => setProblem(e.target.value)} rows={3} />
          </label>
          <button
            className="ui-btn"
            data-testid="reject-submit"
            disabled={off || !target.trim() || !problem.trim()}
            onClick={(e) => {
              if (!e.nativeEvent.isTrusted) return;
              void run(obj, rpc.call("approval.decide", { ...base, decision: "reject", feedback: { target, problem } }), "已打回");
            }}
          >
            提交打回
          </button>
        </div>
      )}
    </CardFrame>
  );
}

/**
 * 09 定稿的两个输入（Spec 12 S8-R19 / Spec 10 S10-R1）：
 * 封面选项 = host 直读 `07-cover/` 下的现存文件清单（确定性事实，按文件名排序，**不排名**——判据 2/10），
 * 标题文本框；两者非空才可点批准（core 侧还会再校验路径与长度）。approval 调用点在批准处理器里，本组件不碰。
 */
export function FinalizeInputs({ epKey, rpc, cover, title, onCover, onTitle }: { epKey: string; rpc: RpcClient; cover: string; title: string; onCover: (v: string) => void; onTitle: (v: string) => void }) {
  const [covers, setCovers] = useState<TreeEntry[] | null>(null);
  useEffect(() => {
    let live = true;
    rpc.call<TreeEntry[]>("tree.list", { epKey, relDir: "07-cover" }).then(
      (es) => {
        if (live) setCovers(es.filter((e) => e.kind === "file" && /\.(png|jpe?g)$/i.test(e.name)).sort((a, b) => a.name.localeCompare(b.name, "en", { numeric: true })));
      },
      () => {
        if (live) setCovers([]);
      },
    );
    return () => {
      live = false;
    };
  }, [epKey, rpc]);
  return (
    <div className="finalize" data-testid="finalize-inputs">
      <div className="ui-field">
        <span>封面</span>
        {/* Spec 14 S12-R1（VS-3/S12-R1）：.cover-grid/.cover-opt，缩略图 + 压在图上的文件名走 --overlay-*。
            控件用**原生 radio 组**而不是 aria-pressed 按钮：一是闸门卡片里不得出现非 ui-btn 的 <button>（VS-12，
            红队 🟡-3 采纳的判据），二是「N 选一」本身就是 radio 语义（屏幕阅读器与方向键都是原生的）。 */}
        <div className="cover-grid" data-testid="cover-select" role="radiogroup" aria-label="封面">
          {(covers ?? []).map((c) => (
            <label key={c.rel} className="cover-opt" data-testid="cover-opt" data-rel={c.rel}>
              <input
                type="radio"
                name={`cover-${epKey}`}
                value={c.rel}
                checked={cover === c.rel}
                aria-label={c.rel}
                onChange={() => onCover(c.rel)}
              />
              <img src={encodeMediaUrl("episodes", `${epKey}/${c.rel}`)} alt="" />
              <span className="ui-badge">{c.rel.replace(/^07-cover\//, "")}</span>
            </label>
          ))}
        </div>
      </div>
      {covers?.length === 0 && <div className="muted">07-cover/ 下没有可选封面文件</div>}
      <label className="ui-field">
        标题
        <input className="ui-input" data-testid="title-input" value={title} onChange={(e) => onTitle(e.target.value)} placeholder="最终发布标题" />
      </label>
    </div>
  );
}

/** RF-20：审片页生成时间早于排片文件最后修改时间 → 标出这条确定性事实（不自动重建，RF-11）。 */
function ReviewPageAge({ epKey, rpc }: { epKey: string; rpc: RpcClient }) {
  const [older, setOlder] = useState(false);
  useEffect(() => {
    let live = true;
    rpc.call<TreeEntry[]>("tree.list", { epKey, relDir: "" }).then(
      (es) => {
        const page = es.find((e) => e.name === "04-review.html");
        const clips = es.find((e) => e.name === "04-clips.json");
        if (live) setOlder(!!page && !!clips && page.mtimeMs < clips.mtimeMs);
      },
      () => undefined,
    );
    return () => {
      live = false;
    };
  }, [epKey, rpc]);
  return older ? (
    <div className="notice" data-testid="review-page-older">
      审片页生成时间早于排片文件最后修改时间
    </div>
  ) : null;
}

// ---------------- Spec 9 的四种人审请求卡（Spec 10 §2.4；PR3） ----------------

const DECISION_LABEL: Record<string, string> = { approve: "批准", reject: "拒绝", continue: "继续", stop: "停止" };
const KIND_LABEL: Record<string, string> = { tool_call: "工具卡", fetch: "抓取卡", checkpoint: "检查点", memory_ack: "记忆确认" };

function fieldText(v: unknown): string {
  if (typeof v === "object" && v !== null) return JSON.stringify(v, null, 2);
  return v === null || v === undefined ? "" : String(v);
}

/**
 * 会话请求卡：按钮只取 `request.options`（H-4：UI 不增不减），没有批量、没有默认高亮。
 * browser 卡与抓取卡的「批准」由 host 请 main 弹原生确认框后才写 answer（§2.4 第 5 层），UI 不感知。
 */
export function RequestCard({ convKey, req, rpc, disabled }: { convKey: ConvKey; req: OutFrame; rpc: RpcClient; disabled: boolean }) {
  const requestId = String(req.request_id);
  const kind = String(req.kind);
  const options = Array.isArray(req.options) ? (req.options as unknown[]).map(String) : [];
  const fields = (req.fields ?? {}) as Record<string, unknown>;
  const [feedback, setFeedback] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const tool = kind === "tool_call" && typeof fields.tool === "string" ? fields.tool : null;
  const feedbackAllowed = kind === "tool_call" && req.feedback_allowed === true && options.includes("reject");
  const off = disabled || busy;
  return (
    <CardFrame
      className="decision request-card"
      testId="request-card"
      attrs={{ "data-kind": kind, "data-request-id": requestId }}
      title={`${KIND_LABEL[kind] ?? "请求卡"}${tool ? ` · ${tool}` : ""}${req.title ? ` · ${String(req.title)}` : ""}`}
      sub={<>{String(req.card_text)}</>}
    >
      <ul className="fingerprints ui-kv">
        {Object.entries(fields).map(([k, v]) => (
          <li key={k}>
            <span className="muted">{k}</span>
            <code>{fieldText(v)}</code>
          </li>
        ))}
      </ul>
      {feedbackAllowed && (
        <label className="ui-field">
          告诉它怎么改
          <textarea className="ui-textarea" data-testid="request-feedback" rows={2} value={feedback} onChange={(e) => setFeedback(e.target.value)} />
        </label>
      )}
      <div className="ui-card-actions">
        {options.map((decision) => (
          <button
            key={decision}
            className="ui-btn"
            data-testid={`request-answer-${decision}`}
            disabled={off}
            onClick={(e) => {
              if (!e.nativeEvent.isTrusted) return;
              const fb = feedbackAllowed && decision === "reject" && feedback.trim() !== "" ? feedback : null;
              setBusy(true);
              setError(null);
              void rpc
                .call("conv.answer", fb === null ? { convKey, requestId, decision } : { convKey, requestId, decision, feedback: fb })
                .then(() => setBusy(false))
                .catch((err) => {
                  setBusy(false);
                  setError(errText(err));
                });
            }}
          >
            {DECISION_LABEL[decision] ?? decision}
          </button>
        ))}
        {busy && <span className="muted">处理中…</span>}
      </div>
      {error && (
        <div className="decision-err" data-testid="request-error">
          {error}
        </div>
      )}
    </CardFrame>
  );
}
