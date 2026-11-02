import clsx from "clsx";
import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Check, MessageCircleQuestion, X } from "lucide-react";
import { api } from "../lib/api";
import { canApprove, useAuth } from "../lib/auth";
import { inr, inrShort, relative, ROLE_LABEL } from "../lib/format";
import type { Approval } from "../lib/types";
import { Button, Empty, Field, inputCls, Modal, RiskBadge, SlaTimer } from "../components/ui";
import { ApprovalStatus } from "./RequestDetail";
import { useServerSettings } from "../components/Layout";

type Scope = "mine" | "pending" | "history";

export default function Approvals() {
  const { user } = useAuth();
  const [params] = useSearchParams();
  const [scope, setScope] = useState<Scope>(canApprove(user) ? "mine" : "pending");
  const q = useQuery({ queryKey: ["approvals", scope], queryFn: () => api.get<Approval[]>(`/api/approvals?scope=${scope}`), refetchInterval: 5000 });
  const focus = Number(params.get("focus"));
  return (
    <div className="mx-auto max-w-5xl">
      <h1 className="text-2xl font-semibold tracking-tight">Approvals</h1>
      <p className="text-sm text-ink-3">
        {canApprove(user)
          ? `You can approve up to ${inrShort(user!.approval_limit_inr)} in ${user!.region === "ALL" ? "all regions" : `the ${user!.region} region`}. Requests you raised go to someone else.`
          : "Your role cannot approve spend. You can follow every approval here."}
      </p>
      <div className="mt-5 flex gap-1 rounded-lg border border-rule bg-sheet p-1 text-[13px] w-fit">
        {(["mine", "pending", "history"] as Scope[]).map((s) => (
          <button key={s} onClick={() => setScope(s)} className={clsx("rounded-md px-3 py-1", scope === s ? "bg-ink text-white" : "text-ink-2 hover:bg-rule-2")}>
            {s === "mine" ? "Waiting for me" : s === "pending" ? "All pending" : "Decided"}
          </button>
        ))}
      </div>
      <div className="mt-4 space-y-3">
        {q.data?.length === 0 && <Empty title={scope === "mine" ? "Nothing waiting for your decision" : "No approvals here"} />}
        {q.data?.map((a) => <ApprovalCard key={a.id} a={a} focused={a.id === focus} actionable={scope !== "history" && canApprove(user)} />)}
      </div>
    </div>
  );
}

