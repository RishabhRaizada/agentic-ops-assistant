const IST = "Asia/Kolkata";

export const inr = (n: number | null | undefined) =>
  n === null || n === undefined ? "—" : "₹" + Math.round(n).toLocaleString("en-IN");

export const inrShort = (n: number | null | undefined) => {
  if (n === null || n === undefined) return "—";
  const trim = (x: number, d: number) => x.toFixed(d).replace(/\.?0+$/, "");
  if (n >= 1e7) return `₹${trim(n / 1e7, 2)} Cr`;
  if (n >= 1e5) return `₹${trim(n / 1e5, 2)} L`;
  return inr(n);
};

export const dt = (iso: string | null | undefined, opts: Intl.DateTimeFormatOptions = { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) =>
  iso ? new Date(iso).toLocaleString("en-IN", { timeZone: IST, hour12: false, ...opts }) : "—";

export const time = (iso: string | null | undefined) => dt(iso, { hour: "2-digit", minute: "2-digit", second: "2-digit" });

export function relative(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return "—";
  const diff = new Date(iso).getTime() - now;
  const abs = Math.abs(diff);
  const m = Math.round(abs / 60000);
  const s = m < 1 ? "<1 min" : m < 60 ? `${m} min` : m < 60 * 36 ? `${Math.floor(m / 60)} h ${m % 60} m` : `${Math.round(m / 1440)} d`;
  return diff >= 0 ? `in ${s}` : `${s} ago`;
}

export const pct = (n: number | null | undefined, digits = 0) => (n === null || n === undefined ? "—" : `${(n * 100).toFixed(digits)}%`);

export const titleCase = (s: string) => s.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());

export const ROLE_LABEL: Record<string, string> = {
  analyst: "Ops analyst", ops_manager: "Ops manager", regional_head: "Regional head", director: "Director",
  auditor: "Auditor", admin: "Admin",
};

export const STATUS_LABEL: Record<string, string> = {
  NEW: "New", SCREENING: "Screening", ANALYZING: "Analysing", NEEDS_INFO: "Waiting for info", NEEDS_REVIEW: "Needs review",
  READY_FOR_APPROVAL: "Ready to submit", AWAITING_APPROVAL: "Awaiting approval", REPLANNING: "Re-planning", APPROVED: "Approved",
  IN_PROGRESS: "In progress", RESOLVED: "Resolved", CLOSED: "Closed", ON_HOLD: "On hold", MERGED: "Merged", CANCELLED: "Cancelled",
};

export function display(v: unknown): string {
  if (v === null || v === undefined || v === "") return "—";
  if (typeof v === "number") return v.toLocaleString("en-IN");
  if (typeof v === "string" && /^\d{4}-\d{2}-\d{2}T/.test(v)) return dt(v);
  if (typeof v === "string" && /^\d{4}-\d{2}-\d{2}$/.test(v)) return new Date(v + "T00:00:00").toLocaleDateString("en-IN", { day: "numeric", month: "short", weekday: "short" });
  return String(v);
}
