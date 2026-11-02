import { useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Copy, FileUp, Mailbox, Plus, ShieldAlert, Unplug } from "lucide-react";
import { api } from "../lib/api";
import { canWrite, useAuth } from "../lib/auth";
import { relative, titleCase } from "../lib/format";
import type { CaseSummary, Scenario } from "../lib/types";
import { Button, ConfidenceMeter, Empty, Field, inputCls, Modal, RiskBadge, StatusBadge } from "../components/ui";

const FILTERS = [
  { id: "open", label: "Open", status: "" },
  { id: "attention", label: "Needs attention", status: "NEEDS_REVIEW,NEEDS_INFO,READY_FOR_APPROVAL,ON_HOLD" },
  { id: "approval", label: "Awaiting approval", status: "AWAITING_APPROVAL" },
  { id: "progress", label: "In progress", status: "APPROVED,IN_PROGRESS,RESOLVED" },
  { id: "done", label: "Closed", status: "CLOSED,CANCELLED,MERGED" },
  { id: "all", label: "All", status: "" },
];

export function Flags({ c }: { c: CaseSummary }) {
  const f = c.flags || {};
  return (
    <span className="flex flex-wrap gap-1">
      {f.injection_suspected && <span title="Suspicious content" className="inline-flex items-center gap-0.5 rounded bg-signal-soft px-1 text-[11px] text-signal"><ShieldAlert size={11} />Injection</span>}
      {f.degraded_mode && <span title="Processed without AI" className="inline-flex items-center gap-0.5 rounded bg-amber-soft px-1 text-[11px] text-amber"><Unplug size={11} />Degraded</span>}
      {f.escalated && <span className="rounded bg-pen-soft px-1 text-[11px] text-pen">Escalated</span>}
      {f.sla_breached && <span className="rounded bg-signal px-1 text-[11px] text-white">SLA breach</span>}
      {c.message_count > 1 && <span title="Linked messages" className="inline-flex items-center gap-0.5 rounded bg-rule-2 px-1 text-[11px] text-ink-2"><Copy size={11} />{c.message_count}</span>}
      {c.blocking_issue_count > 0 && <span className="rounded bg-amber-soft px-1 text-[11px] text-amber">{c.blocking_issue_count} blocking</span>}
    </span>
  );
}

