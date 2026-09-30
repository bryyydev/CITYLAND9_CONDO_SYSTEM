// Admin dashboard, Units directory, Water readings, Reports (shared with Accounting).
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useAuth, useUser } from "../../auth/AuthContext";
import { BarChart } from "../../components/base/BarChart";
import { DataTable } from "../../components/base/DataTable";
import { Drawer, useToast } from "../../components/base/overlays";
import { Badge, Button, Card, CardHeader, EmptyState, ErrorState, Field, Icon, Notice, PageHeader, SearchInput, Skeleton, StatCard, StatusBadge, Tabs } from "../../components/base/ui";
import { legacyUrl } from "../../components/feature/ModuleRoute";
import { ROLES } from "../../config/roles";
import { useAction, useAsync } from "../../hooks/useAsync";
import { currentMonth, dateLabel, monthLabel, shortMonth, todayIso } from "../../lib/format";
import { isAmount, peso, pesoShort, toCents } from "../../lib/money";
import { IS_MOCK, api } from "../../services/api";
import type { CollectionSummary, Unit } from "../../services/types";
import { GenerateBillsPanel, MonthPicker } from "./Billing";

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

// ------------------------------------------------------------------ Units
export function UnitsPage() {
  const { data, error, loading, reload } = useAsync(() => api.units.list(), []);
  const [q, setQ] = useState("");
  const [kind, setKind] = useState<"residential" | "PARKING" | "STORAGE">("residential");
  const [open, setOpen] = useState<Unit | null>(null);
  const rows = useMemo(() => {
    const n = q.trim().toLowerCase();
    return (data ?? []).filter((u) => (kind === "residential" ? !["PARKING", "STORAGE"].includes(u.type) : u.type === kind) &&
      (!n || [u.unitNo, u.ownerName ?? "", u.tenantName ?? ""].some((v) => v.toLowerCase().includes(n))));
  }, [data, q, kind]);
  const count = (k: typeof kind) => (data ?? []).filter((u) => (k === "residential" ? !["PARKING", "STORAGE"].includes(u.type) : u.type === k)).length;
  return (
    <>
      <PageHeader eyebrow="Property & Billing" title="Units Directory" description="Units with their current owner and tenant. Parking and storage are units too, assigned to a residential unit." />
      <Card>
        <div className="flex flex-wrap items-center gap-3 border-b border-ink-100 p-4">
          <Tabs value={kind} onChange={setKind} tabs={[{ key: "residential", label: "Residential", count: count("residential") }, { key: "PARKING", label: "Parking", count: count("PARKING") }, { key: "STORAGE", label: "Storage", count: count("STORAGE") }]} />
          <div className="flex-1" /><SearchInput value={q} onChange={setQ} placeholder="Unit, owner or tenant" />
        </div>
        <DataTable rows={data ? rows : null} loading={loading} error={error} onRetry={reload} rowKey={(u) => u.id} onRowClick={setOpen}
          columns={[
            { key: "u", header: "Unit", cell: (u) => <><b className="text-ink-900">{u.unitNo}</b><span className="block text-[12px] text-ink-500">{u.type} · {u.floor}</span></> },
            { key: "a", header: "Area", align: "right", cell: (u) => `${u.areaSqm} m²` },
            { key: "o", header: "Owner", cell: (u) => u.ownerName ?? "—" },
            { key: "t", header: "Tenant", cell: (u) => u.tenantName ?? "—" },
            { key: "p", header: "Parking / storage", cell: (u) => [u.parkingUnitNo, u.storageUnitNo].filter(Boolean).join(" · ") || "—" },
            { key: "s", header: "Status", cell: (u) => <StatusBadge status={u.status} /> },
            { key: "d", header: "Monthly dues", align: "right", cell: (u) => (toCents(u.monthlyDues) ? peso(u.monthlyDues) : "—") },
          ]} />
      </Card>
      <UnitDrawer unit={open} onClose={() => setOpen(null)} />
    </>
  );
}

