import clsx from "clsx";
import { useEffect, useRef, useState, type ButtonHTMLAttributes, type ReactNode } from "react";
import { X } from "lucide-react";
import { relative, STATUS_LABEL, pct } from "../lib/format";

export function Button({ variant = "primary", size = "md", className, ...p }: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "ghost" | "danger" | "quiet"; size?: "sm" | "md";
}) {
  return (
    <button
      {...p}
      className={clsx(
        "inline-flex items-center justify-center gap-1.5 rounded-md font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-45",
        size === "sm" ? "h-8 px-2.5 text-[13px]" : "h-9 px-3.5 text-sm",
        variant === "primary" && "bg-teal text-white hover:bg-teal-2",
        variant === "secondary" && "border border-rule bg-sheet text-ink hover:border-ink-4",
        variant === "ghost" && "text-ink-2 hover:bg-rule-2",
        variant === "quiet" && "text-teal hover:underline px-0 h-auto",
        variant === "danger" && "border border-signal/30 bg-sheet text-signal hover:bg-signal-soft",
        className,
      )}
    />
  );
}

const STATUS_TONE: Record<string, string> = {
  NEW: "bg-rule-2 text-ink-2", SCREENING: "bg-teal-soft text-teal", ANALYZING: "bg-teal-soft text-teal",
  NEEDS_INFO: "bg-amber-soft text-amber", NEEDS_REVIEW: "bg-amber-soft text-amber", READY_FOR_APPROVAL: "bg-pen-soft text-pen",
  AWAITING_APPROVAL: "bg-pen-soft text-pen", REPLANNING: "bg-teal-soft text-teal", APPROVED: "bg-go-soft text-go",
  IN_PROGRESS: "bg-go-soft text-go", RESOLVED: "bg-go-soft text-go", CLOSED: "bg-rule-2 text-ink-3", ON_HOLD: "bg-amber-soft text-amber",
  MERGED: "bg-rule-2 text-ink-3", CANCELLED: "bg-rule-2 text-ink-3",
};

export function StatusBadge({ status, processing }: { status: string; processing?: boolean }) {
  return (
    <span className={clsx("inline-flex items-center gap-1.5 whitespace-nowrap rounded px-1.5 py-0.5 text-xs font-medium", STATUS_TONE[status] || "bg-rule-2")}>
      {processing && <span className="working-dot size-1.5 rounded-full bg-current" aria-hidden />}
      {processing ? "Agents working" : STATUS_LABEL[status] || status}
    </span>
  );
}

const RISK_TONE: Record<string, string> = {
  critical: "bg-signal text-white", high: "bg-signal-soft text-signal", medium: "bg-amber-soft text-amber", low: "bg-go-soft text-go",
};

export function RiskBadge({ level, priority }: { level: string | null; priority?: string | null }) {
  if (!level) return <span className="text-xs text-ink-4">—</span>;
  return (
    <span className={clsx("inline-flex items-center gap-1 whitespace-nowrap rounded px-1.5 py-0.5 text-xs font-semibold", RISK_TONE[level])}>
      {level[0].toUpperCase() + level.slice(1)}
      {priority && <span className="font-normal opacity-80">· {priority}</span>}
    </span>
  );
}

export function ConfidenceMeter({ value, compact }: { value: number | null | undefined; compact?: boolean }) {
  if (value === null || value === undefined) return <span className="text-xs text-ink-4">—</span>;
  const tone = value >= 0.75 ? "bg-go" : value >= 0.5 ? "bg-amber" : "bg-signal";
  return (
    <span className="inline-flex items-center gap-1.5" title={`Composite confidence ${pct(value)}`}>
      <span className={clsx("relative overflow-hidden rounded-full bg-rule", compact ? "h-1.5 w-10" : "h-2 w-16")}>
        <span className={clsx("absolute inset-y-0 left-0 rounded-full", tone)} style={{ width: `${Math.round(value * 100)}%` }} />
      </span>
      <span className="text-xs tabular-nums text-ink-2">{Math.round(value * 100)}</span>
    </span>
  );
}

export function useNow(ms = 1000) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), ms);
    return () => clearInterval(t);
  }, [ms]);
  return now;
}

