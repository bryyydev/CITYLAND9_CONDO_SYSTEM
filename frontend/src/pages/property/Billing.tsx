// Billing & SOA: monthly bills, generate bills, bill detail, record payment, recalculate.
// Amounts and statuses come from the server; OR numbers are issued by the server.
import { useMemo, useState } from "react";
import { Can, useAuth } from "../../auth/AuthContext";
import { DataTable } from "../../components/base/DataTable";
import { ConfirmDialog, Drawer, useToast } from "../../components/base/overlays";
import { Badge, Button, Card, CardHeader, Field, Icon, Notice, PageHeader, SearchInput, Skeleton, StatusBadge, Tabs } from "../../components/base/ui";
import { SoaDocument } from "../../components/feature/Documents";
import { useAction, useAsync } from "../../hooks/useAsync";
import { addMonths, currentMonth, dateLabel, monthLabel, todayIso } from "../../lib/format";
import { fromCents, isAmount, peso, toCents } from "../../lib/money";
import { api } from "../../services/api";
import type { BillDetail, BillRow, Month, PaymentInput, PaymentMethod, RecordPaymentResult } from "../../services/types";

export const CORPORATION = "CITYLAND 9 CONDOMINIUM CORPORATION";
type StatusTab = "All" | "Pending" | "Paid" | "Partially Paid" | "Overdue";

export function MonthPicker({ value, onChange, max }: { value: Month; onChange: (m: Month) => void; max?: Month }) {
  return (
    <div className="flex items-center gap-1 rounded-lg border border-ink-200 bg-white p-1 shadow-sm">
      <button className="grid size-8 place-items-center rounded-md text-ink-500 hover:bg-ink-100" onClick={() => onChange(addMonths(value, -1))} aria-label="Previous month"><Icon name="arrow-left-s-line" /></button>
      <input type="month" aria-label="Month" className="h-8 rounded-md border-0 px-1 text-center font-semibold text-ink-800 outline-none" value={value} max={max} onChange={(e) => e.target.value && onChange(e.target.value)} />
      <button className="grid size-8 place-items-center rounded-md text-ink-500 hover:bg-ink-100 disabled:opacity-30" disabled={!!max && value >= max} onClick={() => onChange(addMonths(value, 1))} aria-label="Next month"><Icon name="arrow-right-s-line" /></button>
    </div>
  );
}

