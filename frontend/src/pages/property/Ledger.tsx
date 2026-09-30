// Official receipts ledger, advance payments, and a "record a payment" finder.
import { useMemo, useState } from "react";
import { Can, useAuth } from "../../auth/AuthContext";
import { DataTable } from "../../components/base/DataTable";
import { Drawer, useToast } from "../../components/base/overlays";
import { Badge, Button, Card, CardHeader, Field, Icon, Notice, PageHeader, SearchInput, StatCard } from "../../components/base/ui";
import { ReceiptDocument, itemLabel } from "../../components/feature/Documents";
import { useAction, useAsync } from "../../hooks/useAsync";
import { addDays, addMonths, currentMonth, dateLabel, monthLabel, todayIso } from "../../lib/format";
import { fromCents, isAmount, peso, toCents } from "../../lib/money";
import { api } from "../../services/api";
import type { AdvanceInput, BillRow, OfficialReceipt, PaymentMethod } from "../../services/types";
import { CORPORATION, MonthPicker, RecordPaymentForm } from "./Billing";

// ------------------------------------------------------------------ official receipts
export function ReceiptsLedger({ title = "Payments & Official Receipts", eyebrow = "Property & Billing", recordAction = false }: { title?: string; eyebrow?: string; recordAction?: boolean }) {
  const [from, setFrom] = useState(addDays(todayIso(), -60));
  const [to, setTo] = useState(todayIso());
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<OfficialReceipt | null>(null);
  const [recording, setRecording] = useState(false);
  const { data, error, loading, reload } = useAsync(() => api.receipts.list({ from, to, q: "" }), [from, to]);

  const rows = useMemo(() => {
    const n = q.trim().toLowerCase();
    return (data ?? []).filter((r) => !n || r.receiptNo.toLowerCase().includes(n) || r.unitNo.toLowerCase().includes(n) || r.reference.toLowerCase().includes(n));
  }, [data, q]);
  const byMethod = rows.reduce<Record<string, number>>((acc, r) => ({ ...acc, [r.method]: (acc[r.method] ?? 0) + toCents(r.amount) }), {});
  const total = rows.reduce((s, r) => s + toCents(r.amount), 0);

  return (
    <>
      <PageHeader eyebrow={eyebrow} title={title} description="Every payment gets an official receipt, numbered per year without gaps (OR-YYYY-NNNNNN). Receipts are issued by the server and can't be edited."
        actions={recordAction && <Can permission="mark_bill_paid"><Button icon="hand-coin-line" onClick={() => setRecording(true)}>Record a payment</Button></Can>} />
      <div className="mb-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard tone="hero" icon="hand-coin-line" label="Collected in range" value={peso(fromCents(total))} hint={`${rows.length} receipt(s)`} />
        {(["CASH", "CHECK", "ONLINE"] as const).map((m) => <StatCard key={m} icon={{ CASH: "money-dollar-circle-line", CHECK: "bank-card-2-line", ONLINE: "global-line" }[m]} label={m === "ONLINE" ? "Online / transfer" : m[0] + m.slice(1).toLowerCase()} value={peso(fromCents(byMethod[m] ?? 0))} />)}
      </div>
      <Card>
        <div className="flex flex-wrap items-end gap-3 border-b border-ink-100 p-4">
          <Field label="From">{(id) => <input id={id} type="date" className="input" value={from} max={to} onChange={(e) => setFrom(e.target.value)} />}</Field>
          <Field label="To">{(id) => <input id={id} type="date" className="input" value={to} min={from} onChange={(e) => setTo(e.target.value)} />}</Field>
          <div className="flex-1" />
          <SearchInput value={q} onChange={setQ} placeholder="OR no., unit or reference" />
        </div>
        <DataTable rows={data ? rows : null} loading={loading} error={error} onRetry={reload} rowKey={(r) => r.id} onRowClick={setOpen}
          empty={{ icon: "receipt-line", title: "No receipts in this range" }}
          columns={[
            { key: "o", header: "OR No.", cell: (r) => <b className="text-ink-900 tabular">{r.receiptNo}</b> },
            { key: "d", header: "Date", cell: (r) => dateLabel(r.date) },
            { key: "u", header: "Unit", cell: (r) => r.unitNo },
            { key: "f", header: "Paid for", cell: (r) => <span className="line-clamp-1">{r.items.map(itemLabel).join(", ")}</span> },
            { key: "m", header: "Method", cell: (r) => <>{r.method}{r.reference && <span className="block text-[12px] text-ink-500">Ref. {r.reference}</span>}</> },
            { key: "b", header: "Received by", cell: (r) => <>{r.receivedBy}{r.backfilled && <Badge tone="neutral" dot={false}>backfilled</Badge>}</> },
            { key: "a", header: "Amount", align: "right", cell: (r) => <b>{peso(r.amount)}</b> },
          ]} />
      </Card>
      <Drawer open={!!open} onClose={() => setOpen(null)} title={open?.receiptNo ?? ""} subtitle={open ? `Unit ${open.unitNo} · ${dateLabel(open.date)}` : ""} width="max-w-3xl"
        footer={<Button icon="printer-line" onClick={() => window.print()}>Print receipt</Button>}>
        {open && <ReceiptDocument corporation={CORPORATION} address="9 Dela Rosa Street, Barangay Pio del Pilar, Makati City" unitNo={open.unitNo} receivedFrom="" receipt={open} />}
      </Drawer>
      <Drawer open={recording} onClose={() => setRecording(false)} title="Record a payment" subtitle="Find the unit's bill, then record what was received." width="max-w-2xl">
        {recording && <PaymentFinder onDone={() => { setRecording(false); reload(); }} />}
      </Drawer>
    </>
  );
}

