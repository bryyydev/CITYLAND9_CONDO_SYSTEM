// Admin dashboard and Reports (shared with Accounting). Units Directory: UnitsPage.tsx; Water Readings: WaterPage.tsx.
import { type ReactNode, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useAuth, useUser } from "../../auth/AuthContext";
import { BarChart } from "../../components/base/BarChart";
import { DataTable } from "../../components/base/DataTable";
import { useToast } from "../../components/base/overlays";
import { Badge, Button, Card, CardHeader, EmptyState, ErrorState, Field, Icon, Notice, PageHeader, Skeleton, StatCard, StatusBadge, Tabs } from "../../components/base/ui";
import { ROLES } from "../../config/roles";
import { useAsync } from "../../hooks/useAsync";
import { dateLabel, monthLabel, shortMonth } from "../../lib/format";
import { peso, pesoShort, toCents } from "../../lib/money";
import { api } from "../../services/api";
import type { CollectionSummary, PropertyReport, ReportPeriod, ReportQuery } from "../../services/types";
import { GenerateBillsPanel } from "./Billing";

const pct = (n: number) => `${Math.round(n * 100)}%`;

export function CollectionsChart({ data }: { data: CollectionSummary[] }) {
  return (
    <BarChart ariaLabel="Billed versus collected per month" format={(v) => pesoShort(v)}
      series={[{ key: "billed", label: "Billed", color: "chart-1" }, { key: "collected", label: "Collected", color: "chart-2" }]}
      data={data.map((c) => ({ label: shortMonth(c.month), detail: monthLabel(c.month), values: { billed: toCents(c.billed) / 100, collected: toCents(c.collected) / 100 } }))} />
  );
}

export function CollectionsTable({ data }: { data: CollectionSummary[] }) {
  return (
    <DataTable rows={[...data].reverse()} rowKey={(c) => c.month} columns={[
      { key: "m", header: "Month", cell: (c) => <b className="text-ink-900">{monthLabel(c.month)}</b> },
      { key: "b", header: "Billed", align: "right", cell: (c) => peso(c.billed) },
      { key: "c", header: "Collected", align: "right", cell: (c) => peso(c.collected) },
      { key: "r", header: "Collection rate", align: "right", cell: (c) => <Badge tone={c.collectionRate >= 0.85 ? "ok" : c.collectionRate >= 0.6 ? "warn" : "bad"}>{pct(c.collectionRate)}</Badge> },
      { key: "o", header: "Receivables (end of month)", align: "right", cell: (c) => <b>{peso(c.outstanding)}</b> },
    ]} />
  );
}