export function BillingWorkspace({ embedded = false }: { embedded?: boolean }) {
  const { can } = useAuth();
  const [month, setMonth] = useState(addMonths(currentMonth(), -1));
  const [tab, setTab] = useState<StatusTab>("All");
  const [q, setQ] = useState("");
  const [type, setType] = useState("");
  const [openBill, setOpenBill] = useState<number | null>(null);
  const [generating, setGenerating] = useState(false);
  const bills = useAsync(() => api.billing.list(month), [month]);
  const toast = useToast();
  const { busy, act } = useAction();

  const counts = useMemo(() => {
    const rows = bills.data ?? [];
    const c = (s: string) => rows.filter((b) => b.status === s).length;
    return { All: rows.length, Pending: c("Unpaid") + c("Partially Paid"), Paid: c("Paid"), "Partially Paid": c("Partially Paid"), Overdue: c("Overdue") };
  }, [bills.data]);

  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return (bills.data ?? []).filter((b) =>
      (tab === "All" || (tab === "Pending" ? b.status === "Unpaid" || b.status === "Partially Paid" : b.status === tab)) &&
      (!type || b.unitType === type) && (!needle || b.unitNo.toLowerCase().includes(needle) || b.payerName.toLowerCase().includes(needle)));
  }, [bills.data, tab, q, type]);

  const totals = rows.reduce((s, b) => ({ total: s.total + toCents(b.total), paid: s.paid + toCents(b.amountPaid), balance: s.balance + toCents(b.balance) }), { total: 0, paid: 0, balance: 0 });

  async function emailSoas() {
    try {
      const r = await act(() => api.billing.emailSoas(month));
      toast({ tone: "success", title: "SOAs sent", message: `${r.sent} statement(s) emailed for ${monthLabel(month)}.` });
    } catch (err) {
      toast({ tone: "error", title: "Couldn't send SOAs", message: (err as Error).message });
    }
  }

  return (
    <>
      {!embedded && (
        <PageHeader eyebrow="Property & Billing" title="Billing & SOA" description="Monthly bills per unit. Issued bills are frozen; open a bill to record a payment, recalculate it, or print its SOA."
          actions={<>
            <Can permission="send_billing_emails"><Button variant="secondary" icon="mail-send-line" loading={busy} onClick={emailSoas}>Email SOAs</Button></Can>
            {can("billing") && <Button icon="file-add-line" onClick={() => setGenerating(true)}>Generate bills</Button>}
          </>} />
      )}
      <Card>
        <div className="flex flex-wrap items-center gap-3 border-b border-ink-100 p-4">
          <MonthPicker value={month} onChange={setMonth} max={currentMonth()} />
          <Tabs value={tab} onChange={setTab} tabs={(["All", "Pending", "Paid", "Partially Paid", "Overdue"] as StatusTab[]).map((k) => ({ key: k, label: k, count: counts[k] }))} />
          <div className="flex-1" />
          <select className="input sm:w-40" aria-label="Unit type" value={type} onChange={(e) => setType(e.target.value)}>
            <option value="">All unit types</option>{["STUDIO TYPE", "1 BEDROOM", "2 BEDROOM", "3 BEDROOM"].map((t) => <option key={t}>{t}</option>)}
          </select>
          <SearchInput value={q} onChange={setQ} placeholder="Search unit or name" />
          {embedded && can("billing") && <Button icon="file-add-line" onClick={() => setGenerating(true)}>Generate bills</Button>}
        </div>
        <DataTable rows={bills.data ? rows : null} loading={bills.loading} error={bills.error} onRetry={bills.reload} rowKey={(b) => b.id} onRowClick={(b) => setOpenBill(b.id)}
          empty={{ icon: "file-list-3-line", title: bills.data?.length ? "No bills match these filters" : `No bills for ${monthLabel(month)}`, text: bills.data?.length ? "Try another status tab or search." : "Generate the month's bills to create one SOA per residential unit." }}
          columns={[
            { key: "u", header: "Unit", cell: (b) => <><b className="text-ink-900">{b.unitNo}</b><span className="block text-[12px] text-ink-500">{b.unitType}</span></> },
            { key: "n", header: "Billed to", cell: (b) => b.payerName },
            { key: "d", header: "Due", cell: (b) => dateLabel(b.dueDate) },
            { key: "t", header: "Total", align: "right", cell: (b) => peso(b.total) },
            { key: "p", header: "Paid", align: "right", cell: (b) => peso(b.amountPaid) },
            { key: "b", header: "Balance", align: "right", cell: (b) => <b>{peso(b.balance)}</b> },
            { key: "s", header: "Status", cell: (b) => <StatusBadge status={b.status} /> },
            { key: "a", header: "", cell: () => <Icon name="arrow-right-s-line" className="text-ink-400" /> },
          ]}
          footer={rows.length > 0 && (
            <tr className="bg-ink-50 font-semibold"><td className="td" colSpan={3}>{rows.length} bill(s)</td><td className="td num">{peso(fromCents(totals.total))}</td><td className="td num">{peso(fromCents(totals.paid))}</td><td className="td num">{peso(fromCents(totals.balance))}</td><td className="td" colSpan={2} /></tr>
          )} />
      </Card>
      <BillDrawer billId={openBill} onClose={() => setOpenBill(null)} onChanged={bills.reload} />
      <GenerateBillsDrawer open={generating} onClose={() => setGenerating(false)} onDone={(m) => { setMonth(m); bills.reload(); }} />
    </>
  );
}

export default function BillingPage() {
  return <BillingWorkspace />;
}

