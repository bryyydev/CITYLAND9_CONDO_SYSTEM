// Resident portal: own unit only (the server enforces it on every request).
import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useUser } from "../../auth/AuthContext";
import { BarChart } from "../../components/base/BarChart";
import { DataTable } from "../../components/base/DataTable";
import { ConfirmDialog, Drawer, useToast } from "../../components/base/overlays";
import { Badge, Button, Card, CardHeader, EmptyState, ErrorState, Field, Icon, Notice, PageHeader, Skeleton, StatCard, StatusBadge } from "../../components/base/ui";
import { ChangePasswordDrawer } from "../../components/feature/ChangePasswordDrawer";
import { ReceiptDocument, SoaDocument, itemLabel } from "../../components/feature/Documents";
import { useAction, useAsync } from "../../hooks/useAsync";
import { addDays, dateLabel, dateTimeLabel, monthLabel, shortMonth, todayIso } from "../../lib/format";
import { peso, toCents } from "../../lib/money";
import { api } from "../../services/api";
import type { GatePassType, ResidentProfile } from "../../services/types";

const base = "/resident";
const useUnit = () => {
  const user = useUser();
  return user.unitId as number;
};

// ------------------------------------------------------------------ Home + pay-dues preview
export function ResidentHome() {
  const unitId = useUnit();
  const summary = useAsync(() => api.resident.summary(unitId), [unitId]);
  const notices = useAsync(() => api.resident.notices(), []);
  const [preview, setPreview] = useState(false);

  if (summary.error) return <ErrorState title="Couldn't load your unit" message={summary.error.message} onRetry={summary.reload} />;
  if (!summary.data) return <Card><Skeleton rows={6} /></Card>;
  const { unit, residentName, outstandingBalance, latestSoa, openTickets } = summary.data;
  const owes = toCents(outstandingBalance) > 0;

  return (
    <>
      <PageHeader eyebrow="Resident Portal" title={`Welcome, ${residentName ?? "resident"}`}
        description={`Unit ${unit.unitNo}${unit.type ? ` · ${unit.type}` : ""}${unit.floor ? ` · ${unit.floor}` : ""}`}
        actions={<>
          <Link to={`${base}/my-maintenance`} className="inline-flex h-10 items-center gap-2 rounded-lg border border-ink-200 bg-white px-4 font-semibold shadow-sm hover:bg-ink-50"><Icon name="tools-line" />Request maintenance</Link>
          {latestSoa && <Button icon="wallet-3-line" variant="copper" onClick={() => setPreview(true)}>Pay dues</Button>}
        </>} />
      <div className="grid gap-4 md:grid-cols-3">
        <StatCard tone={owes ? "hero" : "default"} icon="wallet-3-line" label="Outstanding balance" value={peso(outstandingBalance)}
          hint={owes ? (latestSoa?.dueDate ? `Due ${dateLabel(latestSoa.dueDate)}` : "Please settle at the Admin Office") : "Nothing to pay. Thank you!"} />
        <StatCard icon="file-text-line" label="Latest statement" value={latestSoa ? monthLabel(latestSoa.billingMonth) : "—"}
          hint={latestSoa ? <span className="inline-flex items-center gap-2"><StatusBadge status={latestSoa.status} /> Total {peso(latestSoa.total)}</span> : "No statements yet."} />
        <StatCard icon="tools-line" label="Open maintenance requests" value={openTickets} hint={<Link className="font-semibold text-brand-600 hover:underline" to={`${base}/my-maintenance`}>See requests →</Link>} />
      </div>
      <div className="mt-6 grid gap-6 lg:grid-cols-[2fr_1fr]">
        <Card>
          <CardHeader title="Latest notices" actions={<Link to={`${base}/announcements`} className="text-[13px] font-semibold text-brand-600 hover:underline">All notices</Link>} />
          {notices.error ? <ErrorState message={notices.error.message} onRetry={notices.reload} /> : !notices.data ? <Skeleton rows={3} /> : notices.data.length === 0 ? (
            <EmptyState icon="megaphone-line" title="No notices yet" />
          ) : (
            <ul className="divide-y divide-ink-100">
              {notices.data.slice(0, 3).map((n) => (
                <li key={n.id} className="flex items-start justify-between gap-4 px-5 py-3.5">
                  <div><p className="font-semibold text-ink-900">{n.title}</p><p className="line-clamp-2 text-ink-500">{n.message}</p></div>
                  <span className="text-[12.5px] whitespace-nowrap text-ink-400">{dateLabel(n.publishDate)}</span>
                </li>
              ))}
            </ul>
          )}
        </Card>
        <Card>
          <CardHeader title="Quick links" />
          <div className="grid gap-1 p-3">
            {([["my-soa", "file-text-line", "Statements of account"], ["payment-history", "receipt-line", "Payments & receipts"], ["water", "drop-line", "Water usage"], ["gate-pass", "passport-line", "Gate pass & moving"]] as const).map(([to, icon, label]) => (
              <Link key={to} to={`${base}/${to}`} className="flex items-center gap-3 rounded-lg px-3 py-2.5 font-medium text-ink-700 hover:bg-brand-50 hover:text-brand-700">
                <span className="grid size-8 place-items-center rounded-lg bg-brand-50 text-brand-600"><Icon name={icon} /></span>{label}<Icon name="arrow-right-s-line" className="ml-auto text-ink-400" />
              </Link>
            ))}
          </div>
        </Card>
      </div>
      {latestSoa && <PayDuesPreview open={preview} onClose={() => setPreview(false)} unitId={unitId} billId={latestSoa.id} unitNo={unit.unitNo} />}
    </>
  );
}