// ------------------------------------------------------------------ Admin dashboard
type DashTab = "soa" | "occupancy" | "collections" | "water";
export function AdminDashboard() {
  const user = useUser();
  const { data, error, reload } = useAsync(() => api.dashboards.property(), []);
  const [tab, setTab] = useState<DashTab>("soa");
  if (error) return <ErrorState title="Couldn't load the dashboard" message={error.message} onRetry={reload} />;
  if (!data) return <Card><Skeleton rows={8} /></Card>;
  const last = data.collections.at(-2) ?? data.collections.at(-1)!;
  const portal = ROLES[user.role].portal;

  return (
    <>
      <PageHeader eyebrow={`Good day, ${user.displayName?.split(" ")[0] ?? user.username}`} title="Property overview" description={`${monthLabel(data.month)} · ${data.units.residential} residential units, ${data.units.parking} parking and ${data.units.storage} storage units.`} />
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard tone="hero" icon="building-2-line" label="Occupancy" value={pct(data.units.occupied / data.units.residential)} hint={`${data.units.occupied} occupied · ${data.units.vacant} vacant`} />
        <StatCard icon="hand-coin-line" label={`Collected · ${monthLabel(last.month, "short")}`} value={pesoShort(last.collected)} hint={`${pct(last.collectionRate)} of ${pesoShort(last.billed)} billed`} />
        <StatCard tone="copper" icon="error-warning-line" label="Receivables" value={pesoShort(last.outstanding)} hint="Unpaid balances on latest SOAs" />
        <StatCard icon="passport-line" label="Needs attention" value={data.pendingGatePasses + data.openTickets} hint={<>{data.pendingGatePasses} gate pass request(s) · {data.openTickets} open ticket(s)</>} />
      </div>

      <Card className="mt-6">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-ink-100 p-4">
          <Tabs value={tab} onChange={setTab} tabs={[{ key: "soa", label: "Monthly SOA generation" }, { key: "occupancy", label: "Occupancy" }, { key: "collections", label: "Collections" }, { key: "water", label: "Water meter reading" }]} />
        </div>
        <div className="p-5">
          {tab === "soa" && (
            <div className="grid gap-8 lg:grid-cols-[1.2fr_1fr]">
              <GenerateBillsPanel compact />
              <div>
                <h3 className="mb-3 font-semibold text-ink-900">This month's bills</h3>
                {data.billing.generated ? (
                  <div className="grid grid-cols-2 gap-3">
                    {([["Paid", data.billing.paid], ["Partially Paid", data.billing.partial], ["Unpaid", data.billing.unpaid], ["Overdue", data.billing.overdue]] as const).map(([s, n]) => (
                      <div key={s} className="rounded-xl bg-ink-50 p-4"><p className="font-display text-[24px] font-semibold">{n}</p><StatusBadge status={s} /></div>
                    ))}
                  </div>
                ) : <Notice tone="copper" icon="calendar-todo-line" title={`${monthLabel(data.month)} bills are not generated yet`}>Enter the month's water readings, then generate the bills. Residents see their SOA as soon as it's issued.</Notice>}
                <Link to={`/${portal}/billing`} className="mt-4 inline-flex items-center gap-1 font-semibold text-brand-600 hover:underline">Open Billing & SOA <Icon name="arrow-right-line" /></Link>
              </div>
            </div>
          )}
          {tab === "occupancy" && (
            <div className="grid gap-8 lg:grid-cols-[2fr_1fr]">
              <BarChart ariaLabel="Occupied and vacant units per floor" stacked format={(v) => String(Math.round(v))}
                series={[{ key: "occupied", label: "Occupied", color: "chart-1" }, { key: "vacant", label: "Vacant", color: "muted" }]}
                data={data.occupancyByFloor.map((f) => ({ label: f.floor, detail: `Floor ${f.floor}`, values: { occupied: f.occupied, vacant: f.vacant } }))} />
              <div className="space-y-3">
                {([["Residential units", data.units.residential], ["Occupied", data.units.occupied], ["Vacant", data.units.vacant], ["Parking slots", data.units.parking], ["Storage units", data.units.storage]] as const).map(([k, v]) => (
                  <div key={k} className="flex justify-between rounded-lg bg-ink-50 px-4 py-2.5"><span className="text-ink-600">{k}</span><b className="tabular">{v}</b></div>
                ))}
              </div>
            </div>
          )}
          {tab === "collections" && <><CollectionsChart data={data.collections} /><div className="mt-6 -mx-5 -mb-5 border-t border-ink-100"><CollectionsTable data={data.collections} /></div></>}
          {tab === "water" && (
            <div className="grid gap-6 lg:grid-cols-[1fr_1.4fr]">
              <div>
                <p className="text-ink-500">{monthLabel(data.month)} readings entered</p>
                <p className="font-display text-[34px] font-semibold">{data.waterReadingsEntered}<span className="text-[18px] text-ink-400"> / {data.units.residential}</span></p>
                <div className="mt-2 h-2.5 overflow-hidden rounded-full bg-ink-100"><div className="h-full rounded-full bg-chart-1" style={{ width: pct(data.waterReadingsEntered / data.units.residential) }} /></div>
                <Link to={`/${portal}/water-readings`} className="mt-5 inline-flex h-10 items-center gap-2 rounded-lg bg-brand-600 px-4 font-semibold text-white hover:bg-brand-700"><Icon name="drop-line" />Open meter reading tool</Link>
              </div>
              <Notice title="How water is billed">Usage = current − previous reading (pre-filled from last month). Amount = usage × rate (₱/m³). Saving a reading updates that month's bill if it was already generated.</Notice>
            </div>
          )}
        </div>
      </Card>
    </>
  );
}

// ------------------------------------------------------------------ Reports (Admin: Property Reports, Accounting: Financial Reports)
const PERIODS: { key: ReportPeriod; label: string }[] = [
  { key: "daily", label: "Today" }, { key: "weekly", label: "This week" }, { key: "monthly", label: "This month" }, { key: "yearly", label: "This year" }, { key: "custom", label: "Custom dates" },
];
const CHARGE_LABELS: [keyof PropertyReport["billing"]["charges"], string][] = [
  ["condo", "Condo dues"], ["parking", "Parking"], ["storage", "Storage"], ["water", "Water"], ["other", "Other charges"], ["adjustment", "Adjustments"], ["penalty", "Penalties"],
];

