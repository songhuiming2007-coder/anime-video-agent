// D50-A S7：03.5 停机卡内的结构化打点。单独成文件：打点的 1–5 分段按钮不是闸门按钮，
// 不受 HumanCards.tsx 的 VS-12「闸门按钮同权」约束。
import { useState } from "react";
import { FloatingTooltip } from "./floating";
import { SegmentedBar } from "./motion";
import { errText, type RpcClient } from "./rpc";

const REVIEW_DIMS: readonly [string, string][] = [
  ["voice", "音色"],
  ["prosody", "韵律"],
  ["misread", "读音准确"],
];

/**
 * D50-A S7：03.5 卡内结构化打点（原先只能在终端 `tts --review`）。三维各 1–5，写进 manifest 的 human_review，
 * 不重合成。写 manifest 会改动 03.5 对象钉住的指纹：宿主的自愈会重钉，卡随之刷新（e2e 钉住这一点）。
 */
export function ReviewRating({ epKey, rpc, disabled }: { epKey: string; rpc: RpcClient; disabled: boolean }) {
  const [score, setScore] = useState<Record<string, number>>({});
  const [msg, setMsg] = useState<{ kind: "ok" | "err"; text: string; n: number } | null>(null);
  const [busy, setBusy] = useState(false);
  const full = REVIEW_DIMS.every(([k]) => score[k] !== undefined);
  const review = REVIEW_DIMS.map(([k]) => `${k}=${score[k] ?? 0}`).join(",");

  const finish = async (call: Promise<unknown>) => {
    setBusy(true);
    setMsg(null);
    try {
      await call;
      setMsg((prev) => ({ kind: "ok", text: "已记下打点", n: (prev?.n ?? 0) + 1 }));
    } catch (e) {
      setMsg((prev) => ({ kind: "err", text: errText(e), n: (prev?.n ?? 0) + 1 }));
    } finally {
      setBusy(false);
    }
  };

  const saveTip = !full ? "请先为音色、韵律、读音准确三项打分（1 差 → 5 优）" : disabled ? "当前状态不可保存打点" : "保存本次顺听打点到配音清单";

  return (
    <div className="review-rating" data-testid="review-rating">
      <span className="muted">顺听打点</span>
      <span className="review-scale">1 差 → 5 优</span>
      {REVIEW_DIMS.map(([k, label]) => (
        <span key={k} className="review-dim">
          {label}
          <SegmentedBar activeKey={score[k] ?? null} label={label}>
            {[1, 2, 3, 4, 5].map((n) => (
              <button
                key={n}
                type="button"
                aria-pressed={score[k] === n}
                data-testid={`review-${k}-${n}`}
                onClick={() => {
                  setMsg(null);
                  setScore((s) => ({ ...s, [k]: n }));
                }}
              >
                {n}
              </button>
            ))}
          </SegmentedBar>
        </span>
      ))}
      <FloatingTooltip
        label={saveTip}
        renderTrigger={(t) => (
          <span ref={t.ref} {...t.props}>
            <button
              type="button"
              className="ui-btn ui-btn--ghost ui-btn--sm"
              data-testid="review-save"
              disabled={disabled || busy || !full}
              onClick={(e) => {
                if (!e.nativeEvent.isTrusted) return;
                void finish(rpc.call("voice.review", { epKey, review }));
              }}
            >
              {busy && <span className="ui-spinner" />}
              {busy ? "正在保存…" : "记下打点"}
            </button>
          </span>
        )}
      />
      {msg !== null && (
        <span
          key={msg.n}
          className={msg.kind === "err" ? "notice--err ui-error-shake" : "muted ui-confirm-enter"}
          data-testid="review-msg"
          role={msg.kind === "err" ? "alert" : "status"}
        >
          {msg.text}
        </span>
      )}
    </div>
  );
}
