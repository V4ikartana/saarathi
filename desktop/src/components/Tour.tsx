// The first-run guided tour: a spotlight moves from part to part of the app with a card explaining each one.
// Runs once automatically (when the workspace first appears) and on demand from Settings or the command palette.
import { ArrowLeft, ArrowRight, X } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

import { cn } from "../lib/ui";
import { useApp } from "../store/app";
import { Button, EXIT, SOFT, SPRING } from "./primitives";

const DONE_KEY = "saarathi.tour.v2.done"; // bump to show a reworked tour once more

interface Step {
  target?: string; // data-tour value; no target means a centred card
  title: string;
  body: string;
}

const STEPS: Step[] = [
  {
    title: "Welcome to Saarathi",
    body: "Saarathi turns one request into a plan, splits it into tasks, and has Claude Code, Codex and Antigravity build them in isolated branches, checking every result itself. This one-minute tour shows where everything is.",
  },
  { target: "new-session", title: "Start a session", body: "Each session is one conversation about one piece of work. Press ⌘N any time to start another." },
  { target: "sessions", title: "Your sessions", body: "Every session and its latest state live here. Runs keep going even if you close the window." },
  { target: "composer", title: "Describe the change", body: "Write what you want built or fixed. Press Enter to send, Shift+Enter for a new line. The engineering lead reads it, asks if something is unclear, and proposes a plan for your approval." },
  { target: "session-controls", title: "How much to delegate, and to whom", body: "Choose whether plans need your approval, and optionally prefer or pin one worker model. The project's policy sets the limits." },
  { target: "view-switch", title: "Conversation and timeline", body: "Follow the run as a conversation, or switch to the timeline to see the task graph and every attempt over time." },
  { target: "execution", title: "Tasks as they run", body: "Each task, who is working on it and whether it passed. Click a task to inspect its attempts, routing decisions, side-by-side diff and logs." },
  { target: "nav-usage", title: "Usage and plan limits", body: "How much of each subscription is left, straight from each CLI, plus the tokens every run used. Saarathi avoids providers that are nearly out." },
  { target: "nav-policy", title: "Policy and the engineering lead", body: "The project's rules: which model leads, which providers may work, and what agents may do. Edit them here; every change is checked and backed up." },
  { target: "nav-settings", title: "Make it yours", body: "Theme, accent, size and motion, which models Saarathi may use at all, and defaults for new sessions. You can replay this tour from here." },
  { target: "palette", title: "Everything from the keyboard", body: "Press ⌘K to jump to any session or page, or run an action." },
  { title: "You're ready", body: "Start with a small, well-defined change to see the whole flow: plan, approval, parallel work, verification and review." },
];

export function tourDone(): boolean {
  try {
    return localStorage.getItem(DONE_KEY) === "1";
  } catch {
    return true; // without storage, never force the tour on every launch
  }
}

function markDone(): void {
  try {
    localStorage.setItem(DONE_KEY, "1");
  } catch {
    // The tour may show again next launch; harmless.
  }
}

interface Box {
  x: number;
  y: number;
  w: number;
  h: number;
}

const PAD = 8;
const CARD_W = 340;

function locate(target: string | undefined): Box | null {
  if (!target) return null;
  const el = document.querySelector(`[data-tour="${target}"]`);
  if (!el) return null;
  const r = el.getBoundingClientRect();
  if (r.width === 0 || r.height === 0) return null;
  return { x: r.left - PAD, y: r.top - PAD, w: r.width + PAD * 2, h: r.height + PAD * 2 };
}

/** Place the card beside the spotlight, inside the window. */
export function cardPosition(box: Box | null, cardH: number): { left: number; top: number } {
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  if (!box) return { left: (vw - CARD_W) / 2, top: Math.max(24, (vh - cardH) / 2) };
  const clampTop = (t: number) => Math.max(16, Math.min(vh - cardH - 16, t));
  const clampLeft = (l: number) => Math.max(16, Math.min(vw - CARD_W - 16, l));
  if (box.x + box.w + 16 + CARD_W < vw) return { left: box.x + box.w + 16, top: clampTop(box.y) };
  if (box.x - 16 - CARD_W > 0) return { left: box.x - 16 - CARD_W, top: clampTop(box.y) };
  if (box.y + box.h + 16 + cardH < vh) return { left: clampLeft(box.x), top: box.y + box.h + 16 };
  return { left: clampLeft(box.x), top: clampTop(box.y - cardH - 16) };
}