/** The condo management report for a period (Property Reports; Accounting: Financial Reports). */
export function ReportsPage({ variant, children }: { variant: "property" | "financial"; children?: ReactNode }) {
  const { can } = useAuth();
  const user = useUser();
  const toast = useToast();
  const [params, setParams] = useSearchParams();
  const period = (PERIODS.some((p) => p.key === params.get("period")) ? params.get("period") : "monthly") as ReportPeriod;
  const query: ReportQuery = { period, from: params.get("from") ?? undefined, to: params.get("to") ?? undefined };
  const { data, error, loading, reload } = useAsync(() => api.reports.get(query), [period, query.from, query.to]);
  const setQuery = (next: Partial<ReportQuery>) => {
    const p = new URLSearchParams(params);
    for (const [k, v] of Object.entries(next)) if (v) p.set(k, v); else p.delete(k);
    if ((next.period ?? period) !== "custom") { p.delete("from"); p.delete("to"); }
    setParams(p, { replace: true });
  };
  const ledger = `/${ROLES[user.role].portal}/${variant === "financial" ? "collections" : "payments"}`;
  async function exportExcel() {
    try {
      await api.reports.exportExcel({ ...query, period: "custom", from: data?.from, to: data?.to });
    } catch (err) {
      toast({ tone: "info", title: "Excel export", message: (err as Error).message });
    }
  }
  return (
    <>
      <PageHeader eyebrow={variant === "financial" ? "Finance" : "Reports"} title={variant === "financial" ? "Financial Reports" : "Property Reports"}
        description="Charges billed, money collected (official receipts), expenses and what units still owe, for any period."
        actions={<>
          <Button variant="secondary" icon="printer-line" disabled={!data} onClick={() => window.print()}>Print</Button>
          {can("reports_export") && <Button variant="secondary" icon="file-excel-2-line" disabled={!data} onClick={exportExcel}>Export to Excel</Button>}
        </>} />
      {children}
      <Card className="no-print mb-6">
        <div className="flex flex-wrap items-end gap-3 p-4">
          <Tabs value={period} onChange={(p) => setQuery({ period: p })} tabs={PERIODS} />
          {period === "custom" && (
            <div className="flex flex-wrap items-end gap-3">
              <Field label="From">{(id) => <input id={id} type="date" className="input w-44" value={query.from ?? data?.from ?? ""} onChange={(e) => setQuery({ from: e.target.value })} />}</Field>
              <Field label="To">{(id) => <input id={id} type="date" className="input w-44" value={query.to ?? data?.to ?? ""} onChange={(e) => setQuery({ to: e.target.value })} />}</Field>
            </div>
          )}
        </div>
      </Card>
      {error ? <ErrorState message={error.message} onRetry={reload} /> : !data ? <Card><Skeleton rows={8} /></Card> : (
        <div className={loading ? "opacity-60 transition-opacity" : undefined} aria-busy={loading || undefined}>
          <div className="mb-4 flex flex-wrap items-baseline justify-between gap-2">
            <h2 className="font-display text-[20px] font-bold text-ink-900">{data.label}</h2>
            {data.period !== "custom" && <span className="text-ink-500">{dateLabel(data.from)}{data.to !== data.from && <> – {dateLabel(data.to)}</>}</span>}
          </div>
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <StatCard icon="file-list-3-line" label="Charges billed" value={peso(data.billing.charged)} hint={`${data.billing.bills} bill${data.billing.bills === 1 ? "" : "s"} generated`} />
            <StatCard tone="hero" icon="hand-coin-line" label="Collected" value={peso(data.collections.total)} hint={`${data.collections.receipts} official receipt${data.collections.receipts === 1 ? "" : "s"}`} />
            <StatCard icon="wallet-3-line" label="Expenses" value={peso(data.expenses.total)} />
            <StatCard tone={toCents(data.netCashFlow) < 0 ? "copper" : "default"} icon="scales-3-line" label="Net cash flow" value={peso(data.netCashFlow)} hint="Collected minus expenses" />
          </div>
          {(data.collections.voided.count > 0 || data.collections.withoutReceipt > 0) && (
            <div className="mt-4 space-y-2">
              {data.collections.voided.count > 0 && <Notice tone="info">{data.collections.voided.count} voided receipt{data.collections.voided.count === 1 ? "" : "s"} ({peso(data.collections.voided.amount)}) in this period {data.collections.voided.count === 1 ? "is" : "are"} not counted as collected.</Notice>}
              {data.collections.withoutReceipt > 0 && <Notice tone="warn">{data.collections.withoutReceipt} payment{data.collections.withoutReceipt === 1 ? " was" : "s were"} recorded without an official receipt (e.g. imported from Excel). {data.collections.withoutReceipt === 1 ? "It is" : "They are"} counted and marked “No receipt” below.</Notice>}
            </div>
          )}
          <div className="mt-6 grid gap-6 lg:grid-cols-3">
            <Card>
              <CardHeader title="Collections" subtitle="What the money paid for" />
              <SummaryRows rows={[["Statements of account", data.collections.bills], ["Water", data.collections.water], ["Advance payments", data.collections.advances]]} total={["Total collected", data.collections.total]} />
              <div className="border-t border-ink-100 px-5 pt-3 text-[12px] font-semibold tracking-wide text-ink-500 uppercase">By payment method</div>
              <SummaryRows rows={Object.entries(data.collections.byMethod).map(([m, v]) => [m.charAt(0) + m.slice(1).toLowerCase(), v])} />
            </Card>
            <Card>
              <CardHeader title="Charges billed" subtitle="Bills generated in the period. Earlier unpaid balances are not counted again." />
              <SummaryRows rows={CHARGE_LABELS.filter(([k]) => toCents(data.billing.charges[k]) !== 0 || ["condo", "water", "penalty"].includes(k)).map(([k, label]) => [label, data.billing.charges[k]])}
                total={["Total charged", data.billing.charged]} />
              {toCents(data.billing.advanceCredits) > 0 && <p className="px-5 pb-4 text-[12.5px] text-ink-500">Advance payments covered {peso(data.billing.advanceCredits)} of these charges.</p>}
            </Card>
            <Card>
              <CardHeader title="Receivables & property" subtitle="As of today" />
              <SummaryRows rows={[["Owed by units", data.receivables.total]]} />
              <dl className="divide-y divide-ink-100 border-t border-ink-100 text-[13.5px]">
                {([["Units with a balance", data.receivables.units], ["…of which overdue", data.receivables.overdueUnits], ["Residential units", data.property.residentialUnits],
                  ["Occupied / vacant", `${data.property.occupied} / ${data.property.vacant}`], ["Current tenants", data.property.currentTenants], ["Units with parking", data.property.withParking]] as [string, string | number][]).map(([k, v]) => (
                  <div key={k} className="flex justify-between gap-3 px-5 py-2"><dt className="text-ink-500">{k}</dt><dd className="font-semibold text-ink-900 tabular">{v}</dd></div>
                ))}
              </dl>
            </Card>
          </div>
          <Card className="mt-6">
            <CardHeader title="Collection transactions" subtitle={`${data.transactions.length} in this period`} />
            <DataTable rows={data.transactions} rowKey={(t) => `${t.receiptNo ?? "x"}-${t.date}-${t.unitNo}-${t.amount}-${t.description}`} empty={{ icon: "hand-coin-line", title: "No collections in this period" }}
              columns={[
                { key: "d", header: "Date", cell: (t) => <span className="whitespace-nowrap">{dateLabel(t.date)}</span> },
                { key: "r", header: "OR No.", cell: (t) => (t.receiptId ? <Link className="font-semibold whitespace-nowrap text-brand-700 hover:underline" to={`${ledger}?receipt=${t.receiptId}`}>{t.receiptNo}</Link> : <Badge tone="warn" dot={false}>No receipt</Badge>) },
                { key: "u", header: "Unit", cell: (t) => t.unitNo || "—" },
                { key: "x", header: "For", cell: (t) => <span className="text-[13px]">{t.description}</span> },
                { key: "m", header: "Method", cell: (t) => <>{t.method}{t.reference && <span className="block text-[12px] text-ink-500">{t.reference}</span>}</> },
                { key: "a", header: "Amount", align: "right", cell: (t) => <b className="whitespace-nowrap">{peso(t.amount)}</b> },
              ]} />
          </Card>
          <Card className="mt-6">
            <CardHeader title="Expenses" subtitle={Object.entries(data.expenses.byCategory).map(([c, v]) => `${c} ${peso(v)}`).join(" · ") || undefined} />
            <DataTable rows={data.expenses.rows} rowKey={(e) => e.id} empty={{ icon: "wallet-3-line", title: "No expenses in this period" }}
              columns={[
                { key: "d", header: "Date", cell: (e) => <span className="whitespace-nowrap">{dateLabel(e.date)}</span> },
                { key: "c", header: "Category", cell: (e) => e.category },
                { key: "x", header: "Description", cell: (e) => e.description },
                { key: "a", header: "Amount", align: "right", cell: (e) => <b className="whitespace-nowrap">{peso(e.amount)}</b> },
              ]} />
          </Card>
        </div>
      )}
    </>
  );
}

