// 人审卡片（Spec 10 §2.4）：`CardFrame` + `StopPointCard`（原 DecisionBar.tsx 搬入，语义不变）+ `AnswerDock`。
// 全仓唯一出现 "approval.decide" 与（PR3 起）"conv.answer" 的文件。
//
// 「显式点击」纪律（Spec 10 §2.4 第 1 层，TG-4′）：答复类方法只许写在 JSX 事件属性（onClick）的值函数里，
// 处理器首句检查第一个形参的 nativeEvent.isTrusted、体内不许有循环与迭代方法、每个处理器至多一处此类调用。
// 只呈现确定性事实（停机点类型、对象创建时间、关联产物指纹、note、审片页时间先后），UI 自身零判定、不做 LLM 推荐（direction §5）。
// 失败时 stdout 与 stderr 尾部都原样显示（补丁段号在 stdout，红队 B3）；不提供任何重试按钮（RF-18）。
import { Fragment, useEffect, useRef, useState, type ReactNode } from "react";
import type { ApprovalJson, StopType } from "../shared/contracts";
import { isStopType } from "../shared/contracts";
import type { OutFrame } from "../shared/convFrames";
import { encodeMediaUrl } from "../shared/mediaUrl";
import type { ConvKey, Health, RpcError, TreeEntry } from "../shared/protocol";
import { toolCategory } from "../shared/toolView";
import { CopyButton } from "./copy";
import type { ConvState } from "./convStore";
import { Icon } from "./icons";
import { Collapsible, Disclosure } from "./motion";
import { ReviewRating } from "./ReviewRating";
import type { RpcClient } from "./rpc";
import { HOST_LINK_LOST, RpcFailure, errText } from "./rpc";
import { approvalsOf, type EpisodeState } from "./store";
import { Badge } from "./ui";

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

const STOP_NAMES: Record<StopType, string> = { "02.5": "人审改稿", "03.5": "配音顺听", "05": "审时间码", "09": "人工发布" };

const REJECT_PLACEHOLDER: Record<StopType, string> = {
  "02.5": "例如 第 3 段开头",
  "03.5": "例如 第 2 段读音",
  "05": "例如 第 7 段 1:20",
  "09": "例如 封面或标题",
};

const TOOL_TITLES: Readonly<Record<string, string>> = {
  bash: "执行终端命令",
  write_episode_file: "写入本期文件",
  write_project_file: "写入企划文件",
  write_note: "写入番剧笔记",
  write_memory: "写入项目记忆",
  run_pipeline: "运行流水线",
  acquire_propose: "素材获取方案",
  cover_edit: "编辑封面",
  fact_add: "写入事实库",
  browser: "浏览器操作",
  chrome: "浏览器操作",
};

/** 决策条展示的对象：可 ack 的（PENDING，或 artifact 已对齐、待确认）。判定以 host 为准，这里只决定画不画卡片。 */
function actionable(a: ApprovalJson): boolean {
  return a.status === "pending" || (a.status === "approved" && a.resolved_by === "artifact" && a.confirmed_by === null);
}

