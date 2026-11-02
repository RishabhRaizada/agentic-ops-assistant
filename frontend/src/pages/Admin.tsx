import { useState } from "react";
import { Navigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { inr, ROLE_LABEL } from "../lib/format";
import { Button, Panel } from "../components/ui";

interface S {
  chaos: Record<string, boolean | number>;
  approval_matrix: { max_inr: number | null; role: string }[];
  approval_sla_hours: Record<string, number>;
  conf_high: number; conf_block: number; clock_offset_seconds: number; now: string; local_now: string;
  llm: { configured: boolean; primary: string; fallback: string };
}

const CHAOS = [
  ["llm_primary_down", "Primary AI model down", "Agents switch to the fallback model."],
  ["llm_all_down", "All AI models down", "Requests are processed by deterministic rules and sent to human review."],
  ["inventory_down", "Inventory system down", "Stock figures can't be verified; the circuit breaker opens."],
  ["mail_down", "Email delivery failing", "Outbound email retries with backoff, then dead-letters."],
] as const;

export default function Admin() {
  const { user } = useAuth();
  const qc = useQueryClient();
  const s = useQuery({ queryKey: ["settings"], queryFn: () => api.get<S>("/api/admin/settings") });
  const [matrix, setMatrix] = useState<S["approval_matrix"] | null>(null);
  const put = useMutation({
    mutationFn: ({ key, value }: { key: string; value: unknown }) => api.put(`/api/admin/settings/${key}`, { value }),
    onSuccess: () => { qc.invalidateQueries(); toast.success("Saved"); },
    onError: (e: Error) => toast.error(e.message),
  });
  const clock = useMutation({
    mutationFn: (body: { advance_minutes?: number; reset?: boolean }) => api.post<{ sla_actions: number }>("/api/admin/clock", body),
    onSuccess: (r) => { qc.invalidateQueries(); toast.success(`Clock moved. SLA monitor took ${r.sla_actions} action(s).`); },
  });
  const reset = useMutation({
    mutationFn: () => api.post("/api/admin/reset-demo"),
    onSuccess: () => { toast.success("Demo data reset"); setTimeout(() => (window.location.href = "/login"), 600); },
  });
  if (user && user.role !== "admin") return <Navigate to="/" />;
  const d = s.data;
  if (!d) return <p className="text-sm text-ink-3">Loading…</p>;
  const m = matrix || d.approval_matrix;
  return (
    <div className="mx-auto max-w-4xl space-y-5">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Admin & demo controls</h1>
        <p className="text-sm text-ink-3">Policy settings, fault injection for resilience testing, and the demo clock. Every change is audited.</p>
      </div>

      <Panel title="Fault injection">
        <ul className="divide-y divide-rule-2">
          {CHAOS.map(([k, label, help]) => (
            <li key={k} className="flex items-center justify-between gap-3 px-4 py-3">
              <div><p className="text-sm font-medium">{label}</p><p className="text-xs text-ink-3">{help}</p></div>
              <button role="switch" aria-checked={!!d.chaos[k]} onClick={() => put.mutate({ key: "chaos", value: { [k]: !d.chaos[k] } })}
                className={`relative h-6 w-11 rounded-full transition-colors ${d.chaos[k] ? "bg-signal" : "bg-rule"}`}>
                <span className={`absolute top-0.5 size-5 rounded-full bg-white shadow transition-all ${d.chaos[k] ? "left-[22px]" : "left-0.5"}`} />
              </button>
            </li>
          ))}
        </ul>
      </Panel>

      <Panel title="Demo clock">
        <div className="space-y-3 px-4 py-3 text-sm">
          <p className="text-ink-2">Move time forward to watch approval reminders, escalations and retries happen. Server time now: <span className="font-medium">{new Date(d.now).toLocaleString("en-IN", { timeZone: "Asia/Kolkata" })}</span>{d.clock_offset_seconds ? ` (shifted +${(d.clock_offset_seconds / 3600).toFixed(2)} h)` : ""}.</p>
          <div className="flex flex-wrap gap-2">
            {[15, 60, 180, 1440].map((n) => <Button key={n} variant="secondary" size="sm" onClick={() => clock.mutate({ advance_minutes: n })}>+{n >= 60 ? `${n / 60} h` : `${n} min`}</Button>)}
            <Button variant="ghost" size="sm" onClick={() => clock.mutate({ reset: true })}>Back to real time</Button>
          </div>
        </div>
      </Panel>

      <Panel title="Approval matrix" action={matrix && <Button size="sm" onClick={() => { put.mutate({ key: "approval_matrix", value: matrix }); setMatrix(null); }}>Save matrix</Button>}>
        <table className="w-full text-sm">
          <tbody>
            {m.map((b, i) => (
              <tr key={i} className="border-b border-rule-2 last:border-0">
                <td className="px-4 py-2 text-ink-2">{i === 0 ? "Up to" : b.max_inr === null ? "Above" : "Up to"}</td>
                <td className="px-2 py-2">
                  {b.max_inr === null ? inr(m[i - 1]?.max_inr) : (
                    <input className="w-32 rounded border border-rule px-2 py-1" inputMode="numeric" value={b.max_inr}
                      onChange={(e) => setMatrix(m.map((x, j) => (j === i ? { ...x, max_inr: Number(e.target.value.replace(/\D/g, "")) } : x)))} />
                  )}
                </td>
                <td className="px-4 py-2 font-medium">{ROLE_LABEL[b.role]}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="border-t border-rule-2 px-4 py-2 text-xs text-ink-3">
          Approval is also mandatory for high/critical risk, customer commitments, supplier order changes, confidence below {Math.round(d.conf_high * 100)}%, suspected injection and degraded mode.
          Approver SLA by priority: {Object.entries(d.approval_sla_hours).map(([p, h]) => `${p} ${h} h`).join(", ")}.
        </p>
      </Panel>

      <Panel title="AI models">
        <p className="px-4 py-3 text-sm text-ink-2">
          {d.llm.configured ? <>Primary <span className="font-medium">{d.llm.primary}</span>, fallback <span className="font-medium">{d.llm.fallback}</span>, then deterministic rules.</> : "No model configured: running in rules-only mode."}
        </p>
      </Panel>

      <Panel title="Reset demo">
        <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-3">
          <p className="text-sm text-ink-2">Delete every request and re-seed master data and history. Signs everyone out.</p>
          <Button variant="danger" onClick={() => confirm("Reset all demo data?") && reset.mutate()}>Reset demo data</Button>
        </div>
      </Panel>
    </div>
  );
}
