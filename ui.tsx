// Base UI building blocks shared by every page.
import { type ButtonHTMLAttributes, type ReactNode, useId } from "react";

export const cx = (...parts: (string | false | null | undefined)[]) => parts.filter(Boolean).join(" ");

export function Icon({ name, className }: { name: string; className?: string }) {
  return <i className={cx(`ri-${name}`, "leading-none", className)} aria-hidden="true" />;
}

type Variant = "primary" | "secondary" | "ghost" | "danger" | "copper";
const VARIANTS: Record<Variant, string> = {
  primary: "bg-brand-600 text-white shadow-sm hover:bg-brand-700 active:bg-brand-800",
  secondary: "border border-ink-200 bg-white text-ink-800 shadow-sm hover:border-ink-300 hover:bg-ink-50",
  ghost: "text-ink-600 hover:bg-ink-100 hover:text-ink-900",
  danger: "bg-red-600 text-white shadow-sm hover:bg-red-700",
  copper: "bg-copper-500 text-white shadow-sm hover:bg-copper-600",
};

export function Button({ variant = "primary", size = "md", icon, loading, children, className, ...rest }:
  ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; size?: "sm" | "md"; icon?: string; loading?: boolean }) {
  return (
    <button
      {...rest}
      disabled={rest.disabled || loading}
      className={cx("inline-flex items-center justify-center gap-2 rounded-lg font-semibold whitespace-nowrap transition disabled:opacity-60",
        size === "sm" ? "h-8 px-3 text-[13px]" : "h-10 px-4 text-[14px]", VARIANTS[variant], className)}
    >
      {loading ? <Icon name="loader-4-line" className="animate-spin" /> : icon && <Icon name={icon} className="text-[1.1em]" />}
      {children}
    </button>
  );
}

export function IconButton({ icon, label, className, ...rest }: ButtonHTMLAttributes<HTMLButtonElement> & { icon: string; label: string }) {
  return (
    <button {...rest} aria-label={label} title={label}
      className={cx("inline-grid size-9 place-items-center rounded-lg text-ink-600 transition hover:bg-ink-100 hover:text-ink-900", className)}>
      <Icon name={icon} className="text-lg" />
    </button>
  );
}

export function Card({ children, className }: { children: ReactNode; className?: string }) {
  return <section className={cx("rounded-xl border border-ink-200/80 bg-white shadow-card", className)}>{children}</section>;
}

