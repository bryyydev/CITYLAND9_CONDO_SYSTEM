// Admin dashboard and Reports (shared with Accounting). Units Directory: UnitsPage.tsx; Water Readings: WaterPage.tsx.
import { useState } from "react";
import { Link } from "react-router-dom";
import { useAuth, useUser } from "../../auth/AuthContext";
import { BarChart } from "../../components/base/BarChart";
import { DataTable } from "../../components/base/DataTable";
import { useToast } from "../../components/base/overlays";
import { Badge, Button, Card, CardHeader, EmptyState, ErrorState, Icon, Notice, PageHeader, Skeleton, StatCard, StatusBadge, Tabs } from "../../components/base/ui";
import { legacyUrl } from "../../components/feature/ModuleRoute";
import { ROLES } from "../../config/roles";
import { useAsync } from "../../hooks/useAsync";
import { monthLabel, shortMonth } from "../../lib/format";
import { peso, pesoShort, toCents } from "../../lib/money";
import { IS_MOCK, api } from "../../services/api";
import type { CollectionSummary } from "../../services/types";
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
export function ReportsPage({ variant }: { variant: "property" | "financial" }) {
  const { can } = useAuth();
  const toast = useToast();
  const [months, setMonths] = useState(6);
  const { data, error, reload } = useAsync(() => api.admin.collections(months), [months]);
  const totals = (data ?? []).reduce((s, c) => ({ billed: s.billed + toCents(c.billed), collected: s.collected + toCents(c.collected) }), { billed: 0, collected: 0 });
  const latest = data?.at(-1);
  return (
    <>
      <PageHeader eyebrow={variant === "financial" ? "Finance" : "Reports"} title={variant === "financial" ? "Financial Reports" : "Property Reports"}
        description="Billed charges versus money collected (official receipts), and receivables at the end of each month."
        actions={<>
          <select className="input w-40" aria-label="Period" value={months} onChange={(e) => setMonths(Number(e.target.value))}><option value={3}>Last 3 months</option><option value={6}>Last 6 months</option><option value={12}>Last 12 months</option></select>
          {can("reports_export") && (IS_MOCK
            ? <Button variant="secondary" icon="file-excel-2-line" onClick={() => toast({ tone: "info", title: "Excel export runs on the server", message: "In the live system this downloads the condo report workbook." })}>Export to Excel</Button>
            : <a className="inline-flex h-10 items-center gap-2 rounded-lg border border-ink-200 bg-white px-4 font-semibold shadow-sm hover:bg-ink-50" href={legacyUrl("/reports/export.xlsx")}><Icon name="file-excel-2-line" />Export to Excel</a>)}
        </>} />
      {error ? <ErrorState message={error.message} onRetry={reload} /> : !data ? <Card><Skeleton rows={8} /></Card> : (
        <>
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <StatCard icon="file-list-3-line" label={`Billed · ${months} months`} value={pesoShort(totals.billed / 100)} />
            <StatCard tone="hero" icon="hand-coin-line" label={`Collected · ${months} months`} value={pesoShort(totals.collected / 100)} hint={`${totals.billed ? pct(totals.collected / totals.billed) : "—"} collection rate`} />
            <StatCard tone="copper" icon="error-warning-line" label="Receivables now" value={latest ? pesoShort(latest.outstanding) : "—"} hint="Sum of balances on each unit's latest SOA" />
            <StatCard icon="calendar-line" label="Latest month" value={latest ? monthLabel(latest.month, "short") : "—"} hint={latest && `${pct(latest.collectionRate)} collected so far`} />
          </div>
          <Card className="mt-6"><CardHeader title="Billed vs collected" subtitle="Collected = official receipts issued in the month (bills, water and advances)." /><div className="p-5"><CollectionsChart data={data} /></div></Card>
          <Card className="mt-6"><CardHeader title="Monthly summary" /><CollectionsTable data={data} /></Card>
        </>
      )}
    </>
  );
}

export function NotConnectedCard({ title }: { title: string }) {
  return <Card><EmptyState icon="plug-line" title={title}>This view isn't connected to the server yet.</EmptyState></Card>;
}
