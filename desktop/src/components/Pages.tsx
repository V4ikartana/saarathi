// Project-level pages. Usage and permissions are read-only views of engine records and the policy file; settings
// changes only per-viewer conveniences (theme, defaults for new sessions). The policy itself is edited in its file.
import { CheckCircle2, CircleAlert, Compass, Gauge, Pencil, RefreshCw, TriangleAlert, XCircle } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useEffect, useState, type ReactNode } from "react";

import { BridgeError, call } from "../lib/bridge";
import { type Accent, ACCENTS, loadDefaultPreferences, type Motion, saveDefaultPreferences, type Scale, type Theme } from "../lib/theme";
import type {
  ApprovalPreset,
  BridgeDiagnostics,
  DoctorReport,
  ModelInfo,
  PolicyPreview,
  PolicyView,
  ProviderLimits,
  SessionPreferences,
  UsageRow,
  UsageSummary,
} from "../lib/types";
import { cn, fmtDuration, fmtTokens, resetsIn, timeOf } from "../lib/ui";
import { useBridge, type Query } from "../lib/useBridge";
import { useApp } from "../store/app";
import { AnimatedNumber, Skeleton, SkeletonCard, SkeletonLines, ThinkingDots } from "./feedback";
import { Button, EXIT, Meter, Reveal, Segmented, SPRING, StatePill, Switch } from "./primitives";

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

function Loaded<T>({ query, children, skeleton = 2 }: { query: Query<T>; children: (data: T) => ReactNode; skeleton?: number }) {
  if (query.data !== undefined) return <>{children(query.data)}</>;
  if (query.error) return <p className="text-[13px] text-err">{query.error.message}</p>;
  return <div className="space-y-6">{Array.from({ length: skeleton }, (_, i) => <SkeletonCard key={i} lines={i === 0 ? 2 : 4} />)}</div>;
}

function Mono({ children }: { children: ReactNode }) {
  return <span className="font-mono text-[12px]">{children}</span>;
}

// ---------------------------------------------------------------- usage

function cost(row: UsageRow): string {
  if (row.cost_usd === null) return "not reported";
  return `${row.cost_is_estimate ? "≈ " : ""}$${row.cost_usd.toFixed(4)}`;
}

function Stat({ label, value, note }: { label: string; value: ReactNode; note?: string }) {
  return (
    <div>
      <div className="text-[12px] text-faint">{label}</div>
      <div className="mt-1 text-[28px] font-bold leading-none tracking-tight tabular-nums">{value}</div>
      {note && <div className="mt-1.5 text-[12px] text-faint">{note}</div>}
    </div>
  );
}

const compact = (n: number) => (n >= 10_000 ? `${(n / 1000).toFixed(1)}k` : Math.round(n).toLocaleString());

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
      <Button tone="ghost" disabled={state.loading} onClick={() => void load(true)} aria-label="Refresh plan limits">
        <RefreshCw className={cn("size-3.5", state.loading && "animate-spin")} />{state.loading ? "Reading" : "Refresh"}
      </Button>
    }>
      {state.error && <p className="text-[13px] text-err">{state.error}</p>}
      {!state.limits && !state.error && (
        <div className="grid gap-5 md:grid-cols-2" role="status" aria-busy="true" aria-label="Reading plan limits">
          {[0, 1].map((i) => (
            <div key={i} className="rounded-xl bg-raised/50 px-4 py-3">
              <Skeleton className="mb-4 h-4 w-1/3" />
              <Skeleton className="mb-2 w-2/3" /><Skeleton className="mb-4 h-1.5 w-full rounded-full" />
              <Skeleton className="mb-2 w-1/2" /><Skeleton className="h-1.5 w-full rounded-full" />
            </div>
          ))}
          <p className="col-span-full flex items-center gap-2 text-[12px] text-faint"><ThinkingDots className="text-accent" />Asking each provider CLI. Antigravity takes about ten seconds.</p>
        </div>
      )}
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
                <Stat label="Calls" value={<AnimatedNumber value={u.totals.calls} />} note={`${u.totals.succeeded} passed · ${u.totals.failed} failed`} />
                <Stat label="Input tokens" value={<AnimatedNumber value={u.totals.input_tokens} format={compact} />} note={u.totals.cached_input_tokens ? `${fmtTokens(u.totals.cached_input_tokens)} cached` : undefined} />
                <Stat label="Output tokens" value={<AnimatedNumber value={u.totals.output_tokens} format={compact} />} note={u.totals.unreported ? `${u.totals.unreported} calls reported none` : undefined} />
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