/** Countdown against the server's (possibly demo-shifted) clock. */
export function SlaTimer({ due, offsetSeconds = 0, paused }: { due: string | null; offsetSeconds?: number; paused?: boolean }) {
  const now = useNow(15000) + offsetSeconds * 1000;
  if (!due) return <span className="text-xs text-ink-4">No deadline</span>;
  if (paused) return <span className="text-xs text-amber">Paused · waiting on requester</span>;
  const left = new Date(due).getTime() - now;
  const tone = left < 0 ? "text-signal font-semibold" : left < 3600e3 ? "text-amber font-medium" : "text-ink-2";
  return <span className={clsx("text-xs tabular-nums", tone)}>{left < 0 ? `Overdue ${relative(due, now).replace(" ago", "")}` : `Due ${relative(due, now)}`}</span>;
}

export function Modal({ open, onClose, title, children, wide }: { open: boolean; onClose: () => void; title: string; children: ReactNode; wide?: boolean }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
  }, [open]);
  return (
    <dialog
      ref={ref}
      onClose={onClose}
      onClick={(e) => e.target === ref.current && onClose()}
      className={clsx("m-auto w-[calc(100%-32px)] rounded-xl border border-rule bg-sheet p-0 text-ink shadow-2xl backdrop:bg-ink/40", wide ? "max-w-3xl" : "max-w-lg")}
    >
      {open && (
        <div className="flex max-h-[85vh] flex-col">
          <div className="flex items-center justify-between border-b border-rule px-5 py-3.5">
            <h2 className="text-[15px] font-semibold">{title}</h2>
            <button onClick={onClose} className="rounded p-1 text-ink-3 hover:bg-rule-2" aria-label="Close"><X size={16} /></button>
          </div>
          <div className="overflow-y-auto px-5 py-4">{children}</div>
        </div>
      )}
    </dialog>
  );
}

export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return (
    <label className="block">
      <span className="mb-1 block text-[13px] font-medium text-ink-2">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-xs text-ink-3">{hint}</span>}
    </label>
  );
}

export const inputCls = "w-full rounded-md border border-rule bg-sheet px-2.5 py-2 text-sm placeholder:text-ink-4 focus:border-teal focus:outline-none";

export function Tabs<T extends string>({ tabs, value, onChange }: { tabs: { id: T; label: string; count?: number; alert?: boolean }[]; value: T; onChange: (v: T) => void }) {
  return (
    <div role="tablist" className="flex gap-1 overflow-x-auto border-b border-rule scroll-thin">
      {tabs.map((t) => (
        <button
          key={t.id}
          role="tab"
          aria-selected={value === t.id}
          onClick={() => onChange(t.id)}
          className={clsx(
            "-mb-px flex items-center gap-1.5 whitespace-nowrap border-b-2 px-3 py-2 text-sm",
            value === t.id ? "border-teal font-semibold text-ink" : "border-transparent text-ink-3 hover:text-ink",
          )}
        >
          {t.label}
          {t.count !== undefined && t.count > 0 && (
            <span className={clsx("rounded px-1 text-[11px] tabular-nums", t.alert ? "bg-signal text-white" : "bg-rule-2 text-ink-2")}>{t.count}</span>
          )}
        </button>
      ))}
    </div>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="rounded-lg border border-dashed border-rule px-6 py-10 text-center">
      <p className="text-sm font-medium text-ink-2">{title}</p>
      {children && <div className="mt-1 text-sm text-ink-3">{children}</div>}
    </div>
  );
}

export function Panel({ title, action, children, className }: { title?: ReactNode; action?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={clsx("rounded-lg border border-rule bg-sheet", className)}>
      {title && (
        <header className="flex items-center justify-between gap-3 border-b border-rule-2 px-4 py-2.5">
          <h2 className="text-sm font-semibold">{title}</h2>
          {action}
        </header>
      )}
      {children}
    </section>
  );
}

export function ActorDot({ type }: { type: "ai" | "human" | "system" }) {
  const map = { ai: ["bg-marker text-ink", "AI"], human: ["bg-pen text-white", "H"], system: ["bg-ink-3 text-white", "S"] } as const;
  const [cls, txt] = map[type];
  return <span className={clsx("inline-flex size-5 shrink-0 items-center justify-center rounded-full text-[10px] font-bold", cls)} aria-label={type}>{txt}</span>;
}
