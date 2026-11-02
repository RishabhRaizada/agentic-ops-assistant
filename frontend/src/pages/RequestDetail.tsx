import clsx from "clsx";
import { Fragment, useMemo, useState, type ReactNode } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Check, ChevronDown, ChevronRight, CircleAlert, Pencil, Send, ShieldAlert, Sparkles, Wrench } from "lucide-react";
import { api } from "../lib/api";
import { canApprove, useAuth } from "../lib/auth";
import { display, dt, inr, pct, relative, ROLE_LABEL, time, titleCase } from "../lib/format";
import type { AgentRun, Approval, AuditEvent, CaseDetail, Comm, Fact, Issue, Message, Recommendation } from "../lib/types";
import { ActorDot, Button, ConfidenceMeter, Empty, Field, inputCls, Modal, Panel, RiskBadge, SlaTimer, StatusBadge, Tabs } from "../components/ui";
import { useServerSettings } from "../components/Layout";

type Tab = "facts" | "risk" | "options" | "actions" | "comms";

const STEPS = [
  { label: "Received", statuses: ["NEW", "SCREENING", "ANALYZING"] },
  { label: "Reviewed", statuses: ["NEEDS_INFO", "NEEDS_REVIEW", "READY_FOR_APPROVAL", "ON_HOLD", "REPLANNING"] },
  { label: "Decision", statuses: ["AWAITING_APPROVAL"] },
  { label: "Executing", statuses: ["APPROVED", "IN_PROGRESS"] },
  { label: "Resolved", statuses: ["RESOLVED"] },
  { label: "Closed", statuses: ["CLOSED", "CANCELLED", "MERGED"] },
];

function useAct(caseId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ url, body, method = "post" }: { url: string; body?: unknown; method?: "post" | "patch"; ok?: string }) =>
      method === "patch" ? api.patch(url, body) : api.post(url, body),
    onSuccess: (_d, v) => {
      if (v.ok) toast.success(v.ok);
      qc.invalidateQueries({ queryKey: ["case", caseId] });
      qc.invalidateQueries({ queryKey: ["cases"] });
      qc.invalidateQueries({ queryKey: ["approvals"] });
    },
    onError: (e: Error) => toast.error(e.message),
  });
}

export default function RequestDetail() {
  const id = Number(useParams().id);
  const { user } = useAuth();
  const settings = useServerSettings();
  const [tab, setTab] = useState<Tab>("options");
  const [hot, setHot] = useState<string | null>(null);
  const q = useQuery({
    queryKey: ["case", id],
    queryFn: () => api.get<CaseDetail>(`/api/requests/${id}`),
    refetchInterval: (query) => (query.state.data?.processing ? 1200 : 5000),
  });
  const c = q.data;
  const act = useAct(id);
  if (q.isLoading) return <p className="text-sm text-ink-3">Loading…</p>;
  if (!c) return <Empty title="Request not found" />;

  const live = c.recommendations.filter((r) => ["proposed", "selected"].includes(r.status));
  const openIssues = c.issues.filter((i) => i.status === "open");
  const pending = c.approvals.find((a) => a.status === "pending");
  const offset = settings.data?.clock_offset_seconds || 0;
  const step = STEPS.findIndex((s) => s.statuses.includes(c.status));

  return (
    <div className="mx-auto max-w-[1600px]">
      {/* ---------------------------------------------------------------- header */}
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <Link to="/requests" className="text-xs text-ink-3 hover:text-teal">Requests</Link>
          <div className="mt-1 flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <span className="label text-[28px] font-semibold leading-none text-ink-3">{c.ref}</span>
            <h1 className="text-[22px] font-semibold leading-tight tracking-tight">{c.title}</h1>
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-2 text-sm">
            <StatusBadge status={c.status} processing={c.processing} />
            <RiskBadge level={c.risk_level} priority={c.priority} />
            <span className="text-xs text-ink-3">{titleCase(c.category)} · {c.region || "region unknown"}{c.store_code ? ` · ${c.store_code}` : ""}</span>
            {c.duplicate_of_ref && <Link to={`/requests/${c.duplicate_of_id}`} className="text-xs text-teal underline">Merged into {c.duplicate_of_ref}</Link>}
            {c.parent_ref && <Link to={`/requests/${c.parent_case_id}`} className="text-xs text-teal underline">Split from {c.parent_ref}</Link>}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-5">
          <div>
            <p className="text-xs text-ink-3">{pending ? "Approval due" : "Decision deadline"}</p>
            <SlaTimer due={pending?.due_at || c.decision_deadline_at} offsetSeconds={offset} paused={!!c.sla_paused_at && !pending} />
          </div>
          <ConfidenceBlock c={c} />
        </div>
      </div>

      <ol className="mt-5 grid grid-cols-6 gap-1" aria-label="Progress">
        {STEPS.map((s, i) => (
          <li key={s.label} className="min-w-0">
            <div className={clsx("h-1 rounded-full", i < step ? "bg-teal" : i === step ? (["CANCELLED", "MERGED"].includes(c.status) ? "bg-ink-4" : "bg-teal") : "bg-rule")} />
            <p className={clsx("mt-1 truncate text-[11px]", i === step ? "font-semibold text-ink" : "text-ink-4")}>
              {i === step && ["CANCELLED", "MERGED"].includes(c.status) ? titleCase(c.status.toLowerCase()) : s.label}
            </p>
          </li>
        ))}
      </ol>

      {c.processing && (
        <div className="mt-4 flex items-center gap-2 rounded-md border border-teal/20 bg-teal-soft px-3 py-2 text-sm text-teal">
          <span className="working-dot size-2 rounded-full bg-teal" /> Agents are working on this request. The timeline on the right updates live.
        </div>
      )}

      <div className="mt-5 grid gap-5 xl:grid-cols-[minmax(300px,0.85fr)_minmax(0,1.7fr)_minmax(280px,0.8fr)]">
        {/* ------------------------------------------------------------ source */}
        <div className="space-y-4 xl:sticky xl:top-16 xl:max-h-[calc(100vh-80px)] xl:overflow-y-auto scroll-thin">
          {c.messages.map((m) => <SourceMessage key={m.id} m={m} facts={c.facts} hot={hot} />)}
          {c.merged.length > 0 && (
            <p className="text-xs text-ink-3">Linked requests: {c.merged.map((x) => <Link key={x.id} className="mr-2 text-teal underline" to={`/requests/${x.id}`}>{x.ref}</Link>)}</p>
          )}
          {c.children.length > 0 && (
            <p className="text-xs text-ink-3">Split into: {c.children.map((x) => <Link key={x.id} className="mr-2 text-teal underline" to={`/requests/${x.id}`}>{x.ref}</Link>)}</p>
          )}
        </div>

        {/* ------------------------------------------------------------ work */}
        <div className="min-w-0 space-y-4">
          {openIssues.length > 0 && <IssuesPanel c={c} issues={openIssues} act={act} />}
          {c.analysis?.intake?.summary && (
            <p className="text-[15px] leading-relaxed text-ink-2">{c.analysis.intake.summary}</p>
          )}
          <Tabs<Tab>
            value={tab}
            onChange={setTab}
            tabs={[
              { id: "options", label: "Options & approval", count: live.length },
              { id: "facts", label: "Facts", count: c.facts.filter((f) => f.status === "missing").length, alert: true },
              { id: "risk", label: "Risk" },
              { id: "actions", label: "Actions", count: c.actions.filter((a) => a.status === "open").length },
              { id: "comms", label: "Communications", count: c.communications.filter((x) => ["draft", "failed", "dead"].includes(x.status)).length },
            ]}
          />
          {tab === "facts" && <FactsTab c={c} act={act} setHot={setHot} />}
          {tab === "risk" && <RiskTab c={c} />}
          {tab === "options" && <OptionsTab c={c} act={act} user={user} />}
          {tab === "actions" && <ActionsTab c={c} act={act} />}
          {tab === "comms" && <CommsTab c={c} act={act} />}
        </div>

        {/* ------------------------------------------------------------ timeline */}
        <div className="xl:sticky xl:top-16 xl:max-h-[calc(100vh-80px)] xl:overflow-y-auto scroll-thin">
          <Timeline events={c.timeline} runs={c.agent_runs} />
        </div>
      </div>
    </div>
  );
}

