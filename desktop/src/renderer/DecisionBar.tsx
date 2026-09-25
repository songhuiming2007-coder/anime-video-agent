// Approval 决策条（Spec 8 §2.6）：显式点击是 ack 的唯一触发源——approval.decide 只出现在本文件的 onClick 里（TG-4）。
// 只呈现确定性事实（停机点类型、对象创建时间、关联产物指纹、note、审片页时间先后），UI 自身零判定、不做 LLM 推荐（direction §5）。
// 失败时 stdout 与 stderr 尾部都原样显示（补丁段号在 stdout，红队 B3）；不提供任何重试按钮（RF-18）。
import { useEffect, useState } from "react";
import type { ApprovalJson, StopType } from "../shared/contracts";
import { isStopType } from "../shared/contracts";
import type { Health, RpcError, TreeEntry } from "../shared/protocol";
import type { RpcClient } from "./rpc";
import { HOST_LINK_LOST, RpcFailure } from "./rpc";
import { approvalsOf, type EpisodeState } from "./store";

const STOP_NAMES: Record<StopType, string> = { "02.5": "人审改稿", "03.5": "配音顺听", "05": "审时间码", "09": "人工发布" };

/** 决策条展示的对象：可 ack 的（PENDING，或 artifact 已对齐、待确认）。判定以 host 为准，这里只决定画不画卡片。 */
function actionable(a: ApprovalJson): boolean {
  return a.status === "pending" || (a.status === "approved" && a.resolved_by === "artifact" && a.confirmed_by === null);
}

/** 最近一次决定的结果挂在决策条上而不是卡片上：成功或陈旧后对象离开可操作态，卡片随 resnapshot 卸载 */
type Outcome = { approvalId: string; stop: StopType } & ({ kind: "ok"; text: string } | { kind: "err"; error: RpcError });

export function DecisionBar({ ep, health, rpc }: { ep: EpisodeState; health: Health | null; rpc: RpcClient }) {
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const [busy, setBusy] = useState(false);
  const items = approvalsOf(ep).filter((a) => actionable(a) && isStopType(a.type));
  const a = ep.approvals;

  const run = async (obj: ApprovalJson, call: () => Promise<unknown>, okText: string) => {
    const head = { approvalId: obj.approval_id, stop: obj.type as StopType };
    setBusy(true);
    setOutcome(null);
    try {
      await call();
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

  if (a.state === "unsupported" || (health && !health.capabilities.approvals)) {
    return (
      <div className="decision readonly" data-testid="decision-readonly">
        决策条只读：{health?.capabilities.approvalsDetail ?? "approval 能力缺席"}
      </div>
    );
  }
  if (items.length === 0 && a.state !== "error" && !outcome) return null;
  const disabled = a.state !== "ok" || health?.reach !== "ok" || busy;
  return (
    <div className="decisions" data-testid="decisions">
      {a.state === "error" && <div className="error">对象库读取失败：{a.message}（以下为上次成功读取的结果，按钮已禁用）</div>}
      {items.map((obj) => (
        <DecisionCard key={obj.approval_id} ep={ep} obj={obj} rpc={rpc} disabled={disabled} run={run} />
      ))}
      {busy && <div className="muted">处理中…</div>}
      {outcome?.kind === "ok" && (
        <div className="decision-ok" data-testid="decision-ok" data-approval-id={outcome.approvalId}>
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

type Run = (obj: ApprovalJson, call: () => Promise<unknown>, okText: string) => Promise<void>;

function DecisionCard({ ep, obj, rpc, disabled, run }: { ep: EpisodeState; obj: ApprovalJson; rpc: RpcClient; disabled: boolean; run: Run }) {
  const stop = obj.type as StopType;
  const [rejecting, setRejecting] = useState(false);
  const [target, setTarget] = useState("");
  const [problem, setProblem] = useState("");
  const aligned = obj.status === "approved";
  const off = disabled;

  const base = { epKey: ep.epKey, approvalId: obj.approval_id, stop };
  return (
    <div className="decision" data-testid="decision" data-stop={stop} data-approval-id={obj.approval_id}>
      <div className="decision-head">
        <strong>
          停机点 {stop} {STOP_NAMES[stop]}
        </strong>
        <span className="muted">
          {aligned ? "解封物已在终端生成，待你确认" : "待审"} · 创建于 {obj.created_at} · {obj.approval_id}
        </span>
      </div>
      <ul className="fingerprints">
        {obj.artifacts.map((f) => (
          <li key={f.path}>
            <code>{f.path}</code> {f.size < 0 ? "（钉住时缺失）" : `${f.size} 字节 · mtime_ns ${f.mtime_ns}`}
          </li>
        ))}
      </ul>
      {obj.note && <div className="muted">{obj.note}</div>}
      {(stop === "03.5" || stop === "05") && (
        <div className="notice" data-testid="no-human-time">
          本次审阅不计人时
        </div>
      )}
      {stop === "05" && <ReviewPageAge epKey={ep.epKey} rpc={rpc} />}
      <div className="decision-actions">
        <button data-testid="approve" disabled={off} onClick={() => void run(obj, () => rpc.call("approval.decide", { ...base, decision: "approve" }), aligned ? "已确认批准" : "已批准")}>
          批准
        </button>
        {stop === "03.5" && <span className="muted">结构化打点（manifest human_review）须在终端 /voice 完成</span>}
        {!aligned && (
          <button data-testid="reject-open" disabled={off} onClick={() => setRejecting((v) => !v)}>
            打回…
          </button>
        )}
      </div>
      {rejecting && !aligned && (
        <div className="reject-form" data-testid="reject-form">
          <label>
            哪段
            <input data-testid="reject-target" value={target} onChange={(e) => setTarget(e.target.value)} placeholder="例如 04-clips.json s07 1:20" />
          </label>
          <label>
            问题
            <textarea data-testid="reject-problem" value={problem} onChange={(e) => setProblem(e.target.value)} rows={3} />
          </label>
          <button
            data-testid="reject-submit"
            disabled={off || !target.trim() || !problem.trim()}
            onClick={() => void run(obj, () => rpc.call("approval.decide", { ...base, decision: "reject", feedback: { target, problem } }), "已打回")}
          >
            提交打回
          </button>
        </div>
      )}
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