/** Pick a bill with a balance, then record the payment against it. */
function PaymentFinder({ onDone }: { onDone: () => void }) {
  const [month, setMonth] = useState(addMonths(currentMonth(), -1));
  const [q, setQ] = useState("");
  const [bill, setBill] = useState<BillRow | null>(null);
  const bills = useAsync(() => api.billing.list(month), [month]);
  if (bill) return <RecordPaymentForm bill={bill} onCancel={() => setBill(null)} onDone={onDone} />;
  const n = q.trim().toLowerCase();
  const open = (bills.data ?? []).filter((b) => toCents(b.balance) > 0 && (!n || b.unitNo.toLowerCase().includes(n) || b.payerName.toLowerCase().includes(n)));
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3"><MonthPicker value={month} onChange={setMonth} max={currentMonth()} /><SearchInput value={q} onChange={setQ} placeholder="Unit or name" /></div>
      <Card>
        <DataTable rows={bills.data ? open : null} loading={bills.loading} error={bills.error} rowKey={(b) => b.id} onRowClick={setBill}
          empty={{ icon: "checkbox-circle-line", title: "No unpaid bills found", text: `Everything for ${monthLabel(month)} is paid, or nothing matches the search.` }}
          columns={[
            { key: "u", header: "Unit", cell: (b) => <b>{b.unitNo}</b> },
            { key: "n", header: "Billed to", cell: (b) => b.payerName },
            { key: "b", header: "Balance", align: "right", cell: (b) => <b>{peso(b.balance)}</b> },
            { key: "go", header: "", cell: () => <span className="font-semibold text-brand-600">Select →</span> },
          ]} />
      </Card>
    </div>
  );
}

// ------------------------------------------------------------------ advance payments
const EMPTY_ADV = (): AdvanceInput => ({ unitId: 0, amount: "", startMonth: currentMonth(), months: 3, method: "CASH", reference: "", date: todayIso(), remarks: "" });

