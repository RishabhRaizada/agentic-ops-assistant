import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Bar, BarChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api } from "../lib/api";
import { canApprove, canWrite, useAuth } from "../lib/auth";
import { inr, pct, titleCase } from "../lib/format";
import type { Metrics } from "../lib/metrics";
import type { Approval, CaseSummary } from "../lib/types";
import { Button, Empty, Panel, RiskBadge, SlaTimer } from "../components/ui";
import { CaseTable } from "./Requests";
import { useServerSettings } from "../components/Layout";

export default function Dashboard() {
  const { user } = useAuth();
  const settings = useServerSettings();
  const m = useQuery({ queryKey: ["metrics"], queryFn: () => api.get<Metrics>("/api/analytics/summary"), refetchInterval: 8000 });
  const attention = useQuery({
    queryKey: ["cases", "attention-dash"],
    queryFn: () => api.get<CaseSummary[]>("/api/requests?status=NEEDS_REVIEW,NEEDS_INFO,READY_FOR_APPROVAL,ON_HOLD,REPLANNING,ANALYZING,SCREENING,NEW"),
    refetchInterval: 5000,
  });
  const mine = useQuery({ queryKey: ["approvals", "mine"], queryFn: () => api.get<Approval[]>("/api/approvals?scope=mine"), refetchInterval: 5000 });
  const t = m.data?.totals;
  const hour = new Date().getHours();
  const strip = t ? [
    { k: "Open requests", v: t.open, tone: "" },
    { k: "Critical", v: t.critical_open, tone: t.critical_open ? "text-signal" : "" },
    { k: "Need attention", v: t.needs_attention, tone: t.needs_attention ? "text-amber" : "" },
    { k: "Awaiting approval", v: t.awaiting_approval, tone: "" },
    { k: "Approvals due ≤ 60 min", v: t.approvals_due_60m, tone: t.approvals_due_60m ? "text-amber" : "" },
    { k: "SLA breaches", v: t.sla_breached, tone: t.sla_breached ? "text-signal" : "" },
  ] : [];
  const cats = Object.entries(m.data?.by_category || {}).map(([k, v]) => ({ name: titleCase(k), v })).sort((a, b) => b.v - a.v);
  const q = m.data?.ai_quality;

  return (
    <div className="mx-auto max-w-7xl space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Good {hour < 12 ? "morning" : hour < 17 ? "afternoon" : "evening"}, {user?.name.split(" ")[0]}</h1>
          <p className="text-sm text-ink-3">Here is what needs a decision across operations right now.</p>
        </div>
        {canWrite(user) && <Link to="/requests?inbox=1"><Button>Open simulated inbox</Button></Link>}
      </div>

      <div className="grid grid-cols-2 overflow-hidden rounded-lg border border-rule bg-sheet sm:grid-cols-3 lg:grid-cols-6">
        {strip.map((s, i) => (
          <div key={s.k} className={`px-4 py-3 ${i ? "border-l border-rule-2" : ""} max-lg:border-t max-lg:border-rule-2`}>
            <p className={`label text-[34px] font-semibold leading-none ${s.tone}`}>{s.v}</p>
            <p className="mt-1 text-xs text-ink-3">{s.k}</p>
          </div>
        ))}
      </div>

      <div className="grid gap-5 lg:grid-cols-[1.6fr_1fr]">
        <Panel title="Requests that need a person" action={<Link to="/requests?f=attention" className="text-xs text-teal hover:underline">All requests</Link>}>
          {attention.data?.length ? <CaseTable rows={attention.data.slice(0, 8)} compact /> : <div className="p-4"><Empty title="All clear">No request is waiting on a human.</Empty></div>}
        </Panel>
        <div className="space-y-5">
          {canApprove(user) && (
            <Panel title="Waiting for your approval" action={<Link to="/approvals" className="text-xs text-teal hover:underline">Approval center</Link>}>
              {mine.data?.length ? (
                <ul className="divide-y divide-rule-2">
                  {mine.data.map((a) => (
                    <li key={a.id}>
                      <Link to={`/approvals?focus=${a.id}`} className="flex items-center justify-between gap-3 px-4 py-2.5 hover:bg-paper">
                        <span className="min-w-0">
                          <span className="block truncate text-sm font-medium">{a.option?.title}</span>
                          <span className="block text-xs text-ink-3">{a.case?.ref} · <SlaTimer due={a.due_at} offsetSeconds={settings.data?.clock_offset_seconds} /></span>
                        </span>
                        <span className="label text-lg font-semibold">{inr(a.amount_inr)}</span>
                      </Link>
                    </li>
                  ))}
                </ul>
              ) : <p className="px-4 py-4 text-sm text-ink-3">Nothing waiting for you.</p>}
            </Panel>
          )}
          <Panel title="Open requests by risk">
            <div className="flex flex-wrap gap-3 px-4 py-3">
              {["critical", "high", "medium", "low"].map((r) => (
                <span key={r} className="flex items-center gap-1.5"><RiskBadge level={r} /><span className="label text-xl font-semibold">{m.data?.by_risk[r] || 0}</span></span>
              ))}
            </div>
          </Panel>
          <Panel title="How the AI is doing">
            <dl className="grid grid-cols-2 gap-px bg-rule-2 text-sm">
              {[
                ["Fields corrected by people", pct(q?.human_override_rate)],
                ["AI option accepted", pct(q?.ai_recommendation_acceptance)],
                ["Runs on fallback model", pct(q?.fallback_rate)],
                ["Suspicious emails caught", q?.injection_flags ?? "—"],
              ].map(([k, v]) => (
                <div key={k as string} className="bg-sheet px-4 py-2.5"><dt className="text-xs text-ink-3">{k}</dt><dd className="label text-xl font-semibold">{v}</dd></div>
              ))}
            </dl>
          </Panel>
        </div>
      </div>

      <Panel title="Requests by type (all time)">
        <div className="h-56 px-2 py-3">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={cats} layout="vertical" margin={{ left: 40, right: 16 }}>
              <XAxis type="number" allowDecimals={false} tick={{ fontSize: 11, fill: "#66717e" }} axisLine={false} tickLine={false} />
              <YAxis type="category" dataKey="name" width={150} interval={0} tick={{ fontSize: 12, fill: "#3a4553" }} axisLine={false} tickLine={false} />
              <Tooltip cursor={{ fill: "#eceeeb" }} contentStyle={{ fontSize: 12, borderRadius: 6, border: "1px solid #dfe3df" }} />
              <Bar dataKey="v" name="Requests" fill="#0e5e6f" radius={[0, 3, 3, 0]} barSize={14} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </Panel>
    </div>
  );
}