export function CardHeader({ title, subtitle, actions }: { title: ReactNode; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border-b border-ink-100 px-5 py-4">
      <div>
        <h2 className="text-[15px] font-semibold text-ink-900">{title}</h2>
        {subtitle && <p className="text-[12.5px] text-ink-500">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

export function PageHeader({ eyebrow, title, description, actions }: { eyebrow?: string; title: ReactNode; description?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="no-print mb-6 flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0">
        {eyebrow && <div className="mb-1 text-[11.5px] font-semibold tracking-[0.12em] text-copper-600 uppercase">{eyebrow}</div>}
        <h1 className="text-[24px] leading-tight font-semibold text-ink-900 sm:text-[26px]">{title}</h1>
        {description && <p className="mt-1 max-w-3xl text-ink-500">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

const TONES = {
  ok: "bg-emerald-50 text-emerald-700 ring-emerald-600/20",
  warn: "bg-amber-50 text-amber-800 ring-amber-600/25",
  bad: "bg-red-50 text-red-700 ring-red-600/20",
  info: "bg-brand-50 text-brand-700 ring-brand-600/20",
  neutral: "bg-ink-100 text-ink-600 ring-ink-500/20",
  copper: "bg-copper-50 text-copper-700 ring-copper-600/25",
} as const;
export type Tone = keyof typeof TONES;

export function Badge({ tone = "neutral", children, dot = true }: { tone?: Tone; children: ReactNode; dot?: boolean }) {
  return (
    <span className={cx("inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-[12px] font-semibold whitespace-nowrap ring-1 ring-inset", TONES[tone])}>
      {dot && <span className="size-1.5 rounded-full bg-current" />}
      {children}
    </span>
  );
}

// One place that maps every status in the system to a colour + label (never colour alone).
const STATUS_TONE: Record<string, Tone> = {
  Paid: "ok", "Partially Paid": "warn", Overdue: "bad", Unpaid: "neutral",
  Open: "neutral", "In Progress": "warn", Resolved: "ok", Closed: "ok",
  Requested: "warn", Issued: "ok", Rejected: "bad", Cancelled: "neutral",
  Pending: "warn", Approved: "ok", Draft: "neutral", Released: "ok",
  Present: "ok", Late: "warn", Absent: "bad", "On Leave": "info", "Half Day": "warn",
  Active: "ok", Inactive: "neutral", Occupied: "ok", Vacant: "neutral", Resigned: "neutral",
};
export function StatusBadge({ status, label }: { status: string; label?: string }) {
  return <Badge tone={STATUS_TONE[status] ?? "neutral"}>{label ?? status}</Badge>;
}

export function StatCard({ label, value, hint, icon, tone = "default" }: { label: string; value: ReactNode; hint?: ReactNode; icon?: string; tone?: "default" | "hero" | "copper" }) {
  return (
    <div className={cx("rounded-xl border p-4 shadow-card", tone === "hero" ? "border-transparent bg-gradient-to-br from-brand-600 to-brand-800 text-white"
      : tone === "copper" ? "border-copper-200 bg-copper-50" : "border-ink-200/80 bg-white")}>
      <div className={cx("flex items-center gap-2 text-[12.5px] font-medium", tone === "hero" ? "text-brand-100" : "text-ink-500")}>
        {icon && <span className={cx("grid size-7 place-items-center rounded-lg", tone === "hero" ? "bg-white/15" : tone === "copper" ? "bg-copper-100 text-copper-700" : "bg-brand-50 text-brand-600")}><Icon name={icon} /></span>}
        {label}
      </div>
      <div className="mt-2 font-display text-[24px] leading-tight font-semibold tabular">{value}</div>
      {hint && <div className={cx("mt-1 text-[12.5px]", tone === "hero" ? "text-brand-100" : "text-ink-500")}>{hint}</div>}
    </div>
  );
}

export function Field({ label, error, hint, children, className }: { label: string; error?: string | false; hint?: ReactNode; children: (id: string) => ReactNode; className?: string }) {
  const id = useId();
  return (
    <div className={cx("space-y-1.5", className)}>
      <label htmlFor={id} className="block text-[12.5px] font-semibold text-ink-700">{label}</label>
      {children(id)}
      {error ? <p className="text-[12px] text-red-600" role="alert">{error}</p> : hint ? <p className="text-[12px] text-ink-500">{hint}</p> : null}
    </div>
  );
}

export function Tabs<K extends string>({ tabs, value, onChange }: { tabs: { key: K; label: string; count?: number }[]; value: K; onChange: (k: K) => void }) {
  return (
    <div className="flex gap-1 overflow-x-auto rounded-lg bg-ink-100 p-1" role="tablist">
      {tabs.map((t) => (
        <button key={t.key} role="tab" aria-selected={value === t.key} onClick={() => onChange(t.key)}
          className={cx("flex items-center gap-2 rounded-md px-3 py-1.5 text-[13px] font-semibold whitespace-nowrap transition",
            value === t.key ? "bg-white text-ink-900 shadow-sm" : "text-ink-500 hover:text-ink-800")}>
          {t.label}
          {t.count !== undefined && <span className={cx("rounded-full px-1.5 text-[11px]", value === t.key ? "bg-brand-50 text-brand-700" : "bg-ink-200 text-ink-600")}>{t.count}</span>}
        </button>
      ))}
    </div>
  );
}

export function SearchInput({ value, onChange, placeholder }: { value: string; onChange: (v: string) => void; placeholder: string }) {
  return (
    <div className="relative">
      <Icon name="search-line" className="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-ink-400" />
      <input className="input pl-9 sm:w-64" value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder} aria-label={placeholder} />
    </div>
  );
}

export function Skeleton({ rows = 5 }: { rows?: number }) {
  return (
    <div className="space-y-3 p-5" aria-busy="true" aria-label="Loading">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="h-3.5 animate-pulse rounded bg-ink-100" style={{ width: `${[92, 76, 85, 64, 88][i % 5]}%` }} />
      ))}
    </div>
  );
}

export function EmptyState({ icon = "inbox-line", title, children, action }: { icon?: string; title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center px-6 py-12 text-center">
      <div className="mb-3 grid size-12 place-items-center rounded-full bg-brand-50 text-xl text-brand-600"><Icon name={icon} /></div>
      <h3 className="text-[15px] font-semibold text-ink-900">{title}</h3>
      {children && <p className="mt-1 max-w-md text-ink-500">{children}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function ErrorState({ title = "Something went wrong", message, onRetry }: { title?: string; message?: string; onRetry?: () => void }) {
  return (
    <div className="m-5 flex gap-3 rounded-xl border border-red-200 bg-red-50 p-4 text-red-800" role="alert">
      <Icon name="error-warning-line" className="mt-0.5 text-xl" />
      <div>
        <p className="font-semibold">{title}</p>
        {message && <p className="text-[13px] text-red-700">{message}</p>}
        {onRetry && <Button size="sm" variant="secondary" className="mt-2" onClick={onRetry} icon="refresh-line">Try again</Button>}
      </div>
    </div>
  );
}

export function Notice({ tone = "info", icon, title, children }: { tone?: "info" | "warn" | "copper"; icon?: string; title?: string; children: ReactNode }) {
  const styles = { info: "border-brand-200 bg-brand-50 text-brand-800", warn: "border-amber-200 bg-amber-50 text-amber-900", copper: "border-copper-200 bg-copper-50 text-copper-700" }[tone];
  return (
    <div className={cx("flex gap-3 rounded-xl border p-3.5 text-[13px]", styles)}>
      <Icon name={icon ?? (tone === "warn" ? "alert-line" : "information-line")} className="mt-0.5 text-lg" />
      <div>{title && <p className="font-semibold">{title}</p>}<div>{children}</div></div>
    </div>
  );
}
