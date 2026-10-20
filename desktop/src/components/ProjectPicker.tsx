import { open } from "@tauri-apps/plugin-dialog";
import { ArrowRight, FolderGit2, FolderOpen, ShieldCheck } from "lucide-react";
import { motion } from "motion/react";

import { useApp } from "../store/app";
import { AnimatedMark } from "./brand";
import { Button, SOFT, STAGGER } from "./primitives";

function baseName(path: string): string {
  return path.split(/[\\/]/).filter(Boolean).pop() ?? path;
}

export function ProjectPicker() {
  const { project, recent, openProject, initProject, busy, bridge, bridgeState } = useApp();
  const choose = async () => {
    const dir = await open({ directory: true, multiple: false, title: "Choose a Git repository" });
    if (typeof dir === "string") await openProject(dir);
  };
  const needsSetup = project && !project.initialized;
  return (
    <div className="relative grid h-full place-items-center overflow-hidden px-6">
      <div className="accent-glow pointer-events-none absolute left-1/2 top-[18%] size-[420px] -translate-x-1/2 opacity-30" aria-hidden />
      <div className="relative w-full max-w-[460px]">
        <motion.div className="flex flex-col items-center text-center" initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={SOFT}>
          <AnimatedMark size={76} />
          <h1 className="mt-6 text-[30px] font-bold tracking-tight">Saarathi</h1>
          <p className="mt-1.5 text-[15px] text-muted">Many agents. One direction.</p>
        </motion.div>

        <motion.div className="mt-9" initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ ...SOFT, delay: 0.12 }}>
          {needsSetup ? (
            <div className="rounded-[var(--radius-card)] border border-line bg-panel p-5 elev-2">
              <div className="flex items-center gap-2 text-[15px] font-semibold"><ShieldCheck className="size-4 text-accent" />Set up {project.name}</div>
              <p className="mt-1.5 text-[13px] leading-relaxed text-muted">
                This creates <code className="font-mono text-[12px]">.saarathi/</code> with a cautious default policy. Nothing
                else in the repository changes.
              </p>
              <Button tone="primary" className="mt-4 h-10 w-full" loading={busy.project} onClick={() => void initProject()}>
                Set up Saarathi here <ArrowRight className="size-4" />
              </Button>
            </div>
          ) : (
            <Button tone="primary" className="h-11 w-full text-[14px]" disabled={bridgeState !== "ready"} loading={busy.project}
              onClick={() => void choose()}>
              <FolderOpen className="size-4" />Open a Git repository
            </Button>
          )}
        </motion.div>

        {recent.length > 0 && (
          <div className="mt-8">
            <div className="mb-2 text-[12px] font-medium text-faint">Recent</div>
            <ul className="space-y-1.5">
              {recent.map((r, i) => (
                <motion.li key={r} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ ...SOFT, delay: 0.2 + i * STAGGER }}>
                  <motion.button type="button" whileHover={{ x: 2 }} whileTap={{ scale: 0.98 }} onClick={() => void openProject(r)}
                    className="group flex w-full items-center gap-3 rounded-xl border border-transparent px-3 py-2.5 text-left hover:border-line hover:bg-panel">
                    <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-raised text-muted group-hover:text-accent">
                      <FolderGit2 className="size-4" />
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[14px] font-medium">{baseName(r)}</span>
                      <span className="block truncate text-[12px] text-faint" title={r}>{r}</span>
                    </span>
                    <ArrowRight className="size-4 text-faint opacity-0 transition-opacity group-hover:opacity-100" />
                  </motion.button>
                </motion.li>
              ))}
            </ul>
          </div>
        )}
        <div className="mt-10 text-center text-[12px] text-faint tabular">
          {bridgeState === "ready" && bridge ? <>Engine {bridge.saarathi_version} · Python {bridge.python}</> : "Starting the engine…"}
        </div>
      </div>
    </div>
  );
}
