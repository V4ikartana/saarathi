// Project-level pages. Usage and permissions are read-only views of engine records and the policy file; settings
// changes only per-viewer conveniences (theme, defaults for new sessions). The policy itself is edited in its file.
import { CheckCircle2, CircleAlert, Gauge, RefreshCw, TriangleAlert, XCircle } from "lucide-react";
import { motion } from "motion/react";
import { useEffect, useState, type ReactNode } from "react";

import { call } from "../lib/bridge";
import { type Accent, ACCENTS, loadDefaultPreferences, type Motion, saveDefaultPreferences, type Scale, type Theme } from "../lib/theme";
import type {
  ApprovalPreset,
  BridgeDiagnostics,
  DoctorReport,
  ModelInfo,
  PolicyView,
  ProviderLimits,
  SessionPreferences,
  UsageRow,
  UsageSummary,
} from "../lib/types";
import { cn, fmtDuration, fmtTokens, resetsIn, timeOf } from "../lib/ui";
import { useBridge, type Query } from "../lib/useBridge";
import { useApp } from "../store/app";
import { Button, Meter, Reveal, Segmented, StatePill, Switch } from "./primitives";

function PageShell({ title, subtitle, actions, children }: { title: string; subtitle?: ReactNode; actions?: ReactNode; children: ReactNode }) {
  return (
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto max-w-4xl px-8 py-8">
        <Reveal className="flex items-start gap-4">
          <div className="min-w-0 flex-1">
            <h1 className="text-[26px] font-bold tracking-tight">{title}</h1>
            {subtitle && <p className="mt-1.5 max-w-2xl text-[14px] leading-relaxed text-muted">{subtitle}</p>}
          </div>
          {actions}
        </Reveal>
        <div className="mt-8 space-y-6">{children}</div>
      </div>
    </div>
  );
}

function Card({ title, children, className, actions, delay = 0 }: { title?: string; children: ReactNode; className?: string; actions?: ReactNode; delay?: number }) {
  return (
    <Reveal delay={delay}>
      <section className={cn("rounded-2xl border border-line bg-panel shadow-sm shadow-black/5", className)}>
        {title && (
          <div className="flex items-center gap-3 border-b border-line px-5 py-3">
            <h2 className="flex-1 text-[13px] font-semibold">{title}</h2>
            {actions}
          </div>
        )}
        <div className="px-5 py-4">{children}</div>
      </section>
    </Reveal>
  );
}

function Loaded<T>({ query, children }: { query: Query<T>; children: (data: T) => ReactNode }) {
  if (query.data !== undefined) return <>{children(query.data)}</>;
  return <p className="text-[13px] text-faint">{query.error ? query.error.message : "Loading…"}</p>;
}

function Mono({ children }: { children: ReactNode }) {
  return <span className="font-mono text-[12px]">{children}</span>;
}

// ---------------------------------------------------------------- usage

function cost(row: UsageRow): string {
  if (row.cost_usd === null) return "not reported";
  return `${row.cost_is_estimate ? "≈ " : ""}$${row.cost_usd.toFixed(4)}`;
}

function Stat({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div>
      <div className="text-[11px] uppercase tracking-wide text-faint">{label}</div>
      <div className="mt-0.5 text-[18px] font-semibold tabular-nums">{value}</div>
      {note && <div className="text-[11px] text-faint">{note}</div>}
    </div>
  );
}

const TONE_TEXT = { ok: "text-ok", warn: "text-warn", err: "text-err" } as const;

function limitTone(remaining: number): "ok" | "warn" | "err" {
  return remaining <= 10 ? "err" : remaining <= 30 ? "warn" : "ok";
}