export function Tour() {
  const { tourOpen, setTourOpen } = useApp();
  const [index, setIndex] = useState(0);
  const [box, setBox] = useState<Box | null>(null);
  const [cardH, setCardH] = useState(200);
  const card = useRef<HTMLDivElement>(null);
  const next = useRef<HTMLButtonElement>(null);

  // Steps whose part of the app is not on screen right now are skipped.
  const visible = STEPS.filter((s) => !s.target || locate(s.target));
  const step = visible[Math.min(index, visible.length - 1)];

  const close = useCallback(() => {
    markDone();
    setTourOpen(false);
    setIndex(0);
  }, [setTourOpen]);

  useLayoutEffect(() => {
    if (!tourOpen || !step) return;
    const update = () => {
      setBox(locate(step.target));
      if (card.current) setCardH(card.current.offsetHeight);
    };
    update();
    window.addEventListener("resize", update);
    const timer = window.setInterval(update, 400); // follow layout changes (panels opening, scrolling)
    return () => {
      window.removeEventListener("resize", update);
      window.clearInterval(timer);
    };
  }, [tourOpen, step]);

  useEffect(() => {
    if (tourOpen) next.current?.focus();
  }, [tourOpen, index]);

  useEffect(() => {
    if (!tourOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
      else if (e.key === "ArrowRight" || e.key === "Enter") {
        e.preventDefault();
        if (index >= visible.length - 1) close();
        else setIndex((i) => i + 1);
      } else if (e.key === "ArrowLeft") setIndex((i) => Math.max(0, i - 1));
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [tourOpen, index, visible.length, close]);

  const last = index >= visible.length - 1;
  const position = cardPosition(box, cardH);
  return (
    <AnimatePresence>
      {tourOpen && step && (
        <motion.div key="tour" className="fixed inset-0 z-[70]" initial={{ opacity: 0 }} animate={{ opacity: 1 }}
          exit={{ opacity: 0, transition: EXIT }} role="dialog" aria-modal="true" aria-labelledby="tour-title">
          {/* The dimmed backdrop with a hole cut around the spotlight. */}
          <svg className="absolute inset-0 size-full" aria-hidden>
            <defs>
              <mask id="tour-hole">
                <rect width="100%" height="100%" fill="white" />
                {box && (
                  <motion.rect fill="black" rx="14" initial={false} animate={{ x: box.x, y: box.y, width: box.w, height: box.h }} transition={SPRING} />
                )}
              </mask>
            </defs>
            <rect width="100%" height="100%" fill="rgb(0 0 0 / 0.62)" mask="url(#tour-hole)" />
          </svg>
          {box && (
            <motion.div className="pointer-events-none absolute rounded-[14px] ring-2 ring-accent shadow-[0_0_0_6px_color-mix(in_oklab,var(--color-accent)_22%,transparent)]"
              initial={false} animate={{ left: box.x, top: box.y, width: box.w, height: box.h }} transition={SPRING} />
          )}
          <motion.div ref={card} key={`card-${index}`} className="absolute rounded-[var(--radius-card)] border border-line-strong bg-panel p-5 elev-3"
            style={{ width: CARD_W }} initial={{ opacity: 0, y: 8, ...position }} animate={{ opacity: 1, y: 0, ...position }} transition={SOFT}>
            <div className="flex items-start gap-3">
              <h2 id="tour-title" className="min-w-0 flex-1 text-[16px] font-semibold tracking-tight">{step.title}</h2>
              <button type="button" aria-label="Skip the tour" onClick={close} className="-mr-1 -mt-1 rounded-md p-1 text-faint hover:text-ink">
                <X className="size-4" />
              </button>
            </div>
            <p className="mt-2 text-[13px] leading-relaxed text-muted">{step.body}</p>
            <div className="mt-4 flex items-center gap-3">
              <div className="flex flex-1 gap-1" aria-label={`Step ${index + 1} of ${visible.length}`}>
                {visible.map((s, i) => (
                  <motion.span key={s.title} className={cn("h-1.5 rounded-full", i === index ? "bg-accent" : "bg-line-strong")}
                    animate={{ width: i === index ? 18 : 6 }} transition={SPRING} />
                ))}
              </div>
              {index > 0 && (
                <Button tone="ghost" onClick={() => setIndex((i) => i - 1)} aria-label="Previous step"><ArrowLeft className="size-3.5" /></Button>
              )}
              <Button ref={next} tone="primary" onClick={() => (last ? close() : setIndex((i) => i + 1))}>
                {last ? "Start building" : index === 0 ? "Show me" : "Next"}
                {!last && <ArrowRight className="size-3.5" />}
              </Button>
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