function UnitDrawer({ unit, onClose }: { unit: Unit | null; onClose: () => void }) {
  const people = useAsync(() => (unit ? api.units.people(unit.id) : Promise.resolve([])), [unit?.id]);
  return (
    <Drawer open={!!unit} onClose={onClose} title={unit ? `Unit ${unit.unitNo}` : ""} subtitle={unit ? `${unit.type} · ${unit.floor} · ${unit.areaSqm} m²` : ""}>
      {unit && (
        <div className="space-y-6">
          <div className="grid grid-cols-2 gap-3">
            {([["Rate per m²", peso(unit.ratePerSqm)], ["Monthly dues", toCents(unit.monthlyDues) ? peso(unit.monthlyDues) : "—"], ["Occupancy", unit.occupancy], ["Status", unit.status], ["Parking", unit.parkingUnitNo ?? "—"], ["Storage", unit.storageUnitNo ?? "—"], ["Contact", unit.contactNo ?? "—"], ["Email", unit.email ?? "—"]] as const).map(([k, v]) => (
              <div key={k} className="rounded-lg bg-ink-50 px-3 py-2"><p className="text-[11.5px] text-ink-500">{k}</p><p className="truncate font-semibold text-ink-900">{v}</p></div>
            ))}
          </div>
          <div>
            <h3 className="mb-2 font-semibold text-ink-900">Owners & tenants</h3>
            {!people.data ? <Skeleton rows={3} /> : people.data.length === 0 ? <p className="text-ink-500">No owner or tenant recorded.</p> : (
              <ul className="divide-y divide-ink-100 rounded-xl border border-ink-200">
                {people.data.map((p) => (
                  <li key={p.id} className="flex items-center justify-between gap-3 px-4 py-2.5">
                    <div><p className="font-semibold">{p.name}</p><p className="text-[12.5px] text-ink-500">{p.type} · moved in {dateLabel(p.moveIn)}{p.moveOut ? ` · out ${dateLabel(p.moveOut)}` : ""}</p></div>
                    <Badge tone={p.status === "Current" ? "ok" : "neutral"}>{p.status}</Badge>
                  </li>
                ))}
              </ul>
            )}
            <p className="mt-3 text-[12.5px] text-ink-500">Marking a tenant or owner "Past" ends their resident portal access automatically.</p>
          </div>
        </div>
      )}
    </Drawer>
  );
}

// ------------------------------------------------------------------ Water readings
export function WaterReadingsPage() {
  const [month, setMonth] = useState(currentMonth());
  const list = useAsync(() => api.water.list(month), [month]);
  const units = useAsync(() => api.units.list(), []);
  const residential = (units.data ?? []).filter((u) => !["PARKING", "STORAGE"].includes(u.type) && u.active);
  const done = new Set((list.data ?? []).map((r) => r.unitId));
  const missing = residential.filter((u) => !done.has(u.id));
  return (
    <>
      <PageHeader eyebrow="Property & Billing" title="Water Readings" description="Enter each unit's meter reading. The previous reading is filled in from last month; saving updates that month's bill if it was already generated."
        actions={<MonthPicker value={month} onChange={setMonth} max={currentMonth()} />} />
      <div className="grid gap-6 xl:grid-cols-[400px_1fr]">
        <Card className="self-start">
          <CardHeader title="Meter reading tool" subtitle={`${done.size} of ${residential.length} units read for ${monthLabel(month)}`} />
          <div className="px-5 pt-4"><div className="h-2 overflow-hidden rounded-full bg-ink-100"><div className="h-full rounded-full bg-chart-1 transition-all" style={{ width: `${residential.length ? (done.size / residential.length) * 100 : 0}%` }} /></div></div>
          <ReadingForm key={month} month={month} units={residential} missing={missing.map((u) => u.id)} onSaved={list.reload} />
        </Card>
        <Card>
          <CardHeader title={`Readings · ${monthLabel(month)}`} />
          <DataTable rows={list.data} loading={list.loading} error={list.error} onRetry={list.reload} rowKey={(r) => r.id}
            empty={{ icon: "drop-line", title: "No readings yet for this month", text: "Use the meter reading tool to enter them." }}
            columns={[
              { key: "u", header: "Unit", cell: (r) => <b className="text-ink-900">{r.unitNo}</b> },
              { key: "p", header: "Previous", align: "right", cell: (r) => r.previous },
              { key: "c", header: "Current", align: "right", cell: (r) => r.current },
              { key: "x", header: "Usage", align: "right", cell: (r) => <b>{r.usage} m³</b> },
              { key: "r", header: "Rate", align: "right", cell: (r) => peso(r.rate) },
              { key: "a", header: "Amount", align: "right", cell: (r) => peso(r.amount) },
              { key: "d", header: "Read on", cell: (r) => dateLabel(r.readingDate) },
              { key: "s", header: "", cell: (r) => r.paid && <Badge tone="ok">Paid separately</Badge> },
            ]} />
        </Card>
      </div>
    </>
  );
}