// ------------------------------------------------------------------ bill detail
export function BillDrawer({ billId, onClose, onChanged }: { billId: number | null; onClose: () => void; onChanged: () => void }) {
  const { can } = useAuth();
  const detail = useAsync(() => (billId ? api.billing.detail(billId) : Promise.resolve(null)), [billId]);
  const [paying, setPaying] = useState(false);
  const [confirmRecalc, setConfirmRecalc] = useState(false);
  const { busy, act } = useAction();
  const toast = useToast();
  const d = detail.data;

  async function recalc() {
    if (!d) return;
    try {
      const r = await act(() => api.billing.recalculate(d.id));
      detail.setData(r.detail);
      onChanged();
      toast(r.changed ? { tone: "success", title: "SOA recalculated", message: "Amounts updated from the current rates. Recorded in the Audit Logs." }
        : { tone: "info", title: "Nothing changed", message: "The current rates give the same amounts." });
    } catch (err) {
      toast({ tone: "error", title: "Couldn't recalculate", message: (err as Error).message });
    } finally {
      setConfirmRecalc(false);
    }
  }

  return (
    <Drawer open={billId !== null} onClose={() => { setPaying(false); onClose(); }} width="max-w-3xl"
      title={d ? `Unit ${d.unitNo} · ${monthLabel(d.billingMonth)}` : "Bill"} subtitle={d ? `Billed to ${d.payerName}` : undefined}
      footer={d && !paying && <>
        <Button variant="secondary" icon="printer-line" onClick={() => window.print()}>Print SOA</Button>
        {can("recalculate_soa") && <Button variant="secondary" icon="refresh-line" onClick={() => setConfirmRecalc(true)} disabled={d.manualOverride}>Recalculate</Button>}
        {can("mark_bill_paid") && <Button icon="hand-coin-line" onClick={() => setPaying(true)}>Record payment</Button>}
      </>}>
      {detail.error ? <Notice tone="warn">{detail.error.message}</Notice> : !d ? <Skeleton rows={8} /> : paying ? (
        <RecordPaymentForm bill={d} onCancel={() => setPaying(false)} onDone={() => { setPaying(false); detail.reload(); onChanged(); }} />
      ) : (
        <div className="space-y-4">
          {d.manualOverride && <Notice tone="warn">This SOA was corrected by hand, so it can't be recalculated from rates.</Notice>}
          <SoaDocument corporation={CORPORATION} unitNo={d.unitNo} statement={d} />
        </div>
      )}
      <ConfirmDialog open={confirmRecalc} title="Recalculate this SOA?" confirmLabel="Recalculate" busy={busy} onClose={() => setConfirmRecalc(false)} onConfirm={recalc}
        message="Issued bills are frozen. Recalculating re-prices condo dues, parking and storage from the CURRENT rates. The change is recorded in the Audit Logs." />
    </Drawer>
  );
}

// ------------------------------------------------------------------ record payment
export function RecordPaymentForm({ bill, onCancel, onDone }: { bill: BillDetail | BillRow; onCancel: () => void; onDone: (r: RecordPaymentResult) => void }) {
  const [form, setForm] = useState<PaymentInput>({ amount: bill.balance, method: "CASH", type: "FULL", reference: "", date: todayIso(), remarks: "" });
  const [touched, setTouched] = useState(false);
  const [result, setResult] = useState<RecordPaymentResult | null>(null);
  const { busy, act } = useAction();
  const toast = useToast();
  const balance = toCents(bill.balance);
  const amount = toCents(form.amount);
  const refNeeded = form.method !== "CASH";
  const errors = {
    amount: !isAmount(form.amount) && "Enter an amount greater than zero, e.g. 5000 or 5000.50.",
    reference: refNeeded && !form.reference.trim() && "A reference number is required for check and online payments.",
    date: !form.date && "Choose the payment date.",
  };

  async function submit() {
    setTouched(true);
    if (errors.amount || errors.reference || errors.date) return;
    try {
      const r = await act(() => api.billing.recordPayment(bill.id, { ...form, type: amount >= balance ? "FULL" : "PARTIAL" }));
      setResult(r);
      toast({ tone: "success", title: `Payment recorded · ${r.receiptNo}`, message: toCents(r.excessToAdvance) > 0 ? `${peso(r.excessToAdvance)} moved to advance payment.` : undefined });
    } catch (err) {
      toast({ tone: "error", title: "Payment not recorded", message: (err as Error).message });
    }
  }

  if (result) {
    return (
      <div className="animate-pop-in space-y-4 text-center">
        <div className="mx-auto grid size-14 place-items-center rounded-full bg-emerald-50 text-3xl text-emerald-600"><Icon name="checkbox-circle-fill" /></div>
        <div><p className="text-ink-500">Official receipt issued</p><p className="font-display text-[26px] font-semibold text-ink-900">{result.receiptNo}</p></div>
        <dl className="mx-auto max-w-sm divide-y divide-ink-100 rounded-xl border border-ink-200 text-left">
          <div className="flex justify-between px-4 py-2.5"><dt>Applied to this bill</dt><dd className="font-semibold tabular">{peso(result.appliedToBill)}</dd></div>
          {toCents(result.excessToAdvance) > 0 && <div className="flex justify-between px-4 py-2.5"><dt>Moved to advance payment</dt><dd className="font-semibold tabular">{peso(result.excessToAdvance)}</dd></div>}
          <div className="flex justify-between px-4 py-2.5"><dt>Bill status now</dt><dd><StatusBadge status={result.status} /></dd></div>
        </dl>
        <Button onClick={() => onDone(result)}>Done</Button>
      </div>
    );
  }

  const applied = Math.min(amount, Math.max(balance, 0));
  const excess = Math.max(amount - Math.max(balance, 0), 0);
  return (
    <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); void submit(); }} noValidate>
      <div className="flex items-center justify-between rounded-xl bg-ink-50 px-4 py-3">
        <span className="text-ink-600">Balance due · Unit {"unitNo" in bill ? bill.unitNo : ""} · {monthLabel(bill.billingMonth)}</span>
        <b className="font-display text-[18px] tabular">{peso(bill.balance)}</b>
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Amount received (₱)" error={touched && errors.amount}>{(id) => <input id={id} inputMode="decimal" className="input tabular" data-autofocus aria-invalid={Boolean(touched && errors.amount)} value={form.amount} onChange={(e) => setForm({ ...form, amount: e.target.value })} />}</Field>
        <Field label="Payment date" error={touched && errors.date}>{(id) => <input id={id} type="date" className="input" max={todayIso()} value={form.date} onChange={(e) => setForm({ ...form, date: e.target.value })} />}</Field>
        <Field label="Method">{(id) => (
          <select id={id} className="input" value={form.method} onChange={(e) => setForm({ ...form, method: e.target.value as PaymentMethod })}>
            <option value="CASH">Cash</option><option value="CHECK">Check</option><option value="ONLINE">Online / bank transfer</option>
          </select>)}</Field>
        <Field label={refNeeded ? "Reference no. (required)" : "Reference no. (optional)"} error={touched && errors.reference}>{(id) => <input id={id} className="input" placeholder={form.method === "CHECK" ? "Check number" : form.method === "ONLINE" ? "Transaction reference" : ""} aria-invalid={Boolean(touched && errors.reference)} value={form.reference} onChange={(e) => setForm({ ...form, reference: e.target.value })} />}</Field>
        <Field label="Remarks" className="sm:col-span-2">{(id) => <input id={id} className="input" maxLength={300} value={form.remarks} onChange={(e) => setForm({ ...form, remarks: e.target.value })} />}</Field>
      </div>
      {isAmount(form.amount) && (
        <div className="grid gap-2 rounded-xl border border-brand-200 bg-brand-50 p-4 text-[13.5px] text-brand-800">
          <div className="flex justify-between"><span>Applied to this bill</span><b className="tabular">{peso(fromCents(applied))}</b></div>
          {excess > 0 && <div className="flex justify-between"><span>Excess → advance payment (condo dues, from {monthLabel(bill.billingMonth)})</span><b className="tabular">{peso(fromCents(excess))}</b></div>}
          <div className="flex justify-between"><span>Payment type</span><Badge tone="info" dot={false}>{amount >= balance ? "FULL" : "PARTIAL"}</Badge></div>
          <p className="text-[12px] text-brand-700">The official receipt number is assigned by the server when you save.</p>
        </div>
      )}
      <div className="flex justify-end gap-2 pt-2">
        <Button type="button" variant="secondary" onClick={onCancel}>Back</Button>
        <Button type="submit" icon="hand-coin-line" loading={busy}>Record payment & issue OR</Button>
      </div>
    </form>
  );
}

