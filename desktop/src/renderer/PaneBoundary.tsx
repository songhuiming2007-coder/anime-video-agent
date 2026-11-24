// D73：预览区的渲染异常兜底。React 没有错误边界时，任何一个组件在渲染中抛错都会卸掉整棵树——
// 整窗白屏、没有一行线索（2026-10-10 伪恋期「切回 03 什么都不显示」无从定位，正是这个形态）。
// 边界把异常收在预览区内：原文显示报错、给「重试」，侧栏与对话照常可用。
// `resetKey` 变了（换预览目标、换期）即自动复位，不必人点。
import { Component, type ErrorInfo, type ReactNode } from "react";
import { CopyButton } from "./copy";
import { StateView } from "./ui";

interface Props {
  resetKey: string;
  children: ReactNode;
}
interface State {
  error: Error | null;
  key: string;
}

export class PaneBoundary extends Component<Props, State> {
  state: State = { error: null, key: this.props.resetKey };

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { error };
  }

  static getDerivedStateFromProps(p: Props, s: State): Partial<State> | null {
    return p.resetKey !== s.key ? { error: null, key: p.resetKey } : null;
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("[ava] 预览区渲染异常", error, info.componentStack);
  }

  render(): ReactNode {
    const { error } = this.state;
    if (error === null) return this.props.children;
    return (
      <div className="pane-crash ui-card-enter" data-testid="pane-crash">
        <StateView kind="error" title={`预览区渲染出错：${error.message}`} detail="可以切换其他文件或点「重试」恢复；若反复出现，可复制报错信息反馈。" />
        <div className="ui-card-actions">
          <button className="ui-btn ui-btn--sm" data-testid="pane-crash-retry" onClick={() => this.setState({ error: null })}>
            重试
          </button>
          <CopyButton text={error.message} label="复制报错" testId="pane-crash-copy" />
        </div>
      </div>
    );
  }
}