/** Remaining plan usage, read from each provider CLI's own usage command. */
function PlanLimits() {
  const project = useApp((s) => s.project);
  const [state, setState] = useState<{ limits?: ProviderLimits[]; error?: string; loading: boolean }>({ loading: true });
  const [tick, setTick] = useState(0);
  const load = async (refresh: boolean) => {
    if (!project) return;
    setState((s) => ({ ...s, loading: true, error: undefined }));
    try {
      const r = await call<{ limits: ProviderLimits[]; pending: boolean }>("usage.limits", { project: project.root, refresh });
      setState({ limits: r.limits.length || !r.pending ? r.limits : undefined, loading: r.pending });
      if (r.pending) window.setTimeout(() => setTick((t) => t + 1), 1500); // the bridge reads in the background
    } catch (err) {
      setState((s) => ({ ...s, loading: false, error: err instanceof Error ? err.message : String(err) }));
    }
  };
  useEffect(() => {
    void load(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project?.root, tick]);
  return (
    <Card title="Plan limits" actions={
      <Button tone="ghost" disabled={state.loading} onClick={() => void load(true)}>
        <RefreshCw className={cn("size-3.5", state.loading && "animate-spin")} />{state.loading ? "Reading…" : "Refresh"}
      </Button>
    }>
      {state.error && <p className="text-[13px] text-err">{state.error}</p>}
      {!state.limits && !state.error && <p className="text-[13px] text-faint">Asking each provider CLI… (Antigravity takes about 10 seconds)</p>}
      <div className="grid gap-5 md:grid-cols-2">
        {(state.limits ?? []).map((p, i) => (
          <Reveal key={p.adapter} delay={i * 0.05}>
            <div className="rounded-xl bg-raised/50 px-4 py-3">
              <div className="flex items-center gap-2 text-[13px]">
                <Gauge className="size-4 text-muted" />
                <Mono>{p.adapter}</Mono>
                {p.plan && <span className="rounded-full bg-accent/15 px-2 py-0.5 text-[11px] font-medium capitalize text-accent">{p.plan}</span>}
              </div>
              {p.error ? (
                <p className="mt-2 flex gap-1.5 text-[12px] text-warn"><TriangleAlert className="mt-0.5 size-3.5 shrink-0" />Not available: {p.error}</p>
              ) : (
                <ul className="mt-3 space-y-3">
                  {p.windows.map((w) => (
                    <li key={w.label}>
                      <div className="mb-1.5 flex items-baseline gap-2 text-[12px]">
                        <span className="min-w-0 flex-1 truncate">{w.label}</span>
                        <span className={cn("font-semibold tabular-nums", TONE_TEXT[limitTone(w.remaining_percent)])}>{Math.round(w.remaining_percent)}% left</span>
                      </div>
                      <Meter value={w.remaining_percent} tone={limitTone(w.remaining_percent)} />
                      <div className="mt-1 text-[11px] text-faint">
                        resets {w.resets_at ? `${resetsIn(w.resets_at)} · ${new Date(w.resets_at).toLocaleString()}` : (w.resets_text ?? "time not reported")}
                      </div>
                    </li>
                  ))}
                </ul>
              )}
              <div className="mt-3 text-[11px] text-faint">via <span className="font-mono">{p.source}</span></div>
            </div>
          </Reveal>
        ))}
      </div>
    </Card>
  );
}

export function UsagePage() {
  const project = useApp((s) => s.project);
  const usage = useBridge<UsageSummary>("usage.summary", project ? { project: project.root } : null);
  return (
    <PageShell title="Usage" subtitle="How much of each plan is left, and the tokens and costs each provider reported for this project's runs.">
      <PlanLimits />
      <Loaded query={usage}>
        {(u) => (
          <>
            <div className="flex items-start gap-3 rounded-xl border border-line bg-raised/40 px-4 py-3 text-[13px] text-muted">
              <CircleAlert className="mt-0.5 size-4 shrink-0" />
              <span>{u.quota_note} Costs, where shown, are what the provider or adapter recorded; "≈" marks client-side estimates.</span>
            </div>
            <Card>
              <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
                <Stat label="Calls" value={String(u.totals.calls)} note={`${u.totals.succeeded} passed · ${u.totals.failed} failed`} />
                <Stat label="Input tokens" value={fmtTokens(u.totals.input_tokens)} note={u.totals.cached_input_tokens ? `${fmtTokens(u.totals.cached_input_tokens)} cached` : undefined} />
                <Stat label="Output tokens" value={fmtTokens(u.totals.output_tokens)} note={u.totals.unreported ? `${u.totals.unreported} calls reported none` : undefined} />
                <Stat label="Cost" value={cost(u.totals)} note={u.totals.cost_reported ? `${u.totals.cost_reported} calls reported a cost` : undefined} />
              </div>
            </Card>
            {u.limits.length > 0 && (
              <Card title="Provider limits in effect">
                <ul className="space-y-1 text-[13px]">
                  {u.limits.map((l) => (
                    <li key={l.adapter} className="text-warn"><Mono>{l.adapter}</Mono> {l.state.replace("_", " ")} until {timeOf(l.until)} · {l.reason}</li>
                  ))}
                </ul>
              </Card>
            )}
            <Card title="By model">
              {u.by_model.length === 0 ? <p className="text-[13px] text-faint">No calls yet.</p> : (
                <table className="w-full text-[12px]">
                  <thead className="text-left text-faint">
                    <tr><th className="pb-1 font-normal">Model</th><th className="pb-1 font-normal">Role</th>
                      <th className="pb-1 text-right font-normal">Calls</th><th className="pb-1 text-right font-normal">Input</th>
                      <th className="pb-1 text-right font-normal" title="Input the provider served from its prompt cache">Cached</th>
                      <th className="pb-1 text-right font-normal">Output</th><th className="pb-1 text-right font-normal">Cost</th>
                      <th className="pb-1 text-right font-normal">Time</th></tr>
                  </thead>
                  <tbody>
                    {u.by_model.map((m) => (
                      <tr key={`${m.role}-${m.candidate}`} className="border-t border-line/60">
                        <td className="py-1.5"><Mono>{m.candidate}</Mono></td>
                        <td className="py-1.5 text-muted">{m.role}</td>
                        <td className="py-1.5 text-right tabular-nums">{m.calls}<span className="text-faint"> ({m.succeeded}✓ {m.failed}✗)</span></td>
                        <td className="py-1.5 text-right tabular-nums">{m.unreported === m.calls ? <span className="text-faint">not reported</span> : fmtTokens(m.input_tokens)}</td>
                        <td className="py-1.5 text-right tabular-nums text-muted">{m.unreported === m.calls ? "" : fmtTokens(m.cached_input_tokens)}</td>
                        <td className="py-1.5 text-right tabular-nums">{m.unreported === m.calls ? <span className="text-faint">not reported</span> : fmtTokens(m.output_tokens)}</td>
                        <td className="py-1.5 text-right tabular-nums">{cost(m)}</td>
                        <td className="py-1.5 text-right tabular-nums text-muted">{m.role === "lead" ? "—" : fmtDuration(m.duration_s * 1000)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </Card>
            <Card title="Recent runs">
              {u.runs.length === 0 ? <p className="text-[13px] text-faint">No runs yet.</p> : (
                <ul className="divide-y divide-line/60">
                  {u.runs.map((r) => (
                    <li key={r.run_id} className="flex items-center gap-3 py-1.5 text-[12px]">
                      <StatePill state={r.state} />
                      <Mono>{r.run_id}</Mono>
                      <span className="text-faint">{new Date(r.created_at).toLocaleString()}</span>
                      <span className="ml-auto tabular-nums text-muted">
                        {r.calls} calls · in {fmtTokens(r.input_tokens)} · out {fmtTokens(r.output_tokens)} · {cost(r)}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </Card>
          </>
        )}
      </Loaded>
    </PageShell>
  );
}

// ---------------------------------------------------------------- permissions

function Flag({ on, label }: { on: boolean; label: string }) {
  return (
    <li className="flex items-center gap-2 text-[13px]">
      {on ? <CheckCircle2 className="size-4 text-warn" /> : <XCircle className="size-4 text-faint" />}
      <span className={on ? "" : "text-muted"}>{label}: {on ? "allowed" : "not allowed"}</span>
    </li>
  );
}

const PERMISSION_LABELS: Record<string, string> = {
  allow_network: "Network access for workers",
  allow_dependency_install: "Installing dependencies",
  allow_deployment: "Deployment",
};

export function PermissionsPage() {
  const project = useApp((s) => s.project);
  const policy = useBridge<PolicyView>("policy.get", project ? { project: project.root } : null, { live: false });
  return (
    <PageShell title="Permissions"
      subtitle={<>What Saarathi and its workers may do in this project. Read-only here: the policy owner edits <Mono>{policy.data?.path ?? ".saarathi/policies.yaml"}</Mono>, and changes take effect on the next request.</>}>
      <Loaded query={policy}>
        {(p) => (
          <>
            <Card title="Approvals">
              <p className="text-[13px] text-muted">Each session picks a preset. This project allows up to <b className="text-ink">{p.approvals.presets.find((x) => x.id === p.approvals.max_preset)?.label}</b>.</p>
              <ul className="mt-3 space-y-2">
                {p.approvals.presets.map((x) => (
                  <li key={x.id} className={cn("rounded-lg border px-3 py-2", x.allowed ? "border-line" : "border-line/50 opacity-60")}>
                    <div className="flex items-center gap-2 text-[13px] font-medium">{x.label}
                      {!x.allowed && <span className="text-[11px] font-normal text-faint">not allowed by approvals.max_preset</span>}</div>
                    <p className="mt-0.5 text-[12px] text-muted">{x.description}</p>
                  </li>
                ))}
              </ul>
              <div className="mt-3 text-[12px] text-muted">Always needs a person, under every preset:</div>
              <ul className="mt-1 list-disc pl-5 text-[12px] text-muted">{p.approvals.never_automatic.map((n) => <li key={n}>{n}</li>)}</ul>
            </Card>
            <Card title="Workers and their containment">
              <ul className="space-y-4">
                {p.workers.map((w) => (
                  <li key={w.adapter}>
                    <div className="flex items-center gap-2 text-[13px]">
                      <Mono>{w.adapter}</Mono><span className="text-faint">{w.provider}</span>
                      {!w.enabled && <span className="rounded bg-raised px-1.5 text-[11px] text-faint">disabled</span>}
                      <span className="ml-auto text-[11px] text-faint">declared models: {w.models.join(", ")}</span>
                    </div>
                    <p className="mt-1 text-[12px] text-muted">{w.isolation}</p>
                    {w.worker_command ? (
                      <details className="mt-1">
                        <summary className="cursor-pointer text-[12px] text-faint">Exact command for a worker task</summary>
                        <pre className="mt-1 overflow-x-auto rounded-md bg-raised px-3 py-2 font-mono text-[11px] whitespace-pre-wrap break-all text-muted">{w.worker_command}</pre>
                      </details>
                    ) : <p className="mt-1 text-[12px] text-faint">No external command (CLI not installed, or a test adapter).</p>}
                  </li>
                ))}
              </ul>
            </Card>
            <div className="grid gap-6 md:grid-cols-2">
              <Card title="Project permissions">
                <ul className="space-y-1.5">
                  {Object.entries(PERMISSION_LABELS).map(([k, label]) => <Flag key={k} on={Boolean(p.permissions[k])} label={label} />)}
                </ul>
                <p className="mt-2 text-[12px] text-muted">Saarathi never merges into your branches; finished work stays on its own branch.</p>
              </Card>
              <Card title="Routing">
                <dl className="space-y-1 text-[12px]">
                  <div><dt className="inline text-faint">Lead: </dt><dd className="inline"><Mono>{p.lead}</Mono>{p.lead_substitute ? <> (substitute <Mono>{p.lead_substitute}</Mono>)</> : " (no substitute: pauses if unavailable)"}</dd></div>
                  <div><dt className="inline text-faint">Providers: </dt><dd className="inline">{p.routing.allowed_providers.join(", ")}</dd></div>
                  <div><dt className="inline text-faint">Models: </dt><dd className="inline"><Mono>{p.routing.allowed_models.join("  ")}</Mono></dd></div>
                  {p.routing.denied_models.length > 0 && <div><dt className="inline text-faint">Denied: </dt><dd className="inline"><Mono>{p.routing.denied_models.join("  ")}</Mono></dd></div>}
                  <div><dt className="inline text-faint">Parallel workers: </dt><dd className="inline">{p.routing.max_parallel_workers}</dd></div>
                  <div><dt className="inline text-faint">Attempts per task: </dt><dd className="inline">{String(p.execution.max_attempts)}</dd></div>
                </dl>
              </Card>
            </div>
            <Card title="Integrations">
              <dl className="grid grid-cols-1 gap-x-6 gap-y-1 text-[12px] sm:grid-cols-2">
                {Object.entries(p.integrations).map(([k, v]) => (
                  <div key={k}><dt className="inline text-faint">{k.replaceAll("_", " ")}: </dt>
                    <dd className="inline font-mono">{Array.isArray(v) ? (v.length ? v.join(", ") : "none") : String(v)}</dd></div>
                ))}
              </dl>
            </Card>
          </>
        )}
      </Loaded>
    </PageShell>
  );
}

// ---------------------------------------------------------------- settings


function ModelsPanel({ root }: { root: string }) {
  const models = useBridge<ModelInfo[]>("models.list", root ? { project: root } : null, { live: false });
  const policy = useBridge<PolicyView>("policy.get", root ? { project: root } : null, { live: false });
  const [overrides, setOverrides] = useState<Record<string, boolean>>({});
  const [error, setError] = useState<string | null>(null);
  const toggle = async (candidate: string, enabled: boolean) => {
    setOverrides((o) => ({ ...o, [candidate]: !enabled }));
    setError(null);
    try {
      await call("models.set_enabled", { candidate, enabled });
    } catch (err) {
      setOverrides((o) => ({ ...o, [candidate]: enabled }));
      setError(err instanceof Error ? err.message : String(err));
    }
  };
  const groups = new Map<string, ModelInfo[]>();
  for (const m of models.data ?? []) groups.set(m.adapter, [...(groups.get(m.adapter) ?? []), m]);
  return (
    <Card title="Models" delay={0.04}>
      <p className="text-[13px] text-muted">
        Turn a model off and Saarathi never uses it, in any project: the router skips it, sessions cannot prefer or pin it,
        and it cannot lead. Tiers come from your policy, the provider, or Saarathi's model catalogue.
      </p>
      {error && <p className="mt-2 text-[13px] text-err">{error}</p>}
      {(policy.data?.unconfigured_clis ?? []).map((c) => (
        <div key={c.adapter} className="mt-3 rounded-xl border border-warn/40 bg-warn/10 px-4 py-3 text-[13px]">
          <div className="font-medium">{c.adapter} is installed but this project's policy does not include it.</div>
          <ul className="mt-1 list-disc pl-5 text-[12px] text-muted">{c.steps.map((step) => <li key={step}>{step}</li>)}</ul>
          <div className="mt-1 text-[12px] text-faint">Edit <span className="font-mono">{policy.data?.path}</span>; changes apply on the next request.</div>
        </div>
      ))}
      {!models.data && <p className="mt-3 text-[13px] text-faint">{models.error?.message ?? "Loading…"}</p>}
      <div className="mt-4 space-y-5">
        {[...groups.entries()].map(([adapter, list]) => (
          <div key={adapter}>
            <div className="mb-1.5 flex items-center gap-2 text-[12px] font-semibold uppercase tracking-wide text-faint">
              {adapter}<span className="font-normal normal-case tracking-normal">· {list[0]?.provider}</span>
            </div>
            <ul className="divide-y divide-line/60 rounded-xl border border-line">
              {list.map((m) => {
                const off = overrides[m.candidate] ?? m.disabled;
                return (
                  <motion.li key={m.candidate} layout className="flex items-center gap-3 px-3 py-2">
                    <div className="min-w-0 flex-1">
                      <div className={cn("flex items-center gap-2 text-[13px]", off && "text-faint line-through decoration-faint/60")}>
                        <Mono>{m.model}</Mono>
                        <TierPill tier={m.reasoning_tier} />
                        {m.source.startsWith("discovered") && <span className="text-[11px] text-faint">discovered</span>}
                      </div>
                      {!off && !m.available && m.unavailable_reason && (
                        <div className="mt-0.5 text-[11px] text-warn">{m.unavailable_reason}</div>
                      )}
                    </div>
                    <Switch label={`Use ${m.candidate}`} checked={!off} onChange={(on) => void toggle(m.candidate, on)} />
                  </motion.li>
                );
              })}
            </ul>
          </div>
        ))}
      </div>
    </Card>
  );
}

const TIER_STYLE: Record<string, string> = {
  frontier: "bg-accent/15 text-accent",
  balanced: "bg-ok/15 text-ok",
  economy: "bg-raised text-muted",
  unknown: "bg-raised text-faint",
};

function TierPill({ tier }: { tier: string }) {
  return <span className={cn("rounded-full px-2 py-0.5 text-[11px] font-medium", TIER_STYLE[tier] ?? TIER_STYLE.unknown)}>{tier}</span>;
}

function Row({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <div className="flex flex-wrap items-center gap-x-6 gap-y-2 py-2.5">
      <div className="min-w-48 flex-1">
        <div className="text-[13px] font-medium">{label}</div>
        {hint && <div className="text-[12px] text-faint">{hint}</div>}
      </div>
      <div className="min-w-64">{children}</div>
    </div>
  );
}

function AppearancePanel() {
  const { appearance, setAppearance } = useApp();
  return (
    <Card title="Appearance">
      <div className="divide-y divide-line/60">
        <Row label="Theme" hint="System follows macOS light and dark mode.">
          <Segmented label="Theme" value={appearance.theme} onChange={(theme: Theme) => setAppearance({ theme })}
            options={[{ value: "system", label: "System" }, { value: "light", label: "Light" }, { value: "dark", label: "Dark" }]} />
        </Row>
        <Row label="Accent colour">
          <div className="flex gap-2.5" role="radiogroup" aria-label="Accent colour">
            {(Object.keys(ACCENTS) as Accent[]).map((a) => (
              <motion.button key={a} type="button" role="radio" aria-checked={appearance.accent === a} aria-label={ACCENTS[a].label}
                title={ACCENTS[a].label} whileTap={{ scale: 0.85 }} whileHover={{ scale: 1.1 }}
                onClick={() => setAppearance({ accent: a })}
                className={cn("size-7 rounded-full ring-offset-2 ring-offset-panel", appearance.accent === a && "ring-2 ring-ink")}
                style={{ background: ACCENTS[a].dark[0] }} />
            ))}
          </div>
        </Row>
        <Row label="Interface size" hint="Scales text and spacing everywhere.">
          <Segmented label="Interface size" value={appearance.scale} onChange={(scale: Scale) => setAppearance({ scale })}
            options={[{ value: 0.9, label: "Small" }, { value: 1, label: "Default" }, { value: 1.1, label: "Large" }, { value: 1.2, label: "Larger" }]} />
        </Row>
        <Row label="Motion" hint="Reduced turns off animations; System follows macOS accessibility settings.">
          <Segmented label="Motion" value={appearance.motion} onChange={(motion: Motion) => setAppearance({ motion })}
            options={[{ value: "system", label: "System" }, { value: "full", label: "Full" }, { value: "reduced", label: "Reduced" }]} />
        </Row>
        <Row label="Execution panel" hint="The run and task list beside each session.">
          <Switch label="Show the execution panel" checked={appearance.showExecution} onChange={(showExecution) => setAppearance({ showExecution })} />
        </Row>
      </div>
    </Card>
  );
}

export function SettingsPage() {
  const { project, bridge } = useApp();
  const root = project?.root ?? "";
  const policy = useBridge<PolicyView>("policy.get", root ? { project: root } : null, { live: false });
  const [doctor, setDoctor] = useState<{ data?: DoctorReport; error?: string; loading: boolean }>({ loading: false });
  const [diag, setDiag] = useState<BridgeDiagnostics | null>(null);
  const [defaults, setDefaults] = useState<SessionPreferences>(loadDefaultPreferences);

  const runDoctor = async () => {
    setDoctor({ loading: true });
    try {
      setDoctor({ data: await call<DoctorReport>("system.doctor", { project: root }), loading: false });
    } catch (err) {
      setDoctor({ error: err instanceof Error ? err.message : String(err), loading: false });
    }
  };
  useEffect(() => {
    void import("@tauri-apps/api/core")
      .then(({ invoke }) => invoke<BridgeDiagnostics>("bridge_diagnostics"))
      .then(setDiag)
      .catch(() => setDiag(null));
  }, []);
  const updateDefaults = (next: Partial<SessionPreferences>) => {
    const merged = { ...defaults, ...next };
    setDefaults(merged);
    saveDefaultPreferences(merged);
  };

  return (
    <PageShell title="Settings" subtitle="Make Saarathi yours. Appearance and defaults stay on this computer; turned-off models apply to every project. Project rules live in the policy file (see Permissions).">
      <AppearancePanel />
      <ModelsPanel root={root} />
      <Card title="Defaults for new sessions" delay={0.08}>
        <Row label="Approvals" hint={'If a project’s policy refuses the default, the session keeps "Ask before actions".'}>
          <select className="h-8 w-full rounded-lg border border-line bg-bg px-2 text-[13px]" value={defaults.approvals}
            onChange={(e) => updateDefaults({ approvals: e.target.value as ApprovalPreset })}>
            {(policy.data?.approvals.presets ?? []).map((x) => (
              <option key={x.id} value={x.id} disabled={!x.allowed}>{x.label}{x.allowed ? "" : " (not allowed in this project)"}</option>
            ))}
          </select>
        </Row>
      </Card>
      <Card title="Providers" delay={0.12} actions={
        <Button disabled={!root || doctor.loading} onClick={() => void runDoctor()}><RefreshCw className={cn("size-3.5", doctor.loading && "animate-spin")} />Check providers</Button>
      }>
        <p className="text-[13px] text-muted">Checks each provider CLI: installed, version, sign-in method and whether it is metered. Nothing is sent to a model.</p>
        {doctor.error && <p className="mt-2 text-[13px] text-err">{doctor.error}</p>}
        {doctor.data && (
          <ul className="mt-3 space-y-3">
            {doctor.data.adapters.map((a, i) => (
              <Reveal key={a.name} delay={i * 0.04}>
                <li className="rounded-xl border border-line px-3 py-2">
                  <div className="flex items-center gap-2 text-[13px]">
                    {a.available ? <CheckCircle2 className="size-4 text-ok" /> : <XCircle className="size-4 text-err" />}
                    <Mono>{a.name}</Mono><span className="text-faint">{a.provider}</span>
                    {a.test_only && <span className="rounded bg-raised px-1.5 text-[11px] text-faint">test only</span>}
                    <span className="ml-auto text-[12px] text-muted">{a.version ?? ""}{a.auth_method ? ` · ${a.auth_method}` : ""}{a.metered === true ? " · metered (blocked)" : ""}</span>
                  </div>
                  {a.reason && <p className="mt-1 text-[12px] text-warn">{a.reason}</p>}
                  {a.notices.map((n) => <p key={n} className="mt-1 text-[12px] text-muted">{n}</p>)}
                </li>
              </Reveal>
            ))}
            <li className="text-[12px] text-muted">Lead: {doctor.data.lead ? <Mono>{doctor.data.lead}</Mono> : <span className="text-err">unavailable: {doctor.data.lead_problems.join("; ")}</span>}</li>
          </ul>
        )}
      </Card>
      <Card title="Engine" delay={0.16}>
        <dl className="space-y-1 text-[12px]">
          <div><dt className="inline text-faint">Saarathi: </dt><dd className="inline">{bridge ? `${bridge.saarathi_version} (protocol ${bridge.protocol}, Python ${bridge.python})` : "not connected"}</dd></div>
          <div><dt className="inline text-faint">Data directory: </dt><dd className="inline"><Mono>{bridge?.data_dir ?? "—"}</Mono></dd></div>
          <div><dt className="inline text-faint">Bridge command: </dt><dd className="inline"><Mono>{diag?.command ?? "—"}</Mono></dd></div>
          <div className="text-faint">Set <Mono>SAARATHI_BRIDGE</Mono> to use a specific <Mono>saarathi</Mono>; otherwise the app finds it on your login shell's PATH.</div>
        </dl>
      </Card>
    </PageShell>
  );
}
