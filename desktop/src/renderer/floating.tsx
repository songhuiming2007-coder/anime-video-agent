// CSP-safe positioned popovers, tooltips, and modal dialogs built on @floating-ui/react.
// Positioning uses inline CSSOM styles only (VS-7 exempts floatingStyles; zero runtime <style> tags).
// All overlays use useTransitionStatus so directional enter/exit motion is interruptible and anchor-aware.
import {
  autoUpdate,
  flip,
  FloatingDelayGroup,
  FloatingFocusManager,
  FloatingOverlay,
  FloatingPortal,
  offset,
  shift,
  useClick,
  useDelayGroup,
  useDismiss,
  useFloating,
  useFocus,
  useHover,
  useInteractions,
  useRole,
  useTransitionStatus,
  type Placement,
} from "@floating-ui/react";
import { useLayoutEffect, useRef, useState, type ReactElement, type ReactNode, type RefObject } from "react";

function isReducedMotion(): boolean {
  return typeof window !== "undefined" && typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function splitPlacement(p: Placement): { side: string; align: string } {
  const [side, align = "center"] = p.split("-");
  return { side, align };
}

export interface FloatingTrigger {
  ref: (node: Element | null) => void;
  props: Record<string, unknown>;
  open: boolean;
}

/** Shared tooltip delay group: gliding between toolbar icons opens subsequent tooltips instantaneously. */
export function TooltipGroup({ children }: { children: ReactNode }) {
  return (
    <FloatingDelayGroup delay={{ open: 350, close: 80 }} timeoutMs={240}>
      {children}
    </FloatingDelayGroup>
  );
}

export interface FloatingPopoverProps {
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  placement?: Placement;
  offsetPx?: number;
  role?: "dialog" | "menu" | "listbox";
  /** -1 leaves focus on the trigger/caller until user moves into the popover. */
  initialFocus?: number | RefObject<HTMLElement | null>;
  portal?: boolean;
  className?: string;
  testId?: string;
  renderTrigger: (trigger: FloatingTrigger) => ReactElement;
  renderContent: (close: () => void) => ReactNode;
}

/** Anchored popover with flip/shift collision handling, anchor-directional enter/exit motion, Escape/outside dismiss, and focus return. */
export function FloatingPopover({
  open: controlledOpen,
  onOpenChange,
  placement = "bottom-end",
  offsetPx = 6,
  role = "dialog",
  initialFocus = -1,
  portal = true,
  className,
  testId,
  renderTrigger,
  renderContent,
}: FloatingPopoverProps) {
  const [uncontrolledOpen, setUncontrolledOpen] = useState(false);
  const isControlled = controlledOpen !== undefined;
  const open = isControlled ? controlledOpen : uncontrolledOpen;
  const setOpen = (next: boolean) => {
    if (!isControlled) setUncontrolledOpen(next);
    onOpenChange?.(next);
  };

  const { refs, floatingStyles, context, placement: resolvedPlacement } = useFloating({
    open,
    onOpenChange: setOpen,
    placement,
    whileElementsMounted: autoUpdate,
    middleware: [offset(offsetPx), flip({ padding: 8 }), shift({ padding: 8 })],
  });

  const reduced = isReducedMotion();
  const { isMounted, status } = useTransitionStatus(context, {
    duration: reduced ? 0 : { open: 240, close: 140 },
  });

  const click = useClick(context);
  const dismiss = useDismiss(context);
  const ariaRole = useRole(context, { role });
  const { getReferenceProps, getFloatingProps } = useInteractions([click, dismiss, ariaRole]);
  const { side, align } = splitPlacement(resolvedPlacement);

  const wasOpenRef = useRef(open);
  // 退场前在 layout 阶段同步归还焦点（先于绘制和 inert），避免 Chromium 报 aria-hidden 焦点冲突，且不从刚打开的新模态框抢焦
  useLayoutEffect(() => {
    if (wasOpenRef.current && !open) {
      const floating = refs.floating.current;
      const active = document.activeElement;
      const modalOpening = document.querySelector('.ui-modal-backdrop:not([data-status="close"])') !== null;
      const focusInside = Boolean(floating && active && floating.contains(active));
      if (!modalOpening && (!active || active === document.body || focusInside)) {
        const refEl = refs.domReference.current;
        const canFocusRef =
          refEl instanceof HTMLElement &&
          refEl.isConnected &&
          !("disabled" in refEl && (refEl as HTMLButtonElement).disabled);
        if (canFocusRef) {
          refEl.focus({ preventScroll: true });
        } else if (focusInside && active instanceof HTMLElement) {
          active.blur();
        }
      }
    }
    wasOpenRef.current = open;
  }, [open, refs.domReference, refs.floating]);

  const panel = isMounted ? (
    <FloatingFocusManager context={context} modal={false} disabled={!open} initialFocus={initialFocus} returnFocus={false}>
      <div
        ref={refs.setFloating}
        className={["ui-pop-anim", className].filter(Boolean).join(" ")}
        data-testid={open ? testId : undefined}
        inert={!open}
        data-status={status}
        data-side={side}
        data-align={align}
        style={floatingStyles}
        {...getFloatingProps()}
      >
        {renderContent(() => setOpen(false))}
      </div>
    </FloatingFocusManager>
  ) : null;

  return (
    <>
      {renderTrigger({ ref: refs.setReference, props: getReferenceProps(), open })}
      {panel && (portal ? <FloatingPortal>{panel}</FloatingPortal> : panel)}
    </>
  );
}

export interface FloatingTooltipProps {
  label: ReactNode;
  placement?: Placement;
  disabled?: boolean;
  renderTrigger: (trigger: FloatingTrigger) => ReactElement;
}

/** Hover/focus tooltip with FloatingDelayGroup gliding, directional entrance/exit, and viewport clamping. */
export function FloatingTooltip({ label, placement = "bottom", disabled, renderTrigger }: FloatingTooltipProps) {
  const [open, setOpen] = useState(false);
  const activeOpen = !disabled && open;
  const { refs, floatingStyles, context, placement: resolvedPlacement } = useFloating({
    open: activeOpen,
    onOpenChange: setOpen,
    placement,
    whileElementsMounted: autoUpdate,
    middleware: [offset(6), flip({ padding: 8 }), shift({ padding: 8 })],
  });

  const { delay } = useDelayGroup(context);
  const hover = useHover(context, { delay, move: false, enabled: !disabled });
  const focus = useFocus(context, { enabled: !disabled });
  const dismiss = useDismiss(context);
  const ariaRole = useRole(context, { role: "tooltip" });
  const { getReferenceProps, getFloatingProps } = useInteractions([hover, focus, dismiss, ariaRole]);

  const reduced = isReducedMotion();
  const { isMounted, status } = useTransitionStatus(context, {
    duration: reduced ? 0 : { open: 160, close: 110 },
  });
  const { side, align } = splitPlacement(resolvedPlacement);

  return (
    <>
      {renderTrigger({ ref: refs.setReference, props: getReferenceProps(), open: activeOpen })}
      {!disabled && isMounted && (
        <FloatingPortal>
          <div
            ref={refs.setFloating}
            className="ui-tooltip ui-pop-anim"
            data-status={status}
            data-side={side}
            data-align={align}
            style={floatingStyles}
            {...getFloatingProps()}
          >
            {label}
          </div>
        </FloatingPortal>
      )}
    </>
  );
}

export interface FloatingModalProps {
  open: boolean;
  onClose: () => void;
  ariaLabel?: string;
  testId?: string;
  backdropTestId?: string;
  className?: string;
  initialFocus?: number | RefObject<HTMLElement | null>;
  children: ReactNode;
}

/** Accessible centered modal dialog with smooth spring enter/exit, backdrop blur fade, scroll lock, and focus trap. */
export function FloatingModal({ open, onClose, ariaLabel, testId, backdropTestId, className, initialFocus = 0, children }: FloatingModalProps) {
  const { refs, context } = useFloating({
    open,
    onOpenChange: (next) => {
      if (!next) onClose();
    },
  });

  const reduced = isReducedMotion();
  const { isMounted, status } = useTransitionStatus(context, {
    duration: reduced ? 0 : { open: 240, close: 140 },
  });

  const dismiss = useDismiss(context, { outsidePressEvent: "mousedown" });
  const ariaRole = useRole(context, { role: "dialog" });
  const { getFloatingProps } = useInteractions([dismiss, ariaRole]);

  if (!isMounted) return null;

  return (
    <FloatingPortal>
      <FloatingOverlay lockScroll className="ui-modal-backdrop" data-status={status} data-testid={open ? backdropTestId : undefined}>
        <FloatingFocusManager context={context} modal={true} disabled={!open} initialFocus={initialFocus} returnFocus={true}>
          <div
            ref={refs.setFloating}
            className={["ui-modal-panel", className].filter(Boolean).join(" ")}
            data-status={status}
            data-testid={open ? testId : undefined}
            inert={!open}
            aria-label={ariaLabel}
            {...getFloatingProps()}
          >
            {children}
          </div>
        </FloatingFocusManager>
      </FloatingOverlay>
    </FloatingPortal>
  );
}
