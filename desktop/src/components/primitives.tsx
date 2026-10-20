import { cva, type VariantProps } from "class-variance-authority";
import { motion, type HTMLMotionProps } from "motion/react";
import { useId, type ReactNode } from "react";

import { cn, STATE_LABEL, stateTone } from "../lib/ui";

/*
 * Motion tokens (design system §Motion). Springs for everything that arrives or moves (skill: spring-physics);
 * exits are faster than entrances and accelerate away (skill: exit-faster-than-enter, easing).
 */
export const SPRING = { type: "spring", stiffness: 520, damping: 38, mass: 0.8 } as const; // indicators, presses
export const SOFT = { type: "spring", stiffness: 260, damping: 30 } as const; // content arriving
export const EXIT = { duration: 0.16, ease: [0.7, 0, 0.84, 0] } as const; // leaving
export const STAGGER = 0.04; // seconds between list items (skill: 30–50 ms)

const button = cva(
  "inline-flex select-none items-center justify-center gap-1.5 rounded-[var(--radius-control)] px-3.5 h-8 text-[13px] font-medium disabled:opacity-45 disabled:cursor-not-allowed",
  {
    variants: {
      tone: {
        primary: "bg-accent text-accent-ink elev-1 hover:brightness-110",
        ghost: "text-muted hover:text-ink hover:bg-raised",
        outline: "border border-line text-ink hover:bg-raised hover:border-faint/50",
        danger: "border border-err/40 text-err hover:bg-err/10",
      },
    },
    defaultVariants: { tone: "outline" },
  },
);

export function Button({
  tone, className, disabled, loading, children, ...props
}: HTMLMotionProps<"button"> & VariantProps<typeof button> & { loading?: boolean; children?: ReactNode }) {
  const inactive = disabled || loading;
  return (
    <motion.button type="button" whileTap={inactive ? undefined : { scale: 0.96 }} whileHover={inactive ? undefined : { y: -1 }}
      transition={SPRING} className={cn(button({ tone }), "relative", className)} disabled={inactive}
      aria-busy={loading || undefined} {...props}>
      {/* The label keeps its width while busy, so the button never jumps (skill: content jumping). */}
      <span className={cn("inline-flex items-center gap-1.5", loading && "invisible")}>{children}</span>
      {loading && (
        <span className="absolute inset-0 grid place-items-center" aria-hidden>
          <svg viewBox="0 0 24 24" className="size-4 animate-spin">
            <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeOpacity="0.25" strokeWidth="3" />
            <path d="M21 12a9 9 0 0 0-9-9" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
          </svg>
        </span>
      )}
    </motion.button>
  );
}

const TONES = {
  ok: "bg-ok/15 text-ok",
  warn: "bg-warn/15 text-warn",
  err: "bg-err/15 text-err",
  busy: "bg-accent/15 text-accent",
  idle: "bg-raised text-muted",
};

export function StatePill({ state, className }: { state: string | null | undefined; className?: string }) {
  if (!state) return null;
  const tone = stateTone(state);
  return (
    <motion.span layout="position" transition={SPRING}
      className={cn("inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium whitespace-nowrap", TONES[tone], className)}>
      {tone === "busy" && (
        <span className="relative flex size-1.5">
          <span className="absolute inline-flex size-full animate-ping rounded-full bg-current opacity-60" />
          <span className="relative inline-flex size-1.5 rounded-full bg-current" />
        </span>
      )}
      {STATE_LABEL[state] ?? state}
    </motion.span>
  );
}

export function Kbd({ children }: { children: ReactNode }) {
  return <kbd className="rounded border border-line px-1 font-mono text-[10px] text-faint">{children}</kbd>;
}

/** Choose one of a few options; the selection slides between them. */
export function Segmented<T extends string | number>({
  options, value, onChange, label, className, size = "md", "data-tour": tour,
}: {
  options: { value: T; label: ReactNode }[];
  value: T;
  onChange: (value: T) => void;
  label: string;
  className?: string;
  size?: "sm" | "md";
  "data-tour"?: string;
}) {
  const id = useId();
  return (
    <div role="radiogroup" aria-label={label} data-tour={tour}
      className={cn("relative flex gap-0.5 rounded-lg bg-raised p-0.5", size === "sm" ? "text-[12px]" : "text-[13px]", className)}>
      {options.map((o) => {
        const active = o.value === value;
        return (
          <button key={String(o.value)} type="button" role="radio" aria-checked={active} onClick={() => onChange(o.value)}
            className={cn("relative flex-1 rounded-md px-3", size === "sm" ? "py-0.5" : "py-1",
              active ? "text-ink" : "text-muted hover:text-ink")}>
            {active && (
              <motion.span layoutId={`seg-${id}`} transition={SPRING}
                className="absolute inset-0 rounded-md bg-panel shadow-sm shadow-black/15 ring-1 ring-line/60" />
            )}
            <span className="relative">{o.label}</span>
          </button>
        );
      })}
    </div>
  );
}

/** An on/off switch. */
export function Switch({ checked, onChange, label, disabled }: { checked: boolean; onChange: (v: boolean) => void; label: string; disabled?: boolean }) {
  return (
    <button type="button" role="switch" aria-checked={checked} aria-label={label} disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cn("relative inline-flex h-[22px] w-[38px] shrink-0 items-center rounded-full p-0.5 disabled:opacity-40",
        checked ? "bg-accent" : "bg-line")}>
      <motion.span layout transition={SPRING}
        className={cn("size-[18px] rounded-full bg-white shadow-sm shadow-black/30", checked ? "ml-auto" : "")} />
    </button>
  );
}

/** A horizontal meter that animates to its value. `value` is 0–100. */
export function Meter({ value, tone = "accent" }: { value: number; tone?: "accent" | "ok" | "warn" | "err" }) {
  const clamped = Math.max(0, Math.min(100, value));
  const fill = { accent: "bg-accent", ok: "bg-ok", warn: "bg-warn", err: "bg-err" }[tone];
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-raised" role="meter" aria-valuenow={clamped} aria-valuemin={0} aria-valuemax={100}>
      <motion.div className={cn("h-full rounded-full", fill)} initial={{ width: 0 }} animate={{ width: `${clamped}%` }} transition={SOFT} />
    </div>
  );
}

/** Content that fades and rises into place; used for lists and page sections. */
export function Reveal({ children, delay = 0, className }: { children: ReactNode; delay?: number; className?: string }) {
  return (
    <motion.div className={className} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}
      transition={{ ...SOFT, delay }}>
      {children}
    </motion.div>
  );
}

/** The Saarathi mark: several agents converging into one direction. Follows the chosen accent. */
export function Mark({ size = 28 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="64 64 896 896" aria-hidden role="img">
      <rect x="64" y="64" width="896" height="896" rx="200" fill="#14171c" />
      <g style={{ fill: "var(--color-accent)" }}>
        <circle cx="300" cy="380" r="46" />
        <circle cx="300" cy="512" r="46" />
        <circle cx="300" cy="644" r="46" />
      </g>
      <g fill="none" strokeWidth="40" strokeLinecap="round" opacity="0.55" style={{ stroke: "var(--color-accent)" }}>
        <path d="M352 380 C 480 380, 520 512, 600 512" />
        <path d="M352 644 C 480 644, 520 512, 600 512" />
        <path d="M352 512 L 600 512" />
      </g>
      <path d="M590 392 L 770 512 L 590 632 Z" style={{ fill: "var(--color-accent)" }} />
    </svg>
  );
}
