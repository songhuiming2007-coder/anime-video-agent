// D39 S2：栏间拖拽手柄。role="separator"（可聚焦的窗口分隔条），←/→ 每次 16px、Home/End 到上下限、双击恢复默认。
// 键盘逻辑在 layout.ts 的纯函数 nudge 里（单元测试覆盖，不做键盘自动化，D37）。
import { useEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import { nudge } from "./layout";

export function Splitter({
  label,
  controls,
  value,
  min,
  max,
  dir,
  collapsed = false,
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
  /** 固定 5 轨常驻：收起时折叠到 0px 且不可聚焦/不可交互，保持 grid 轨道数恒定 */
  collapsed?: boolean;
  onChange: (w: number) => void;
  onReset: () => void;
  onDraggingChange?: (dragging: boolean) => void;
  testId: string;
}) {
  const drag = useRef<{ x: number; v: number; last: number } | null>(null);
  const onDragRef = useRef(onDraggingChange);
  onDragRef.current = onDraggingChange;
  const [dragging, setDragging] = useState(false);
  const clamp = (v: number) => Math.min(max, Math.max(min, Math.round(v)));
  const endDrag = () => {
    if (!drag.current) return;
    drag.current = null;
    setDragging(false);
    onDragRef.current?.(false);
  };
  // 卸载时若正处于拖拽态，强制复位，避免全局 data-dragging="1" 卡死
  useEffect(() => () => {
    if (drag.current) {
      drag.current = null;
      onDragRef.current?.(false);
    }
  }, []);
  useEffect(() => {
    if (collapsed && drag.current) endDrag();
  }, [collapsed]);
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
      aria-hidden={collapsed || undefined}
      tabIndex={collapsed ? -1 : 0}
      title={collapsed ? undefined : `${label}（双击恢复默认）`}
      data-testid={testId}
      data-collapsed={collapsed ? "1" : undefined}
      data-dragging={dragging ? "1" : undefined}
      onPointerDown={(e: PointerEvent<HTMLDivElement>) => {
        if (collapsed || e.button !== 0) return;
        e.preventDefault();
        e.currentTarget.setPointerCapture(e.pointerId);
        drag.current = { x: e.clientX, v: value, last: value };
        setDragging(true);
        onDragRef.current?.(true);
      }}
      onPointerMove={(e: PointerEvent<HTMLDivElement>) => {
        const d = drag.current;
        if (!d) return;
        const next = clamp(d.v + dir * (e.clientX - d.x));
        if (next === d.last) return;
        d.last = next;
        onChange(next);
      }}
      onPointerUp={(e: PointerEvent<HTMLDivElement>) => {
        endDrag();
        if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId);
      }}
      onPointerCancel={endDrag}
      onLostPointerCapture={endDrag}
      onDoubleClick={() => {
        if (!collapsed) onReset();
      }}
      onKeyDown={(e: KeyboardEvent<HTMLDivElement>) => {
        if (collapsed) return;
        const v = nudge(value, e.key, min, max, dir);
        if (v === null) return;
        e.preventDefault();
        onChange(v);
      }}
    />
  );
}