// ---------------------------------------------------------------- policy

type Values = PolicyView["editable"];

const PRESET_LABEL: Record<string, string> = { ask: "Ask before actions", auto_safe: "Auto-approve safe actions", bypass: "Bypass supported approvals" };
const MODES = [
  { value: "deny", label: "Never" },
  { value: "require_approval", label: "Ask me" },
  { value: "allow", label: "Allow" },
];

function NumberField({ value, onChange, min, max, label, suffix }: { value: number; onChange: (v: number) => void; min: number; max: number; label: string; suffix?: string }) {
  return (
    <label className="flex items-center gap-2">
      <input type="number" aria-label={label} value={value} min={min} max={max}
        onChange={(e) => onChange(Math.max(min, Math.min(max, Number(e.target.value) || min)))}
        className="h-8 w-24 rounded-[var(--radius-control)] border border-line bg-bg px-2 text-[13px] tabular outline-none focus:border-accent/60" />
      {suffix && <span className="text-[12px] text-faint">{suffix}</span>}
    </label>
  );
}

function Select({ value, onChange, label, options }: { value: string; onChange: (v: string) => void; label: string; options: { value: string; label: string; disabled?: boolean }[] }) {
  return (
    <select aria-label={label} value={value} onChange={(e) => onChange(e.target.value)}
      className="h-8 w-full max-w-sm rounded-[var(--radius-control)] border border-line bg-bg px-2 text-[13px] outline-none focus:border-accent/60">
      {options.map((o) => <option key={o.value} value={o.value} disabled={o.disabled}>{o.label}</option>)}
    </select>
  );
}

function ReviewDialog({ preview, saving, onCancel, onConfirm }: { preview: PolicyPreview; saving: boolean; onCancel: () => void; onConfirm: () => void }) {
  const [understood, setUnderstood] = useState(false);
  const risky = preview.warnings.length > 0;
  return (
    <motion.div className="fixed inset-0 z-[55] grid place-items-center bg-black/55 px-4 backdrop-blur-[2px]" initial={{ opacity: 0 }}
      animate={{ opacity: 1 }} exit={{ opacity: 0, transition: EXIT }} onMouseDown={onCancel}>
      <motion.div role="alertdialog" aria-modal="true" aria-labelledby="policy-review-title" onMouseDown={(e) => e.stopPropagation()}
        initial={{ opacity: 0, y: 12, scale: 0.97 }} animate={{ opacity: 1, y: 0, scale: 1 }} exit={{ opacity: 0, scale: 0.98, transition: EXIT }}
        transition={SPRING} className="w-full max-w-lg rounded-[var(--radius-card)] border border-line-strong bg-panel p-6 elev-3">
        <div className="flex items-center gap-2.5">
          <span className={cn("grid size-9 place-items-center rounded-full", risky ? "bg-warn/15 text-warn" : "bg-accent/15 text-accent")}>
            <TriangleAlert className="size-4.5" aria-hidden />
          </span>
          <h2 id="policy-review-title" className="text-[17px] font-semibold">Change this project's policy?</h2>
        </div>
        <p className="mt-3 text-[13px] leading-relaxed text-muted">
          The policy sets what AI agents may do in this repository. Saarathi checks the new policy before saving, keeps a
          backup of the current file, and records the change. Runs already in progress keep the policy they started with.
        </p>
        <div className="mt-4 rounded-xl bg-raised/60 px-4 py-3">
          <div className="text-[12px] font-medium text-faint">Changes</div>
          <ul className="mt-1.5 space-y-1 font-mono text-[12px]">{preview.summary.map((line) => <li key={line} className="break-anywhere">{line}</li>)}</ul>
        </div>
        {risky && (
          <div className="mt-3 rounded-xl border border-warn/40 bg-warn/10 px-4 py-3 text-[13px]">
            <div className="font-medium text-warn">This loosens what agents may do</div>
            <ul className="mt-1.5 list-disc space-y-0.5 pl-5">{preview.warnings.map((w) => <li key={w}>{w}</li>)}</ul>
            <label className="mt-3 flex items-center gap-2 text-[13px]">
              <input type="checkbox" checked={understood} onChange={(e) => setUnderstood(e.target.checked)} className="size-4 accent-[var(--color-accent)]" />
              I understand and want to apply these changes
            </label>
          </div>
        )}
        <div className="mt-5 flex justify-end gap-2">
          <Button tone="ghost" onClick={onCancel}>Keep the current policy</Button>
          <Button tone="primary" disabled={risky && !understood} loading={saving} onClick={onConfirm}>Save policy</Button>
        </div>
      </motion.div>
    </motion.div>
  );
}

