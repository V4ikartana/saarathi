import { cva, type VariantProps } from "class-variance-authority";
import type { ButtonHTMLAttributes, ReactNode } from "react";

import { cn, STATE_LABEL, stateTone } from "../lib/ui";

const button = cva(
  "inline-flex items-center gap-1.5 rounded-md px-2.5 h-8 text-[13px] font-medium transition-colors disabled:opacity-40 disabled:cursor-not-allowed focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60",
  {
    variants: {
      tone: {
        primary: "bg-accent text-accent-ink hover:brightness-110",
        ghost: "text-muted hover:text-ink hover:bg-raised",
        outline: "border border-line text-ink hover:bg-raised",
        danger: "border border-err/40 text-err hover:bg-err/10",
      },
    },
    defaultVariants: { tone: "outline" },
  },
);

export function Button({ tone, className, ...props }: ButtonHTMLAttributes<HTMLButtonElement> & VariantProps<typeof button>) {
  return <button type="button" className={cn(button({ tone }), className)} {...props} />;
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
    <span className={cn("inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium", TONES[tone], className)}>
      {tone === "busy" && <span className="size-1.5 rounded-full bg-current animate-pulse" />}
      {STATE_LABEL[state] ?? state}
    </span>
  );
}

export function Kbd({ children }: { children: ReactNode }) {
  return <kbd className="rounded border border-line px-1 font-mono text-[10px] text-faint">{children}</kbd>;
}

/** The Saarathi mark: several agents converging into one direction. */
export function Mark({ size = 28 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="64 64 896 896" aria-hidden role="img">
      <rect x="64" y="64" width="896" height="896" rx="200" fill="#14171c" />
      <g fill="#e3a94a">
        <circle cx="300" cy="380" r="46" />
        <circle cx="300" cy="512" r="46" />
        <circle cx="300" cy="644" r="46" />
      </g>
      <g fill="none" stroke="#e3a94a" strokeWidth="40" strokeLinecap="round" opacity="0.55">
        <path d="M352 380 C 480 380, 520 512, 600 512" />
        <path d="M352 644 C 480 644, 520 512, 600 512" />
        <path d="M352 512 L 600 512" />
      </g>
      <path d="M590 392 L 770 512 L 590 632 Z" fill="#e3a94a" />
    </svg>
  );
}