function ReadingForm({ month, units, missing, onSaved }: { month: string; units: Unit[]; missing: number[]; onSaved: () => void }) {
  const [unitId, setUnitId] = useState(0);
  const [previous, setPrevious] = useState("");
  const [current, setCurrent] = useState("");
  const [rate, setRate] = useState("50.00");
  const [date, setDate] = useState(todayIso());
  const [touched, setTouched] = useState(false);
  const { busy, act } = useAction();
  const toast = useToast();

  async function pick(id: number) {
    setUnitId(id);
    setCurrent("");
    setTouched(false);
    if (id) setPrevious(String(await api.water.previousReading(id, month)));
  }
  const prev = Number(previous), cur = Number(current);
  const usage = current !== "" && previous !== "" && cur >= prev ? cur - prev : null;
  const errors = {
    unit: !unitId && "Choose a unit.",
    current: current === "" ? "Enter the current reading." : cur < prev ? "Can't be lower than the previous reading." : "",
    rate: !isAmount(rate) && "Enter the rate per m³.",
  };

  async function save() {
    setTouched(true);
    if (errors.unit || errors.current || errors.rate) return;
    try {
      const r = await act(() => api.water.save({ unitId, month, previous: prev, current: cur, rate, readingDate: date }));
      toast({ tone: "success", title: `Saved ${r.unitNo}: ${r.usage} m³`, message: `${peso(r.amount)} for ${monthLabel(month)}.` });
      onSaved();
      const next = missing.find((id) => id !== unitId);
      void pick(next ?? 0);
    } catch (err) {
      toast({ tone: "error", title: "Reading not saved", message: (err as Error).message });
    }
  }

  return (
    <form className="grid gap-4 p-5 sm:grid-cols-2" onSubmit={(e) => { e.preventDefault(); void save(); }} noValidate>
      <Field label="Unit" className="sm:col-span-2" error={touched && errors.unit} hint={missing.length ? `${missing.length} unit(s) still to read` : "All units read"}>{(id) => (
        <select id={id} className="input" value={unitId} onChange={(e) => void pick(Number(e.target.value))}>
          <option value={0}>Choose a unit…</option>
          {units.map((u) => <option key={u.id} value={u.id}>{u.unitNo}{missing.includes(u.id) ? "" : " ✓ read"}</option>)}
        </select>)}</Field>
      <Field label="Previous reading">{(id) => <input id={id} inputMode="decimal" className="input tabular" value={previous} onChange={(e) => setPrevious(e.target.value)} />}</Field>
      <Field label="Current reading" error={touched && errors.current}>{(id) => <input id={id} inputMode="decimal" className="input tabular" aria-invalid={Boolean(touched && errors.current)} value={current} onChange={(e) => setCurrent(e.target.value)} />}</Field>
      <Field label="Rate (₱ per m³)" error={touched && errors.rate}>{(id) => <input id={id} inputMode="decimal" className="input tabular" value={rate} onChange={(e) => setRate(e.target.value)} />}</Field>
      <Field label="Reading date">{(id) => <input id={id} type="date" className="input" max={todayIso()} value={date} onChange={(e) => setDate(e.target.value)} />}</Field>
      <div className="flex items-center justify-between rounded-xl bg-brand-50 px-4 py-3 sm:col-span-2">
        <span className="text-brand-800">Usage {usage === null ? "—" : `${Math.round(usage * 100) / 100} m³`}</span>
        <b className="font-display text-[18px] text-brand-900 tabular">{usage === null || !isAmount(rate) ? "—" : peso(Math.round(usage * toCents(rate)) / 100)}</b>
      </div>
      <div className="sm:col-span-2"><Button type="submit" icon="save-3-line" loading={busy} className="w-full">Save reading</Button></div>
    </form>
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
