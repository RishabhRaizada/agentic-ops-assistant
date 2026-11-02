import clsx from "clsx";
import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { toast } from "sonner";
import { Download, ShieldCheck } from "lucide-react";
import { api } from "../lib/api";
import { dt } from "../lib/format";
import type { AuditEvent } from "../lib/types";
import { ActorDot, Button, inputCls, Panel } from "../components/ui";

export default function Audit() {
  const [actor, setActor] = useState("");
  const [search, setSearch] = useState("");
  const [open, setOpen] = useState<number | null>(null);
  const q = useQuery({
    queryKey: ["audit", actor, search],
    queryFn: () => api.get<AuditEvent[]>(`/api/audit?${new URLSearchParams({ ...(actor ? { actor_type: actor } : {}), ...(search ? { q: search } : {}), limit: "500" })}`),
    refetchInterval: 6000,
  });
  const verify = useMutation({
    mutationFn: () => api.get<{ ok: boolean; checked: number; broken_at_id?: number }>("/api/audit/verify"),
    onSuccess: (r) => (r.ok ? toast.success(`Chain intact: ${r.checked} events verified`) : toast.error(`Tampering detected at event #${r.broken_at_id}`)),
  });
  return (
    <div className="mx-auto max-w-7xl">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Audit trail</h1>
          <p className="max-w-2xl text-sm text-ink-3">Every AI, human and system action. Events are append-only and hash-chained: editing or deleting any past event breaks verification.</p>
        </div>
        <div className="flex gap-2">
          <Button variant="secondary" onClick={() => verify.mutate()}><ShieldCheck size={15} /> Verify chain</Button>
          <a href="/api/audit/export.csv"><Button variant="secondary"><Download size={15} /> Export CSV</Button></a>
        </div>
      </div>
      <div className="mt-5 flex flex-wrap gap-2">
        <div className="flex gap-1 rounded-lg border border-rule bg-sheet p-1 text-[13px]">
          {[["", "Everyone"], ["ai", "AI agents"], ["human", "People"], ["system", "System"]].map(([v, l]) => (
            <button key={v} onClick={() => setActor(v)} className={clsx("rounded-md px-3 py-1", actor === v ? "bg-ink text-white" : "text-ink-2 hover:bg-rule-2")}>{l}</button>
          ))}
        </div>
        <input className={`${inputCls} max-w-xs`} placeholder="Search events" value={search} onChange={(e) => setSearch(e.target.value)} />
      </div>
      <Panel className="mt-4">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[820px] text-sm">
            <thead>
              <tr className="border-b border-rule text-left text-xs text-ink-3">
                <th className="px-4 py-2 font-medium">When</th><th className="px-2 py-2 font-medium">Actor</th><th className="px-2 py-2 font-medium">Request</th>
                <th className="px-2 py-2 font-medium">Event</th><th className="px-4 py-2 text-right font-medium">Hash</th>
              </tr>
            </thead>
            <tbody>
              {q.data?.map((e) => (
                <tr key={e.id} className={clsx("cursor-pointer border-b border-rule-2 align-top last:border-0 hover:bg-paper", e.action === "unauthorized_attempt" && "bg-signal-soft/50")} onClick={() => setOpen(open === e.id ? null : e.id)}>
                  <td className="whitespace-nowrap px-4 py-2 text-xs tabular-nums text-ink-3">{dt(e.ts, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", second: "2-digit" })}</td>
                  <td className="px-2 py-2"><span className="flex items-center gap-1.5"><ActorDot type={e.actor_type} /><span className="max-w-40 truncate text-xs text-ink-2">{e.actor_id}</span></span></td>
                  <td className="px-2 py-2 text-xs">{e.case_id ? <Link onClick={(ev) => ev.stopPropagation()} to={`/requests/${e.case_id}`} className="text-teal hover:underline">{e.case_ref}</Link> : "—"}</td>
                  <td className="px-2 py-2">
                    <p className={clsx(e.pinned && "font-medium")}>{e.summary}</p>
                    <p className="text-[11px] text-ink-4">{e.action}</p>
                    {open === e.id && (
                      <pre className="mt-2 max-h-64 overflow-auto rounded bg-paper p-2 text-[11px] text-ink-2">{JSON.stringify({ before: e.before, after: e.after, meta: e.meta, prev_hash: e.prev_hash }, null, 1)}</pre>
                    )}
                  </td>
                  <td className="px-4 py-2 text-right font-[monospace] text-[11px] text-ink-4">{e.hash.slice(0, 10)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
    </div>
  );
}