/** What the resident will pay at the office. There is no online payment in the system. */
function PayDuesPreview({ open, onClose, unitId, billId, unitNo }: { open: boolean; onClose: () => void; unitId: number; billId: number; unitNo: string }) {
  const { data, error } = useAsync(() => (open ? api.resident.statement(unitId, billId) : Promise.resolve(null)), [open, unitId, billId]);
  const s = data?.statement;
  return (
    <Drawer open={open} onClose={onClose} title="Pay dues — preview" subtitle={`Unit ${unitNo}`} width="max-w-md"
      footer={<><Link to={`${base}/my-soa/${billId}`} className="inline-flex h-10 items-center gap-2 rounded-lg border border-ink-200 bg-white px-4 font-semibold hover:bg-ink-50" onClick={onClose}><Icon name="file-text-line" />Open full SOA</Link><Button onClick={onClose}>Done</Button></>}>
      {error ? <Notice tone="warn">{error.message}</Notice> : !s ? <Skeleton rows={5} /> : (
        <div className="space-y-5">
          <div className="rounded-xl bg-gradient-to-br from-brand-600 to-brand-800 p-5 text-white">
            <p className="text-brand-100">Amount to pay · {monthLabel(s.billingMonth)}</p>
            <p className="font-display text-[30px] font-semibold tabular">{peso(s.balance)}</p>
            <p className="text-[13px] text-brand-100">Due {dateLabel(s.dueDate)} · <StatusBadge status={s.status} /></p>
          </div>
          <dl className="divide-y divide-ink-100 rounded-xl border border-ink-200 text-[13.5px]">
            {([["Condo dues", s.charges.condoDues], ["Parking", s.charges.parking], ["Water", s.charges.waterPaidSeparately ? "0" : s.charges.water], ["Previous balance", s.charges.previousBalance], ["Penalty", s.charges.penalty]] as const)
              .filter(([, v]) => toCents(v) > 0).map(([k, v]) => <div key={k} className="flex justify-between px-4 py-2.5"><dt className="text-ink-600">{k}</dt><dd className="tabular">{peso(v)}</dd></div>)}
            {toCents(s.charges.advanceApplied) > 0 && <div className="flex justify-between px-4 py-2.5"><dt className="text-ink-600">Advance applied</dt><dd className="tabular">− {peso(s.charges.advanceApplied)}</dd></div>}
            {toCents(s.amountPaid) > 0 && <div className="flex justify-between px-4 py-2.5"><dt className="text-ink-600">Already paid</dt><dd className="tabular">− {peso(s.amountPaid)}</dd></div>}
          </dl>
          <Notice tone="copper" icon="store-2-line" title="How to pay">
            Pay at the Admin Office (Ground Floor) by cash, check or bank transfer. Bring your unit number; for checks and transfers give the reference number. Your official receipt (OR) will appear in Payment History.
          </Notice>
        </div>
      )}
    </Drawer>
  );
}

