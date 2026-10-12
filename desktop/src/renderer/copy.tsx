// D76（人选方案 C）：长命令一键复制。三处共用：助手回复的代码块、bash 工具行、bash 命令审批卡。
// 复制的永远是原文（代码块的 textContent / 帧与请求里的命令字段），不是屏幕上折行后的样子，也不带卡面的「│」前缀。
import { useEffect, useRef, useState, type ReactNode } from "react";

const DONE_MS = 1500; // 「✓ 已复制」停留时长：够人看见，又不至于下一次点击时还挂着旧状态

/** 写系统剪贴板。异步剪贴板 API 要求窗口有焦点，失败时退回 execCommand（隐藏 textarea）。 */
export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    try {
      return document.execCommand("copy");
    } catch {
      return false;
    } finally {
      ta.remove();
    }
  }
}

type CopyState = "idle" | "done" | "failed";
const LABEL_DONE = "✓ 已复制";
const LABEL_FAILED = "复制失败";

/** `label` 可以是图标（D80 工具行悬停复制）：此时必须给 `ariaLabel`，读屏念它；复制后照旧显示「✓ 已复制」文字 */
export function CopyButton({ text, label = "复制", testId = "copy-btn", ariaLabel, className = "" }: { text: string; label?: ReactNode; testId?: string; ariaLabel?: string; className?: string }) {
  const [state, setState] = useState<CopyState>("idle");
  useEffect(() => {
    if (state === "idle") return;
    const t = setTimeout(() => setState("idle"), DONE_MS);
    return () => clearTimeout(t);
  }, [state]);
  return (
    <button
      type="button"
      className={`ui-btn ui-btn--ghost ui-btn--sm copy-btn${state === "done" ? " copy-btn--done" : ""}${className ? ` ${className}` : ""}`}
      data-testid={testId}
      aria-label={state === "idle" ? ariaLabel : undefined}
      title={ariaLabel}
      data-state={state}
      onClick={() => void copyText(text).then((ok) => setState(ok ? "done" : "failed"))}
    >
      {state === "done" ? LABEL_DONE : state === "failed" ? LABEL_FAILED : label}
    </button>
  );
}

/**
 * 给容器里每个 `<pre>` 挂一个「复制」按钮（助手回复的代码块）。
 * 按钮由宿主在渲染后挂上，不进 `renderChatMarkdown` 的输出：D46 的不变量（模型文字渲染不出任何控件）保持不动。
 * `html` 变了 React 会整块重设 innerHTML，按钮随之消失，本 effect 按新内容重挂。
 */
export function useCodeCopyButtons(html: string) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const root = ref.current;
    if (!root) return;
    const timers: ReturnType<typeof setTimeout>[] = [];
    for (const pre of root.querySelectorAll<HTMLElement>("pre")) {
      if (pre.querySelector(":scope > .code-copy")) continue;
      const b = document.createElement("button");
      b.type = "button";
      b.className = "ui-btn ui-btn--ghost ui-btn--sm copy-btn code-copy";
      b.dataset.testid = "code-copy";
      b.textContent = "复制";
      b.addEventListener("click", () => {
        // 去掉 markdown-it 给代码块末尾补的那个换行：粘进终端不会多回车
        const text = (pre.querySelector("code")?.textContent ?? "").replace(/\n$/, "");
        void copyText(text).then((ok) => {
          b.textContent = ok ? LABEL_DONE : LABEL_FAILED;
          b.classList.toggle("copy-btn--done", ok);
          timers.push(
            setTimeout(() => {
              b.textContent = "复制";
              b.classList.remove("copy-btn--done");
            }, DONE_MS),
          );
        });
      });
      pre.appendChild(b);
    }
    return () => timers.forEach(clearTimeout);
  }, [html]);
  return ref;
}
