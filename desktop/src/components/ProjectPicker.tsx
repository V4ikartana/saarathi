import { open } from "@tauri-apps/plugin-dialog";
import { FolderOpen } from "lucide-react";

import { useApp } from "../store/app";
import { Button, Mark } from "./primitives";

export function ProjectPicker() {
  const { project, recent, openProject, initProject, busy, bridge, bridgeState } = useApp();
  const choose = async () => {
    const dir = await open({ directory: true, multiple: false, title: "Choose a Git repository" });
    if (typeof dir === "string") await openProject(dir);
  };
  return (
    <div className="grid h-full place-items-center">
      <div className="w-[420px]">
        <div className="mb-6 flex items-center gap-3">
          <Mark size={40} />
          <div>
            <div className="text-[18px] font-semibold">Saarathi</div>
            <div className="text-[12px] text-muted">Many agents. One direction.</div>
          </div>
        </div>
        {project && !project.initialized ? (
          <div className="rounded-xl border border-line bg-panel p-4">
            <div className="text-[14px]">{project.name} isn't set up for Saarathi yet.</div>
            <p className="mt-1 text-[12px] text-muted">This creates <code>.saarathi/</code> with a conservative default policy. Nothing else changes.</p>
            <Button tone="primary" className="mt-3" disabled={busy.project} onClick={() => void initProject()}>Set up Saarathi here</Button>
          </div>
        ) : (
          <Button tone="primary" className="w-full justify-center h-10" disabled={bridgeState !== "ready" || busy.project}
            onClick={() => void choose()}><FolderOpen className="size-4" />Open a Git repository</Button>
        )}
        {recent.length > 0 && (
          <div className="mt-6">
            <div className="text-[11px] font-medium uppercase tracking-wide text-faint">Recent</div>
            <ul className="mt-2">
              {recent.map((r) => (
                <li key={r}>
                  <button type="button" onClick={() => void openProject(r)}
                    className="w-full truncate rounded-md px-2 py-1.5 text-left text-[13px] text-muted hover:bg-raised hover:text-ink">{r}</button>
                </li>
              ))}
            </ul>
          </div>
        )}
        <div className="mt-8 text-[11px] text-faint">
          {bridgeState === "ready" && bridge ? <>Engine {bridge.saarathi_version} · protocol {bridge.protocol}</>
            : bridgeState === "starting" ? "Starting the engine…" : "Engine unavailable"}
        </div>
      </div>
    </div>
  );
}