// ------------------------------------------------------------------ SOA
export function ResidentSoaList() {
  const unitId = useUnit();
  const { data, error, loading, reload } = useAsync(() => api.resident.statements(unitId), [unitId]);
  return (
    <>
      <PageHeader eyebrow="My Unit" title="Statement of Account" description="Every monthly statement for your unit. Open one to see the charges and payments, print it, or save it as PDF." />
      <Card>
        <DataTable rows={data} loading={loading} error={error} onRetry={reload} rowKey={(s) => s.id}
          empty={{ icon: "file-text-line", title: "No statements yet", text: "Your first statement will appear after the monthly billing is generated." }}
          columns={[
            { key: "m", header: "Billing period", cell: (s) => <b className="text-ink-900">{monthLabel(s.billingMonth)}</b> },
            { key: "d", header: "Due date", cell: (s) => dateLabel(s.dueDate) },
            { key: "t", header: "Total", align: "right", cell: (s) => peso(s.total) },
            { key: "p", header: "Paid", align: "right", cell: (s) => peso(s.amountPaid) },
            { key: "b", header: "Balance", align: "right", cell: (s) => <b>{peso(s.balance)}</b> },
            { key: "s", header: "Status", cell: (s) => <StatusBadge status={s.status} /> },
            { key: "v", header: "", cell: (s) => <Link className="font-semibold text-brand-600 hover:underline" to={`${base}/my-soa/${s.id}`}>View</Link> },
          ]} />
      </Card>
    </>
  );
}

export function ResidentSoaDetail() {
  const unitId = useUnit();
  const billId = Number(useParams().billId);
  const { data, error, reload } = useAsync(() => api.resident.statement(unitId, billId), [unitId, billId]);
  const docRef = useRef<HTMLElement>(null);
  const { busy, act } = useAction();
  const toast = useToast();

  async function downloadPdf() {
    if (!data || !docRef.current) return;
    try {
      await act(async () => {
        // Bundled locally and loaded only when needed; no internet required.
        const html2pdf = (await import("html2pdf.js")).default;
        await html2pdf().set({ margin: 10, filename: `SOA-${data.unit.unitNo}-${data.statement.billingMonth}.pdf`, html2canvas: { scale: 2, backgroundColor: "#ffffff" }, jsPDF: { unit: "mm", format: "a4" } })
          .from(docRef.current!).save();
      });
    } catch {
      toast({ tone: "error", title: "Couldn't create the PDF", message: "Please use Print instead." });
    }
  }

  if (error) return <><BackLink to={`${base}/my-soa`} label="All statements" /><ErrorState title={error.status === 404 ? "Statement not found" : "Couldn't load the statement"} message={error.message} onRetry={error.status === 404 ? undefined : reload} /></>;
  if (!data) return <Card><Skeleton rows={8} /></Card>;
  return (
    <>
      <PageHeader eyebrow="Statement of Account" title={monthLabel(data.statement.billingMonth)} description={`Unit ${data.unit.unitNo}`}
        actions={<><BackLink to={`${base}/my-soa`} label="All statements" /><Button variant="secondary" icon="file-download-line" loading={busy} onClick={downloadPdf}>Download PDF</Button><Button icon="printer-line" onClick={() => window.print()}>Print</Button></>} />
      <SoaDocument ref={docRef} corporation={data.corporation} unitNo={data.unit.unitNo} statement={data.statement} />
    </>
  );
}