// ------------------------------------------------------------------ generate bills
export function GenerateBillsPanel({ onDone, compact = false }: { onDone?: (month: Month) => void; compact?: boolean }) {
  const [month, setMonth] = useState(currentMonth());
  const water = useAsync(() => api.water.list(month), [month]);
  const units = useAsync(() => api.units.list(), []);
  const existing = useAsync(() => api.billing.list(month), [month]);
  const [confirm, setConfirm] = useState(false);
  const { busy, act } = useAction();
  const toast = useToast();
  const residential = (units.data ?? []).filter((u) => !["PARKING", "STORAGE"].includes(u.type) && u.active).length;
  const readings = water.data?.length ?? 0;
  const billed = existing.data?.length ?? 0;

  async function generate() {
    try {
      const r = await act(() => api.billing.generate(month));
      toast({ tone: "success", title: `Generated ${r.created} bill(s) for ${monthLabel(r.month)}`, message: r.skipped ? `${r.skipped} unit(s) were already billed and were skipped.` : undefined });
      existing.reload();
      onDone?.(month);
    } catch (err) {
      toast({ tone: "error", title: "Bills not generated", message: (err as Error).message });
    } finally {
      setConfirm(false);
    }
  }

  const check = (ok: boolean, label: string, hint: string) => (
    <li className="flex gap-3">
      <Icon name={ok ? "checkbox-circle-fill" : "error-warning-fill"} className={ok ? "mt-0.5 text-lg text-emerald-600" : "mt-0.5 text-lg text-amber-500"} />
      <div><p className="font-semibold text-ink-800">{label}</p><p className="text-[12.5px] text-ink-500">{hint}</p></div>
    </li>
  );

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-3">
        <span className="font-semibold text-ink-700">Billing month</span>
        <MonthPicker value={month} onChange={setMonth} />
      </div>
      <ul className="space-y-3">
        {check(readings >= residential && residential > 0, `Water readings: ${readings} of ${residential} units`, readings >= residential ? "All meters read for this month." : "Units without a reading are billed ₱0.00 water. Enter readings first if possible.")}
        {check(billed === 0, billed ? `${billed} unit(s) already billed` : "No bills yet for this month", billed ? "Already-billed units are skipped; only missing bills are created." : "One bill will be created per active residential unit.")}
        {check(true, "Rules applied by the server", "Condo dues = area × rate; parking/storage from assigned units; previous balance, penalty and advance payments; due on the 8th.")}
      </ul>
      {!compact && <Notice tone="copper" icon="lock-line">Bills are frozen once issued. Later rate changes don't alter them unless someone recalculates a bill on purpose (audited).</Notice>}
      <Button icon="file-add-line" onClick={() => setConfirm(true)} disabled={!residential || billed >= residential}>Generate bills for {monthLabel(month)}</Button>
      <ConfirmDialog open={confirm} title={`Generate bills for ${monthLabel(month)}?`} confirmLabel="Generate bills" busy={busy} onClose={() => setConfirm(false)} onConfirm={generate}
        message={<>This creates {Math.max(residential - billed, 0)} bill(s). Issued bills are frozen and residents will see them in their portal.</>} />
    </div>
  );
}

function GenerateBillsDrawer({ open, onClose, onDone }: { open: boolean; onClose: () => void; onDone: (m: Month) => void }) {
  return (
    <Drawer open={open} onClose={onClose} title="Generate monthly bills" subtitle="Creates one SOA per active residential unit." width="max-w-lg">
      {open && <GenerateBillsPanel onDone={(m) => { onDone(m); onClose(); }} />}
    </Drawer>
  );
}

export function BillsSummaryCard({ month }: { month: Month }) {
  const { data } = useAsync(() => api.billing.list(month), [month]);
  if (!data) return <Card><Skeleton rows={3} /></Card>;
  return (
    <Card>
      <CardHeader title={`Bills · ${monthLabel(month)}`} />
      <div className="grid grid-cols-2 gap-3 p-5 text-center sm:grid-cols-4">
        {(["Paid", "Partially Paid", "Unpaid", "Overdue"] as const).map((s) => (
          <div key={s} className="rounded-lg bg-ink-50 p-3"><p className="font-display text-[22px] font-semibold">{data.filter((b) => b.status === s).length}</p><StatusBadge status={s} /></div>
        ))}
      </div>
    </Card>
  );
}
