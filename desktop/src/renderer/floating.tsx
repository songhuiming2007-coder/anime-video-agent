// CSP-safe positioned popovers, tooltips, and modal dialogs built on @floating-ui/react.
// Positioning uses inline CSSOM styles only (VS-7 exempts this file; zero runtime <style> tags).
import {
  autoUpdate,
  flip,
  FloatingFocusManager,
  FloatingOverlay,
  FloatingPortal,
  offset,
  shift,
  useClick,
  useDismiss,
  useFloating,
  useFocus,
  useHover,
  useInteractions,
  useRole,
  type Placement,
} from "@floating-ui/react";
import { useState, type ReactElement, type ReactNode } from "react";

export interface FloatingTrigger {
  ref: (node: Element | null) => void;
  props: Record<string, unknown>;
  open: boolean;
}

export interface FloatingPopoverProps {
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  placement?: Placement;
  offsetPx?: number;
  role?: "dialog" | "menu" | "listbox";
  /** -1 leaves focus on the trigger/caller until user moves into the popover. */
  initialFocus?: number;
  portal?: boolean;
  className?: string;
  testId?: string;
  renderTrigger: (trigger: FloatingTrigger) => ReactElement;
  renderContent: (close: () => void) => ReactNode;
}

/** Anchored popover with flip/shift collision handling, Escape/outside dismiss, and focus return. */
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

  const { refs, floatingStyles, context } = useFloating({
    open,
    onOpenChange: setOpen,
    placement,
    whileElementsMounted: autoUpdate,
    middleware: [offset(offsetPx), flip({ padding: 8 }), shift({ padding: 8 })],
  });

  const click = useClick(context);
  const dismiss = useDismiss(context);
  const ariaRole = useRole(context, { role });
  const { getReferenceProps, getFloatingProps } = useInteractions([click, dismiss, ariaRole]);

  const panel = open ? (
    <FloatingFocusManager context={context} modal={false} guards={false} initialFocus={initialFocus} returnFocus={true}>
      <div
        ref={refs.setFloating}
        className={["ui-pop-anim", className].filter(Boolean).join(" ")}
        data-testid={testId}
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

/** Hover/focus tooltip with smooth entrance and viewport collision clamping. */
export function FloatingTooltip({ label, placement = "bottom", disabled, renderTrigger }: FloatingTooltipProps) {
  const [open, setOpen] = useState(false);
  const { refs, floatingStyles, context } = useFloating({
    open: !disabled && open,
    onOpenChange: setOpen,
    placement,
    whileElementsMounted: autoUpdate,
    middleware: [offset(6), flip({ padding: 8 }), shift({ padding: 8 })],
  });

  const hover = useHover(context, { delay: { open: 220, close: 60 }, move: false, enabled: !disabled });
  const focus = useFocus(context, { enabled: !disabled });
  const dismiss = useDismiss(context);
  const ariaRole = useRole(context, { role: "tooltip" });
  const { getReferenceProps, getFloatingProps } = useInteractions([hover, focus, dismiss, ariaRole]);

  return (
    <>
      {renderTrigger({ ref: refs.setReference, props: getReferenceProps(), open })}
      {!disabled && open && (
        <FloatingPortal>
          <div ref={refs.setFloating} className="ui-tooltip ui-pop-anim" style={floatingStyles} {...getFloatingProps()}>
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
  children: ReactNode;
}

/** Accessible centered modal dialog with scroll lock, focus trap, and Escape/backdrop dismiss. */
export function FloatingModal({ open, onClose, ariaLabel, testId, backdropTestId, className, children }: FloatingModalProps) {
  const { refs, context } = useFloating({
    open,
    onOpenChange: (next) => {
      if (!next) onClose();
    },
  });

  const dismiss = useDismiss(context, { outsidePressEvent: "mousedown" });
  const ariaRole = useRole(context, { role: "dialog" });
  const { getFloatingProps } = useInteractions([dismiss, ariaRole]);

  if (!open) return null;

  return (
    <FloatingPortal>
      <FloatingOverlay lockScroll className="ui-modal-backdrop" data-testid={backdropTestId}>
        <FloatingFocusManager context={context} modal={true} returnFocus={true}>
          <div
            ref={refs.setFloating}
            className={["ui-pop-anim", className].filter(Boolean).join(" ")}
            data-testid={testId}
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
