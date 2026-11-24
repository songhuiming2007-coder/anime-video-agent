// D39 S2：栏间拖拽手柄。role="separator"（可聚焦的窗口分隔条），←/→ 每次 16px、Home/End 到上下限、双击恢复默认。
// 键盘逻辑在 layout.ts 的纯函数 nudge 里（单元测试覆盖，不做键盘自动化，D37）。
import { useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import { nudge } from "./layout";

export function Splitter({
  label,
  controls,
  value,
  min,
  max,
  dir,
  onChange,
  onReset,
  onDraggingChange,
  testId,
}: {
  label: string;
  controls: string;
  value: number;
  min: number;
  max: number;
  /** 1：向右拖让被调的栏变宽（左栏）；-1：向左拖让被调的栏变宽（预览） */
  dir: 1 | -1;
  onChange: (w: number) => void;
  onReset: () => void;
  onDraggingChange?: (dragging: boolean) => void;
  testId: string;
}) {
  const drag = useRef<{ x: number; v: number } | null>(null);
  const [dragging, setDragging] = useState(false);
  const clamp = (v: number) => Math.min(max, Math.max(min, Math.round(v)));
  const endDrag = () => {
    if (!drag.current) return;
    drag.current = null;
    setDragging(false);
    onDraggingChange?.(false);
  };
  return (
    <div
      className="splitter"
      role="separator"
      aria-orientation="vertical"
      aria-label={label}
      aria-controls={controls}
      aria-valuenow={value}
      aria-valuemin={min}
      aria-valuemax={max}
      tabIndex={0}
      data-testid={testId}
      data-dragging={dragging ? "1" : undefined}
      onPointerDown={(e: PointerEvent<HTMLDivElement>) => {
        if (e.button !== 0) return;
        e.preventDefault();
        e.currentTarget.setPointerCapture(e.pointerId);
        drag.current = { x: e.clientX, v: value };
        setDragging(true);
        onDraggingChange?.(true);
      }}
      onPointerMove={(e: PointerEvent<HTMLDivElement>) => {
        const d = drag.current;
        if (d) onChange(clamp(d.v + dir * (e.clientX - d.x)));
      }}
      onPointerUp={(e: PointerEvent<HTMLDivElement>) => {
        endDrag();
        if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId);
      }}
      onPointerCancel={endDrag}
      onLostPointerCapture={endDrag}
      onDoubleClick={onReset}
      onKeyDown={(e: KeyboardEvent<HTMLDivElement>) => {
        const v = nudge(value, e.key, min, max, dir);
        if (v === null) return;
        e.preventDefault();
        onChange(v);
      }}
    />
  );
}