function BackLink({ to, label }: { to: string; label: string }) {
  return <Link to={to} className="no-print inline-flex h-10 items-center gap-1.5 rounded-lg px-3 font-semibold text-ink-600 hover:bg-ink-100"><Icon name="arrow-left-line" />{label}</Link>;
}

// ------------------------------------------------------------------ Payments & receipts
export function ResidentPayments() {
  const unitId = useUnit();
  const { data, error, loading, reload } = useAsync(() => api.resident.receipts(unitId), [unitId]);
  return (
    <>
      <PageHeader eyebrow="My Unit" title="Payment History" description="Every payment the Admin Office received for your unit, with its official receipt (OR)." />
      {data && data.receipts.length > 0 && <div className="mb-4 grid gap-4 sm:grid-cols-3"><StatCard icon="hand-coin-line" label="Total paid" value={peso(data.totalPaid)} hint={`${data.receipts.length} official receipt(s)`} /></div>}
      <Card>
        <DataTable rows={data?.receipts ?? null} loading={loading} error={error} onRetry={reload} rowKey={(r) => r.id}
          empty={{ icon: "receipt-line", title: "No payments yet", text: "Payments received at the Admin Office will be listed here with their official receipt." }}
          columns={[
            { key: "d", header: "Date", cell: (r) => dateLabel(r.date) },
            { key: "o", header: "OR No.", cell: (r) => <b className="text-ink-900">{r.receiptNo}</b> },
            { key: "f", header: "Paid for", cell: (r) => r.items.map(itemLabel).join(", ") },
            { key: "m", header: "Method", cell: (r) => <>{r.method}{r.reference && <span className="block text-[12px] text-ink-500">Ref. {r.reference}</span>}</> },
            { key: "a", header: "Amount", align: "right", cell: (r) => <b>{peso(r.amount)}</b> },
            { key: "v", header: "", cell: (r) => <Link className="font-semibold text-brand-600 hover:underline" to={`${base}/payment-history/${r.id}`}>Receipt</Link> },
          ]} />
      </Card>
    </>
  );
}

export function ResidentReceipt() {
  const unitId = useUnit();
  const receiptId = Number(useParams().receiptId);
  const { data, error, reload } = useAsync(() => api.resident.receipt(unitId, receiptId), [unitId, receiptId]);
  if (error) return <><BackLink to={`${base}/payment-history`} label="Payment history" /><ErrorState title="Couldn't load this receipt" message={error.message} onRetry={reload} /></>;
  if (!data) return <Card><Skeleton rows={6} /></Card>;
  return (
    <>
      <PageHeader eyebrow="Official Receipt" title={data.receipt.receiptNo} description={`Unit ${data.unit.unitNo} · ${dateLabel(data.receipt.date)}`}
        actions={<><BackLink to={`${base}/payment-history`} label="Payment history" /><Button icon="printer-line" onClick={() => window.print()}>Print</Button></>} />
      <ReceiptDocument corporation={data.corporation} address={data.address} unitNo={data.unit.unitNo} receivedFrom={data.receivedFrom} receipt={data.receipt} />
    </>
  );
}

