// Spec 10 §2.3 / §4.3：对话流（foldConv 的行）。纯展示，不含任何答复/发送类方法名（TG-4′/TG-10/TG-16）。
// 助手回复按纯文本渲染（H-3：不渲染 Markdown、不识别链接）；失败调用的 observation 逐字展开（判据 4）。
import { useLayoutEffect, useRef, type ReactNode } from "react";
import type { ConvRow } from "../shared/convFold";
import { Icon } from "./icons";

/** 离底部不超过这么多像素就算「人在看最新」：新行到来时跟到底；人往上翻了就不打扰（D39 S2） */
const STICK_PX = 48;

export function ConversationPane({ rows, lead }: { rows: ConvRow[]; lead?: ReactNode }) {
  const ref = useRef<HTMLDivElement | null>(null);
  const stick = useRef(true);
  // 每次渲染后（新帧、卡片进出、窗口变化引起的重排）若人在底部就跟到底；App 以 convKey 为 key 重挂，切会话即从底部开始
  useLayoutEffect(() => {
    const el = ref.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  });
  return (
    <div
      className="conv-stream"
      data-testid="conv-stream"
      ref={ref}
      onScroll={(e) => {
        const el = e.currentTarget;
        stick.current = el.scrollHeight - el.scrollTop - el.clientHeight <= STICK_PX;
      }}
    >
      {lead}
      {rows.map((r, i) => (
        <Row key={i} row={r} />
      ))}
    </div>
  );
}

function Row({ row }: { row: ConvRow }) {
  switch (row.k) {
    case "user":
      return (
        <div className="conv-user" data-testid="conv-row" data-kind="user">
          {row.text}
        </div>
      );
    case "assistant": {
      const label = row.label ?? (row.kind === "wrapup" ? "收尾总结" : row.kind === "local_note" ? "本地说明（未进入会话历史）" : null);
      return (
        <div className={row.kind === "answer" ? "conv-assistant" : "conv-note"} data-testid="conv-row" data-kind={row.kind}>
          {label && <span className="conv-label" data-testid="conv-label">{label}</span>}
          {row.text}
        </div>
      );
    }
    case "tool":
      return (
        <div data-testid="conv-row" data-kind="tool" data-ok={row.ok === null ? "pending" : row.ok ? "ok" : "fail"} data-duplicate={row.duplicate ? "1" : "0"}>
          <div className={`conv-tool ${row.ok === false ? "conv-tool-fail" : row.ok ? "conv-tool-ok" : ""}`}>
            <Icon name={row.ok === false ? "alert" : row.ok ? "check" : "pulse"} size="sm" />
            <code>{row.name}</code>
            <span>{row.summary}</span>
            {row.duplicate && <span className="ui-badge ui-badge--warn">重复调用</span>}
            <span className="spacer" />
            {row.durationMs !== null && <span>{Math.round(row.durationMs / 100) / 10}s</span>}
          </div>
          {row.observation !== null && (
            // 非成功默认展开、逐字、不截断（S9-R1 判定由 core 给出；host 不解析）
            <details open data-testid="conv-observation">
              <summary className="conv-note">模型看到的原文</summary>
              <pre className="conv-obs">{row.observation}</pre>
            </details>
          )}
          {row.logs.length > 0 && (
            <details data-testid="conv-logs">
              <summary className="conv-note">作业输出（末 {row.logs.length} 行）</summary>
              <pre className="conv-obs">{row.logs.join("\n")}</pre>
            </details>
          )}
        </div>
      );
    case "log":
      return (
        <details data-testid="conv-row" data-kind="log">
          <summary className="conv-note">作业输出（末 {row.lines.length} 行）</summary>
          <pre className="conv-obs">{row.lines.join("\n")}</pre>
        </details>
      );
    case "card":
      return (
        <div className="conv-note" data-testid="conv-row" data-kind="card" data-request-id={row.requestId} data-status={row.status}>
          {row.text}
        </div>
      );
    case "stop":
      return (
        <div className="conv-note" data-testid="conv-row" data-kind="stop">
          {row.text}
        </div>
      );
    case "notice":
      return (
        <div className={`conv-notice conv-notice--${row.level}`} data-testid="conv-row" data-kind="notice" data-code={row.code} data-level={row.level}>
          <Icon name={row.level === "error" ? "alert" : "info"} size="sm" />
          <code>{row.code}</code>
          <span>{row.text}</span>
        </div>
      );
    case "footer":
      return (
        <div className="conv-foot" data-testid="conv-row" data-kind="footer">
          {row.text}
        </div>
      );
    case "historySeparator":
      // 文案与 Spec 10 §2.3 一致；红线 3 / TG-14 禁止 desktop/src 出现「会话记录文件名」字面量，故拆开拼接
      return (
        <div className="conv-foot" data-testid="conv-row" data-kind="history-separator">
          {"以下为恢复的历史，重建自 " + "session" + ".jsonl" + "：只含消息，不含当时的工具耗时与作业输出"}
        </div>
      );
    case "exited":
      return (
        <div data-testid="conv-row" data-kind="exited">
          <div className="conv-note">
            会话已结束（{row.code === null ? `信号 ${row.signal ?? "?"}` : `退出码 ${row.code}`}）
          </div>
          {row.stderrTail.trim() !== "" && (
            <details>
              <summary className="conv-note">stderr 尾部（诊断，从不解析）</summary>
              <pre className="conv-obs">{row.stderrTail}</pre>
            </details>
          )}
        </div>
      );
    case "framesLost":
      return (
        <div className="conv-notice conv-notice--warn" data-testid="conv-row" data-kind="frames-lost">
          <Icon name="alert" size="sm" />
          此处丢失 {row.count} 帧（{row.reason === "oversize" ? "超过单帧上限" : "无法解析"}）
        </div>
      );
  }
}
