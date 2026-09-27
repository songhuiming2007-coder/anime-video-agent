// Spec 10 §2.3 / §4.3：输入框与发送 / 停止。全仓唯一出现 "conv.send"、"conv.interrupt" 的文件。
//
// 「显式点击」纪律（§2.4 第 1 层，TG-10）：这两个方法的调用点只在原生 <textarea>/<button> 的事件处理器里，
// 处理器首句检查第一个形参的 nativeEvent.isTrusted、体内无循环与迭代方法、每个处理器至多一处此类调用。
// Enter 发送、Shift+Enter 换行、输入法选词中的 Enter 不发送（isComposing 或 keyCode 229，A2）。
import { useState } from "react";
import type { ConvKey } from "../shared/protocol";
import { errText, type RpcClient } from "./rpc";
import { Icon } from "./icons";

export function Composer({ rpc, convKey, running, turnId, disabled, placeholder }: { rpc: RpcClient; convKey: ConvKey; running: boolean; turnId: string | null; disabled: boolean; placeholder: string }) {
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const canSend = !running && !disabled && value.trim() !== "";
  return (
    <div className="composer">
      <textarea
        className="ui-textarea"
        data-testid="composer-input"
        rows={3}
        value={value}
        disabled={disabled}
        placeholder={placeholder}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (!e.nativeEvent.isTrusted) return;
          if (e.key !== "Enter" || e.shiftKey || e.nativeEvent.isComposing || e.keyCode === 229) return;
          if (running || disabled || value.trim() === "") return;
          e.preventDefault();
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
        <span className="muted" data-testid="composer-target">
          {convKey === "idea" ? "选题会话" : convKey.slice(3)}
        </span>
        <span className="spacer" />
        {error && (
          <span className="error" data-testid="composer-error">
            {error}
          </span>
        )}
        {running ? (
          <button
            className="ui-btn ui-btn--sm"
            data-testid="composer-stop"
            onClick={(e) => {
              if (!e.nativeEvent.isTrusted) return;
              void rpc.call("conv.interrupt", { convKey, turnId: turnId ?? "" }).catch((err) => setError(errText(err)));
            }}
          >
            <Icon name="stop" size="sm" />
            停止
          </button>
        ) : (
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
            发送
          </button>
        )}
      </div>
    </div>
  );
}