/* ====================================================================== confidence */

function ConfidenceBlock({ c }: { c: CaseDetail }) {
  const [open, setOpen] = useState(false);
  const b = c.confidence_breakdown;
  return (
    <div className="relative">
      <p className="text-xs text-ink-3">AI confidence</p>
      <button onClick={() => setOpen(!open)} className="flex items-center gap-1" aria-expanded={open}>
        <ConfidenceMeter value={c.overall_confidence} />
        <ChevronDown size={14} className="text-ink-3" />
      </button>
      {open && b.components && (
        <div className="absolute right-0 z-20 mt-2 w-80 rounded-lg border border-rule bg-sheet p-4 text-sm shadow-xl">
          <p className="font-semibold">How this score was computed</p>
          <p className="mt-1 text-xs text-ink-3">The product of independent checks. The model's own confidence is only one input and is capped by evidence.</p>
          <dl className="mt-3 space-y-1.5">
            {Object.entries(b.components).map(([k, v]) => (
              <div key={k} className="flex justify-between gap-3">
                <dt className="text-ink-2">{titleCase(k)}</dt>
                <dd className="tabular-nums">{pct(v)}</dd>
              </div>
            ))}
          </dl>
          {!!b.notes?.length && <ul className="mt-3 list-disc space-y-0.5 pl-4 text-xs text-amber">{b.notes.map((n) => <li key={n}>{n}</li>)}</ul>}
          <p className="mt-3 text-xs text-ink-3">Below 75% an approval is always required; below 50% the AI recommendation cannot be submitted without a written justification.</p>
        </div>
      )}
    </div>
  );
}

/* ====================================================================== source email */

function SourceMessage({ m, facts, hot }: { m: Message; facts: Fact[]; hot: string | null }) {
  const spans = useMemo(() => {
    const marks: { quote: string; ref: string; kind: "ai" | "threat" }[] = [];
    for (const f of facts) if (f.evidence_quote && f.grounded) marks.push({ quote: f.evidence_quote, ref: f.ref, kind: "ai" });
    for (const s of m.guard_signals || []) if (s.source === "heuristic" && s.span && !s.span.includes("chars")) marks.push({ quote: s.span, ref: s.signal, kind: "threat" });
    return marks;
  }, [facts, m.guard_signals]);
  const body = useMemo(() => highlight(m.body, spans, hot), [m.body, spans, hot]);
  return (
    <article className={clsx("rounded-lg border bg-sheet", m.guard_label && m.guard_label !== "safe" ? "border-signal/40" : "border-rule")}>
      <header className="border-b border-rule-2 px-4 py-3">
        <div className="flex items-center justify-between gap-2 text-xs text-ink-3">
          <span>{m.relation === "original" ? "Original request" : `${titleCase(m.relation)} message`} · {m.channel}</span>
          <span>{dt(m.received_at)}</span>
        </div>
        <p className="mt-1 text-sm"><span className="font-medium">{m.from_name || m.from_addr}</span> <span className="text-ink-3">&lt;{m.from_addr}&gt;</span></p>
        <p className="mt-0.5 font-semibold">{m.subject}</p>
        {m.guard_label && m.guard_label !== "safe" && (
          <p className="mt-2 flex items-start gap-1.5 rounded bg-signal-soft px-2 py-1.5 text-xs text-signal">
            <ShieldAlert size={14} className="mt-px shrink-0" />
            Flagged {m.guard_label}: instructions inside this email are treated as data and cannot trigger any action.
          </p>
        )}
      </header>
      <div className="whitespace-pre-wrap px-4 py-3 text-[14px] leading-relaxed text-ink-2">{body}</div>
      <footer className="flex flex-wrap gap-3 border-t border-rule-2 px-4 py-2 text-[11px] text-ink-3">
        <span><span className="mark-span">text</span> read by the AI</span>
        {spans.some((s) => s.kind === "threat") && <span><span className="mark-threat">text</span> suspicious instruction</span>}
      </footer>
    </article>
  );
}

