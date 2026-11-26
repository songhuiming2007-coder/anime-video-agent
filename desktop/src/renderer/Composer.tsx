// Spec 10 §2.3 / §4.3：输入框与发送 / 停止。全仓唯一出现 "conv.send"、"conv.interrupt" 的文件。
//
// 「显式点击」纪律（§2.4 第 1 层，TG-10）：这两个方法的调用点只在原生元素（<textarea>/<button>/<div>）的事件处理器里，
// 处理器首句检查第一个形参的 nativeEvent.isTrusted、体内无循环与迭代方法、每个处理器至多一处此类调用。
// Enter 发送、Shift+Enter 换行、输入法选词中的 Enter 不发送（isComposing 或 keyCode 229，A2）；⌘. 中断当前回合。
// D79（照 Pi 的 steering）：回合在跑时也能发——消息先排队，这批工具跑完后送达模型；
// 排队中的消息显示在输入框上方，回合非正常结束（停止 / 出错）时退回输入框。
import { useEffect, useRef, useState, type ReactNode } from "react";
import type { QueuedMsg } from "../shared/convFold";
import type { ConvKey } from "../shared/protocol";
import { FloatingTooltip } from "./floating";
import { Icon } from "./icons";
import { Disclosure, IconSwap } from "./motion";
import { errText, type RpcClient } from "./rpc";

/** 按会话保存未发送草稿，切去其他期或选题再切回时不丢字 */
const drafts = new Map<ConvKey, string>();
/** 按会话记录已回填过的退回消息键，切回时不再重复回填 */
const seenReturns = new Map<ConvKey, string>();