function ApprovalCard({ a, focused, actionable }: { a: Approval; focused: boolean; actionable: boolean }) {
  const qc = useQueryClient();
  const settings = useServerSettings();
  const [mode, setMode] = useState<null | "reject" | "request_info">(null);
  const [reason, setReason] = useState("");
  const [cap, setCap] = useState("");
  const decide = useMutation({
    mutationFn: (decision: string) =>
      api.post(`/api/approvals/${a.id}/decision`, { decision, reason, budget_cap_inr: cap ? Number(cap.replace(/[^\d.]/g, "")) : null },
        { "Idempotency-Key": `${a.id}-${decision}-${crypto.randomUUID?.() ?? Date.now()}` }),
    onSuccess: (_r, decision) => {
      toast.success(decision === "approve" ? "Approved - execution started" : decision === "reject" ? "Rejected - the planner is preparing alternatives" : "Question sent to the analyst");
      setMode(null);
      qc.invalidateQueries();
    },
    onError: (e: Error) => toast.error(e.message, { duration: 8000 }),
  });
  const o = a.option;
  const c = a.case;
  return (
    <article className={clsx("rounded-lg border bg-sheet", focused ? "border-teal ring-2 ring-teal/20" : "border-rule")}>
      <div className="flex flex-wrap items-start justify-between gap-3 px-5 pt-4">
        <div className="min-w-0">
          {c && (
            <Link to={`/requests/${c.id}`} className="text-sm hover:text-teal">
              <span className="label mr-1.5 text-[15px] font-semibold text-ink-3">{c.ref}</span>{c.title}
            </Link>
          )}
          <h2 className="mt-1 text-[17px] font-semibold">{o?.title}</h2>
          <p className="mt-0.5 text-xs text-ink-3">
            Requested by {a.requested_by?.name || "the system"} {relative(a.created_at)} · routed to {a.assigned_to?.name || ROLE_LABEL[a.required_role]} ({ROLE_LABEL[a.required_role]})
            {a.escalation_level > 0 && <span className="font-medium text-amber"> · escalated</span>}
          </p>
        </div>
        <div className="text-right">
          <p className="label text-[28px] font-semibold leading-none">{inr(a.amount_inr)}</p>
          <div className="mt-1 flex items-center justify-end gap-2">
            {c && <RiskBadge level={c.risk_level} priority={c.priority} />}
            {a.status === "pending" ? <SlaTimer due={a.due_at} offsetSeconds={settings.data?.clock_offset_seconds} /> : <ApprovalStatus a={a} />}
          </div>
        </div>
      </div>
      {o && (
        <div className="grid gap-4 px-5 py-3 text-sm md:grid-cols-[1.3fr_1fr]">
          <div>
            <p className="leading-relaxed text-ink-2">{o.rationale}</p>
            <ol className="mt-2 list-decimal space-y-0.5 pl-5 text-ink-2">{o.steps.map((s) => <li key={s}>{s}</li>)}</ol>
          </div>
          <dl className="space-y-1.5 text-[13px]">
            <div className="flex justify-between gap-2"><dt className="text-ink-3">Covers</dt><dd>{o.covers_qty} units in {o.eta_days} days {o.meets_deadline ? "" : "(misses event date)"}</dd></div>
            <div className="flex justify-between gap-2"><dt className="text-ink-3">Cost working</dt><dd className="text-right">{o.cost_breakdown}</dd></div>
            <div><dt className="text-ink-3">Why you are asked</dt><dd><ul className="list-disc pl-4">{a.reasons.map((r) => <li key={r}>{r}</li>)}</ul></dd></div>
            {a.review?.verdict && <div className="flex justify-between gap-2"><dt className="text-ink-3">Reviewer agent</dt><dd>{a.review.verdict}</dd></div>}
            {!a.is_recommended && o.source !== "human" && <p className="text-amber">The analyst chose this over the AI's recommended option.</p>}
            {o.source === "human" && <p className="text-pen">Operator-defined option.</p>}
          </dl>
        </div>
      )}
      {a.decision_reason && a.status !== "pending" && <p className="px-5 pb-3 text-sm text-ink-2">“{a.decision_reason}” - {a.decided_by?.name}</p>}
      {actionable && a.status === "pending" && (
        <div className="flex flex-wrap justify-end gap-2 border-t border-rule-2 px-5 py-3">
          <Button variant="ghost" onClick={() => { setMode("request_info"); setReason(""); }}><MessageCircleQuestion size={15} /> Ask a question</Button>
          <Button variant="danger" onClick={() => { setMode("reject"); setReason(""); }}><X size={15} /> Reject</Button>
          <Button disabled={decide.isPending} onClick={() => decide.mutate("approve")}><Check size={15} /> Approve {inr(a.amount_inr)}</Button>
        </div>
      )}
      <Modal open={!!mode} onClose={() => setMode(null)} title={mode === "reject" ? "Reject this option" : "Ask the analyst a question"}>
        <div className="space-y-3">
          {mode === "reject" && <p className="text-sm text-ink-2">The planner will prepare alternatives that address your reason and stay within any budget cap you set.</p>}
          <Field label={mode === "reject" ? "Reason (required)" : "Question (required)"}>
            <textarea className={`${inputCls} min-h-20`} value={reason} onChange={(e) => setReason(e.target.value)} autoFocus />
          </Field>
          {mode === "reject" && <Field label="Budget cap for alternatives (optional, ₹)"><input className={inputCls} inputMode="numeric" value={cap} onChange={(e) => setCap(e.target.value)} placeholder="e.g. 50000" /></Field>}
          <div className="flex justify-end">
            <Button variant={mode === "reject" ? "danger" : "primary"} disabled={!reason.trim() || decide.isPending} onClick={() => decide.mutate(mode!)}>
              {mode === "reject" ? "Reject and re-plan" : "Send question"}
            </Button>
          </div>
        </div>
      </Modal>
    </article>
  );
}
