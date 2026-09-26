// 纯展示组件（Spec 14 §4.2）：Badge / StatusDot / StateView。
// 约束（VS-10，2026-09-26 裁决）：只许 import react 与 ./icons（图标路径唯一落点在 icons.tsx，§2.5）；
// 导出的 props 类型里没有任何 on* 键。
import type { ReactNode } from "react";
import { Icon } from "./icons";

export type BadgeTone = "wait" | "ok" | "warn" | "danger" | "accent";
export type DotTone = "neutral" | "wait" | "ok" | "danger" | "unknown";
export type StateKind = "empty" | "error" | "loading" | "inline";

const cls = (base: string, mod?: string) => (mod ? `${base} ${base}--${mod}` : base);

export function Badge({ tone, children }: { tone?: BadgeTone; children: ReactNode }) {
  return <span className={cls("ui-badge", tone)}>{children}</span>;
}

export function StatusDot({ tone = "neutral" }: { tone?: DotTone }) {
  return <span className={tone === "neutral" ? "ui-dot" : cls("ui-dot", tone)} />;
}

function StateIcon({ kind }: { kind: StateKind }) {
  if (kind === "loading") return <span className="ui-spinner" />;
  const name = kind === "error" ? "alert" : kind === "inline" ? "info" : "file";
  return <Icon name={name} size={kind === "inline" ? "sm" : undefined} />;
}

export function StateView({ kind, title, detail }: { kind: StateKind; title: string; detail?: string }) {
  return (
    <div className={kind === "empty" || kind === "loading" ? "ui-state" : cls("ui-state", kind)}>
      <StateIcon kind={kind} />
      <div className="ui-state-title">{title}</div>
      {detail ? <div className="ui-state-detail">{detail}</div> : null}
    </div>
  );
}