function highlight(text: string, marks: { quote: string; ref: string; kind: "ai" | "threat" }[], hot: string | null): ReactNode[] {
  const ranges: { s: number; e: number; ref: string; kind: string }[] = [];
  for (const m of marks) {
    const q = m.quote.replace(/\^/g, " ").trim().replace(/[.,'"]+$/, "");
    if (q.length < 2) continue;
    const rx = new RegExp(q.split(/\s+/).map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("\\s+"), "i");
    const hit = rx.exec(text);
    if (hit && !ranges.some((r) => hit.index < r.e && hit.index + hit[0].length > r.s)) ranges.push({ s: hit.index, e: hit.index + hit[0].length, ref: m.ref, kind: m.kind });
  }
  ranges.sort((a, b) => a.s - b.s);
  const out: ReactNode[] = [];
  let i = 0;
  for (const r of ranges) {
    if (r.s > i) out.push(text.slice(i, r.s));
    out.push(
      <mark key={r.s} className={clsx(r.kind === "threat" ? "mark-threat" : "mark-span", hot === r.ref && "is-hot", "text-ink")} title={r.kind === "ai" ? `Evidence for ${r.ref}` : r.ref}>
        {text.slice(r.s, r.e)}
      </mark>,
    );
    i = r.e;
  }
  out.push(text.slice(i));
  return out;
}

/* ====================================================================== issues */

const ISSUE_ICON: Record<string, string> = {
  conflict: "Conflicting data", missing: "Missing information", injection: "Security", contradiction: "Agents disagree",
  low_confidence: "Low confidence", degraded: "Degraded mode", ambiguity: "Unclear request", duplicate: "Possible duplicate",
  comms_failure: "Delivery failure", material_change: "Changed circumstances", cancellation: "Cancellation", sla_breach: "SLA",
  out_of_scope: "Out of scope", system_unavailable: "System unavailable",
};

function IssuesPanel({ c, issues, act }: { c: CaseDetail; issues: Issue[]; act: ReturnType<typeof useAct> }) {
  const [note, setNote] = useState<Record<number, string>>({});
  const resolve = (i: Issue, choice?: unknown, resolution?: string) =>
    act.mutate({ url: `/api/requests/${c.id}/issues/${i.id}/resolve`, body: { choice, resolution: resolution ?? note[i.id] ?? "" }, ok: "Resolved" });
  const sorted = [...issues].sort((a, b) => Number(b.blocking) - Number(a.blocking));
  return (
    <section className="rounded-lg border border-amber/30 bg-amber-soft/40" aria-label="Open issues">
      <header className="flex items-center gap-2 border-b border-amber/20 px-4 py-2.5">
        <CircleAlert size={16} className="text-amber" />
        <h2 className="text-sm font-semibold">{issues.length} item{issues.length > 1 ? "s" : ""} need a human</h2>
        <span className="text-xs text-ink-3">{issues.filter((i) => i.blocking).length} blocking submission</span>
      </header>
      <ul className="divide-y divide-amber/15">
        {sorted.map((i) => (
          <li key={i.id} className="px-4 py-3">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div className="min-w-0">
                <p className="text-xs text-ink-3">{ISSUE_ICON[i.type] || titleCase(i.type)}{i.blocking ? " · blocking" : ""}</p>
                <p className="text-sm font-medium">{i.title}</p>
                {i.type === "injection" && i.detail.signals && (
                  <ul className="mt-1 space-y-0.5 text-xs text-signal">
                    {i.detail.signals.slice(0, 5).map((s: { signal: string; span: string }, k: number) => <li key={k}>{titleCase(s.signal)}: “{s.span}”</li>)}
                  </ul>
                )}
                {i.type === "contradiction" && (
                  <p className="mt-1 text-xs text-ink-2">Rules engine factors: {(i.detail.rules_factors || []).join("; ")}</p>
                )}
                {i.type === "ambiguity" && i.detail.issues && (
                  <ul className="mt-1 list-disc pl-4 text-xs text-ink-2">{i.detail.issues.map((x: { title: string }, k: number) => <li key={k}>{x.title}</li>)}</ul>
                )}
                {i.type === "material_change" && i.detail.before && (
                  <p className="mt-1 text-xs text-ink-2">
                    {Object.keys(i.detail.after || {}).map((k) => `${titleCase(k)}: ${display(i.detail.before[k])} → ${display(i.detail.after[k])}`).join("; ")}
                    {i.detail.approvals_superseded ? ` · ${i.detail.approvals_superseded} pending approval withdrawn` : ""}
                  </p>
                )}
              </div>
              {c.can_write && (
                <div className="flex flex-wrap gap-2">
                  {i.type === "conflict" && i.detail.options?.map((o: { source: string; label: string }) => (
                    <Button key={o.source} size="sm" variant="secondary" onClick={() => resolve(i, o.source)}>{o.label}</Button>
                  ))}
                  {i.type === "duplicate" && i.detail.target_ref && (
                    <Button size="sm" variant="secondary" onClick={() => act.mutate({ url: `/api/requests/${c.id}/merge`, body: { target_ref: i.detail.target_ref }, ok: `Merged into ${i.detail.target_ref}` })}>
                      Merge into {i.detail.target_ref}
                    </Button>
                  )}
                  {i.type === "ambiguity" && i.detail.action === "split" && (
                    <Button size="sm" variant="secondary" onClick={() => act.mutate({ url: `/api/requests/${c.id}/split`, ok: "Split into linked requests" })}>Split into {i.detail.issues.length} requests</Button>
                  )}
                  {i.type === "cancellation" && (
                    <>
                      <Button size="sm" variant="danger" onClick={() => act.mutate({ url: `/api/requests/${c.id}/cancel`, body: { reason: "Requester confirmed postponement/cancellation" }, ok: "Request cancelled" })}>Cancel request</Button>
                      <Button size="sm" variant="secondary" onClick={() => resolve(i, "resume", "Need still stands - resume")}>Resume</Button>
                    </>
                  )}
                  {i.type === "missing" && (
                    <Button size="sm" variant="secondary" onClick={() => act.mutate({ url: `/api/requests/${c.id}/comms/generate`, body: { audience: "clarification" }, ok: "Clarification email drafted - see Communications" })}>Draft clarification email</Button>
                  )}
                  {!["conflict", "cancellation"].includes(i.type) && (
                    <span className="flex gap-1">
                      <input className={`${inputCls} h-8 w-40 py-1 text-xs`} placeholder="Note (optional)" value={note[i.id] || ""} onChange={(e) => setNote({ ...note, [i.id]: e.target.value })} />
                      <Button size="sm" variant="ghost" onClick={() => resolve(i)}>{i.type === "injection" ? "Mark reviewed" : i.type === "contradiction" ? "Acknowledge" : "Resolve"}</Button>
                    </span>
                  )}
                </div>
              )}
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

/* ====================================================================== facts */

const SOURCE_LABEL: Record<string, string> = { email: "Email", inventory: "Inventory system", po: "Purchase order", calendar: "Store calendar", derived: "Computed", human: "Human", update: "Later email" };
const EDITABLE = new Set(["item", "quantity_required", "quantity_available_claimed", "available_qty", "location", "supplier", "delay_days", "event", "event_date", "decision_deadline", "cost_mentioned", "customer", "store", "sku"]);

function FactsTab({ c, act, setHot }: { c: CaseDetail; act: ReturnType<typeof useAct>; setHot: (r: string | null) => void }) {
  const [edit, setEdit] = useState<Fact | null>(null);
  const [val, setVal] = useState("");
  const [reason, setReason] = useState("");
  const groups = [
    { title: "From the request", keys: (f: Fact) => ["email", "update"].includes(f.source) || (f.source === "human" && !["available_qty"].includes(f.key)) },
    { title: "Checked against systems", keys: (f: Fact) => ["inventory", "po", "calendar"].includes(f.source) || ["store", "sku", "available_qty"].includes(f.key) },
    { title: "Computed", keys: (f: Fact) => f.source === "derived" && !["store", "sku", "available_qty"].includes(f.key) },
  ];
  const seen = new Set<string>();
  return (
    <div className="space-y-4">
      <p className="text-xs text-ink-3">
        <span className="mark-ai">Highlighted</span> values were read by the AI and cite the email text (hover to see it on the left). <span className="mark-pen">Underlined</span> values were corrected by a person; the AI's original value is kept.
      </p>
      {groups.map((g) => {
        const rows = c.facts.filter((f) => !seen.has(f.key) && g.keys(f));
        rows.forEach((f) => seen.add(f.key));
        if (!rows.length) return null;
        return (
          <Panel key={g.title} title={g.title}>
            <table className="w-full text-sm">
              <tbody>
                {rows.map((f) => (
                  <tr key={f.ref} className="border-b border-rule-2 last:border-0" onMouseEnter={() => setHot(f.ref)} onMouseLeave={() => setHot(null)}>
                    <td className="w-10 py-2 pl-4 text-xs text-ink-4">{f.ref}</td>
                    <td className="py-2 pr-3 text-ink-2">{f.label}</td>
                    <td className="py-2 pr-3">
                      {f.status === "missing" && f.human_value == null ? <span className="text-xs font-medium text-amber">Missing</span> : (
                        <span className={clsx(f.human_value != null ? "mark-pen" : f.source === "email" || f.source === "update" ? "mark-ai" : "", "font-medium")}>{display(f.value)}</span>
                      )}
                      {f.human_value != null && <span className="block text-[11px] text-ink-3">AI read {display(f.ai_value)} · {f.edited_by}: {f.edit_reason}</span>}
                      {f.raw_text && f.human_value == null && f.source !== "derived" && String(f.raw_text) !== String(f.value) && <span className="block text-[11px] text-ink-3">{f.raw_text}</span>}
                    </td>
                    <td className="py-2 pr-3 text-xs text-ink-3">
                      {SOURCE_LABEL[f.source] || f.source}
                      {f.status === "unverified" && <span className="ml-1 text-amber">unverified</span>}
                      {f.status === "inferred" && <span className="ml-1 text-amber">inferred</span>}
                      {f.grounded === false && <span className="ml-1 text-signal">no supporting text</span>}
                    </td>
                    <td className="py-2 pr-3">{["email", "update"].includes(f.source) && f.status !== "missing" ? <ConfidenceMeter value={f.confidence} compact /> : null}</td>
                    <td className="py-2 pr-4 text-right">
                      {c.can_write && EDITABLE.has(f.key) && (
                        <button className="rounded p-1 text-ink-3 hover:bg-rule-2 hover:text-ink" aria-label={`Correct ${f.label}`}
                          onClick={() => { setEdit(f); setVal(f.value == null ? "" : String(f.value)); setReason(""); }}>
                          <Pencil size={14} />
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Panel>
        );
      })}
      <Modal open={!!edit} onClose={() => setEdit(null)} title={`Correct: ${edit?.label}`}>
        {edit && (
          <div className="space-y-3">
            <p className="text-sm text-ink-3">AI value: <span className="mark-ai font-medium text-ink">{display(edit.ai_value)}</span>{edit.evidence_quote && <> from “{edit.evidence_quote}”</>}</p>
            <Field label="Correct value" hint={edit.key.includes("date") || edit.key === "decision_deadline" ? "e.g. 12 Nov 4 PM, tomorrow 11:00, next week" : undefined}>
              <input className={inputCls} value={val} onChange={(e) => setVal(e.target.value)} autoFocus />
            </Field>
            <Field label="Reason (recorded in the audit trail)"><input className={inputCls} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. PO confirms 400 units, email was wrong" /></Field>
            <p className="text-xs text-ink-3">Saving re-runs the risk, planning and review agents with the corrected fact. Any pending approval is withdrawn.</p>
            <div className="flex justify-end">
              <Button disabled={!reason.trim() || act.isPending} onClick={() => act.mutate(
                { url: `/api/requests/${c.id}/facts/${edit.key}`, method: "patch", body: { value: val, reason }, ok: "Correction saved - re-analysing" },
                { onSuccess: () => setEdit(null) })}>Save correction</Button>
            </div>
          </div>
        )}
      </Modal>
    </div>
  );
}

/* ====================================================================== risk */

function RiskTab({ c }: { c: CaseDetail }) {
  const r = c.analysis?.risk;
  if (!r) return <Empty title="Risk not assessed yet" />;
  const ag = r.agent;
  const rev = c.facts.find((f) => f.key === "revenue_at_risk")?.value as number | undefined;
  return (
    <div className="space-y-4">
      <div className={clsx("rounded-lg border px-4 py-3 text-sm", r.reconciled.contradiction ? "border-signal/30 bg-signal-soft" : "border-rule bg-sheet")}>
        <span className="font-semibold">Final risk: {titleCase(r.reconciled.final)}.</span> {r.reconciled.basis}.
        {rev ? <> Revenue at risk if nothing is done: <span className="font-semibold">{inr(rev)}</span> (store plan × launch slip).</> : null}
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <Panel title={<span className="flex items-center gap-1.5"><Sparkles size={14} /> Risk agent</span>} action={ag && <RiskBadge level={ag.risk_level} priority={ag.urgency} />}>
          {ag ? (
            <div className="space-y-3 px-4 py-3 text-sm">
              <p className="leading-relaxed text-ink-2">{ag.business_impact}</p>
              {ag.customer_impact && <p className="text-ink-2"><span className="font-medium text-ink">Customer impact:</span> {ag.customer_impact}</p>}
              <div>
                <p className="text-xs font-medium text-ink-3">Drivers</p>
                <ul className="mt-1 space-y-1">
                  {ag.drivers.map((d: { factor: string; fact_refs: string[] }, i: number) => (
                    <li key={i} className="flex gap-2"><span className="text-ink-2">{d.factor}</span><span className="text-xs text-ink-4">{d.fact_refs.join(", ")}</span></li>
                  ))}
                </ul>
              </div>
              {!!ag.assumptions?.length && <div><p className="text-xs font-medium text-ink-3">Assumptions</p><ul className="mt-1 list-disc pl-4 text-ink-2">{ag.assumptions.map((a: string) => <li key={a}>{a}</li>)}</ul></div>}
              <p className="text-xs text-ink-3">Self-reported confidence {pct(ag.confidence)}</p>
            </div>
          ) : <p className="px-4 py-3 text-sm text-ink-3">Risk agent unavailable - rules engine used alone.</p>}
        </Panel>
        <Panel title="Rules engine (independent check)" action={<RiskBadge level={r.rules.level} priority={r.rules.urgency} />}>
          <div className="px-4 py-3 text-sm">
            <p className="text-ink-2">Deterministic scoring from the fact ledger: {r.rules.points} points.</p>
            <ul className="mt-2 list-disc space-y-0.5 pl-4 text-ink-2">{r.rules.factors.map((f: string) => <li key={f}>{f}</li>)}</ul>
            <p className="mt-3 text-xs text-ink-3">If the agent and the rules differ by two or more levels, the request is held for a human to decide.</p>
          </div>
        </Panel>
      </div>
    </div>
  );
}

/* ====================================================================== options & approval */

function OptionsTab({ c, act, user }: { c: CaseDetail; act: ReturnType<typeof useAct>; user: ReturnType<typeof useAuth>["user"] }) {
  const [custom, setCustom] = useState(false);
  const [submitRec, setSubmitRec] = useState<Recommendation | null>(null);
  const current = c.recommendations.filter((r) => ["proposed", "selected", "approved"].includes(r.status));
  const old = c.recommendations.filter((r) => ["rejected", "superseded"].includes(r.status));
  const plan = c.analysis?.plan;
  const canSubmit = c.can_write && ["READY_FOR_APPROVAL", "NEEDS_REVIEW", "REPLANNING"].includes(c.status) && !c.processing;
  const blocking = c.issues.filter((i) => i.status === "open" && i.blocking);
  return (
    <div className="space-y-4">
      {c.approvals.length > 0 && <ApprovalStrip c={c} approvals={c.approvals} user={user} />}
      {plan && (
        <div className="rounded-lg border border-rule bg-sheet px-4 py-3 text-sm">
          <p><span className="font-semibold">{plan.source === "rules" ? "Rules-based plan (AI unavailable)" : "Planner recommendation"}:</span> <span className="text-ink-2">{plan.rationale}</span></p>
          {plan.review && <p className="mt-1 text-xs text-ink-3">Reviewer agent: {plan.review.verdict}{plan.revised ? " after one revision" : ""} - {plan.review.summary}</p>}
          {!!plan.tools_used?.length && <p className="mt-1 text-xs text-ink-3">Systems queried: {plan.tools_used.map((t: { ref: string; tool: string; error?: string }) => `${t.ref} ${t.tool.replace(/_/g, " ")}${t.error ? " (failed)" : ""}`).join(" · ")}</p>}
          {!!plan.open_questions?.length && <p className="mt-1 text-xs text-amber">Open questions: {plan.open_questions.join("; ")}</p>}
        </div>
      )}
      {current.length === 0 && <Empty title={c.status === "NEEDS_INFO" ? "Waiting for missing information" : "No options yet"}>{c.status === "NEEDS_INFO" ? "Planning starts once the missing facts are filled in - by the requester's reply or by correcting the facts." : "Options appear when analysis finishes."}</Empty>}
      <div className="grid gap-3">
        {current.map((r) => <OptionCard key={r.id} r={r} canSubmit={canSubmit && blocking.length === 0 && r.status !== "approved"} onSubmit={() => setSubmitRec(r)} />)}
      </div>
      {canSubmit && blocking.length > 0 && current.length > 0 && (
        <p className="text-sm text-amber">Resolve the {blocking.length} blocking item{blocking.length > 1 ? "s" : ""} above before submitting an option.</p>
      )}
      {c.can_write && !["CLOSED", "CANCELLED", "MERGED"].includes(c.status) && (
        <Button variant="secondary" onClick={() => setCustom(true)}><Pencil size={14} /> Propose your own option</Button>
      )}
      {old.length > 0 && (
        <details className="rounded-lg border border-rule bg-sheet px-4 py-2 text-sm">
          <summary className="cursor-pointer text-ink-3">{old.length} earlier option{old.length > 1 ? "s" : ""} (rejected or superseded)</summary>
          <ul className="mt-2 space-y-1 pb-2">
            {old.map((r) => <li key={r.id} className="text-ink-2"><span className="text-xs uppercase text-ink-4">{r.status}</span> · {r.option.title} · {inr(r.option.est_cost_inr)} <span className="text-xs text-ink-4">(facts v{r.ledger_version})</span></li>)}
          </ul>
        </details>
      )}
      <SubmitModal c={c} rec={submitRec} onClose={() => setSubmitRec(null)} act={act} />
      <CustomOptionModal open={custom} onClose={() => setCustom(false)} c={c} act={act} />
    </div>
  );
}

function OptionCard({ r, canSubmit, onSubmit }: { r: Recommendation; canSubmit: boolean; onSubmit: () => void }) {
  const [open, setOpen] = useState(false);
  const o = r.option;
  return (
    <article className={clsx("rounded-lg border bg-sheet", r.is_recommended ? "border-teal" : "border-rule", r.status === "approved" && "border-go")}>
      <div className="flex flex-wrap items-start justify-between gap-3 px-4 pt-3">
        <div className="min-w-0">
          <p className="text-xs text-ink-3">
            Option {r.option_key}{r.is_recommended && <span className="ml-1.5 rounded bg-teal px-1.5 py-px text-[11px] font-semibold text-white">Recommended</span>}
            {r.source === "human" && <span className="ml-1.5 rounded bg-pen-soft px-1.5 py-px text-[11px] text-pen">Operator option</span>}
            {o.source === "rules" && <span className="ml-1.5 rounded bg-amber-soft px-1.5 py-px text-[11px] text-amber">Rules-based</span>}
            {r.status === "approved" && <span className="ml-1.5 rounded bg-go px-1.5 py-px text-[11px] font-semibold text-white">Approved</span>}
            {r.status === "selected" && <span className="ml-1.5 rounded bg-pen px-1.5 py-px text-[11px] text-white">Submitted</span>}
          </p>
          <h3 className="mt-0.5 text-[15px] font-semibold">{o.title}</h3>
        </div>
        <div className="text-right">
          <p className="label text-2xl font-semibold leading-none">{inr(o.est_cost_inr)}</p>
          <p className="text-xs text-ink-3">additional cost</p>
        </div>
      </div>
      <dl className="mt-3 grid grid-cols-3 border-y border-rule-2 text-sm">
        <div className="px-4 py-2"><dt className="text-xs text-ink-3">Covers</dt><dd className="font-medium">{o.covers_qty.toLocaleString("en-IN")} units</dd></div>
        <div className="border-x border-rule-2 px-4 py-2"><dt className="text-xs text-ink-3">Ready in</dt><dd className="font-medium">{o.eta_days} days</dd></div>
        <div className="px-4 py-2"><dt className="text-xs text-ink-3">Before event</dt><dd className={clsx("font-medium", o.meets_deadline ? "text-go" : "text-signal")}>{o.meets_deadline ? "Yes" : "No"}</dd></div>
      </dl>
      <div className="px-4 py-3 text-sm">
        <p className="leading-relaxed text-ink-2">{o.rationale}</p>
        {r.review?.issues?.length > 0 && (
          <ul className="mt-2 space-y-0.5 text-xs text-amber">{r.review.issues.map((i, k) => <li key={k}>Reviewer ({i.severity}): {i.problem}</li>)}</ul>
        )}
        {r.policy && (
          <p className="mt-2 text-xs text-ink-3">
            {r.policy.required ? <>Needs approval by <span className="font-medium text-ink-2">{ROLE_LABEL[r.policy.role]}</span>: {r.policy.reasons.join("; ")}.</> : "No approval needed under the current policy."}
          </p>
        )}
        <button onClick={() => setOpen(!open)} className="mt-2 flex items-center gap-1 text-xs text-teal hover:underline">
          {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />} Steps, cost working and evidence
        </button>
        {open && (
          <div className="mt-2 space-y-3 rounded-md bg-paper p-3 text-[13px]">
            <ol className="list-decimal space-y-0.5 pl-5">{o.steps.map((s) => <li key={s}>{s}</li>)}</ol>
            <p><span className="font-medium">Cost working:</span> {o.cost_breakdown}</p>
            {!!o.risks?.length && <p><span className="font-medium">Risks:</span> {o.risks.join("; ")}</p>}
            <p className="text-xs text-ink-3">Cites facts {o.fact_refs?.join(", ") || "—"} · tool results {o.tool_refs?.join(", ") || "—"}</p>
            {o.tool_evidence && Object.entries(o.tool_evidence).map(([ref, ev]) => (
              <div key={ref} className="rounded border border-rule bg-sheet p-2">
                <p className="text-xs font-medium"><Wrench size={11} className="mr-1 inline" />{ref} · {ev.tool.replace(/_/g, " ")}({Object.values((ev.args || {}) as object).filter(Boolean).join(", ")})</p>
                <pre className="mt-1 max-h-40 overflow-auto text-[11px] leading-snug text-ink-2">{JSON.stringify(ev.data ?? ev.error, null, 1)}</pre>
              </div>
            ))}
          </div>
        )}
      </div>
      {canSubmit && (
        <div className="flex justify-end border-t border-rule-2 px-4 py-2.5">
          <Button onClick={onSubmit}><Send size={14} /> Choose this option</Button>
        </div>
      )}
    </article>
  );
}

function SubmitModal({ c, rec, onClose, act }: { c: CaseDetail; rec: Recommendation | null; onClose: () => void; act: ReturnType<typeof useAct> }) {
  const [cost, setCost] = useState<string>("");
  const [note, setNote] = useState("");
  const low = (c.overall_confidence ?? 1) < 0.5 && rec?.source === "ai";
  return (
    <Modal open={!!rec} onClose={onClose} title={`Choose option ${rec?.option_key}: ${rec?.option.title ?? ""}`}>
      {rec && (
        <div className="space-y-3 text-sm">
          {rec.policy?.required ? (
            <p>This goes to the <span className="font-semibold">{ROLE_LABEL[rec.policy.role]}</span> for approval because it {rec.policy.reasons.join("; ")}.</p>
          ) : <p>Policy allows this to proceed without approval. It moves straight to execution.</p>}
          {!rec.is_recommended && rec.source === "ai" && <p className="text-amber">You are choosing a different option from the AI recommendation. This is recorded as an override.</p>}
          <Field label="Adjust the cost before submitting (optional)" hint={`Planner estimate ${inr(rec.option.est_cost_inr)}`}>
            <input className={inputCls} inputMode="numeric" placeholder={String(Math.round(rec.option.est_cost_inr))} value={cost} onChange={(e) => setCost(e.target.value)} />
          </Field>
          <Field label={low ? "Justification (required: AI confidence is low)" : "Note for the approver (optional)"}>
            <textarea className={`${inputCls} min-h-20`} value={note} onChange={(e) => setNote(e.target.value)} />
          </Field>
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={onClose}>Back</Button>
            <Button disabled={act.isPending || (low && !note.trim())} onClick={() => act.mutate(
              { url: `/api/requests/${c.id}/recommendations/${rec.id}/submit`, body: { note, edits: cost ? { est_cost_inr: Number(cost.replace(/[^\d.]/g, "")) } : null }, ok: rec.policy?.required ? `Sent to ${ROLE_LABEL[rec.policy.role]} for approval` : "Approved by policy - execution started" },
              { onSuccess: onClose })}>
              {rec.policy?.required ? "Submit for approval" : "Proceed"}
            </Button>
          </div>
        </div>
      )}
    </Modal>
  );
}

function CustomOptionModal({ open, onClose, c, act }: { open: boolean; onClose: () => void; c: CaseDetail; act: ReturnType<typeof useAct> }) {
  const [f, setF] = useState({ title: "", est_cost_inr: "", covers_qty: "", eta_days: "", steps: "", rationale: "" });
  return (
    <Modal open={open} onClose={onClose} title="Propose your own option">
      <div className="space-y-3">
        <Field label="Title"><input className={inputCls} value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} /></Field>
        <div className="grid grid-cols-3 gap-2">
          <Field label="Cost (₹)"><input className={inputCls} inputMode="numeric" value={f.est_cost_inr} onChange={(e) => setF({ ...f, est_cost_inr: e.target.value })} /></Field>
          <Field label="Units covered"><input className={inputCls} inputMode="numeric" value={f.covers_qty} onChange={(e) => setF({ ...f, covers_qty: e.target.value })} /></Field>
          <Field label="Days"><input className={inputCls} inputMode="numeric" value={f.eta_days} onChange={(e) => setF({ ...f, eta_days: e.target.value })} /></Field>
        </div>
        <Field label="Steps (one per line)"><textarea className={`${inputCls} min-h-20`} value={f.steps} onChange={(e) => setF({ ...f, steps: e.target.value })} /></Field>
        <Field label="Justification (required)"><textarea className={`${inputCls} min-h-16`} value={f.rationale} onChange={(e) => setF({ ...f, rationale: e.target.value })} /></Field>
        <div className="flex justify-end">
          <Button disabled={!f.title || !f.rationale.trim()} onClick={() => act.mutate({
            url: `/api/requests/${c.id}/recommendations`,
            body: { title: f.title, est_cost_inr: Number(f.est_cost_inr || 0), covers_qty: Number(f.covers_qty || 0), eta_days: Number(f.eta_days || 0), steps: f.steps.split("\n").filter(Boolean), rationale: f.rationale },
            ok: "Option added",
          }, { onSuccess: onClose })}>Add option</Button>
        </div>
      </div>
    </Modal>
  );
}

function ApprovalStrip({ c, approvals, user }: { c: CaseDetail; approvals: Approval[]; user: ReturnType<typeof useAuth>["user"] }) {
  return (
    <Panel title="Approval routing">
      <ol className="divide-y divide-rule-2">
        {approvals.map((a) => (
          <li key={a.id} className="flex flex-wrap items-center justify-between gap-2 px-4 py-2.5 text-sm">
            <div>
              <p>
                <span className="font-medium">{a.assigned_to?.name || ROLE_LABEL[a.required_role]}</span>
                <span className="text-ink-3"> · {ROLE_LABEL[a.required_role]}{a.escalation_level ? ` · escalation level ${a.escalation_level}` : ""}</span>
              </p>
              <p className="text-xs text-ink-3">
                {inr(a.amount_inr)} · requested {relative(a.created_at)}
                {a.decided_by && ` · ${a.status} by ${a.decided_by.name}`}{a.decision_reason && `: “${a.decision_reason}”`}
              </p>
            </div>
            <span className="flex items-center gap-2">
              <ApprovalStatus a={a} />
              {a.status === "pending" && canApprove(user) && <Link to={`/approvals?focus=${a.id}`} className="text-xs text-teal underline">Open in approval center</Link>}
            </span>
          </li>
        ))}
      </ol>
      {c.status === "AWAITING_APPROVAL" && <p className="border-t border-rule-2 px-4 py-2 text-xs text-ink-3">If the approver does not respond by the due time, it escalates automatically to the next level.</p>}
    </Panel>
  );
}

export function ApprovalStatus({ a }: { a: Approval }) {
  const tone: Record<string, string> = {
    pending: a.overdue ? "bg-signal text-white" : "bg-pen-soft text-pen", approved: "bg-go-soft text-go", rejected: "bg-signal-soft text-signal",
    escalated: "bg-amber-soft text-amber", superseded: "bg-rule-2 text-ink-3", cancelled: "bg-rule-2 text-ink-3", info_requested: "bg-amber-soft text-amber",
  };
  return <span className={clsx("rounded px-1.5 py-0.5 text-xs font-medium", tone[a.status])}>{a.status === "pending" && a.overdue ? "Overdue" : titleCase(a.status)}</span>;
}

/* ====================================================================== actions */

function ActionsTab({ c, act }: { c: CaseDetail; act: ReturnType<typeof useAct> }) {
  const [reason, setReason] = useState("resolved_as_planned");
  const [note, setNote] = useState("");
  const [cancelOpen, setCancelOpen] = useState(false);
  const [cancelReason, setCancelReason] = useState("");
  const brief = c.analysis?.brief;
  return (
    <div className="space-y-4">
      {c.actions.length === 0 ? <Empty title="No actions yet">Actions are created from the approved option.</Empty> : (
        <Panel title="Execution checklist">
          <ul className="divide-y divide-rule-2">
            {c.actions.map((a) => (
              <li key={a.id} className="flex items-center gap-3 px-4 py-2.5 text-sm">
                <button disabled={a.status !== "open" || !c.can_write} aria-label="Mark done"
                  onClick={() => act.mutate({ url: `/api/requests/${c.id}/actions/${a.id}/complete`, ok: "Marked done" })}
                  className={clsx("flex size-5 shrink-0 items-center justify-center rounded border", a.status === "done" ? "border-go bg-go text-white" : "border-ink-4 hover:border-teal")}>
                  {a.status === "done" && <Check size={13} />}
                </button>
                <span className={clsx("flex-1", a.status !== "open" && "text-ink-3 line-through")}>{a.title}</span>
                <span className="text-xs text-ink-3">{a.status === "done" ? `done ${relative(a.completed_at)}` : a.status === "cancelled" ? "cancelled" : a.owner}</span>
              </li>
            ))}
          </ul>
        </Panel>
      )}
      {c.can_write && ["RESOLVED", "NEEDS_REVIEW", "NEEDS_INFO"].includes(c.status) && (
        <Panel title="Close the request">
          <div className="flex flex-wrap items-end gap-2 px-4 py-3">
            <Field label="Resolution">
              <select className={inputCls} value={reason} onChange={(e) => setReason(e.target.value)}>
                {["resolved_as_planned", "resolved_alternative", "no_action_needed", "not_actionable", "insufficient_information"].map((r) => <option key={r} value={r}>{titleCase(r)}</option>)}
              </select>
            </Field>
            <div className="min-w-48 flex-1"><Field label="Note"><input className={inputCls} value={note} onChange={(e) => setNote(e.target.value)} /></Field></div>
            <Button onClick={() => act.mutate({ url: `/api/requests/${c.id}/close`, body: { reason, note }, ok: "Request closed" })}>Close request</Button>
          </div>
        </Panel>
      )}
      <Panel title="Management brief" action={<Button size="sm" variant="secondary" onClick={() => act.mutate({ url: `/api/requests/${c.id}/brief`, ok: "Brief generated" })}><Sparkles size={13} /> {brief ? "Regenerate" : "Generate"}</Button>}>
        {brief ? (
          <div className="px-4 py-3 text-sm">
            <p className="font-semibold">{brief.headline}</p>
            <ul className="mt-2 list-disc space-y-0.5 pl-4 text-ink-2">{brief.bullets.map((b: string) => <li key={b}>{b}</li>)}</ul>
            {!!brief.risks?.length && <p className="mt-2 text-xs text-amber">Risks: {brief.risks.join("; ")}</p>}
            {!!brief.asks?.length && <p className="mt-1 text-xs">Asks: {brief.asks.join("; ")}</p>}
            <p className="mt-2 text-[11px] text-ink-4">Written by {brief.generated_by === "ai" ? "the reporting agent from computed figures" : "template (AI unavailable)"}</p>
          </div>
        ) : <p className="px-4 py-3 text-sm text-ink-3">A one-page summary for management, built from the facts and pinned events of this request.</p>}
      </Panel>
      {c.can_write && !["CLOSED", "CANCELLED", "MERGED"].includes(c.status) && (
        <div>
          <Button variant="danger" onClick={() => setCancelOpen(true)}>Cancel request</Button>
          <Modal open={cancelOpen} onClose={() => setCancelOpen(false)} title="Cancel this request">
            <p className="text-sm text-ink-2">Pending approvals are withdrawn and approvers notified; open actions are cancelled; a status update is drafted for the requester.</p>
            <div className="mt-3"><Field label="Reason"><input className={inputCls} value={cancelReason} onChange={(e) => setCancelReason(e.target.value)} placeholder="e.g. store launch postponed by 3 weeks" /></Field></div>
            <div className="mt-4 flex justify-end"><Button variant="danger" disabled={!cancelReason.trim()} onClick={() => act.mutate({ url: `/api/requests/${c.id}/cancel`, body: { reason: cancelReason }, ok: "Request cancelled" }, { onSuccess: () => setCancelOpen(false) })}>Cancel request</Button></div>
          </Modal>
        </div>
      )}
    </div>
  );
}

/* ====================================================================== comms */

const AUDIENCE: Record<string, string> = {
  requester_ack: "Acknowledgement to requester", clarification: "Clarification request", supplier: "Supplier", store_manager: "Store manager",
  status_update: "Status update to requester", management: "Management", approver_brief: "Approval request (system)", system: "System",
};

export function CommStatus({ s }: { s: Comm }) {
  const tone: Record<string, string> = { draft: "bg-rule-2 text-ink-2", queued: "bg-pen-soft text-pen", sent: "bg-go-soft text-go", failed: "bg-amber-soft text-amber", dead: "bg-signal text-white" };
  const label: Record<string, string> = { draft: "Draft", queued: "Queued", sent: "Sent", failed: `Failed · retry ${s.next_attempt_at ? relative(s.next_attempt_at) : ""}`, dead: "Undeliverable" };
  return <span className={clsx("rounded px-1.5 py-0.5 text-xs font-medium", tone[s.status])}>{label[s.status]}</span>;
}

export function CommEditor({ comm, canWrite, onDone }: { comm: Comm; canWrite: boolean; onDone?: () => void }) {
  const qc = useQueryClient();
  const [subject, setSubject] = useState(comm.subject);
  const [body, setBody] = useState(comm.body);
  const [to, setTo] = useState(comm.to.join(", "));
  const send = useMutation({
    mutationFn: () => api.post<Comm>(`/api/communications/${comm.id}/send`, { subject, body, to: to.split(",").map((x) => x.trim()).filter(Boolean) }),
    onSuccess: (r) => {
      if (r.status === "sent") toast.success("Sent");
      else toast.warning(`Delivery failed: ${r.last_error}. It will be retried automatically.`);
      qc.invalidateQueries();
      onDone?.();
    },
    onError: (e: Error) => toast.error(e.message),
  });
  const fallback = useMutation({
    mutationFn: () => api.post<{ delivered_in_app: number; external_needing_manual_contact: string[] }>(`/api/communications/${comm.id}/fallback`),
    onSuccess: (r) => toast.success(`Delivered in-app to ${r.delivered_in_app} internal recipient(s).${r.external_needing_manual_contact.length ? ` Call ${r.external_needing_manual_contact.join(", ")} directly.` : ""}`),
    onError: (e: Error) => toast.error(e.message),
  });
  const editable = canWrite && ["draft", "failed", "dead"].includes(comm.status);
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-ink-3">{AUDIENCE[comm.audience] || comm.audience} · {comm.generated_by === "ai" ? "drafted by AI" : comm.generated_by === "ai+human" ? "AI draft, edited" : comm.generated_by}{comm.attempts ? ` · ${comm.attempts} attempt(s)` : ""}</p>
        <CommStatus s={comm} />
      </div>
      {editable ? (
        <>
          <input className={inputCls} value={to} onChange={(e) => setTo(e.target.value)} aria-label="To" placeholder="recipient@company.com" />
          <input className={`${inputCls} font-medium`} value={subject} onChange={(e) => setSubject(e.target.value)} aria-label="Subject" />
          <textarea className={`${inputCls} min-h-40 leading-relaxed`} value={body} onChange={(e) => setBody(e.target.value)} aria-label="Body" />
        </>
      ) : (
        <div className="rounded-md bg-paper p-3 text-sm">
          <p className="text-xs text-ink-3">To {comm.to.join(", ") || "—"}</p>
          <p className="mt-1 font-medium">{comm.subject}</p>
          <pre className="mt-2 whitespace-pre-wrap font-[inherit] leading-relaxed text-ink-2">{comm.body}</pre>
        </div>
      )}
      {comm.last_error && <p className="text-xs text-signal">Last error: {comm.last_error}</p>}
      {editable && (
        <div className="flex justify-end gap-2">
          {comm.status === "dead" && <Button variant="secondary" size="sm" onClick={() => fallback.mutate()}>Deliver in-app instead</Button>}
          <Button size="sm" disabled={send.isPending} onClick={() => send.mutate()}><Send size={13} /> {comm.status === "draft" ? "Approve & send" : "Retry now"}</Button>
        </div>
      )}
    </div>
  );
}

function CommsTab({ c, act }: { c: CaseDetail; act: ReturnType<typeof useAct> }) {
  const [aud, setAud] = useState("store_manager");
  return (
    <div className="space-y-4">
      <p className="text-xs text-ink-3">Drafts are written by the communications agent from verified facts only. Nothing leaves without a person clicking send; internal approval notices are sent by the system.</p>
      {c.communications.length === 0 && <Empty title="No communications yet" />}
      {[...c.communications].reverse().map((m) => (
        <Panel key={m.id}><div className="px-4 py-3"><CommEditor comm={m} canWrite={c.can_write} /></div></Panel>
      ))}
      {c.can_write && (
        <div className="flex flex-wrap items-end gap-2">
          <Field label="Draft another message">
            <select className={inputCls} value={aud} onChange={(e) => setAud(e.target.value)}>
              {["store_manager", "supplier", "status_update", "management", "clarification", "requester_ack"].map((a) => <option key={a} value={a}>{AUDIENCE[a]}</option>)}
            </select>
          </Field>
          <Button variant="secondary" onClick={() => act.mutate({ url: `/api/requests/${c.id}/comms/generate`, body: { audience: aud }, ok: "Draft ready" })}><Sparkles size={14} /> Draft</Button>
        </div>
      )}
    </div>
  );
}

/* ====================================================================== timeline */

const AGENT_NAME: Record<string, string> = {
  guard: "Guard", intake: "Intake & extraction", correlation: "Correlation", risk: "Risk & impact", planner: "Action planner",
  reviewer: "Reviewer", comms: "Communications", reporter: "Reporting",
};

function Timeline({ events, runs }: { events: AuditEvent[]; runs: AgentRun[] }) {
  const byId = useMemo(() => Object.fromEntries(runs.map((r) => [String(r.id), r])), [runs]);
  const [open, setOpen] = useState<number | null>(null);
  const tokens = runs.reduce((s, r) => s + r.tokens_in + r.tokens_out, 0);
  return (
    <Panel title="Agent & activity timeline" action={<span className="text-[11px] text-ink-3">{runs.length} agent runs · {tokens.toLocaleString("en-IN")} tokens</span>}>
      <ol className="relative px-4 py-3">
        {events.map((e, idx) => {
          const run = e.entity === "agent_run" ? byId[e.entity_id || ""] : undefined;
          const isOpen = open === e.id;
          return (
            <Fragment key={e.id}>
              <li className="relative flex gap-2.5 pb-3">
                {idx < events.length - 1 && <span className="absolute left-[9px] top-6 h-[calc(100%-16px)] w-px bg-rule" aria-hidden />}
                <ActorDot type={e.actor_type} />
                <div className="min-w-0 flex-1">
                  <div className="flex items-baseline justify-between gap-2">
                    <p className={clsx("text-[13px] leading-snug", e.pinned && "font-medium")}>
                      {run ? <span className="font-medium">{AGENT_NAME[run.agent] || run.agent}</span> : null}
                      {run ? <span className="text-ink-2"> · {run.step}</span> : e.summary}
                    </p>
                    <time className="shrink-0 text-[11px] tabular-nums text-ink-4">{time(e.ts)}</time>
                  </div>
                  {run ? (
                    <>
                      <p className="mt-0.5 flex flex-wrap gap-x-2 text-[11px] text-ink-3">
                        <span className={run.status === "ok" ? "text-go" : "text-signal"}>{run.status === "ok" ? "ok" : run.status}</span>
                        <span>{run.model}</span>
                        {run.fallback_used && <span className="font-medium text-amber">fallback model</span>}
                        <span>{(run.latency_ms / 1000).toFixed(1)} s</span>
                        {run.confidence != null && <span>conf {pct(run.confidence)}</span>}
                        {run.validation_retries > 0 && <span className="text-amber">{run.validation_retries} self-correction{run.validation_retries > 1 ? "s" : ""}</span>}
                        {run.tool_calls.length > 0 && <span>{run.tool_calls.length} tool call{run.tool_calls.length > 1 ? "s" : ""}</span>}
                      </p>
                      <button onClick={() => setOpen(isOpen ? null : e.id)} className="mt-0.5 text-[11px] text-teal hover:underline">{isOpen ? "Hide" : "Inspect"} input & output</button>
                      {isOpen && <RunDetail run={run} />}
                    </>
                  ) : (
                    e.actor_type !== "system" || e.pinned ? <p className="text-[11px] text-ink-3">{e.actor_id}</p> : null
                  )}
                </div>
              </li>
            </Fragment>
          );
        })}
      </ol>
    </Panel>
  );
}

function RunDetail({ run }: { run: AgentRun }) {
  return (
    <div className="mt-1.5 space-y-2 rounded-md bg-paper p-2 text-[11px]">
      <p className="text-ink-3">Prompt {run.prompt_version} · facts v{run.ledger_version} · input #{run.input_hash.slice(0, 10)} · {run.tokens_in}→{run.tokens_out} tokens</p>
      {run.error && <p className="text-signal">{run.error}</p>}
      {run.tool_calls.map((t, i) => (
        <div key={i}>
          <p className="font-medium">Tool: {t.tool}({JSON.stringify(t.args)})</p>
          <pre className="max-h-32 overflow-auto text-ink-2">{JSON.stringify(t.result, null, 1)}</pre>
        </div>
      ))}
      {run.output && <><p className="font-medium">Output</p><pre className="max-h-64 overflow-auto text-ink-2">{JSON.stringify(run.output, null, 1)}</pre></>}
    </div>
  );
}
