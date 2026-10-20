// The Saarathi mark in motion: three agents appear, their paths draw together, and they leave as one arrow.
// The animation is the logo's meaning ("many agents, one direction"), so it is motion with meaning (skill §7).
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { useEffect, useState } from "react";

import { useApp } from "../store/app";
import { ThinkingDots } from "./feedback";
import { EXIT, SOFT } from "./primitives";

const DOTS = [380, 512, 644];
const PATHS = ["M352 380 C 480 380, 520 512, 600 512", "M352 512 L 600 512", "M352 644 C 480 644, 520 512, 600 512"];

export function AnimatedMark({ size = 72, loop = false, glow = true }: { size?: number; loop?: boolean; glow?: boolean }) {
  const reduced = useReducedMotion();
  const repeat = loop && !reduced ? { repeat: Infinity, repeatDelay: 1.6, repeatType: "loop" as const } : {};
  return (
    <div className="relative" style={{ width: size, height: size }}>
      {glow && <div className="accent-glow absolute -inset-6 animate-glow" aria-hidden />}
      <svg width={size} height={size} viewBox="64 64 896 896" role="img" aria-label="Saarathi" className="relative">
        <rect x="64" y="64" width="896" height="896" rx="210" className="fill-[#141418]" />
        <rect x="64" y="64" width="896" height="896" rx="210" fill="none" stroke="white" strokeOpacity="0.06" strokeWidth="6" />
        {PATHS.map((d, i) => (
          <motion.path key={d} d={d} fill="none" strokeWidth="40" strokeLinecap="round" style={{ stroke: "var(--color-accent)" }}
            initial={reduced ? false : { pathLength: 0, opacity: 0 }} animate={{ pathLength: 1, opacity: 0.55 }}
            transition={{ duration: 0.7, delay: 0.25 + i * 0.08, ease: [0.16, 1, 0.3, 1], ...repeat }} />
        ))}
        {DOTS.map((cy, i) => (
          <motion.circle key={cy} cx="300" cy={cy} r="46" style={{ fill: "var(--color-accent)" }}
            initial={reduced ? false : { scale: 0, opacity: 0 }} animate={{ scale: 1, opacity: 1 }}
            transition={{ type: "spring", stiffness: 420, damping: 18, delay: i * 0.08, ...repeat }} />
        ))}
        <motion.path d="M590 392 L 770 512 L 590 632 Z" style={{ fill: "var(--color-accent)", originX: "590px", originY: "512px" }}
          initial={reduced ? false : { scale: 0.4, opacity: 0, x: -24 }} animate={{ scale: 1, opacity: 1, x: 0 }}
          transition={{ type: "spring", stiffness: 300, damping: 20, delay: 0.75, ...repeat }} />
      </svg>
    </div>
  );
}

/** Shown while the engine starts (and if it restarts). Fades away once the bridge is ready. */
const FIRST_LAUNCH_MS = 1200; // long enough for the mark to finish drawing; only on first launch

export function BootScreen() {
  const { bridgeState, bridge } = useApp();
  const reduced = useReducedMotion();
  const [minimumElapsed, setMinimumElapsed] = useState(Boolean(reduced));
  useEffect(() => {
    const timer = window.setTimeout(() => setMinimumElapsed(true), FIRST_LAUNCH_MS);
    return () => window.clearTimeout(timer);
  }, []);
  const show = bridgeState !== "ready" || !minimumElapsed;
  return (
    <AnimatePresence>
      {show && (
        <motion.div key="boot" className="fixed inset-0 z-[60] grid place-items-center bg-bg" role="status" aria-live="polite"
          initial={{ opacity: 1 }} exit={{ opacity: 0, scale: 1.02, transition: { ...EXIT, duration: 0.35 } }}>
          <div className="flex flex-col items-center gap-6">
            <AnimatedMark size={88} loop />
            <motion.div className="text-center" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ ...SOFT, delay: 0.4 }}>
              <div className="text-[22px] font-semibold tracking-tight">Saarathi</div>
              <div className="mt-1 text-[13px] text-muted">Many agents. One direction.</div>
            </motion.div>
            <motion.div className="flex items-center gap-2 text-[12px] text-faint" initial={{ opacity: 0 }} animate={{ opacity: 1 }}
              transition={{ delay: 0.8 }}>
              {bridgeState === "unavailable" ? (
                <span className="text-err">The engine could not start. Check that `saarathi` is installed, then reopen the app.</span>
              ) : (
                <>
                  <ThinkingDots className="text-accent" />
                  {bridge ? "Reconnecting to the engine" : "Starting the engine"}
                </>
              )}
            </motion.div>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
