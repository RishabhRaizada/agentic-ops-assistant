import clsx from "clsx";
import { useState } from "react";
import { NavLink, Outlet, Link } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Bell, BarChart3, ClipboardCheck, FileClock, Inbox, LayoutDashboard, LogOut, Mail, SlidersHorizontal, TriangleAlert } from "lucide-react";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { ROLE_LABEL, relative } from "../lib/format";
import type { Approval } from "../lib/types";

interface Ready {
  llm: { configured: boolean; chaos: string | null; breaker: string };
  inventory: { chaos_down: boolean; breaker: string };
  mail: { chaos_down: boolean; breaker: string };
}
interface Settings { clock_offset_seconds: number; llm: { primary: string; fallback: string; configured: boolean; chain?: string[] } }
interface Notif { id: number; kind: string; text: string; read: boolean; case_id: number | null; case_ref: string | null; created_at: string }

export function useServerSettings() {
  return useQuery({ queryKey: ["settings"], queryFn: () => api.get<Settings>("/api/admin/settings"), refetchInterval: 20000 });
}

export default function Layout() {
  const { user, logout } = useAuth();
  const qc = useQueryClient();
  const [bellOpen, setBellOpen] = useState(false);
  const ready = useQuery({ queryKey: ["readyz"], queryFn: () => api.get<Ready>("/api/readyz"), refetchInterval: 10000 });
  const settings = useServerSettings();
  const mine = useQuery({ queryKey: ["approvals", "mine"], queryFn: () => api.get<Approval[]>("/api/approvals?scope=mine"), refetchInterval: 10000 });
  const notifs = useQuery({ queryKey: ["notifications"], queryFn: () => api.get<Notif[]>("/api/notifications"), refetchInterval: 10000 });
  const unread = (notifs.data || []).filter((n) => !n.read).length;

  const nav = [
    { to: "/", label: "Dashboard", icon: LayoutDashboard, end: true },
    { to: "/requests", label: "Requests", icon: Inbox },
    { to: "/approvals", label: "Approvals", icon: ClipboardCheck, count: mine.data?.length },
    { to: "/communications", label: "Communications", icon: Mail },
    { to: "/audit", label: "Audit trail", icon: FileClock },
    { to: "/analytics", label: "Analytics", icon: BarChart3 },
    ...(user?.role === "admin" ? [{ to: "/admin", label: "Admin & demo controls", icon: SlidersHorizontal }] : []),
  ];

  const r = ready.data;
  const banners: string[] = [];
  if (r?.llm.chaos === "all down") banners.push("All AI models are unavailable: requests are processed by deterministic rules and sent to human review.");
  else if (r?.llm.chaos === "primary down") banners.push(`Primary model unavailable: agents are running on the fallback model (${settings.data?.llm.fallback}).`);
  if (r?.inventory.chaos_down) banners.push("Inventory system unavailable: stock figures cannot be verified.");
  if (r?.mail.chaos_down) banners.push("Email delivery failing: outbound messages are queued for retry.");
  const offset = settings.data?.clock_offset_seconds || 0;

  return (
    <div className="flex min-h-full flex-col md:flex-row">
      <aside className="flex shrink-0 flex-col bg-rail text-paper md:sticky md:top-0 md:h-screen md:w-60">
        <Link to="/" className="flex items-center gap-2 px-5 pb-3 pt-4 md:pt-6">
          <img src="/favicon.svg" alt="" className="size-7" />
          <span className="label text-[22px] font-semibold tracking-wide">OpsPilot</span>
        </Link>
        <nav className="flex gap-0.5 overflow-x-auto px-3 pb-2 md:flex-col md:overflow-visible md:pb-0" aria-label="Main">
          {nav.map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              end={n.end}
              className={({ isActive }) =>
                clsx("flex items-center gap-2.5 whitespace-nowrap rounded-md px-2.5 py-2 text-[14px]",
                  isActive ? "bg-white/12 font-medium text-white" : "text-paper/70 hover:bg-white/6 hover:text-white")
              }
            >
              <n.icon size={16} strokeWidth={1.8} />
              <span className="flex-1">{n.label}</span>
              {!!n.count && <span className="rounded bg-marker px-1.5 text-[11px] font-bold text-ink">{n.count}</span>}
            </NavLink>
          ))}
        </nav>
        <div className="mt-auto hidden border-t border-white/10 p-4 md:block">
          <div className="text-sm font-medium">{user?.name}</div>
          <div className="text-xs text-paper/60">{ROLE_LABEL[user?.role || ""]} · {user?.region === "ALL" ? "All regions" : user?.region}</div>
          <button onClick={logout} className="mt-3 flex items-center gap-1.5 text-xs text-paper/70 hover:text-white">
            <LogOut size={13} /> Sign out
          </button>
        </div>
      </aside>

      <div className="min-w-0 flex-1">
        <div className="sticky top-0 z-20 border-b border-rule bg-paper/95 backdrop-blur">
          <div className="flex items-center justify-end gap-3 px-4 py-2 md:px-8">
            {offset !== 0 && (
              <span className="rounded bg-pen-soft px-2 py-0.5 text-xs font-medium text-pen" title="Demo clock is shifted for SLA demonstration">
                Demo clock +{(offset / 3600).toFixed(1)} h
              </span>
            )}
            {settings.data?.llm && (
              <span className="hidden text-xs text-ink-3 sm:inline">
                Models: {(settings.data.llm.chain || [settings.data.llm.primary, settings.data.llm.fallback]).join(" → ")} → rules
              </span>
            )}
            <span className="text-xs text-ink-3 md:hidden">{user?.name}</span>
            <div className="relative">
              <button
                onClick={() => {
                  setBellOpen(!bellOpen);
                  if (!bellOpen && unread) api.post("/api/notifications/read").then(() => qc.invalidateQueries({ queryKey: ["notifications"] }));
                }}
                className="relative rounded-md p-1.5 text-ink-2 hover:bg-rule-2"
                aria-label={`Notifications (${unread} unread)`}
              >
                <Bell size={18} />
                {unread > 0 && <span className="absolute -right-0.5 -top-0.5 rounded-full bg-signal px-1 text-[10px] font-bold text-white">{unread}</span>}
              </button>
              {bellOpen && (
                <div className="absolute right-0 top-10 z-30 w-[min(380px,calc(100vw-32px))] rounded-lg border border-rule bg-sheet shadow-xl">
                  <div className="border-b border-rule-2 px-4 py-2.5 text-sm font-semibold">Notifications</div>
                  <ul className="max-h-96 overflow-y-auto">
                    {(notifs.data || []).length === 0 && <li className="px-4 py-6 text-center text-sm text-ink-3">Nothing new.</li>}
                    {(notifs.data || []).map((n) => (
                      <li key={n.id} className="border-b border-rule-2 px-4 py-2.5 last:border-0">
                        {n.case_id ? (
                          <Link to={`/requests/${n.case_id}`} onClick={() => setBellOpen(false)} className="block text-sm hover:text-teal">{n.text}</Link>
                        ) : <p className="text-sm">{n.text}</p>}
                        <p className="mt-0.5 text-xs text-ink-4">{relative(n.created_at)}</p>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
            <button onClick={logout} className="text-ink-3 md:hidden" aria-label="Sign out"><LogOut size={16} /></button>
          </div>
          {banners.map((b) => (
            <div key={b} className="flex items-center gap-2 border-t border-amber/20 bg-amber-soft px-4 py-1.5 text-[13px] text-amber md:px-8">
              <TriangleAlert size={14} /> {b}
            </div>
          ))}
        </div>
        <main className="px-4 py-6 md:px-8">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
