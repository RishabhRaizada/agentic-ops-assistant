import { useMutation, useQuery } from "@tanstack/react-query";
import { toast } from "sonner";
import { Area, AreaChart, Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Sparkles } from "lucide-react";
import { api } from "../lib/api";
import { inr, pct, titleCase } from "../lib/format";
import type { Metrics } from "../lib/metrics";
import { Button, Panel } from "../components/ui";

interface Report { headline: string; bullets: string[]; risks: string[]; asks: string[]; generated_by: string }

const axis = { fontSize: 11, fill: "#66717e" };

export default function Analytics() {
  const m = useQuery({ queryKey: ["metrics"], queryFn: () => api.get<Metrics>("/api/analytics/summary"), refetchInterval: 10000 });
  const rep = useMutation({ mutationFn: () => api.post<Report>("/api/reports/management"), onError: (e: Error) => toast.error(e.message) });
  const d = m.data;
  if (!d) return <p className="text-sm text-ink-3">Loading…</p>;
  const q = d.ai_quality;
  const cyc = [
    { k: "Intake → triage", v: d.cycle_times_h.intake_to_triage },
    { k: "Intake → decision", v: d.cycle_times_h.intake_to_decision },
    { k: "Approval turnaround", v: d.cycle_times_h.approval_turnaround },
    { k: "Intake → close", v: d.cycle_times_h.intake_to_close },
  ];
  const regions = Object.entries(d.by_region).map(([k, v]) => ({ name: k, v }));
  return (
    <div className="mx-auto max-w-7xl space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Analytics & reporting</h1>
          <p className="text-sm text-ink-3">Throughput, SLA performance and how much the team can trust the agents.</p>
        </div>
        <Button onClick={() => rep.mutate()} disabled={rep.isPending}><Sparkles size={15} /> {rep.isPending ? "Writing…" : "Generate management summary"}</Button>
      </div>

      {rep.data && (
        <Panel title="Management summary" action={<span className="text-xs text-ink-3">{rep.data.generated_by === "ai" ? "Written by the reporting agent from computed figures" : "Template (AI unavailable)"}</span>}>
          <div className="px-5 py-4 text-sm">
            <p className="text-[16px] font-semibold">{rep.data.headline}</p>
            <ul className="mt-2 list-disc space-y-1 pl-5 text-ink-2">{rep.data.bullets.map((b) => <li key={b}>{b}</li>)}</ul>
            {!!rep.data.risks.length && <><p className="mt-3 text-xs font-semibold text-signal">Risks</p><ul className="list-disc pl-5 text-ink-2">{rep.data.risks.map((b) => <li key={b}>{b}</li>)}</ul></>}
            {!!rep.data.asks.length && <><p className="mt-3 text-xs font-semibold">Asks of management</p><ul className="list-disc pl-5 text-ink-2">{rep.data.asks.map((b) => <li key={b}>{b}</li>)}</ul></>}
            <Button size="sm" variant="secondary" className="mt-4" onClick={() => {
              navigator.clipboard.writeText([rep.data!.headline, ...rep.data!.bullets.map((b) => `- ${b}`), ...rep.data!.risks.map((b) => `- Risk: ${b}`), ...rep.data!.asks.map((b) => `- Ask: ${b}`)].join("\n"));
              toast.success("Copied");
            }}>Copy as text</Button>
          </div>
        </Panel>
      )}

      <div className="grid gap-5 lg:grid-cols-2">
        <Panel title="Requests received (last 14 days)">
          <div className="h-52 px-2 py-3">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={d.volume_14d.map((x) => ({ ...x, label: x.date.slice(5) }))}>
                <CartesianGrid stroke="#eceeeb" vertical={false} />
                <XAxis dataKey="label" tick={axis} axisLine={false} tickLine={false} />
                <YAxis allowDecimals={false} tick={axis} axisLine={false} tickLine={false} width={28} />
                <Tooltip contentStyle={{ fontSize: 12 }} />
                <Area dataKey="count" name="Requests" stroke="#0e5e6f" fill="#e3eff1" strokeWidth={2} />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </Panel>
        <Panel title="Average cycle time (hours)">
          <div className="h-52 px-2 py-3">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={cyc.map((c) => ({ ...c, v: c.v ?? 0 }))}>
                <CartesianGrid stroke="#eceeeb" vertical={false} />
                <XAxis dataKey="k" tick={axis} axisLine={false} tickLine={false} />
                <YAxis tick={axis} axisLine={false} tickLine={false} width={28} />
                <Tooltip contentStyle={{ fontSize: 12 }} formatter={(v: number) => `${v.toFixed(1)} h`} />
                <Bar dataKey="v" name="Hours" fill="#1b2430" radius={[3, 3, 0, 0]} barSize={28} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Panel>
      </div>

      <div className="grid gap-5 lg:grid-cols-3">
        <Panel title="SLA">
          <dl className="space-y-2 px-4 py-3 text-sm">
            <div className="flex justify-between"><dt className="text-ink-3">Approvals decided on time</dt><dd className="font-semibold">{pct(d.sla.on_time_rate)}</dd></div>
            <div className="flex justify-between"><dt className="text-ink-3">Decided</dt><dd>{d.sla.decided}</dd></div>
            <div className="flex justify-between"><dt className="text-ink-3">Escalations</dt><dd>{d.sla.escalations}</dd></div>
            <div className="flex justify-between"><dt className="text-ink-3">Open SLA breaches</dt><dd className={d.totals.sla_breached ? "font-semibold text-signal" : ""}>{d.totals.sla_breached}</dd></div>
            <div className="flex justify-between"><dt className="text-ink-3">Duplicates merged</dt><dd>{d.totals.duplicates_merged}</dd></div>
          </dl>
        </Panel>
        <Panel title="Requests by region">
          <div className="h-40 px-2 py-2">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={regions}>
                <XAxis dataKey="name" tick={axis} axisLine={false} tickLine={false} />
                <YAxis allowDecimals={false} tick={axis} axisLine={false} tickLine={false} width={24} />
                <Tooltip contentStyle={{ fontSize: 12 }} />
                <Bar dataKey="v" name="Requests" fill="#0e5e6f" radius={[3, 3, 0, 0]} barSize={26} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Panel>
        <Panel title="AI quality">
          <dl className="space-y-2 px-4 py-3 text-sm">
            {[
              ["Agent runs", q.agent_runs],
              ["Successful runs", pct(q.success_rate)],
              ["Answered by fallback model", pct(q.fallback_rate)],
              ["Runs that self-corrected", pct(q.validation_retry_rate)],
              ["Fields corrected by people", pct(q.human_override_rate)],
              ["Extractions without supporting text", pct(q.ungrounded_rate)],
              ["Approved options that were the AI's pick", pct(q.ai_recommendation_acceptance)],
              ["Estimated model cost", inr(q.est_cost_inr)],
            ].map(([k, v]) => <div key={k as string} className="flex justify-between gap-3"><dt className="text-ink-3">{k}</dt><dd className="font-medium tabular-nums">{v}</dd></div>)}
          </dl>
        </Panel>
      </div>

      <Panel title="Per-agent telemetry">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[560px] text-sm">
            <thead><tr className="border-b border-rule text-left text-xs text-ink-3"><th className="px-4 py-2 font-medium">Agent</th><th className="px-2 py-2 text-right font-medium">Runs</th><th className="px-2 py-2 text-right font-medium">Avg latency</th><th className="px-2 py-2 text-right font-medium">Failures</th><th className="px-4 py-2 text-right font-medium">Fallbacks</th></tr></thead>
            <tbody>
              {Object.entries(q.per_agent).map(([a, s]) => (
                <tr key={a} className="border-b border-rule-2 last:border-0">
                  <td className="px-4 py-2">{titleCase(a)}</td><td className="px-2 py-2 text-right">{s.runs}</td>
                  <td className="px-2 py-2 text-right">{s.avg_latency_ms ? `${(s.avg_latency_ms / 1000).toFixed(1)} s` : "—"}</td>
                  <td className={`px-2 py-2 text-right ${s.failures ? "text-signal" : ""}`}>{s.failures}</td><td className="px-4 py-2 text-right">{s.fallbacks}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
    </div>
  );
}
