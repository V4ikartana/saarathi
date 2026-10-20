// Loading and feedback components (design system: desktop/DESIGN.md, "Loading" and "Feedback").
// Every indicator reflects real state: skeletons while a request is in flight, the working pulse while the engine
// is driving a run, progress from task counts. Nothing animates to suggest progress that is not happening.
import { CheckCircle2, CircleAlert, Info, X } from "lucide-react";
import { animate, AnimatePresence, motion, useMotionValue, useReducedMotion, useTransform } from "motion/react";
import { useEffect } from "react";

import { cn } from "../lib/ui";
import { useApp } from "../store/app";
import { EXIT, SPRING } from "./primitives";

export function Skeleton({ className }: { className?: string }) {
  return <div aria-hidden className={cn("skeleton h-3", className)} />;
}

/** A block of skeleton lines with a natural ragged edge. */
export function SkeletonLines({ lines = 3, className }: { lines?: number; className?: string }) {
  const widths = ["w-11/12", "w-4/5", "w-2/3", "w-3/4", "w-1/2"];
  return (
    <div className={cn("space-y-2.5", className)} aria-hidden>
      {Array.from({ length: lines }, (_, i) => <Skeleton key={i} className={widths[i % widths.length]} />)}
    </div>
  );
}

/** Placeholder card used while a page's data loads; keeps the final layout so nothing jumps (skill: content jumping). */
export function SkeletonCard({ lines = 3, className }: { lines?: number; className?: string }) {
  return (
    <div role="status" aria-busy="true" aria-label="Loading"
      className={cn("rounded-[var(--radius-card)] border border-line bg-panel p-5 elev-1", className)}>
      <Skeleton className="mb-4 h-4 w-1/3" />
      <SkeletonLines lines={lines} />
    </div>
  );
}

/** The AI-native working indicator: three dots pulsing in sequence. */
export function ThinkingDots({ className }: { className?: string }) {
  return (
    <span className={cn("inline-flex items-center gap-1", className)} aria-hidden>
      {[0, 1, 2].map((i) => (
        <span key={i} className="size-1.5 rounded-full bg-current animate-dot" style={{ animationDelay: `${i * 160}ms` }} />
      ))}
    </span>
  );
}

export function Spinner({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={cn("size-4 animate-spin", className)} aria-hidden>
      <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeOpacity="0.2" strokeWidth="3" />
      <path d="M21 12a9 9 0 0 0-9-9" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  );
}

/** Determinate progress (0–100) when the value is known; an indeterminate sweep otherwise. */
export function ProgressBar({ value, label, className }: { value?: number | null; label: string; className?: string }) {
  const known = typeof value === "number";
  return (
    <div role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={100}
      aria-valuenow={known ? Math.round(value) : undefined}
      className={cn("relative h-1 w-full overflow-hidden rounded-full bg-raised", className)}>
      {known ? (
        <motion.div className="h-full rounded-full bg-accent" initial={false}
          animate={{ width: `${Math.max(0, Math.min(100, value))}%` }} transition={{ type: "spring", stiffness: 120, damping: 24 }} />
      ) : (
        <div className="absolute inset-y-0 left-0 w-2/5 rounded-full bg-gradient-to-r from-transparent via-accent to-transparent animate-sweep" />
      )}
    </div>
  );
}

/** A number that counts to its value. Tabular figures so neighbours never shift (skill: number-tabular). */
export function AnimatedNumber({ value, format = (n) => Math.round(n).toLocaleString() }: { value: number; format?: (n: number) => string }) {
  const reduced = useReducedMotion();
  const mv = useMotionValue(reduced ? value : 0);
  const text = useTransform(mv, (n) => format(n));
  useEffect(() => {
    if (reduced) {
      mv.set(value);
      return;
    }
    const controls = animate(mv, value, { duration: 0.9, ease: [0.16, 1, 0.3, 1] });
    return () => controls.stop();
  }, [value, reduced, mv]);
  return <motion.span className="tabular">{text}</motion.span>;
}

const TOAST_ICON = {
  success: <CheckCircle2 className="size-4 text-ok" aria-hidden />,
  error: <CircleAlert className="size-4 text-err" aria-hidden />,
  info: <Info className="size-4 text-accent" aria-hidden />,
};

/** Brief confirmations and errors. Polite live region; never steals focus (skill: toast-accessibility). */
export function Toaster() {
  const { toasts, dismissToast } = useApp();
  return (
    <div aria-live="polite" className="pointer-events-none fixed inset-x-0 bottom-5 z-50 flex flex-col items-center gap-2">
      <AnimatePresence initial={false}>
        {toasts.map((t) => (
          <motion.div key={t.id} layout initial={{ opacity: 0, y: 16, scale: 0.96 }} animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 8, scale: 0.98, transition: EXIT }} transition={SPRING}
            className="glass pointer-events-auto flex max-w-md items-start gap-2.5 rounded-xl border border-line px-3.5 py-2.5 text-[13px] elev-3"
            role={t.kind === "error" ? "alert" : "status"}>
            <span className="mt-0.5">{TOAST_ICON[t.kind]}</span>
            <div className="min-w-0 flex-1">
              <div className="font-medium">{t.title}</div>
              {t.detail && <div className="mt-0.5 text-muted">{t.detail}</div>}
            </div>
            <button type="button" aria-label="Dismiss" onClick={() => dismissToast(t.id)} className="-mr-1 rounded-md p-1 text-faint hover:text-ink">
              <X className="size-3.5" />
            </button>
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  );
}