export function PermissionsPage() {
  const { project, toast } = useApp();
  const root = project?.root ?? "";
  const [version, setVersion] = useState(0);
  const policy = useBridge<PolicyView>("policy.get", root ? { project: root } : null, { live: false, version: String(version) });
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Values>({});
  const [preview, setPreview] = useState<PolicyPreview | null>(null);
  const [checking, setChecking] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const p = policy.data;
  const current = p?.editable ?? {};
  const value = (key: string) => (key in draft ? draft[key] : current[key]);
  const set = (key: string, v: Values[string]) => setDraft((d) => ({ ...d, [key]: v }));
  const changes = Object.fromEntries(Object.entries(draft).filter(([k, v]) => JSON.stringify(v) !== JSON.stringify(current[k])));
  const dirty = Object.keys(changes).length > 0;

  const review = async () => {
    setChecking(true);
    setError(null);
    try {
      setPreview(await call<PolicyPreview>("policy.update", { project: root, changes, dry_run: true }));
    } catch (err) {
      setError(err instanceof BridgeError ? `${err.message}${err.hint ? `. ${err.hint}` : ""}` : String(err));
    } finally {
      setChecking(false);
    }
  };
  const save = async () => {
    setSaving(true);
    try {
      const done = await call<PolicyPreview>("policy.update", { project: root, changes, dry_run: false });
      toast("Policy saved", "success", done.backup ? "The previous policy was backed up." : undefined);
      setPreview(null);
      setEditing(false);
      setDraft({});
      setVersion((v) => v + 1);
    } catch (err) {
      setPreview(null);
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };
  const addAdapter = (adapter: string) => set("providers.add", adapter);

  const actions = !p ? null : editing ? (
    <div className="flex gap-2">
      <Button tone="ghost" onClick={() => { setEditing(false); setDraft({}); setError(null); }}>Cancel</Button>
      <Button tone="primary" disabled={!dirty} loading={checking} onClick={() => void review()}>Review changes</Button>
    </div>
  ) : (
    <Button onClick={() => setEditing(true)}><Pencil className="size-3.5" />Edit policy</Button>
  );

  return (
    <PageShell title="Policy" actions={actions}
      subtitle={<>The rules for this project: who leads, which providers and models may work, and what agents may do. Saved in <Mono>{p?.path ?? ".saarathi/policies.yaml"}</Mono>; edit here or in the file.</>}>
      <Loaded query={policy}>
        {(p) => {
          const lead = String(value("orchestration.lead") ?? "");
          const substitute = (value("orchestration.lead_substitute") as string | null) ?? "";
          const candidates = p.lead_candidates.map((c) => ({
            value: c.candidate,
            label: `${c.candidate} · ${c.tier}${c.available ? "" : ` (unavailable: ${c.reason ?? "unknown"})`}`,
          }));
          return (
            <>
              {error && <div role="alert" className="rounded-xl border border-err/40 bg-err/10 px-4 py-3 text-[13px] text-err">{error}</div>}

              <Card title="Engineering lead">
                <p className="text-[13px] leading-relaxed text-muted">
                  The lead reads each request, asks clarifying questions, writes the plan, rules on escalations and reviews
                  the finished work. It is pinned so it never changes silently between runs. If it is unavailable, runs
                  pause, unless you set a substitute.
                </p>
                <div className="mt-2 divide-y divide-line/60">
                  <Row label="Lead" hint="Plans and reviews every run.">
                    {editing ? <Select label="Engineering lead" value={lead} onChange={(v) => set("orchestration.lead", v)} options={candidates} />
                      : <span className="flex items-center gap-2 text-[14px] font-medium"><Mono>{lead}</Mono></span>}
                  </Row>
                  <Row label="Substitute" hint="Used only when the lead is unavailable.">
                    {editing ? (
                      <Select label="Lead substitute" value={substitute} onChange={(v) => set("orchestration.lead_substitute", v || null)}
                        options={[{ value: "", label: "None: pause instead" }, ...candidates]} />
                    ) : <span className="text-[13px] text-muted">{substitute ? <Mono>{substitute}</Mono> : "None: runs pause instead"}</span>}
                  </Row>
                </div>
              </Card>

              <Card title="Approvals" delay={0.03}>
                <p className="text-[13px] text-muted">The most any session in this project may delegate.</p>
                <div className="mt-3">
                  {editing ? (
                    <Segmented label="Most a session may delegate" value={String(value("approvals.max_preset"))} onChange={(v) => set("approvals.max_preset", v)}
                      options={p.approvals.presets.map((x) => ({ value: x.id, label: PRESET_LABEL[x.id] ?? x.label }))} />
                  ) : (
                    <ul className="space-y-2">
                      {p.approvals.presets.map((x) => (
                        <li key={x.id} className={cn("rounded-xl border px-3 py-2", x.allowed ? "border-line" : "border-line/50 opacity-55")}>
                          <div className="flex items-center gap-2 text-[13px] font-medium">{x.label}
                            {!x.allowed && <span className="text-[11px] font-normal text-faint">not allowed</span>}</div>
                          <p className="mt-0.5 text-[12px] text-muted">{x.description}</p>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
                <div className="mt-3 text-[12px] text-muted">Always needs a person, under every preset:</div>
                <ul className="mt-1 list-disc pl-5 text-[12px] text-muted">{p.approvals.never_automatic.map((n) => <li key={n}>{n}</li>)}</ul>
              </Card>

              <Card title="Providers and routing" delay={0.06}>
                <div className="divide-y divide-line/60">
                  {p.workers.map((w) => {
                    const key = `providers.${w.adapter}.enabled`;
                    const on = Boolean(value(key));
                    return (
                      <Row key={w.adapter} label={w.adapter} hint={`${w.provider} · declared: ${w.models.join(", ")}`}>
                        {editing ? <Switch label={`Use ${w.adapter} in this project`} checked={on} onChange={(v) => set(key, v)} />
                          : <span className={cn("text-[13px]", on ? "text-ok" : "text-faint")}>{on ? "Enabled" : "Disabled"}</span>}
                      </Row>
                    );
                  })}
                  {editing && p.addable_adapters.map((a) => (
                    <Row key={a} label={a} hint={a === "local-openai" ? "Open-source models from Ollama or LM Studio on this computer." : "Installed on this computer, not in this project yet."}>
                      {value("providers.add") === a
                        ? <span className="text-[13px] text-accent">Will be added</span>
                        : <Button onClick={() => addAdapter(a)}>Add to this project</Button>}
                    </Row>
                  ))}
                  <Row label="Parallel workers" hint="Tasks that may run at the same time.">
                    {editing ? <NumberField label="Parallel workers" min={1} max={16} value={Number(value("routing.max_parallel_workers"))} onChange={(v) => set("routing.max_parallel_workers", v)} />
                      : <span className="text-[13px] tabular">{String(current["routing.max_parallel_workers"])}</span>}
                  </Row>
                  <Row label="Avoid nearly exhausted plans" hint="Skip a model while its plan has less than this left. 0 turns it off.">
                    {editing ? <NumberField label="Minimum plan remaining" min={0} max={50} suffix="%" value={Number(value("routing.min_remaining_percent"))} onChange={(v) => set("routing.min_remaining_percent", v)} />
                      : <span className="text-[13px] tabular">{String(current["routing.min_remaining_percent"])}%</span>}
                  </Row>
                  <Row label="Spread across providers" hint="Prefer an idle provider when several can do the task.">
                    {editing ? <Switch label="Spread across providers" checked={Boolean(value("routing.spread_across_providers"))} onChange={(v) => set("routing.spread_across_providers", v)} />
                      : <span className="text-[13px]">{current["routing.spread_across_providers"] ? "On" : "Off"}</span>}
                  </Row>
                </div>
              </Card>

              <Card title="What workers may do" delay={0.09}>
                <div className="divide-y divide-line/60">
                  {([["permissions.allow_network", "Network access", "Workers may reach the internet."],
                    ["permissions.allow_dependency_install", "Install dependencies", "Workers may install packages."]] as const).map(([key, label, hint]) => (
                    <Row key={key} label={label} hint={hint}>
                      {editing ? <Switch label={label} checked={Boolean(value(key))} onChange={(v) => set(key, v)} />
                        : <span className={cn("text-[13px]", current[key] ? "text-warn" : "text-muted")}>{current[key] ? "Allowed" : "Not allowed"}</span>}
                    </Row>
                  ))}
                  <Row label="Attempts per task" hint="Retries before a task is left for you.">
                    {editing ? <NumberField label="Attempts per task" min={1} max={5} value={Number(value("execution.max_attempts"))} onChange={(v) => set("execution.max_attempts", v)} />
                      : <span className="text-[13px] tabular">{String(current["execution.max_attempts"])}</span>}
                  </Row>
                  <Row label="Task time limit" hint="Longest a worker may run on one attempt.">
                    {editing ? <NumberField label="Task time limit in minutes" min={1} max={1440} suffix="min" value={Math.round(Number(value("execution.task_timeout_seconds")) / 60)} onChange={(v) => set("execution.task_timeout_seconds", v * 60)} />
                      : <span className="text-[13px] tabular">{Math.round(Number(current["execution.task_timeout_seconds"]) / 60)} min</span>}
                  </Row>
                  <p className="py-2.5 text-[12px] text-muted">Saarathi never merges into your branches; finished work stays on its own branch.</p>
                </div>
              </Card>

              <Card title="Integrations" delay={0.12}>
                <div className="divide-y divide-line/60">
                  <Row label="Sending, deleting or sharing" hint="External side effects through connected accounts.">
                    {editing ? <Select label="External side effects" value={String(value("integrations.external_side_effects"))} onChange={(v) => set("integrations.external_side_effects", v)} options={MODES.slice(0, 2)} />
                      : <span className="text-[13px]">{MODES.find((m) => m.value === current["integrations.external_side_effects"])?.label}</span>}
                  </Row>
                  <Row label="Moving results between providers">
                    {editing ? <Select label="Cross-provider data transfer" value={String(value("integrations.cross_provider_data_transfer"))} onChange={(v) => set("integrations.cross_provider_data_transfer", v)} options={MODES} />
                      : <span className="text-[13px]">{MODES.find((m) => m.value === current["integrations.cross_provider_data_transfer"])?.label}</span>}
                  </Row>
                  <Row label="Accounts you declare" hint="Identities no provider has verified.">
                    {editing ? <Select label="User-declared identity" value={String(value("integrations.user_declared_identity"))} onChange={(v) => set("integrations.user_declared_identity", v)} options={MODES} />
                      : <span className="text-[13px]">{MODES.find((m) => m.value === current["integrations.user_declared_identity"])?.label}</span>}
                  </Row>
                </div>
              </Card>

              <Card title="Workers and their containment" delay={0.15}>
                <ul className="space-y-4">
                  {p.workers.map((w) => (
                    <li key={w.adapter}>
                      <div className="flex items-center gap-2 text-[13px]"><Mono>{w.adapter}</Mono><span className="text-faint">{w.provider}</span></div>
                      <p className="mt-1 text-[12px] text-muted">{w.isolation}</p>
                      {w.worker_command && (
                        <details className="mt-1">
                          <summary className="text-[12px] text-faint">Exact command for a worker task</summary>
                          <pre className="mt-1 overflow-x-auto rounded-xl bg-raised px-3 py-2 font-mono text-[11px] whitespace-pre-wrap break-all text-muted">{w.worker_command}</pre>
                        </details>
                      )}
                    </li>
                  ))}
                </ul>
              </Card>
            </>
          );
        }}
      </Loaded>
      <AnimatePresence>
        {preview && <ReviewDialog key="review" preview={preview} saving={saving} onCancel={() => setPreview(null)} onConfirm={() => void save()} />}
      </AnimatePresence>
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
  // A tier the person picked here: "default" until the list reloads after a reset.
  const [tiers, setTiers] = useState<Record<string, string>>({});
  const chooseTier = async (m: ModelInfo, value: string) => {
    const before = tiers[m.candidate];
    setTiers((t) => ({ ...t, [m.candidate]: value }));
    setError(null);
    try {
      await call("models.set_tier", { candidate: m.candidate, tier: value === "default" ? null : value });
    } catch (err) {
      setTiers((t) => {
        const next = { ...t };
        if (before === undefined) delete next[m.candidate];
        else next[m.candidate] = before;
        return next;
      });
      setError(err instanceof Error ? err.message : String(err));
    }
  };
  const groups = new Map<string, ModelInfo[]>();
  for (const m of models.data ?? []) groups.set(m.adapter, [...(groups.get(m.adapter) ?? []), m]);
  return (
    <Card title="Models" delay={0.04}>
      <p className="text-[13px] text-muted">
        Turn a model off and Saarathi never uses it, in any project: the router skips it, sessions cannot prefer or pin it,
        and it cannot lead. Tiers come from your policy, the provider, or Saarathi's model catalogue. Change a model's
        tier to decide how Saarathi treats it everywhere: for example, make a local model you trust frontier.
      </p>
      {error && <p className="mt-2 text-[13px] text-err">{error}</p>}
      {(policy.data?.unconfigured_clis ?? []).map((c) => (
        <div key={c.adapter} className="mt-3 rounded-xl border border-warn/40 bg-warn/10 px-4 py-3 text-[13px]">
          <div className="font-medium">{c.adapter === "local-openai" ? `A local model server is running at ${c.binary}` : `${c.adapter} is installed`} but this project's policy does not include it.</div>
          <ul className="mt-1 list-disc pl-5 text-[12px] text-muted">{c.steps.map((step) => <li key={step}>{step}</li>)}</ul>
          <div className="mt-1 text-[12px] text-faint">Edit <span className="font-mono">{policy.data?.path}</span>; changes apply on the next request.</div>
        </div>
      ))}
      {!models.data && (models.error ? <p className="mt-3 text-[13px] text-err">{models.error.message}</p> : <div className="mt-4"><SkeletonLines lines={5} /></div>)}
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
                        <TierSelect model={m} value={tiers[m.candidate]} onChange={(v) => void chooseTier(m, v)} />
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

const TIERS = ["frontier", "balanced", "economy"] as const;

/** The model's tier as a menu: the three tiers, plus going back to the default when the person chose one. */
function TierSelect({ model, value, onChange }: { model: ModelInfo; value: string | undefined; onChange: (tier: string) => void }) {
  const chosen = value === undefined ? (model.tier_set_by_you ? model.reasoning_tier : "default") : value;
  const shown = chosen === "default" ? model.default_tier : chosen;
  return (
    <label className="relative inline-flex items-center">
      <span className="sr-only">{`Tier of ${model.candidate}`}</span>
      <select
        aria-label={`Tier of ${model.candidate}`}
        value={chosen}
        onChange={(e) => onChange(e.target.value)}
        className={cn(
          "cursor-pointer appearance-none rounded-full py-0.5 pl-2 pr-5 text-[11px] font-medium outline-none focus:ring-2 focus:ring-accent/30",
          TIER_STYLE[shown] ?? TIER_STYLE.unknown,
        )}
      >
        <option value="default">{`${model.default_tier} (default)`}</option>
        {TIERS.map((t) => <option key={t} value={t}>{t}</option>)}
      </select>
      <span aria-hidden className="pointer-events-none absolute right-1.5 text-[9px] opacity-70">▾</span>
      {chosen !== "default" && <span className="ml-1.5 text-[11px] text-faint">set by you</span>}
    </label>
  );
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
      <Card title="Guided tour" actions={<Button onClick={() => useApp.getState().setTourOpen(true)}><Compass className="size-3.5" />Take the tour</Button>}>
        <p className="text-[13px] text-muted">A one-minute walk through every part of the app. It runs once on first launch.</p>
      </Card>
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
        <Button disabled={!root} loading={doctor.loading} onClick={() => void runDoctor()}><RefreshCw className="size-3.5" />Check providers</Button>
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
          <div><dt className="inline text-faint">Engine: </dt><dd className="inline">
            {diag ? { bundled: "bundled with the app", SAARATHI_BRIDGE: "from SAARATHI_BRIDGE", PATH: "saarathi on your PATH" }[diag.source] : "—"}
          </dd></div>
          <div><dt className="inline text-faint">Command: </dt><dd className="inline break-anywhere"><Mono>{diag?.command ?? "—"}</Mono></dd></div>
          <div className="text-faint">The app uses its bundled engine. Developers can point <Mono>SAARATHI_BRIDGE</Mono> at another <Mono>saarathi</Mono>.</div>
        </dl>
      </Card>
    </PageShell>
  );
}