// ------------------------------------------------------------------ Water
export function ResidentWater() {
  const unitId = useUnit();
  const { data, error, reload } = useAsync(() => api.resident.water(unitId), [unitId]);
  if (error) return <ErrorState title="Couldn't load your water readings" message={error.message} onRetry={reload} />;
  if (!data) return <Card><Skeleton rows={6} /></Card>;
  const latest = data[0];
  const last12 = data.slice(0, 12);
  const avg = last12.length ? last12.reduce((s, r) => s + r.usage, 0) / last12.length : 0;
  return (
    <>
      <PageHeader eyebrow="My Unit" title="Water Usage" description="Your monthly meter readings. Water is billed on your Statement of Account unless it was paid separately." />
      {!latest ? <Card><EmptyState icon="drop-line" title="No readings yet">Your meter readings will appear here after the first monthly reading.</EmptyState></Card> : (
        <>
          <div className="grid gap-4 md:grid-cols-3">
            <StatCard icon="drop-line" label={`Latest usage · ${monthLabel(latest.month)}`} value={`${latest.usage} m³`} hint={`${peso(latest.amount)} at ${peso(latest.rate)}/m³`} />
            <StatCard icon="bar-chart-2-line" label="Monthly average" value={`${avg.toFixed(1)} m³`} hint={`Last ${last12.length} reading(s)`} />
            <StatCard icon="dashboard-2-line" label="Meter now reads" value={latest.current} hint={`Read on ${dateLabel(latest.readingDate)}`} />
          </div>
          <Card className="mt-6">
            <CardHeader title="Usage per month (m³)" />
            <div className="p-5"><BarChart ariaLabel="Water usage per month" series={[{ key: "usage", label: "Usage", color: "chart-1" }]} format={(v) => `${Math.round(v * 10) / 10} m³`}
              data={[...last12].reverse().map((r) => ({ label: shortMonth(r.month), detail: monthLabel(r.month), values: { usage: r.usage } }))} /></div>
          </Card>
          <Card className="mt-6">
            <CardHeader title="Readings" />
            <DataTable rows={data} rowKey={(r) => r.month} columns={[
              { key: "m", header: "Month", cell: (r) => <b className="text-ink-900">{monthLabel(r.month)}</b> },
              { key: "p", header: "Previous", align: "right", cell: (r) => r.previous },
              { key: "c", header: "Current", align: "right", cell: (r) => r.current },
              { key: "u", header: "Usage (m³)", align: "right", cell: (r) => <b>{r.usage}</b> },
              { key: "r", header: "Rate", align: "right", cell: (r) => peso(r.rate) },
              { key: "a", header: "Amount", align: "right", cell: (r) => peso(r.amount) },
              { key: "s", header: "", cell: (r) => r.paidSeparately && <Badge tone="ok">Paid separately</Badge> },
            ]} />
          </Card>
        </>
      )}
    </>
  );
}

