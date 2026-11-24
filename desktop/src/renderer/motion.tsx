// Unified motion primitives: interruptible Disclosure (0fr <-> 1fr) and sliding SegmentedBar pill.
// Pure React + CSSOM (zero external deps, respects prefers-reduced-motion, VS-7 compliant).
import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";

export function prefersReducedMotion(): boolean {
  return typeof window !== "undefined" && typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

type DisclosureState = "closed" | "opening" | "open" | "closing";

/**
 * Smooth, interruptible height disclosure using CSS grid-template-rows (0fr <-> 1fr).
 * Uses a 4-state machine so opening frame has visibility:visible (autoFocus works on mount)
 * and keepMounted forces layout at 0fr before animating to 1fr.
 */
export function Disclosure({
  open,
  className,
  innerClassName,
  keepMounted = false,
  children,
}: {
  open: boolean;
  className?: string;
  innerClassName?: string;
  keepMounted?: boolean;
  children: ReactNode | (() => ReactNode);
}) {
  const [state, setState] = useState<DisclosureState>(open ? "open" : "closed");
  const elRef = useRef<HTMLDivElement | null>(null);
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
    }, 340);
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
        {typeof children === "function" ? children() : children}
      </div>
    </div>
  );
}

/**
 * Segmented control container that slides an optical pill indicator behind the active button
 * (aria-pressed="true" or aria-selected="true"). Handles dynamic option changes, resize, and zero-jump mount.
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
        return;
      }
      const x = active.offsetLeft;
      const w = active.offsetWidth;
      thumb.dataset.hidden = "0";
      thumb.style.transform = `translateX(${x}px)`;
      thumb.style.width = `${w}px`;
      if (!readyRef.current) {
        thumb.dataset.ready = "0";
        if (readyRaf !== null) cancelAnimationFrame(readyRaf);
        readyRaf = requestAnimationFrame(() => {
          readyRaf = null;
          readyRef.current = true;
          if (thumbRef.current) thumbRef.current.dataset.ready = "1";
        });
      }
    };

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
        ? new MutationObserver(() => {
            attachObservers();
            sync();
          })
        : null;
    mo?.observe(wrap, { childList: true, attributes: true, subtree: true, attributeFilter: ["aria-pressed", "aria-selected"] });

    return () => {
      if (readyRaf !== null) cancelAnimationFrame(readyRaf);
      ro?.disconnect();
      mo?.disconnect();
    };
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