export function Composer({
  rpc,
  convKey,
  running,
  turnId,
  disabled,
  placeholder,
  queued = [],
  returned = null,
  prefillReq = null,
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
  /** 外部请求预填输入框（例如上下文弹层里的「压缩上下文（/compact）」） */
  prefillReq?: { text: string; n: number } | null;
  /** D91：输入框下一行的左侧控件（模型 / 推理强度按钮，照 ZCode 工具栏）；由 App 传入 */
  toolbar?: ReactNode;
  /** D91/D170：裸 `/model`、`/thinking`、`/login` 先交给它（展开工具栏下拉/登录弹窗）；返回 true = 已接手、不发送。 */
  onSlash?: (cmd: "/model" | "/thinking" | "/login") => boolean;
}) {
  const [value, setValue] = useState(() => drafts.get(convKey) ?? "");
  const [error, setError] = useState<string | null>(null);
  const [errNonce, setErrNonce] = useState(0);
  const [inFlight, setInFlight] = useState(0);
  const [stopping, setStopping] = useState(false);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  const convKeyRef = useRef(convKey);
  const canSend = !disabled && value.trim() !== "";
  const target = convKey === "idea" ? "选题会话" : convKey.startsWith("proj:") ? `企划会话 · ${convKey.slice(5)}` : convKey.slice(3);

  const showError = (msg: string) => {
    setError(msg);
    setErrNonce((n) => n + 1);
  };

  // 切换会话时加载该会话草稿并清理瞬时错误 / 停止态
  useEffect(() => {
    convKeyRef.current = convKey;
    setValue(drafts.get(convKey) ?? "");
    setError(null);
    setInFlight(0);
    setStopping(false);
    // 首次访问某会话时记下此刻已有的退回键（无退回记 ""），避免切走后后台新产生的退回在切回时被误吞
    if (!seenReturns.has(convKey)) {
      seenReturns.set(convKey, returned ? returned.key : "");
    }
  }, [convKey]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!running) setStopping(false);
  }, [running]);

  // 退回只回填一次：挂载或切会话时已存在的退回不再回填
  useEffect(() => {
    if (!returned) return;
    if (seenReturns.get(convKey) === returned.key) return;
    seenReturns.set(convKey, returned.key);
    setValue((v) => {
      const next = v.trim() === "" ? returned.text : `${v}\n${returned.text}`;
      drafts.set(convKey, next);
      return next;
    });
  }, [convKey, returned]);

  // 外部预填（例如 /compact）：写入输入框并聚焦
  useEffect(() => {
    if (!prefillReq) return;
    setValue(prefillReq.text);
    drafts.set(convKeyRef.current, prefillReq.text);
    setError(null);
    requestAnimationFrame(() => {
      const el = inputRef.current;
      if (!el) return;
      el.focus();
      el.setSelectionRange(prefillReq.text.length, prefillReq.text.length);
    });
  }, [prefillReq?.n]); // eslint-disable-line react-hooks/exhaustive-deps

  const updateValue = (next: string) => {
    setValue(next);
    if (next === "") drafts.delete(convKey);
    else drafts.set(convKey, next);
    if (error !== null) setError(null);
  };

  return (
    <div
      className="composer"
      onKeyDown={(e) => {
        if (!e.nativeEvent.isTrusted) return;
        if (!running || stopping) return;
        if (!(e.metaKey || e.ctrlKey) || e.key !== ".") return;
        e.preventDefault();
        const targetKey = convKey;
        setStopping(true);
        setError(null);
        void rpc.call("conv.interrupt", { convKey: targetKey, turnId: turnId ?? "" }).catch((err) => {
          if (convKeyRef.current !== targetKey) return;
          setStopping(false);
          showError(errText(err));
        });
      }}
    >
      <Disclosure key={`queue:${convKey}`} open={queued.length > 0} keepLastContent>
        {queued.length > 0 && (
          <div className="composer-queue" data-testid="composer-queue">
            {queued.map((q) => (
              <div key={q.id} className="composer-queued ui-card-enter" data-testid="composer-queued" title="排队中：这批工具跑完后送达">
                <span className="muted">排队中</span>
                <span className="composer-queued-text">{q.text}</span>
              </div>
            ))}
          </div>
        )}
      </Disclosure>
      <textarea
        ref={inputRef}
        className="ui-textarea"
        data-testid="composer-input"
        rows={2}
        value={value}
        disabled={disabled}
        placeholder={
          disabled
            ? "数据目录暂不可达，恢复后可继续对话"
            : running
              ? "回合进行中：消息会排队，这批工具跑完后送达（Enter 发送，⌘. 停止）"
              : placeholder
        }
        onChange={(e) => updateValue(e.target.value)}
        onKeyDown={(e) => {
          if (!e.nativeEvent.isTrusted) return;
          if (e.key !== "Enter" || e.shiftKey || e.nativeEvent.isComposing || e.keyCode === 229) return;
          // N51：Enter 只用来发送，换行走 Shift+Enter。不可发 / 空白时不发，也不能放行默认动作——
          // 否则 textarea 插进一个换行，随下一次发送带出（实测 '你好\n'）
          e.preventDefault();
          if (disabled || value.trim() === "") return;
          const bare = value.trim();
          const bareLower = bare.toLowerCase();
          if ((bareLower === "/model" || bareLower === "/thinking" || bareLower === "/login") && onSlash?.(bareLower)) {
            updateValue("");
            setError(null);
            return;
          }
          if (/^\/login\s+\S/i.test(bare)) {
            updateValue("");
            onSlash?.("/login");
            showError("为防止密钥留在聊天记录中，请直接在登录弹窗内填写凭据");
            return;
          }
          const text = value;
          const targetKey = convKey;
          updateValue("");
          setError(null);
          setInFlight((n) => n + 1);
          void rpc
            .call("conv.send", { convKey: targetKey, text })
            .then(
              () => {
                if (convKeyRef.current === targetKey) setInFlight((n) => Math.max(0, n - 1));
              },
              (err) => {
                // 被拒收：无损合并回输入框（不覆盖用户中途新输入的内容）
                const existing = drafts.get(targetKey) ?? "";
                const restored = existing.trim() === "" ? text : `${text}\n${existing}`;
                drafts.set(targetKey, restored);
                if (convKeyRef.current === targetKey) {
                  setInFlight((n) => Math.max(0, n - 1));
                  setValue(restored);
                  showError(errText(err));
                }
              },
            );
        }}
      />
      <Disclosure key={`err:${convKey}`} open={error !== null} keepLastContent>
        {error && (
          <div key={errNonce} className="composer-err-bar ui-error-shake" data-testid="composer-error" role="alert">
            <Icon name="alert" size="sm" />
            <span>{error}</span>
            <span className="spacer" />
            <button type="button" className="ui-btn ui-btn--ghost ui-btn--sm ui-btn--icon" aria-label="关闭发送错误提示" onClick={() => setError(null)}>
              <Icon name="close" size="sm" />
            </button>
          </div>
        )}
      </Disclosure>
      <div className="composer-row">
        <span className="muted composer-target" data-testid="composer-target" title={target}>
          {target}
        </span>
        {toolbar}
        <span className="spacer" />
        {running && (
          <FloatingTooltip
            label={<><span>停止当前回合</span><kbd className="ui-kbd">⌘.</kbd></>}
            renderTrigger={(tip) => (
              <button
                type="button"
                ref={tip.ref}
                {...tip.props}
                className="ui-btn ui-btn--ghost ui-btn--sm ui-confirm-enter"
                data-testid="composer-stop"
                disabled={stopping}
                onClick={(e) => {
                  if (!e.nativeEvent.isTrusted) return;
                  const targetKey = convKey;
                  setStopping(true);
                  setError(null);
                  void rpc.call("conv.interrupt", { convKey: targetKey, turnId: turnId ?? "" }).catch((err) => {
                    if (convKeyRef.current !== targetKey) return;
                    setStopping(false);
                    showError(errText(err));
                  });
                }}
              >
                {stopping ? <span className="ui-spinner" /> : <Icon name="stop" size="sm" />}
                {stopping ? "正在停止…" : "停止"}
              </button>
            )}
          />
        )}
        <button
          type="button"
          className="ui-btn ui-btn--primary ui-btn--sm"
          data-testid="composer-send"
          disabled={!canSend}
          onClick={(e) => {
            if (!e.nativeEvent.isTrusted) return;
            const text = value;
            const bare = text.trim();
            const bareLower = bare.toLowerCase();
            if (disabled || bare === "") return;
            if ((bareLower === "/model" || bareLower === "/thinking" || bareLower === "/login") && onSlash?.(bareLower)) {
              updateValue("");
              setError(null);
              return;
            }
            if (/^\/login\s+\S/i.test(bare)) {
              updateValue("");
              onSlash?.("/login");
              showError("为防止密钥留在聊天记录中，请直接在登录弹窗内填写凭据");
              return;
            }
            const targetKey = convKey;
            updateValue("");
            setError(null);
            setInFlight((n) => n + 1);
            void rpc
              .call("conv.send", { convKey: targetKey, text })
              .then(
                () => {
                  if (convKeyRef.current === targetKey) setInFlight((n) => Math.max(0, n - 1));
                },
                (err) => {
                  const existing = drafts.get(targetKey) ?? "";
                  const restored = existing.trim() === "" ? text : `${text}\n${existing}`;
                  drafts.set(targetKey, restored);
                  if (convKeyRef.current === targetKey) {
                    setInFlight((n) => Math.max(0, n - 1));
                    setValue(restored);
                    showError(errText(err));
                  }
                },
              );
          }}
        >
          <IconSwap
            active={inFlight > 0 ? "busy" : "idle"}
            items={[
              { key: "idle", icon: <Icon name="arrow-up" size="sm" /> },
              { key: "busy", icon: <span className="ui-spinner" /> },
            ]}
          />
          {running ? "排队" : "发送"}
        </button>
      </div>
    </div>
  );
}
