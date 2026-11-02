import { useState } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api } from "../lib/api";
import { ROLE_LABEL, inrShort } from "../lib/format";
import type { User } from "../lib/types";
import { Button, Field, inputCls } from "../components/ui";

const PW = "Demo@2026";

export default function Login() {
  const nav = useNavigate();
  const loc = useLocation();
  const qc = useQueryClient();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const demo = useQuery({ queryKey: ["demo-users"], queryFn: () => api.get<User[]>("/api/auth/demo-users") });

  async function signIn(e: string, p: string) {
    setBusy(true);
    try {
      await api.post("/api/auth/login", { email: e, password: p });
      await qc.invalidateQueries({ queryKey: ["me"] });
      nav((loc.state as { from?: string })?.from || "/", { replace: true });
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="min-h-full bg-paper">
      <div className="mx-auto grid max-w-6xl gap-10 px-4 py-10 md:grid-cols-[1fr_1.15fr] md:py-20">
        <div>
          <div className="flex items-center gap-2">
            <img src="/favicon.svg" alt="" className="size-9" />
            <span className="label text-3xl font-semibold">OpsPilot</span>
          </div>
          <h1 className="mt-8 max-w-md text-[34px] font-semibold leading-[1.1] tracking-tight">
            Every operations request, read, checked and routed to the right decision-maker.
          </h1>
          <p className="mt-4 max-w-md text-[15px] leading-relaxed text-ink-2">
            AI agents extract the facts, check them against inventory, purchase orders and the store calendar,
            and propose costed options. <span className="mark-ai">People approve, correct and decide</span>; every step is in the audit trail.
          </p>
          <form
            className="mt-8 max-w-sm space-y-3"
            onSubmit={(e) => {
              e.preventDefault();
              signIn(email, password);
            }}
          >
            <Field label="Work email"><input className={inputCls} value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="username" /></Field>
            <Field label="Password"><input type="password" className={inputCls} value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" /></Field>
            <Button type="submit" disabled={busy || !email}>Sign in</Button>
          </form>
        </div>
        {!!demo.data?.length && (
          <div>
            <h2 className="text-sm font-semibold">Demo accounts</h2>
            <p className="mt-1 text-sm text-ink-3">All use the password <code className="rounded bg-rule-2 px-1">{PW}</code>. Pick a role to sign in.</p>
            <ul className="mt-4 divide-y divide-rule overflow-hidden rounded-lg border border-rule bg-sheet">
              {demo.data.map((u) => (
                <li key={u.id}>
                  <button disabled={busy} onClick={() => signIn(u.email, PW)} className="flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-paper">
                    <span className="flex-1">
                      <span className="block text-sm font-medium">{u.name}</span>
                      <span className="block text-xs text-ink-3">{u.title}</span>
                    </span>
                    <span className="text-right text-xs text-ink-3">
                      <span className="block font-medium text-ink-2">{ROLE_LABEL[u.role]}</span>
                      {u.approval_limit_inr > 0 ? `Approves up to ${inrShort(u.approval_limit_inr)}` : u.role === "auditor" ? "Read-only" : u.role === "admin" ? "Settings & demo controls" : "Cannot approve spend"}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </div>
  );
}
