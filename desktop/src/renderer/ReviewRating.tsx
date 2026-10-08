// D50-A S7：03.5 停机卡内的结构化打点。单独成文件：打点的 1–5 分段按钮不是闸门按钮，
// 不受 HumanCards.tsx 的 VS-12「闸门按钮同权」约束。
import { useState } from "react";
import { errText, type RpcClient } from "./rpc";

const REVIEW_DIMS: readonly [string, string][] = [
  ["voice", "音色"],
  ["prosody", "韵律"],
  ["misread", "错读"],
];

/**
 * D50-A S7：03.5 卡内结构化打点（原先只能在终端 `tts --review`）。三维各 1–5，写进 manifest 的 human_review，
 * 不重合成。写 manifest 会改动 03.5 对象钉住的指纹：宿主的自愈会重钉，卡随之刷新（e2e 钉住这一点）。
 */
export function ReviewRating({ epKey, rpc, disabled }: { epKey: string; rpc: RpcClient; disabled: boolean }) {
  const [score, setScore] = useState<Record<string, number>>({});
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const full = REVIEW_DIMS.every(([k]) => score[k] !== undefined);
  const review = REVIEW_DIMS.map(([k]) => `${k}=${score[k] ?? 0}`).join(",");
  const finish = async (call: Promise<unknown>) => {
    setBusy(true);
    setMsg(null);
    try {
      await call;
      setMsg("已记下打点");
    } catch (e) {
      setMsg(errText(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="review-rating" data-testid="review-rating">
      <span className="muted">顺听打点</span>
      {REVIEW_DIMS.map(([k, label]) => (
        <span key={k} className="review-dim">
          {label}
          <span className="ui-seg">
            {[1, 2, 3, 4, 5].map((n) => (
              <button key={n} aria-pressed={score[k] === n} data-testid={`review-${k}-${n}`} onClick={() => setScore((s) => ({ ...s, [k]: n }))}>
                {n}
              </button>
            ))}
          </span>
        </span>
      ))}
      <button
        className="ui-btn ui-btn--sm"
        data-testid="review-save"
        disabled={disabled || busy || !full}
        onClick={(e) => {
          if (!e.nativeEvent.isTrusted) return;
          void finish(rpc.call("voice.review", { epKey, review }));
        }}
      >
        记下打点
      </button>
      {msg !== null && (
        <span className="muted" data-testid="review-msg">
          {msg}
        </span>
      )}
    </div>
  );
}

