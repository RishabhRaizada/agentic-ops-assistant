import clsx from "clsx";
import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { canWrite, useAuth } from "../lib/auth";
import { relative } from "../lib/format";
import type { Comm } from "../lib/types";
import { Empty, Panel } from "../components/ui";
import { CommEditor, CommStatus } from "./RequestDetail";

const FILTERS = [
  { id: "", label: "All" }, { id: "draft", label: "Drafts" }, { id: "failed,dead", label: "Failed" }, { id: "sent", label: "Sent" },
];

export default function Communications() {
  const { user } = useAuth();
  const [f, setF] = useState("");
  const [sel, setSel] = useState<number | null>(null);
  const q = useQuery({ queryKey: ["comms", f], queryFn: () => api.get<Comm[]>(`/api/communications${f ? `?status=${f}` : ""}`), refetchInterval: 5000 });
  const cur = q.data?.find((c) => c.id === sel) || q.data?.[0];
  return (
    <div className="mx-auto max-w-7xl">
      <h1 className="text-2xl font-semibold tracking-tight">Communications</h1>
      <p className="text-sm text-ink-3">Every stakeholder message: drafts awaiting a human, deliveries, retries and failures.</p>
      <div className="mt-5 flex w-fit gap-1 rounded-lg border border-rule bg-sheet p-1 text-[13px]">
        {FILTERS.map((x) => (
          <button key={x.id} onClick={() => { setF(x.id); setSel(null); }} className={clsx("rounded-md px-3 py-1", f === x.id ? "bg-ink text-white" : "text-ink-2 hover:bg-rule-2")}>{x.label}</button>
        ))}
      </div>
      <div className="mt-4 grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.3fr)]">
        <Panel>
          {q.data?.length ? (
            <ul className="divide-y divide-rule-2">
              {q.data.map((c) => (
                <li key={c.id}>
                  <button onClick={() => setSel(c.id)} className={clsx("w-full px-4 py-2.5 text-left", cur?.id === c.id ? "bg-teal-soft" : "hover:bg-paper")}>
                    <span className="flex items-center justify-between gap-2">
                      <span className="truncate text-sm font-medium">{c.subject}</span>
                      <CommStatus s={c} />
                    </span>
                    <span className="block truncate text-xs text-ink-3">{c.case_ref} · to {c.to.join(", ") || "—"} · {relative(c.created_at)}</span>
                  </button>
                </li>
              ))}
            </ul>
          ) : <div className="p-4"><Empty title="No messages" /></div>}
        </Panel>
        {cur && (
          <Panel title={cur.case_id ? <Link to={`/requests/${cur.case_id}`} className="hover:text-teal">{cur.case_ref}</Link> : "System message"}>
            <div className="px-4 py-3"><CommEditor key={cur.id + cur.status} comm={cur} canWrite={canWrite(user)} /></div>
          </Panel>
        )}
      </div>
    </div>
  );
}