export function AdvancesWorkspace({ embedded = false }: { embedded?: boolean }) {
  const { can } = useAuth();
  const { data, error, loading, reload } = useAsync(() => api.advances.list(), []);
  const [open, setOpen] = useState(false);
  const totals = (data ?? []).reduce((s, a) => ({ amount: s.amount + toCents(a.amount), remaining: s.remaining + toCents(a.remaining) }), { amount: 0, remaining: 0 });
  return (
    <>
      {!embedded && <PageHeader eyebrow="Property & Billing" title="Advance Payments" description="Prepaid condo dues. The amount is split evenly per month and applied automatically to future bills (condo dues only)."
        actions={can("advance_payments") && <Button icon="add-line" onClick={() => setOpen(true)}>Record advance payment</Button>} />}
      <div className="mb-6 grid gap-4 sm:grid-cols-3">
        <StatCard icon="calendar-check-line" label="Advances on record" value={data?.length ?? "—"} />
        <StatCard icon="hand-coin-line" label="Total received" value={peso(fromCents(totals.amount))} />
        <StatCard tone="copper" icon="hourglass-line" label="Not yet applied" value={peso(fromCents(totals.remaining))} hint="Will reduce future condo dues" />
      </div>
      <Card>
        {embedded && can("advance_payments") && <CardHeader title="Advance payments" actions={<Button size="sm" icon="add-line" onClick={() => setOpen(true)}>Record advance payment</Button>} />}
        <DataTable rows={data} loading={loading} error={error} onRetry={reload} rowKey={(a) => a.id} empty={{ icon: "calendar-check-line", title: "No advance payments yet" }}
          columns={[
            { key: "d", header: "Date", cell: (a) => dateLabel(a.date) },
            { key: "u", header: "Unit", cell: (a) => <b>{a.unitNo}</b> },
            { key: "c", header: "Covers", cell: (a) => `${a.months} month(s) from ${monthLabel(a.startMonth, "short")}` },
            { key: "o", header: "OR No.", cell: (a) => a.receiptNo || "—" },
            { key: "a", header: "Amount", align: "right", cell: (a) => peso(a.amount) },
            { key: "p", header: "Applied", align: "right", cell: (a) => peso(a.applied) },
            { key: "r", header: "Remaining", align: "right", cell: (a) => <b>{peso(a.remaining)}</b> },
          ]} />
      </Card>
      <Drawer open={open} onClose={() => setOpen(false)} title="Record advance payment" subtitle="Issues an official receipt and schedules the credit on future bills." width="max-w-lg">
        {open && <AdvanceForm onDone={() => { setOpen(false); reload(); }} />}
      </Drawer>
    </>
  );
}