export function CaseTable({ rows, compact }: { rows: CaseSummary[]; compact?: boolean }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[760px] text-sm">
        <thead>
          <tr className="border-b border-rule text-left text-xs text-ink-3">
            <th className="px-4 py-2 font-medium">Request</th>
            <th className="px-2 py-2 font-medium">Status</th>
            <th className="px-2 py-2 font-medium">Risk</th>
            {!compact && <th className="px-2 py-2 font-medium">Confidence</th>}
            <th className="px-2 py-2 font-medium">Region</th>
            {!compact && <th className="px-2 py-2 font-medium">Flags</th>}
            <th className="px-4 py-2 text-right font-medium">Received</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((c) => (
            <tr key={c.id} className="border-b border-rule-2 last:border-0 hover:bg-paper">
              <td className="max-w-[420px] px-4 py-2.5">
                <Link to={`/requests/${c.id}`} className="group block">
                  <span className="label mr-2 text-[15px] font-semibold text-ink-3 group-hover:text-teal">{c.ref}</span>
                  <span className="font-medium group-hover:text-teal">{c.title}</span>
                  <span className="block truncate text-xs text-ink-3">{c.from} · {titleCase(c.category)}</span>
                </Link>
              </td>
              <td className="px-2 py-2.5"><StatusBadge status={c.status} processing={c.processing} /></td>
              <td className="px-2 py-2.5"><RiskBadge level={c.risk_level} priority={c.priority} /></td>
              {!compact && <td className="px-2 py-2.5"><ConfidenceMeter value={c.overall_confidence} compact /></td>}
              <td className="px-2 py-2.5 text-xs text-ink-2">{c.region || "—"}</td>
              {!compact && <td className="px-2 py-2.5"><Flags c={c} /></td>}
              <td className="whitespace-nowrap px-4 py-2.5 text-right text-xs text-ink-3">{relative(c.created_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function Requests() {
  const { user } = useAuth();
  const [params, setParams] = useSearchParams();
  const filter = params.get("f") || "open";
  const [q, setQ] = useState("");
  const [newOpen, setNewOpen] = useState(params.get("new") === "1");
  const [inboxOpen, setInboxOpen] = useState(params.get("inbox") === "1");
  const f = FILTERS.find((x) => x.id === filter)!;
  const list = useQuery({
    queryKey: ["cases", filter, q],
    queryFn: () => api.get<CaseSummary[]>(`/api/requests?${new URLSearchParams({ ...(f.status ? { status: f.status } : {}), ...(filter === "open" ? { include_closed: "false" } : {}), ...(q ? { q } : {}) })}`),
    refetchInterval: 4000,
  });
  const rows = useMemo(() => (list.data || []).filter((c) => filter !== "open" || c.status !== "MERGED"), [list.data, filter]);

  return (
    <div className="mx-auto max-w-7xl">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Requests</h1>
          <p className="text-sm text-ink-3">Every operational request, from receipt to closure.</p>
        </div>
        {canWrite(user) || user?.role === "admin" ? (
          <div className="flex gap-2">
            <Button variant="secondary" onClick={() => setInboxOpen(true)}><Mailbox size={15} /> Simulated inbox</Button>
            {canWrite(user) && <Button onClick={() => setNewOpen(true)}><Plus size={15} /> New request</Button>}
          </div>
        ) : null}
      </div>

      <div className="mt-5 flex flex-wrap items-center gap-2">
        <div className="flex flex-wrap gap-1 rounded-lg border border-rule bg-sheet p-1">
          {FILTERS.map((x) => (
            <button key={x.id} onClick={() => setParams({ f: x.id })}
              className={`rounded-md px-2.5 py-1 text-[13px] ${filter === x.id ? "bg-ink text-white" : "text-ink-2 hover:bg-rule-2"}`}>
              {x.label}
            </button>
          ))}
        </div>
        <input className={`${inputCls} max-w-xs`} placeholder="Search ref, title or summary" value={q} onChange={(e) => setQ(e.target.value)} />
      </div>

      <div className="mt-4 rounded-lg border border-rule bg-sheet">
        {list.isLoading ? <p className="p-6 text-sm text-ink-3">Loading…</p> : rows.length ? <CaseTable rows={rows} /> : (
          <div className="p-6"><Empty title="No requests here">Ingest an email from the simulated inbox or create a request to see the agents at work.</Empty></div>
        )}
      </div>

      <NewRequest open={newOpen} onClose={() => setNewOpen(false)} />
      <SimulatedInbox open={inboxOpen} onClose={() => setInboxOpen(false)} />
    </div>
  );
}

function NewRequest({ open, onClose }: { open: boolean; onClose: () => void }) {
  const nav = useNavigate();
  const qc = useQueryClient();
  const [form, setForm] = useState({ from_name: "", from_addr: "", subject: "", body: "" });
  const create = useMutation({
    mutationFn: () => api.post<{ id: number; ref: string }>("/api/requests", { ...form, channel: "manual" }),
    onSuccess: (r) => {
      toast.success(`${r.ref} created - agents are analysing it`);
      qc.invalidateQueries({ queryKey: ["cases"] });
      onClose();
      nav(`/requests/${r.id}`);
    },
    onError: (e: Error) => toast.error(e.message),
  });
  const upload = useMutation({
    mutationFn: (file: File) => {
      const fd = new FormData();
      fd.append("file", file);
      return api.post<{ id: number; ref: string }>("/api/requests/upload", fd);
    },
    onSuccess: (r) => {
      toast.success(`${r.ref} created from .eml`);
      onClose();
      nav(`/requests/${r.id}`);
    },
    onError: (e: Error) => toast.error(e.message),
  });
  return (
    <Modal open={open} onClose={onClose} title="New request" wide>
      <p className="mb-4 text-sm text-ink-3">Paste an email or type the request. The agents extract the details; you can correct anything they get wrong.</p>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="From (name)"><input className={inputCls} value={form.from_name} onChange={(e) => setForm({ ...form, from_name: e.target.value })} placeholder="Regional Operations Manager" /></Field>
        <Field label="From (email)"><input className={inputCls} value={form.from_addr} onChange={(e) => setForm({ ...form, from_addr: e.target.value })} placeholder="name@company.com" /></Field>
      </div>
      <div className="mt-3 space-y-3">
        <Field label="Subject"><input className={inputCls} value={form.subject} onChange={(e) => setForm({ ...form, subject: e.target.value })} /></Field>
        <Field label="Message"><textarea className={`${inputCls} min-h-48 font-[inherit] leading-relaxed`} value={form.body} onChange={(e) => setForm({ ...form, body: e.target.value })} /></Field>
      </div>
      <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
        <label className="inline-flex cursor-pointer items-center gap-1.5 text-sm text-teal hover:underline">
          <FileUp size={15} /> Upload .eml instead
          <input type="file" accept=".eml,message/rfc822" className="sr-only" onChange={(e) => e.target.files?.[0] && upload.mutate(e.target.files[0])} />
        </label>
        <Button disabled={!form.subject || form.body.length < 5 || create.isPending} onClick={() => create.mutate()}>
          {create.isPending ? "Creating…" : "Create and analyse"}
        </Button>
      </div>
    </Modal>
  );
}

export function SimulatedInbox({ open, onClose }: { open: boolean; onClose: () => void }) {
  const nav = useNavigate();
  const qc = useQueryClient();
  const sc = useQuery({ queryKey: ["scenarios"], queryFn: () => api.get<Scenario[]>("/api/admin/scenarios"), enabled: open });
  const [sel, setSel] = useState<string>("S1");
  const ingest = useMutation({
    mutationFn: (key: string) => api.post<{ id: number; ref: string }>("/api/admin/scenarios/ingest", { key }),
    onSuccess: (r) => {
      toast.success(`Email received as ${r.ref}`);
      qc.invalidateQueries({ queryKey: ["cases"] });
      onClose();
      nav(`/requests/${r.id}`);
    },
    onError: (e: Error) => toast.error(e.message),
  });
  const cur = sc.data?.find((s) => s.key === sel);
  return (
    <Modal open={open} onClose={onClose} title="Simulated inbox" wide>
      <p className="mb-3 text-sm text-ink-3">Sample emails that arrive at the ops mailbox. Each one exercises part of the workflow; pick one to deliver it.</p>
      <div className="grid gap-4 md:grid-cols-[240px_1fr]">
        <ul className="max-h-[52vh] space-y-0.5 overflow-y-auto scroll-thin">
          {sc.data?.map((s) => (
            <li key={s.key}>
              <button onClick={() => setSel(s.key)} className={`w-full rounded-md px-2.5 py-2 text-left ${sel === s.key ? "bg-teal-soft" : "hover:bg-rule-2"}`}>
                <span className="block truncate text-sm font-medium">{s.subject}</span>
                <span className="block text-xs text-ink-3">{s.tag}</span>
              </button>
            </li>
          ))}
        </ul>
        {cur && (
          <div className="min-w-0 rounded-lg border border-rule bg-paper p-4">
            <p className="text-xs text-ink-3">From {cur.from_name} &lt;{cur.from_addr}&gt;</p>
            <p className="mt-1 font-semibold">{cur.subject}</p>
            <pre className="mt-3 whitespace-pre-wrap font-[inherit] text-sm leading-relaxed text-ink-2">{cur.body}</pre>
            {cur.after && <p className="mt-3 text-xs text-amber">Deliver {cur.after} first - this email refers to that request.</p>}
            <div className="mt-4 flex justify-end">
              <Button disabled={ingest.isPending} onClick={() => ingest.mutate(cur.key)}>{ingest.isPending ? "Delivering…" : "Deliver to ops mailbox"}</Button>
            </div>
          </div>
        )}
      </div>
    </Modal>
  );
}
