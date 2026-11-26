// Unified motion primitives: interruptible Disclosure (0fr <-> 1fr), sliding SegmentedBar pill,
// compositor-only usePresence, fixed-track usePaneTransition, IconSwap, and VS-12-safe Collapsible.
// Pure React + CSSOM (zero external deps, respects prefers-reduced-motion, VS-7 compliant).
import { cloneElement, Fragment, isValidElement, useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { Icon } from "./icons";

/** Recursively strips data-testid and role from closing Disclosure snapshots while preserving element keys so exiting subtrees never unmount/remount. */
export function stripClosingAttrs(node: ReactNode): ReactNode {
  if (node === null || node === undefined || typeof node === "boolean" || typeof node === "string" || typeof node === "number") {
    return node;
  }
  if (Array.isArray(node)) {
    let changed = false;
    const mapped = node.map((child) => {
      const next = stripClosingAttrs(child);
      if (next !== child) changed = true;
      return next;
    });
    return changed ? mapped : node;
  }
  if (!isValidElement<Record<string, unknown>>(node)) return node;
  const props = node.props;
  const hasChildren = "children" in props && props.children !== undefined;
  const nextChildren = hasChildren ? stripClosingAttrs(props.children as ReactNode) : undefined;
  if (node.type === Fragment) {
    return hasChildren && nextChildren !== props.children ? cloneElement(node, undefined, nextChildren) : node;
  }
  const patch: Record<string, unknown> = {};
  let changed = false;
  if ("data-testid" in props && props["data-testid"] !== undefined) {
    patch["data-testid"] = undefined;
    changed = true;
  }
  if ("role" in props && props.role !== undefined) {
    patch.role = undefined;
    changed = true;
  }
  if (hasChildren && nextChildren !== props.children) {
    changed = true;
  }
  if (!changed) return node;
  return hasChildren ? cloneElement(node, patch, nextChildren) : cloneElement(node, patch);
}

export function prefersReducedMotion(): boolean {
  return typeof window !== "undefined" && typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

export type DisclosureState = "closed" | "opening" | "open" | "closing";

/**
 * 4-state transition controller for fixed-track panes (sidebar / preview) or symmetric height glides.
 * Uses double-rAF on fresh open so frame 1 paints the opening state before transitioning to open.
 * Instant under prefers-reduced-motion; interruptible mid-flight.
 */
export function usePaneTransition(open: boolean, durMs = 240): DisclosureState {
  const [state, setState] = useState<DisclosureState>(open ? "open" : "closed");
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useLayoutEffect(() => {
    if (timerRef.current !== null) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    if (open) {
      if (prefersReducedMotion()) {
        setState("open");
        return;
      }
      setState((prev) => (prev === "open" || prev === "closing" ? "open" : "opening"));
      return;
    }
    if (prefersReducedMotion()) {
      setState("closed");
      return;
    }
    setState((prev) => (prev === "closed" ? "closed" : "closing"));
    timerRef.current = setTimeout(() => {
      timerRef.current = null;
      setState("closed");
    }, durMs + 30);
    return () => {
      if (timerRef.current !== null) {
        clearTimeout(timerRef.current);
        timerRef.current = null;
      }
    };
  }, [open, durMs]);

  useLayoutEffect(() => {
    if (state !== "opening") return;
    let raf2 = 0;
    const raf1 = requestAnimationFrame(() => {
      raf2 = requestAnimationFrame(() => setState("open"));
    });
    return () => {
      cancelAnimationFrame(raf1);
      if (raf2) cancelAnimationFrame(raf2);
    };
  }, [state]);

  return state;
}

/**
 * Compositor-only overlay presence hook (opacity + transform enter/exit with zero reflow).
 * Attaching the returned `ref` flushes initial closed style on fresh mount so enter plays on the very next frame
 * (~16ms, comfortably within VE-1's 120ms window); without `ref`, falls back to double-rAF.
 */
export function usePresence(open: boolean, exitMs = 140): { mounted: boolean; status: "open" | "close"; ref: (node: HTMLElement | null) => void } {
  const [mounted, setMounted] = useState(open);
  const [status, setStatus] = useState<"open" | "close">(open ? "open" : "close");
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const nodeRef = useRef<HTMLElement | null>(null);
  const ref = useCallback((node: HTMLElement | null) => {
    nodeRef.current = node;
  }, []);

  useLayoutEffect(() => {
    if (timerRef.current !== null) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    if (open) {
      setMounted(true);
      if (prefersReducedMotion()) {
        setStatus("open");
        return;
      }
      // Mid-exit interruption: element is already painted, reverse immediately.
      if (mounted) {
        setStatus("open");
        return;
      }
      return;
    }
    setStatus("close");
    if (prefersReducedMotion()) {
      setMounted(false);
      return;
    }
    timerRef.current = setTimeout(() => {
      timerRef.current = null;
      setMounted(false);
    }, exitMs);
    return () => {
      if (timerRef.current !== null) {
        clearTimeout(timerRef.current);
        timerRef.current = null;
      }
    };
  }, [open, exitMs]); // eslint-disable-line react-hooks/exhaustive-deps

  useLayoutEffect(() => {
    if (!open || !mounted || status === "open") return;
    if (prefersReducedMotion()) {
      setStatus("open");
      return;
    }
    if (nodeRef.current) {
      void nodeRef.current.offsetWidth;
      const raf = requestAnimationFrame(() => setStatus("open"));
      return () => cancelAnimationFrame(raf);
    }
    let raf2 = 0;
    const raf1 = requestAnimationFrame(() => {
      raf2 = requestAnimationFrame(() => setStatus("open"));
    });
    return () => {
      cancelAnimationFrame(raf1);
      if (raf2) cancelAnimationFrame(raf2);
    };
  }, [open, mounted, status]);

  return { mounted, status, ref };
}

/**
 * Optical in-place icon crossfade + micro-scale swap (zero reflow, shared across play/pause, copy/check, eye/eye-off).
 */
export function IconSwap({
  active,
  items,
  className,
}: {
  active: string;
  items: readonly { key: string; icon: ReactNode; tone?: "ok" | "err" }[];
  className?: string;
}) {
  return (
    <span className={["ui-icon-stack", className].filter(Boolean).join(" ")}>
      {items.map((it) => (
        <span
          key={it.key}
          className={`ui-icon-layer${it.tone === "ok" ? " ui-icon-layer--ok" : it.tone === "err" ? " ui-icon-layer--err" : ""}`}
          data-active={active === it.key ? "1" : "0"}
          aria-hidden={active !== it.key || undefined}
        >
          {it.icon}
        </span>
      ))}
    </span>
  );
}

/**
 * Smooth, interruptible height disclosure using CSS grid-template-rows (0fr <-> 1fr).
 * Uses a 4-state machine so opening frame has visibility:visible (autoFocus works on mount)
 * and keepMounted forces layout at 0fr before animating to 1fr.
 * keepLastContent retains the last non-null children during the 220ms closing glide (with data-testid/role stripped).
 */
export function Disclosure({
  open,
  className,
  innerClassName,
  keepMounted = false,
  keepLastContent = false,
  children,
}: {
  open: boolean;
  className?: string;
  innerClassName?: string;
  keepMounted?: boolean;
  keepLastContent?: boolean;
  children: ReactNode | (() => ReactNode);
}) {
  const [state, setState] = useState<DisclosureState>(open ? "open" : "closed");
  const elRef = useRef<HTMLDivElement | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastContentRef = useRef<ReactNode>(null);

  useLayoutEffect(() => {
    if (timerRef.current !== null) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    if (open) {
      if (prefersReducedMotion()) {
        setState("open");
        return;
      }
      setState((prev) => (prev === "open" || prev === "closing" ? "open" : "opening"));
      return;
    }
    if (prefersReducedMotion()) {
      setState("closed");
      return;
    }
    setState((prev) => (prev === "closed" ? "closed" : "closing"));
    timerRef.current = setTimeout(() => {
      timerRef.current = null;
      setState("closed");
    }, 220);
    return () => {
      if (timerRef.current !== null) {
        clearTimeout(timerRef.current);
        timerRef.current = null;
      }
    };
  }, [open]);

  useLayoutEffect(() => {
    if (state !== "opening") return;
    void elRef.current?.offsetHeight;
    const raf = requestAnimationFrame(() => setState("open"));
    return () => cancelAnimationFrame(raf);
  }, [state]);

  const active = state !== "closed";
  let rendered: ReactNode = null;
  if (active || keepMounted) {
    const cur = typeof children === "function" ? children() : children;
    if (keepLastContent) {
      if (open && cur !== null && cur !== undefined && cur !== false) {
        lastContentRef.current = cur;
        rendered = cur;
      } else if (state === "closing" || (!open && active && !prefersReducedMotion())) {
        rendered = stripClosingAttrs(lastContentRef.current);
      } else {
        rendered = cur;
      }
    } else {
      rendered = cur;
    }
  } else if (keepLastContent) {
    lastContentRef.current = null;
  }

  if (!active && !keepMounted) return null;

  return (
    <div
      ref={elRef}
      className={["ui-collapse", className].filter(Boolean).join(" ")}
      data-state={state}
      data-open={state === "open" ? "1" : "0"}
      hidden={!active}
      inert={state === "closing" || state === "closed"}
      onTransitionEnd={(e) => {
        if (!open && e.target === e.currentTarget && e.propertyName === "grid-template-rows") {
          setState("closed");
        }
      }}
    >
      <div className={["ui-collapse-inner", innerClassName].filter(Boolean).join(" ")}>
        {rendered}
      </div>
    </div>
  );
}

/**
 * Unified collapsible block replacing raw <details><summary> across the app.
 * Lives in motion.tsx so HumanCards.tsx can use it without triggering VS-12 gate-button rules.
 */
export function Collapsible({
  summary,
  defaultOpen = false,
  bar = false,
  keepMounted = false,
  className,
  testId,
  summaryTestId,
  dataKind,
  children,
}: {
  summary: ReactNode;
  defaultOpen?: boolean;
  /** True renders full-width .ui-section-like bar (e.g. VoicePanel sections). */
  bar?: boolean;
  keepMounted?: boolean;
  className?: string;
  testId?: string;
  summaryTestId?: string;
  dataKind?: string;
  children: ReactNode | (() => ReactNode);
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div
      className={["ui-collapsible", className].filter(Boolean).join(" ")}
      data-testid={testId}
      data-kind={dataKind}
      data-open={open ? "1" : "0"}
    >
      <button
        type="button"
        className={bar ? "ui-collapsible-trigger ui-collapsible-trigger--bar" : "ui-collapsible-trigger"}
        data-testid={summaryTestId}
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <span className="ui-chev" data-open={open ? "1" : "0"}>
          <Icon name="chevron-right" size="sm" />
        </span>
        <span>{summary}</span>
      </button>
      <Disclosure open={open} keepMounted={keepMounted}>
        {children}
      </Disclosure>
    </div>
  );
}

/**
 * Segmented control container that slides an optical pill indicator behind the active button
 * (aria-pressed="true" or aria-selected="true"). Handles dynamic option changes, null resets, resize, and zero-jump mount.
 */
export function SegmentedBar({
  activeKey,
  className,
  label,
  role = "group",
  testId,
  children,
}: {
  /** Any value that changes when the active segment changes (e.g. selected value or index). */
  activeKey: string | number | null | undefined;
  className?: string;
  label?: string;
  role?: string;
  testId?: string;
  children: ReactNode;
}) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const thumbRef = useRef<HTMLSpanElement>(null);
  const readyRef = useRef(false);
  const syncRef = useRef<(() => void) | null>(null);

  useLayoutEffect(() => {
    const wrap = wrapRef.current;
    const thumb = thumbRef.current;
    if (!wrap || !thumb) return;
    let readyRaf: number | null = null;
    const observed = new Set<Element>();

    const sync = () => {
      const active = wrap.querySelector<HTMLElement>('button[aria-pressed="true"], button[aria-selected="true"]');
      if (!active || active.offsetWidth === 0) {
        thumb.dataset.hidden = "1";
        thumb.dataset.ready = "0";
        readyRef.current = false;
        return;
      }
      const x = active.offsetLeft;
      const y = active.offsetTop;
      const w = active.offsetWidth;
      const h = active.offsetHeight;
      thumb.style.transform = `translate3d(${x}px, ${y}px, 0)`;
      thumb.style.width = `${w}px`;
      thumb.style.height = `${h}px`;
      thumb.dataset.hidden = "0";
      if (!readyRef.current) {
        thumb.dataset.ready = "0";
        void thumb.offsetWidth;
        if (readyRaf !== null) cancelAnimationFrame(readyRaf);
        readyRaf = requestAnimationFrame(() => {
          readyRaf = null;
          readyRef.current = true;
          if (thumbRef.current) thumbRef.current.dataset.ready = "1";
        });
      }
    };
    syncRef.current = sync;

    const ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver(sync) : null;
    const attachObservers = () => {
      if (!ro) return;
      for (const el of observed) ro.unobserve(el);
      observed.clear();
      ro.observe(wrap);
      observed.add(wrap);
      for (const btn of wrap.querySelectorAll("button")) {
        ro.observe(btn);
        observed.add(btn);
      }
    };

    attachObservers();
    sync();

    const mo =
      typeof MutationObserver !== "undefined"
        ? new MutationObserver((mutations) => {
            if (mutations.some((m) => m.type === "childList")) attachObservers();
            sync();
          })
        : null;
    mo?.observe(wrap, { childList: true, attributes: true, subtree: true, attributeFilter: ["aria-pressed", "aria-selected"] });

    return () => {
      syncRef.current = null;
      if (readyRaf !== null) cancelAnimationFrame(readyRaf);
      ro?.disconnect();
      mo?.disconnect();
    };
  }, []);

  useLayoutEffect(() => {
    syncRef.current?.();
  }, [activeKey]);

  return (
    <div ref={wrapRef} className={["ui-seg ui-seg--slide", className].filter(Boolean).join(" ")} role={role} aria-label={label} data-testid={testId}>
      <span ref={thumbRef} className="ui-seg-thumb" aria-hidden="true" data-hidden="1" data-ready="0" />
      {children}
    </div>
  );
}

/**
 * Subtle tactile bump when a numeric count changes after initial mount.
 * Returns a transient "1" flag for 190ms when value changes (0ms on reduced-motion).
 */
export function useCountBump(value: number | string): boolean {
  const prev = useRef(value);
  const [bump, setBump] = useState(false);

  useEffect(() => {
    if (prev.current === value) return;
    prev.current = value;
    if (prefersReducedMotion()) return;
    setBump(true);
    const t = setTimeout(() => setBump(false), 190);
    return () => clearTimeout(t);
  }, [value]);

  return bump;
}