function AdvanceForm({ onDone }: { onDone: () => void }) {
  const units = useAsync(() => api.units.list(), []);
  const [form, setForm] = useState(EMPTY_ADV);
  const [touched, setTouched] = useState(false);
  const { busy, act } = useAction();
  const toast = useToast();
  const residential = (units.data ?? []).filter((u) => !["PARKING", "STORAGE"].includes(u.type) && u.active);
  const unit = residential.find((u) => u.id === form.unitId);
  const errors = {
    unitId: !form.unitId && "Choose the unit.",
    amount: !isAmount(form.amount) && "Enter the amount received.",
    months: (form.months < 1 || form.months > 24) && "Between 1 and 24 months.",
    reference: form.method !== "CASH" && !form.reference.trim() && "Required for check and online payments.",
  };
  const monthly = isAmount(form.amount) && form.months > 0 ? fromCents(Math.floor(toCents(form.amount) / form.months)) : null;

  async function submit() {
    setTouched(true);
    if (Object.values(errors).some(Boolean)) return;
    try {
      const a = await act(() => api.advances.record(form));
      toast({ tone: "success", title: `Advance recorded · ${a.receiptNo}`, message: `${peso(a.monthlyAmount)} per month for ${a.months} month(s) from ${monthLabel(a.startMonth)}.` });
      onDone();
    } catch (err) {
      toast({ tone: "error", title: "Advance not recorded", message: (err as Error).message });
    }
  }

  return (
    <form className="grid gap-4 sm:grid-cols-2" onSubmit={(e) => { e.preventDefault(); void submit(); }} noValidate>
      <Field label="Unit" className="sm:col-span-2" error={touched && errors.unitId}>{(id) => (
        <select id={id} className="input" value={form.unitId} onChange={(e) => setForm({ ...form, unitId: Number(e.target.value) })}>
          <option value={0}>Choose a unit…</option>
          {residential.map((u) => <option key={u.id} value={u.id}>{u.unitNo} — {u.tenantName ?? u.ownerName ?? "vacant"}</option>)}
        </select>)}</Field>
      <Field label="Amount received (₱)" error={touched && errors.amount} hint={unit && `Monthly dues for ${unit.unitNo}: ${peso(unit.monthlyDues)}`}>{(id) => <input id={id} inputMode="decimal" className="input tabular" value={form.amount} onChange={(e) => setForm({ ...form, amount: e.target.value })} />}</Field>
      <Field label="Months covered" error={touched && errors.months}>{(id) => <input id={id} type="number" min={1} max={24} className="input" value={form.months} onChange={(e) => setForm({ ...form, months: Number(e.target.value) })} />}</Field>
      <Field label="Start billing month">{(id) => <input id={id} type="month" className="input" value={form.startMonth} onChange={(e) => setForm({ ...form, startMonth: e.target.value })} />}</Field>
      <Field label="Payment date">{(id) => <input id={id} type="date" className="input" max={todayIso()} value={form.date} onChange={(e) => setForm({ ...form, date: e.target.value })} />}</Field>
      <Field label="Method">{(id) => <select id={id} className="input" value={form.method} onChange={(e) => setForm({ ...form, method: e.target.value as PaymentMethod })}><option value="CASH">Cash</option><option value="CHECK">Check</option><option value="ONLINE">Online / bank transfer</option></select>}</Field>
      <Field label={form.method === "CASH" ? "Reference no. (optional)" : "Reference no. (required)"} error={touched && errors.reference}>{(id) => <input id={id} className="input" value={form.reference} onChange={(e) => setForm({ ...form, reference: e.target.value })} />}</Field>
      <Field label="Remarks" className="sm:col-span-2">{(id) => <input id={id} className="input" maxLength={300} value={form.remarks} onChange={(e) => setForm({ ...form, remarks: e.target.value })} />}</Field>
      {monthly && <div className="sm:col-span-2"><Notice tone="info" icon="calendar-2-line">{peso(monthly)} per month will be applied to condo dues from {monthLabel(form.startMonth)} for {form.months} month(s). The OR number is assigned by the server.</Notice></div>}
      <div className="flex justify-end gap-2 sm:col-span-2"><Button type="submit" icon="save-3-line" loading={busy}>Record & issue OR</Button></div>
    </form>
  );
}

export function ReceiptQuickList({ limit = 6 }: { limit?: number }) {
  const { data } = useAsync(() => api.receipts.list({ from: addDays(todayIso(), -45), to: todayIso(), q: "" }), []);
  return (
    <ul className="divide-y divide-ink-100">
      {(data ?? []).slice(0, limit).map((r) => (
        <li key={r.id} className="flex items-center justify-between gap-3 px-5 py-2.5">
          <div className="min-w-0"><p className="font-semibold text-ink-900 tabular">{r.receiptNo}</p><p className="truncate text-[12.5px] text-ink-500">Unit {r.unitNo} · {r.method} · {dateLabel(r.date)}</p></div>
          <b className="tabular">{peso(r.amount)}</b>
        </li>
      ))}
      {data && data.length === 0 && <li className="px-5 py-6 text-center text-ink-500"><Icon name="inbox-line" /> No receipts in the last 45 days.</li>}
    </ul>
  );
}
