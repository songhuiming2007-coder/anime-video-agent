// Spec 10 §2.3 / §4.3：输入框与发送 / 停止。全仓唯一出现 "conv.send"、"conv.interrupt" 的文件。
//
// 「显式点击」纪律（§2.4 第 1 层，TG-10）：这两个方法的调用点只在原生 <textarea>/<button> 的事件处理器里，
// 处理器首句检查第一个形参的 nativeEvent.isTrusted、体内无循环与迭代方法、每个处理器至多一处此类调用。
// Enter 发送、Shift+Enter 换行、输入法选词中的 Enter 不发送（isComposing 或 keyCode 229，A2）。
// D79（照 Pi 的 steering）：回合在跑时也能发——消息先排队，这批工具跑完后送达模型；
// 排队中的消息显示在输入框上方，回合非正常结束（停止 / 出错）时退回输入框。
import { useEffect, useRef, useState, type ReactNode } from "react";
import type { QueuedMsg } from "../shared/convFold";
import type { ConvKey } from "../shared/protocol";
import { errText, type RpcClient } from "./rpc";
import { Icon } from "./icons";

export function Composer({
  rpc,
  convKey,
  running,
  turnId,
  disabled,
  placeholder,
  queued = [],
  returned = null,
  toolbar = null,
  onSlash,
}: {
  rpc: RpcClient;
  convKey: ConvKey;
  running: boolean;
  turnId: string | null;
  disabled: boolean;
  placeholder: string;
  queued?: QueuedMsg[];
  returned?: { key: string; text: string } | null;
  /** D91：输入框下一行的左侧控件（模型 / 推理强度按钮，照 ZCode 工具栏）；由 App 传入 */
  toolbar?: ReactNode;
  /** D91：裸 `/model`、`/thinking` 先交给它（展开工具栏下拉）；返回 true = 已接手、不发送。会话没起时返回 false，照常发给 core 打印列表 */
  onSlash?: (cmd: "/model" | "/thinking") => boolean;
}) {
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const canSend = !disabled && value.trim() !== "";
  const target = convKey === "idea" ? "选题会话" : convKey.startsWith("proj:") ? `企划会话 · ${convKey.slice(5)}` : convKey.slice(3);
  // 退回只回填一次：挂载时已存在的退回（切会话回来、重放历史）不再回填
  const seenReturn = useRef<string | null>(returned?.key ?? null);
  useEffect(() => {
    if (!returned || returned.key === seenReturn.current) return;
    seenReturn.current = returned.key;
    setValue((v) => (v.trim() === "" ? returned.text : `${v}\n${returned.text}`));
  }, [returned]);
  return (
    <div className="composer">
      {queued.length > 0 && (
        <div className="composer-queue" data-testid="composer-queue">
          {queued.map((q) => (
            <div key={q.id} className="composer-queued" data-testid="composer-queued" title="排队中：这批工具跑完后送达">
              <span className="muted">排队中</span>
              <span className="composer-queued-text">{q.text}</span>
            </div>
          ))}
        </div>
      )}
      <textarea
        className="ui-textarea"
        data-testid="composer-input"
        rows={3}
        value={value}
        disabled={disabled}
        placeholder={running ? "回合进行中：消息会排队，这批工具跑完后送达（Enter 发送）" : placeholder}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (!e.nativeEvent.isTrusted) return;
          if (e.key !== "Enter" || e.shiftKey || e.nativeEvent.isComposing || e.keyCode === 229) return;
          // N51：Enter 只用来发送，换行走 Shift+Enter。不可发 / 空白时不发，也不能放行默认动作——
          // 否则 textarea 插进一个换行，随下一次发送带出（实测 '你好\n'）
          e.preventDefault();
          if (disabled || value.trim() === "") return;
          const bare = value.trim();
          if ((bare === "/model" || bare === "/thinking") && onSlash?.(bare)) {
            setValue("");
            setError(null);
            return;
          }
          const text = value;
          setValue("");
          setError(null);
          void rpc.call("conv.send", { convKey, text }).catch((err) => {
            setValue(text); // 被拒收：输入框保留原文（§2.3）
            setError(errText(err));
          });
        }}
      />
      <div className="composer-row">
        <span className="muted composer-target" data-testid="composer-target" title={target}>
          {target}
        </span>
        {toolbar}
        <span className="spacer" />
        {error && (
          <span className="error" data-testid="composer-error">
            {error}
          </span>
        )}
        {running && (
          <button
            className="ui-btn ui-btn--ghost ui-btn--sm"
            data-testid="composer-stop"
            onClick={(e) => {
              if (!e.nativeEvent.isTrusted) return;
              void rpc.call("conv.interrupt", { convKey, turnId: turnId ?? "" }).catch((err) => setError(errText(err)));
            }}
          >
            <Icon name="stop" size="sm" />
            停止
          </button>
        )}
        <button
          className="ui-btn ui-btn--primary ui-btn--sm"
          data-testid="composer-send"
          disabled={!canSend}
          onClick={(e) => {
            if (!e.nativeEvent.isTrusted) return;
            const text = value;
            if (disabled || text.trim() === "") return;
            setValue("");
            setError(null);
            void rpc.call("conv.send", { convKey, text }).catch((err) => {
              setValue(text);
              setError(errText(err));
            });
          }}
        >
          <Icon name="play" size="sm" />
          {running ? "排队" : "发送"}
        </button>
      </div>
    </div>
  );
}