/** Financial Reports: billed vs collected per month and receivables at each month end. */
export function MonthlyTrend() {
  const [months, setMonths] = useState(6);
  const { data, error, loading, reload } = useAsync(() => api.admin.collections(months), [months]);
  const totals = (data ?? []).reduce((s, c) => ({ billed: s.billed + toCents(c.billed), collected: s.collected + toCents(c.collected) }), { billed: 0, collected: 0 });
  const latest = data?.at(-1);
  return (
    <section className="mb-8" aria-labelledby="trend-title">
      <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 id="trend-title" className="font-display text-[20px] font-bold text-ink-900">Month by month</h2>
          <p className="text-ink-500">Charges billed each month against money collected, and what units owed at each month end.</p>
        </div>
        <select className="input no-print w-44" aria-label="Months shown" value={months} onChange={(e) => setMonths(Number(e.target.value))}>
          <option value={6}>Last 6 months</option><option value={12}>Last 12 months</option><option value={24}>Last 24 months</option>
        </select>
      </div>
      {error ? <ErrorState message={error.message} onRetry={reload} /> : !data ? <Card><Skeleton rows={6} /></Card> : (
        <div className={loading ? "opacity-60 transition-opacity" : undefined} aria-busy={loading || undefined}>
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <StatCard icon="file-list-3-line" label={`Billed · ${months} months`} value={pesoShort(totals.billed / 100)} />
            <StatCard tone="hero" icon="hand-coin-line" label={`Collected · ${months} months`} value={pesoShort(totals.collected / 100)} hint={totals.billed ? `${pct(Math.min(totals.collected / totals.billed, 1))} of billed` : undefined} />
            <StatCard tone="copper" icon="error-warning-line" label={latest ? `Receivables · end of ${monthLabel(latest.month, "short")}` : "Receivables"} value={latest ? pesoShort(toCents(latest.outstanding) / 100) : "—"} hint="Bills up to this month, less payments" />
            <StatCard icon="calendar-line" label="This month" value={latest ? pct(latest.collectionRate) : "—"} hint={latest ? `collected of ${monthLabel(latest.month, "short")} billing` : undefined} />
          </div>
          <Card className="mt-6"><CardHeader title="Billed vs collected" subtitle="Collected = official receipts issued in the month (bills, water and advances), voided receipts excluded." /><div className="p-5"><CollectionsChart data={data} /></div></Card>
          <Card className="mt-6"><CardHeader title="Monthly summary" subtitle="Billed counts each month's own charges; earlier unpaid balances are not counted again." /><CollectionsTable data={data} /></Card>
        </div>
      )}
    </section>
  );
}

function SummaryRows({ rows, total }: { rows: [string, string][]; total?: [string, string] }) {
  return (
    <dl className="px-5 py-3 text-[13.5px]">
      {rows.map(([k, v]) => <div key={k} className="flex justify-between gap-3 py-1"><dt className="text-ink-600">{k}</dt><dd className="tabular text-ink-900">{peso(v)}</dd></div>)}
      {total && <div className="mt-1 flex justify-between gap-3 border-t border-ink-100 pt-2 font-semibold"><dt>{total[0]}</dt><dd className="tabular">{peso(total[1])}</dd></div>}
    </dl>
  );
}

export function NotConnectedCard({ title }: { title: string }) {
  return <Card><EmptyState icon="plug-line" title={title}>This view isn't connected to the server yet.</EmptyState></Card>;
}