// ------------------------------------------------------------------ Maintenance
const EMPTY_TICKET = { title: "", description: "", category: "General", priority: "Normal" };
export function ResidentMaintenance() {
  const unitId = useUnit();
  const { data, error, loading, reload } = useAsync(() => api.resident.tickets(unitId), [unitId]);
  const [form, setForm] = useState(EMPTY_TICKET);
  const [touched, setTouched] = useState(false);
  const { busy, act } = useAction();
  const toast = useToast();

  async function submit() {
    setTouched(true);
    if (!form.title.trim() || !form.description.trim()) return;
    try {
      const t = await act(() => api.resident.fileTicket(unitId, form));
      setForm(EMPTY_TICKET);
      setTouched(false);
      reload();
      toast({ tone: "success", title: "Request sent", message: `Your ticket number is ${t.ticketNo}.` });
    } catch (err) {
      toast({ tone: "error", title: "Couldn't send the request", message: (err as Error).message });
    }
  }

  return (
    <>
      <PageHeader eyebrow="Requests" title="My Unit Maintenance" description="Report a problem in your unit. The building staff will update the status here." />
      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader title="New request" />
          <form className="grid gap-4 p-5 sm:grid-cols-2" onSubmit={(e) => { e.preventDefault(); void submit(); }} noValidate>
            <Field label="Category">{(id) => <select id={id} className="input" value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })}>{(data?.categories ?? ["General"]).map((c) => <option key={c}>{c}</option>)}</select>}</Field>
            <Field label="Priority">{(id) => <select id={id} className="input" value={form.priority} onChange={(e) => setForm({ ...form, priority: e.target.value })}>{(data?.priorities ?? ["Normal"]).map((c) => <option key={c}>{c}</option>)}</select>}</Field>
            <Field label="Title" className="sm:col-span-2" error={touched && !form.title.trim() && "Enter a short title."}>{(id) => <input id={id} className="input" maxLength={200} placeholder="e.g. Leaking kitchen faucet" aria-invalid={touched && !form.title.trim()} value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} />}</Field>
            <Field label="Description" className="sm:col-span-2" error={touched && !form.description.trim() && "Describe the problem."}>{(id) => <textarea id={id} className="input" rows={4} placeholder="What happened, where, and since when?" aria-invalid={touched && !form.description.trim()} value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />}</Field>
            <div className="sm:col-span-2"><Button type="submit" icon="send-plane-line" loading={busy}>Send request</Button></div>
          </form>
        </Card>
        <Card>
          <CardHeader title="My requests" />
          {error ? <ErrorState message={error.message} onRetry={reload} /> : loading && !data ? <Skeleton rows={4} /> : !data?.tickets.length ? (
            <EmptyState icon="tools-line" title="No requests yet">Requests you send will be listed here.</EmptyState>
          ) : (
            <ul className="divide-y divide-ink-100">
              {data.tickets.map((t) => (
                <li key={t.ticketNo} className="flex items-start justify-between gap-3 px-5 py-3.5">
                  <div className="min-w-0">
                    <p className="font-semibold text-ink-900">{t.title}</p>
                    <p className="text-[12.5px] text-ink-500">{t.category} · {t.ticketNo} · {dateTimeLabel(t.requestedAt)}</p>
                    {t.resolution && <p className="mt-1 text-[13px] text-ink-600">Update: {t.resolution}</p>}
                  </div>
                  <StatusBadge status={t.status} />
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </>
  );
}

// ------------------------------------------------------------------ Gate pass & moving
const HINTS: Record<GatePassType, { name: string; purpose: string }> = {
  Visitor: { name: "Visitor's full name", purpose: "e.g. Family visit, 2 guests, staying until 9 PM" },
  Delivery: { name: "Courier / company", purpose: "e.g. Refrigerator delivery via service elevator" },
  "Move-in": { name: "Mover / trucking company", purpose: "e.g. Furniture and boxes, 1 truck, plate ABC 1234" },
  "Move-out": { name: "Mover / trucking company", purpose: "e.g. All furniture, 2 trucks" },
};
export function ResidentGatePass() {
  const unitId = useUnit();
  const { data, error, loading, reload } = useAsync(() => api.resident.gatePasses(unitId), [unitId]);
  const today = todayIso();
  const [form, setForm] = useState({ type: "Visitor" as GatePassType, date: today, name: "", purpose: "" });
  const [touched, setTouched] = useState(false);
  const [cancelId, setCancelId] = useState<number | null>(null);
  const { busy, act } = useAction();
  const toast = useToast();
  const maxDate = addDays(today, data?.maxDaysAhead ?? 90);
  const bad = { name: !form.name.trim(), purpose: !form.purpose.trim(), date: !form.date || form.date < today || form.date > maxDate };

  async function submit() {
    setTouched(true);
    if (bad.name || bad.purpose || bad.date) return;
    try {
      await act(() => api.resident.requestGatePass(unitId, form));
      setForm({ type: "Visitor", date: today, name: "", purpose: "" });
      setTouched(false);
      reload();
      toast({ tone: "success", title: "Request sent", message: "The Admin Office will approve it before the date. You'll see the status here." });
    } catch (err) {
      toast({ tone: "error", title: "Couldn't send the request", message: (err as Error).message });
    }
  }

  async function cancel() {
    if (cancelId === null) return;
    try {
      await act(() => api.resident.cancelGatePass(unitId, cancelId));
      toast({ tone: "success", title: "Request cancelled" });
    } catch (err) {
      toast({ tone: "error", title: "Couldn't cancel", message: (err as Error).message });
    } finally {
      setCancelId(null);
      reload();
    }
  }

  return (
    <>
      <PageHeader eyebrow="Requests" title="Gate Pass & Moving" description="Request a pass for a visitor, a delivery, or moving in / out. The guard lets them in once the Admin Office approves it." />
      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader title="New request" />
          <form className="grid gap-4 p-5 sm:grid-cols-2" onSubmit={(e) => { e.preventDefault(); void submit(); }} noValidate>
            <Field label="Type">{(id) => <select id={id} className="input" value={form.type} onChange={(e) => setForm({ ...form, type: e.target.value as GatePassType })}>{(data?.types ?? (Object.keys(HINTS) as GatePassType[])).map((t) => <option key={t}>{t}</option>)}</select>}</Field>
            <Field label="Date needed" error={touched && bad.date && `Choose a date from today up to ${dateLabel(maxDate)}.`}>{(id) => <input id={id} type="date" className="input" min={today} max={maxDate} value={form.date} aria-invalid={touched && bad.date} onChange={(e) => setForm({ ...form, date: e.target.value })} />}</Field>
            <Field label={HINTS[form.type].name} className="sm:col-span-2" error={touched && bad.name && "Enter a name."}>{(id) => <input id={id} className="input" maxLength={200} value={form.name} aria-invalid={touched && bad.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />}</Field>
            <Field label="Details for the guard" className="sm:col-span-2" error={touched && bad.purpose && "Add a few details for the guard."}>{(id) => <textarea id={id} className="input" rows={3} maxLength={300} placeholder={HINTS[form.type].purpose} value={form.purpose} aria-invalid={touched && bad.purpose} onChange={(e) => setForm({ ...form, purpose: e.target.value })} />}</Field>
            <div className="sm:col-span-2"><Button type="submit" icon="passport-line" loading={busy}>Send request</Button></div>
          </form>
        </Card>
        <Card>
          <CardHeader title="My requests" />
          {error ? <ErrorState message={error.message} onRetry={reload} /> : loading && !data ? <Skeleton rows={4} /> : !data?.passes.length ? (
            <EmptyState icon="passport-line" title="No requests yet">Requests you send will be listed here.</EmptyState>
          ) : (
            <ul className="divide-y divide-ink-100">
              {data.passes.map((p) => (
                <li key={p.id} className="flex items-start justify-between gap-3 px-5 py-3.5">
                  <div className="min-w-0">
                    <p className="font-semibold text-ink-900">{p.type} · {dateLabel(p.date)} <span className="font-normal text-ink-500">· {p.name}</span></p>
                    <p className="text-ink-500">{p.purpose}</p>
                    {p.reviewNote && <p className="mt-1 text-[13px] text-ink-600">Office note: {p.reviewNote}</p>}
                  </div>
                  <div className="flex flex-col items-end gap-2">
                    <StatusBadge status={p.status} label={p.status === "Issued" ? "Approved" : p.status} />
                    {p.status === "Requested" && <Button size="sm" variant="secondary" onClick={() => setCancelId(p.id)}>Cancel</Button>}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
      <ConfirmDialog open={cancelId !== null} title="Cancel this request?" message="The Admin Office will no longer see it." confirmLabel="Cancel request" cancelLabel="Keep it" tone="danger" busy={busy}
        onClose={() => setCancelId(null)} onConfirm={cancel} />
    </>
  );
}

// ------------------------------------------------------------------ Notices
export function ResidentNotices() {
  const { data, error, loading, reload } = useAsync(() => api.resident.notices(), []);
  return (
    <>
      <PageHeader eyebrow="Community" title="Announcements & Notices" description="News from the Cityland 9 administration." />
      <Card>
        {error ? <ErrorState message={error.message} onRetry={reload} /> : loading && !data ? <Skeleton rows={5} /> : !data?.length ? (
          <EmptyState icon="megaphone-line" title="No notices yet">Announcements from the administration will appear here.</EmptyState>
        ) : (
          <ul className="divide-y divide-ink-100">
            {data.map((n) => (
              <li key={n.id} className="px-5 py-4">
                <div className="flex flex-wrap items-baseline justify-between gap-2"><h3 className="font-semibold text-ink-900">{n.title}</h3><span className="text-[12.5px] text-ink-400">{dateLabel(n.publishDate)}</span></div>
                <p className="mt-1 whitespace-pre-line text-ink-600">{n.message}</p>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </>
  );
}

// ------------------------------------------------------------------ Profile & password
export function ResidentProfilePage() {
  const unitId = useUnit();
  const [pwOpen, setPwOpen] = useState(false);
  const { data, error, reload, setData } = useAsync(() => api.resident.profile(unitId), [unitId]);
  if (error) return <ErrorState title="Couldn't load your profile" message={error.message} onRetry={reload} />;
  if (!data) return <Card><Skeleton rows={6} /></Card>;
  return (
    <>
      <PageHeader eyebrow="Account" title={data.name ?? data.displayName} description={`${data.personType} of unit ${data.unit.unitNo} · signed in as ${data.username}`}
        actions={<Button variant="secondary" icon="lock-password-line" onClick={() => setPwOpen(true)}>Change password</Button>} />
      <div className="grid gap-4 md:grid-cols-3">
        <StatCard icon="building-2-line" label="Unit" value={data.unit.unitNo} hint={[data.unit.type, data.unit.floor].filter(Boolean).join(" · ") || "—"} />
        <StatCard icon="ruler-line" label="Floor area" value={data.unit.areaSqm ? `${data.unit.areaSqm} m²` : "—"} hint="Used for your monthly condo dues" />
        <StatCard icon="door-open-line" label="Moved in" value={dateLabel(data.moveIn)} hint="As recorded by the Admin Office" />
      </div>
      <ContactCard key={`${data.contactNo}|${data.email}`} unitId={unitId} profile={data} onSaved={setData} />
      <ChangePasswordDrawer open={pwOpen} onClose={() => setPwOpen(false)} />
    </>
  );
}

function ContactCard({ unitId, profile, onSaved }: { unitId: number; profile: ResidentProfile; onSaved: (p: ResidentProfile) => void }) {
  const [form, setForm] = useState({ contactNo: profile.contactNo, email: profile.email });
  const { busy, act } = useAction();
  const toast = useToast();
  const changed = form.contactNo !== profile.contactNo || form.email !== profile.email;
  useEffect(() => setForm({ contactNo: profile.contactNo, email: profile.email }), [profile]);
  async function save() {
    try {
      onSaved(await act(() => api.resident.updateContact(unitId, form)));
      toast({ tone: "success", title: "Contact details saved", message: "The Admin Office will use these to reach you and send your SOA." });
    } catch (err) {
      toast({ tone: "error", title: "Couldn't save", message: (err as Error).message });
    }
  }
  return (
    <Card className="mt-6 max-w-2xl">
      <CardHeader title="Contact details" />
      <form className="grid gap-4 p-5 sm:grid-cols-2" onSubmit={(e) => { e.preventDefault(); void save(); }}>
        {!profile.canEdit && <div className="sm:col-span-2"><Notice>Your account isn't linked to an owner or tenant record yet, so the Admin Office updates your contact details.</Notice></div>}
        <Field label="Mobile / phone number">{(id) => <input id={id} type="tel" className="input" maxLength={80} disabled={!profile.canEdit} placeholder="e.g. 0917 123 4567" value={form.contactNo} onChange={(e) => setForm({ ...form, contactNo: e.target.value })} />}</Field>
        <Field label="Email">{(id) => <input id={id} type="email" className="input" maxLength={160} disabled={!profile.canEdit} placeholder="name@example.com" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />}</Field>
        {profile.canEdit && <div className="sm:col-span-2"><Button type="submit" icon="save-3-line" loading={busy} disabled={!changed}>Save contact details</Button></div>}
      </form>
    </Card>
  );
}