/** 卡片外框（Spec 10 §2.8；类名按 Spec 14 §3.2）：Spec 12 的 09 定稿控件、Spec 11 的编辑器都挂在这里的 body 插槽里。 */
export function CardFrame({ className, testId, attrs, title, sub, children }: { className: string; testId: string; attrs: Record<string, string>; title: ReactNode; sub?: ReactNode; children?: ReactNode }) {
  return (
    <div className={`ui-card ui-card-enter ${className}`} data-testid={testId} {...attrs}>
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
export function AnswerDock({ ep, health, rpc, treeBump = 0 }: { ep?: EpisodeState; health: Health | null; rpc: RpcClient; treeBump?: number }) {
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const [busy, setBusy] = useState(false);
  const items = ep ? approvalsOf(ep).filter((a) => actionable(a) && isStopType(a.type)) : [];
  const a = ep?.approvals;

  useEffect(() => {
    setOutcome(null);
  }, [ep?.epKey]);

  useEffect(() => {
    if (outcome?.kind !== "ok") return;
    const t = setTimeout(() => setOutcome((cur) => (cur === outcome ? null : cur)), 5000);
    return () => clearTimeout(t);
  }, [outcome]);

  const run = async (obj: ApprovalJson, call: Promise<unknown>, okText: string) => {
    const head = { approvalId: obj.approval_id, stop: obj.type as StopType };
    setBusy(true);
    setOutcome(null);
    try {
      const r = (await call) as { cancelled?: true } | undefined;
      // D89：补丁段确认框里点了取消——什么都没写，卡片原样留着
      setOutcome({ ...head, kind: "ok", text: r?.cancelled ? "已取消（补丁段未确认，未批准）" : okText });
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
  if (items.length === 0 && a?.state !== "error" && !outcome) return null;
  const disabled = a?.state !== "ok" || health?.reach !== "ok" || busy;
  const disabledReason =
    busy
      ? "正在处理上一条决策…"
      : health?.reach !== "ok"
        ? "数据目录不可达，暂不可操作"
        : a?.state !== "ok"
          ? "审批状态未同步，暂不可操作"
          : undefined;
  return (
    <div className="decisions dock" data-testid="decisions">
      {a?.state === "error" && <div className="error">审批状态读取失败：{a.message}（以下为上次成功读取的结果，按钮已禁用）</div>}
      {items.map((obj) => (
        <StopPointCard key={obj.approval_id} ep={ep!} obj={obj} rpc={rpc} disabled={disabled} disabledReason={disabledReason} treeBump={treeBump} run={run} />
      ))}
      {busy && (
        <div className="notice">
          <span className="ui-spinner" />
          <span>处理中…</span>
        </div>
      )}
      {outcome?.kind === "ok" && (
        <div className="decision-ok ui-confirm-enter" data-testid="decision-ok" data-approval-id={outcome.approvalId}>
          <Icon name="check" size="sm" />
          停机点 {outcome.stop}：{outcome.text}（{outcome.approvalId}）
        </div>
      )}
      {outcome?.kind === "err" && (
        <div className="decision-err ui-error-shake" data-testid="decision-err" data-code={outcome.error.code} data-approval-id={outcome.approvalId}>
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

export function StopPointCard({
  ep,
  obj,
  rpc,
  disabled,
  disabledReason,
  treeBump = 0,
  run,
}: {
  ep: EpisodeState;
  obj: ApprovalJson;
  rpc: RpcClient;
  disabled: boolean;
  disabledReason?: string;
  treeBump?: number;
  run: Run;
}) {
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
  const missingArtifacts = obj.artifacts.filter((f) => f.size < 0);
  const approveTitle =
    disabledReason ??
    (!ready ? "请先选定封面并填写发布标题" : undefined);
  const rejectSubmitTitle =
    disabledReason ??
    (!target.trim() || !problem.trim() ? "请填写定位与具体问题后再提交" : undefined);
  return (
    <CardFrame
      className="decision"
      testId="decision"
      attrs={{ "data-stop": stop, "data-approval-id": obj.approval_id, title: `${obj.created_at} · ${obj.approval_id}` }}
      title={
        <>
          {stop} {STOP_NAMES[stop]}
          <Badge tone={aligned ? "wait" : undefined}>{aligned ? "待确认" : "停机"}</Badge>
        </>
      }
      sub={aligned ? "已在终端生成批准文件，待你确认" : undefined}
    >
      {missingArtifacts.map((f) => (
        <div className="warn" key={f.path}>
          <code>{f.path}</code>（当时缺失）
        </div>
      ))}
      {(obj.artifacts.length > 0 || obj.note) && (
        <Collapsible
          className="card-more"
          summary={obj.artifacts.length > 0 ? `产物详情（${obj.artifacts.length} 项）` : "备注说明"}
        >
          {obj.artifacts.length > 0 && (
            <dl className="ui-kv">
              {obj.artifacts.map((f) => (
                <Fragment key={f.path}>
                  <dt>产物</dt>
                  <dd>
                    <code>{f.path}</code>
                    {f.size < 0 ? " （当时缺失）" : ` · ${formatBytes(f.size)}`}
                  </dd>
                </Fragment>
              ))}
            </dl>
          )}
          {obj.note && <div className="muted">{obj.note}</div>}
        </Collapsible>
      )}
      {stop === "05" && <ReviewPageAge epKey={ep.epKey} rpc={rpc} eventsCount={ep.events.length} />}
      {needsFinalize && <FinalizeInputs epKey={ep.epKey} rpc={rpc} treeBump={treeBump} cover={cover} title={title} onCover={setCover} onTitle={setTitle} />}
      {stop === "03.5" && <ReviewRating epKey={ep.epKey} rpc={rpc} disabled={off} />}
      <div className="ui-card-actions">
        <button
          className="ui-btn"
          data-testid="approve"
          disabled={off || !ready}
          title={approveTitle}
          onClick={(e) => {
            if (!e.nativeEvent.isTrusted) return;
            void run(obj, rpc.call("approval.decide", approveParams), aligned ? "已确认批准" : "已批准");
          }}
        >
          {aligned ? "确认批准" : stop === "03.5" ? "顺听无误，进入排片" : "批准"}
        </button>
        {!aligned && stop !== "03.5" && (
          <button
            className="ui-btn"
            data-testid="reject-open"
            aria-expanded={rejecting}
            disabled={off}
            title={disabledReason}
            onClick={() => setRejecting((v) => !v)}
          >
            {rejecting ? "收起打回" : "打回…"}
          </button>
        )}
      </div>
      <Disclosure open={rejecting && !aligned && stop !== "03.5"}>
        <div className="reject-form" data-testid="reject-form">
          <label className="ui-field">
            哪段
            <input className="ui-input" data-testid="reject-target" value={target} onChange={(e) => setTarget(e.target.value)} placeholder={REJECT_PLACEHOLDER[stop]} />
          </label>
          <label className="ui-field">
            问题
            <textarea className="ui-textarea" data-testid="reject-problem" value={problem} onChange={(e) => setProblem(e.target.value)} rows={3} placeholder="描述要修改的具体问题" />
          </label>
          <div className="ui-card-actions">
            <button
              className="ui-btn"
              data-testid="reject-submit"
              disabled={off || !target.trim() || !problem.trim()}
              title={rejectSubmitTitle}
              onClick={(e) => {
                if (!e.nativeEvent.isTrusted) return;
                void run(obj, rpc.call("approval.decide", { ...base, decision: "reject", feedback: { target, problem } }), "已打回");
              }}
            >
              提交打回
            </button>
          </div>
        </div>
      </Disclosure>
    </CardFrame>
  );
}

/**
 * 09 定稿的两个输入（Spec 12 S8-R19 / Spec 10 S10-R1）：
 * 封面选项 = host 直读 `07-cover/` 与 `07-cover/import/` 下的现存文件清单（确定性事实，不排名——判据 2/10），
 * 标题文本框；两者非空才可点批准（core 侧还会再校验路径与长度）。approval 调用点在批准处理器里，本组件不碰。
 */
export function FinalizeInputs({
  epKey,
  rpc,
  treeBump = 0,
  cover,
  title,
  onCover,
  onTitle,
}: {
  epKey: string;
  rpc: RpcClient;
  treeBump?: number;
  cover: string;
  title: string;
  onCover: (v: string) => void;
  onTitle: (v: string) => void;
}) {
  const [covers, setCovers] = useState<TreeEntry[] | null>(null);
  useEffect(() => {
    let live = true;
    const isImg = (e: TreeEntry) => e.kind === "file" && /\.(png|jpe?g)$/i.test(e.name);
    const byName = (a: TreeEntry, b: TreeEntry) => a.name.localeCompare(b.name, "en", { numeric: true });
    Promise.all([
      rpc.call<TreeEntry[]>("tree.list", { epKey, relDir: "07-cover" }).catch(() => [] as TreeEntry[]),
      rpc.call<TreeEntry[]>("tree.list", { epKey, relDir: "07-cover/import" }).catch(() => [] as TreeEntry[]),
    ]).then(([root, imported]) => {
      if (!live) return;
      const topFiles = root.filter(isImg).sort(byName);
      const importedFiles = imported.filter(isImg).sort(byName);
      setCovers([...topFiles, ...importedFiles]);
    });
    return () => {
      live = false;
    };
  }, [epKey, rpc, treeBump]);
  return (
    <div className="finalize" data-testid="finalize-inputs">
      <div className="ui-field">
        <span>封面</span>
        {covers === null ? (
          <div className="ui-skeleton-stack" aria-label="读取可选封面">
            <div className="ui-skeleton ui-skeleton--lg" />
            <div className="ui-skeleton ui-skeleton--md" />
          </div>
        ) : (
          <div className="cover-grid" data-testid="cover-select" role="radiogroup" aria-label="封面">
            {covers.map((c) => (
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
        )}
      </div>
      {covers?.length === 0 && <div className="muted">07-cover/ 下没有可选封面文件，可在右侧预览栏点击「导入封面」添加</div>}
      <label className="ui-field">
        标题
        <input className="ui-input" data-testid="title-input" value={title} onChange={(e) => onTitle(e.target.value)} placeholder="最终发布标题" />
      </label>
    </div>
  );
}

/** RF-20：审片页生成时间早于排片文件最后修改时间 → 标出这条确定性事实（不自动重建，RF-11）。 */
function ReviewPageAge({ epKey, rpc, eventsCount }: { epKey: string; rpc: RpcClient; eventsCount: number }) {
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
  }, [epKey, rpc, eventsCount]);
  return older ? (
    <div className="notice notice--warn" data-testid="review-page-older">
      <Icon name="alert" size="sm" />
      审片页生成时间早于排片文件最后修改时间
    </div>
  ) : null;
}

// ---------------- Spec 9 的四种人审请求卡（Spec 10 §2.4；PR3） ----------------

/** ADR-0030 决策 6：自动聚焦「批准」后这么久之内的回车/空格不算数（人反应时间 > 400 ms；键盘自动重复首延迟 ≈ 500 ms 内的连发也挡住） */
const ENTER_GUARD_MS = 400;

// ADR-0030 决策 5：approve_session = 批准本次，且本会话同类此后免卡（只出现在 bash 硬危险 / cloud 卡上）
const DECISION_LABEL: Record<string, string> = { approve: "批准", approve_session: "本会话都批准", reject: "拒绝", continue: "继续", stop: "停止" };
const KIND_LABEL: Record<string, string> = { tool_call: "工具卡", fetch: "抓取卡", checkpoint: "检查点", memory_ack: "记忆确认" };

function dangerLabel(v: unknown): string | null {
  if (typeof v !== "string") return null;
  const t = v.trim();
  if (t === "" || t === "无") return null;
  return t.replace(/^\[|\]$/g, "");
}

function fieldText(v: unknown): string {
  if (typeof v === "object" && v !== null) return JSON.stringify(v, null, 2);
  return v === null || v === undefined ? "" : String(v);
}

/** D80 R6（方案 C）：本会话的待答请求卡，挂在对话流末尾（正在跑的回合下面）；第一张自动聚焦「批准」 */
export function RequestCards({ conv, rpc, onEditScript }: { conv?: ConvState; rpc: RpcClient; onEditScript?: () => void }) {
  const requests = conv?.open ?? [];
  if (!conv || requests.length === 0) return null;
  return (
    <>
      {requests.map((req, i) => (
        <RequestCard key={String(req.request_id)} convKey={conv.convKey} req={req} rpc={rpc} disabled={false} focusApprove={i === 0} onEditScript={onEditScript} />
      ))}
    </>
  );
}

type ScriptTargetName = "02-script.md" | "02-script.draft.md";

function scriptTargetOf(args: Record<string, unknown>): ScriptTargetName | null {
  for (const v of [args.filename, args.path, args.rel_path]) {
    if (typeof v !== "string") continue;
    if (v === "02-script.md" || v.endsWith("/02-script.md")) return "02-script.md";
    if (v === "02-script.draft.md" || v.endsWith("/02-script.draft.md")) return "02-script.draft.md";
  }
  return null;
}

export function RequestCard({ convKey, req, rpc, disabled, focusApprove = false, onEditScript }: { convKey: ConvKey; req: OutFrame; rpc: RpcClient; disabled: boolean; focusApprove?: boolean; onEditScript?: () => void }) {
  const requestId = String(req.request_id);
  const kind = String(req.kind);
  const options = Array.isArray(req.options) ? (req.options as unknown[]).map(String) : [];
  const fields = (req.fields ?? {}) as Record<string, unknown>;
  const fieldEntries = Object.entries(fields);
  const [feedback, setFeedback] = useState("");
  // D80 R6.2（人选方案 C，反馈照 A）：「告诉它怎么改」默认收起，点「附说明…」才展开；「拒绝」仍一键（有说明就带上）
  const [feedbackOpen, setFeedbackOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [approveFocused, setApproveFocused] = useState(false);
  const tool = kind === "tool_call" && typeof fields.tool === "string" ? fields.tool : null;
  const toolHeading = tool ? TOOL_TITLES[tool] ?? `${toolCategory(tool)} · ${tool}` : KIND_LABEL[kind] ?? "请求卡";
  // D76：bash 卡上的命令每行带「│」前缀，手选会连前缀一起复制；按钮复制的是请求参数里的命令原文
  const args = (fields.args ?? {}) as Record<string, unknown>;
  const scriptTarget = tool === "write_episode_file" ? scriptTargetOf(args) : null;
  const bashCommand = tool === "bash" && typeof args.command === "string" ? args.command : null;
  const purpose = tool === "bash" && typeof args.description === "string" && args.description.trim() !== "" ? args.description : null;
  const mark = dangerLabel(fields.danger);
  const structuredBash = tool === "bash" && bashCommand !== null && typeof req.card_summary !== "string";
  const feedbackAllowed = kind === "tool_call" && req.feedback_allowed === true && options.includes("reject");
  const off = disabled || busy;
  const approveRef = useRef<HTMLButtonElement | null>(null);
  // 自动聚焦后 ENTER_GUARD_MS 内的回车/空格不生效：连按两下 / 按键自动重复不会顺手批掉紧接着出现的下一张卡（原 TX-14 守的事）
  const focusedAt = useRef<number>(-Infinity);
  useEffect(() => {
    if (!focusApprove) return;
    const active = document.activeElement;
    // 人正在输入框、侧栏、右侧编辑器/顺听面板、或弹窗/浮层里操作就不抢焦点，不打断手上正在做的事
    if (
      document.querySelector('.ui-modal-backdrop:not([data-status="close"])') !== null ||
      active instanceof HTMLInputElement ||
      (active instanceof HTMLTextAreaElement && active.value.trim() !== "") ||
      (active instanceof HTMLElement && active.closest("#ava-preview, #ava-left, .ui-modal-backdrop, .ui-popover") !== null)
    ) {
      return;
    }
    approveRef.current?.focus({ preventScroll: true });
    approveRef.current?.closest(".request-card")?.scrollIntoView({ block: "nearest" });
    focusedAt.current = performance.now();
  }, [focusApprove, requestId]);
  const rawText = String(req.card_text ?? "");
  return (
    <CardFrame
      className="decision request-card"
      testId="request-card"
      attrs={{ "data-kind": kind, "data-request-id": requestId }}
      title={
        <>
          {tool === "bash" ? <Icon name="terminal" size="sm" /> : tool ? <Icon name="stack" size="sm" /> : null}
          {toolHeading}
          {tool ? <Badge>{tool}</Badge> : null}
          {mark ? <Badge tone="danger">{mark}</Badge> : null}
        </>
      }
      sub={scriptTarget !== null ? `AI 建议写入 ${scriptTarget}（可采纳直接写入，或参考建议自己打开双栏编辑器修改）` : undefined}
    >
      {typeof req.card_summary === "string" ? (
        <>
          <pre className="request-card-text" data-testid="request-card-text">
            {req.card_summary}
          </pre>
          <Collapsible
            className="request-card-full"
            testId="request-card-full"
            keepMounted
            summary={`工单全文（${rawText.split("\n").length} 行）`}
          >
            <pre className="request-card-text">{rawText}</pre>
          </Collapsible>
        </>
      ) : structuredBash ? (
        <div data-testid="request-card-face">
          {purpose ? <p className="card-purpose">{purpose}</p> : null}
          <pre className="card-cmd">{bashCommand}</pre>
          <div className="request-card-copy">
            <CopyButton text={bashCommand} label="复制命令" testId="card-copy" />
          </div>
          <Collapsible className="card-more" testId="request-card-source" keepMounted summary="原文">
            <pre className="request-card-text" data-testid="request-card-text">
              {rawText}
            </pre>
          </Collapsible>
        </div>
      ) : (
        <>
          <pre className="request-card-text" data-testid="request-card-text">
            {rawText}
          </pre>
          {bashCommand !== null && (
            <div className="request-card-copy">
              <CopyButton text={bashCommand} label="复制命令" testId="card-copy" />
            </div>
          )}
        </>
      )}
      {fieldEntries.length > 0 && (
        <details className="request-card-fields" data-testid="request-card-fields">
          <summary>参数原文</summary>
          <ul className="fingerprints ui-kv">
            {fieldEntries.map(([k, v]) => (
              <li key={k}>
                <span className="muted">{k}</span>
                <code>{fieldText(v)}</code>
              </li>
            ))}
          </ul>
        </details>
      )}
      <Disclosure open={feedbackAllowed && feedbackOpen}>
        <label className="ui-field">
          修改建议（仅点击「拒绝」时发送）
          <textarea
            className="ui-textarea"
            data-testid="request-feedback"
            rows={2}
            autoFocus
            value={feedback}
            placeholder="告诉 AVA 为什么拒绝或希望怎么调整（按 Esc 收起）"
            onChange={(e) => setFeedback(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape") {
                e.preventDefault();
                setFeedbackOpen(false);
              }
            }}
          />
        </label>
      </Disclosure>
      <div className="ui-card-actions">
        {options.map((decision) => (
          <button
            key={decision}
            ref={decision === options[0] ? approveRef : undefined}
            onFocus={decision === options[0] ? () => setApproveFocused(true) : undefined}
            onBlur={decision === options[0] ? () => setApproveFocused(false) : undefined}
            onKeyDown={(e) => {
              // 只拦不答：preventDefault 使这次回车/空格不派发 click；答复照旧只在 onClick 里（TG-4′）
              if ((e.key === "Enter" || e.key === " ") && performance.now() - focusedAt.current < ENTER_GUARD_MS) e.preventDefault();
            }}
            className="ui-btn"
            data-testid={`request-answer-${decision}`}
            title={decision === options[0] ? "焦点在此按钮时按回车即可确认" : undefined}
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
        {scriptTarget !== null && feedbackAllowed && onEditScript !== undefined && (
          <button
            className="ui-btn"
            data-testid="request-answer-self-edit"
            disabled={off}
            onClick={(e) => {
              if (!e.nativeEvent.isTrusted) return;
              setBusy(true);
              setError(null);
              void rpc
                .call("conv.answer", {
                  convKey,
                  requestId,
                  decision: "reject",
                  feedback: "我已参考你的建议，决定在右侧文案编辑器里自己修改，请勿直接覆盖。",
                })
                .then(() => {
                  setBusy(false);
                  onEditScript();
                })
                .catch((err) => {
                  setBusy(false);
                  setError(errText(err));
                });
            }}
          >
            参考建议，我自己改
          </button>
        )}
        {/* 「附说明…」不是答复：只展开/收起说明框。放在聚焦提示之前，避免批准按钮失焦收起提示时把按钮向左拉跑导致误丢点击 */}
        {feedbackAllowed && !busy && (
          <button
            type="button"
            className="ui-btn ui-btn--sm request-feedback-toggle"
            data-testid="request-feedback-toggle"
            aria-expanded={feedbackOpen}
            disabled={off}
            onClick={() => setFeedbackOpen((v) => !v)}
          >
            {feedbackOpen ? "收起说明" : "附说明…"}
          </button>
        )}
        {focusApprove && approveFocused && options.length > 0 && !busy && (
          // 提示放在按钮外，仅当按钮真正聚焦时显示，按钮文字只取 options 的标签（H-4）
          <span className="muted request-enter-hint" data-testid="request-enter-hint">⏎ {DECISION_LABEL[options[0]] ?? options[0]}</span>
        )}
        {busy && (
          <span className="notice">
            <span className="ui-spinner" />
            <span>处理中…</span>
          </span>
        )}
      </div>
      {error && (
        <div className="decision-err ui-error-shake" data-testid="request-error">
          {error}
        </div>
      )}
    </CardFrame>
  );
}
