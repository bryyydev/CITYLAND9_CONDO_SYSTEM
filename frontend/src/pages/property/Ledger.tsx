// Official receipts ledger (live), advance payments, and a "record a payment" finder.
import { useRef, useState } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { Can, useAuth } from "../../auth/AuthContext";
import { DataTable } from "../../components/base/DataTable";
import { Drawer, useToast } from "../../components/base/overlays";
import { Badge, Button, Card, ErrorState, Field, Icon, Notice, PageHeader, SearchInput, Skeleton, StatCard, Tabs, cx } from "../../components/base/ui";
import { useDebounced } from "../../components/feature/AccountFields";
import { ReceiptDocument, itemLabel } from "../../components/feature/Documents";
import { useAction, useAsync } from "../../hooks/useAsync";
import { addDays, addMonths, currentMonth, dateLabel, dateTimeLabel, monthLabel, todayIso } from "../../lib/format";
import { fromCents, isAmount, peso, toCents } from "../../lib/money";
import { api } from "../../services/api";
import { ApiError } from "../../services/http";
import type { AdvanceInput, AdvancePayment, BillRow, PaymentMethod, ReceiptQuery } from "../../services/types";
import { CORPORATION, MonthPicker, RecordPaymentForm, newFormToken } from "./Billing";

// ------------------------------------------------------------------ official receipts
// Live: /api/receipts. Receipts are issued by the server when a payment is recorded and are never
// edited; a void keeps the number, marks it VOID with the reason and reverses its payments.
export function ReceiptsLedger({ title = "Payments & Official Receipts", eyebrow = "Property & Billing", recordAction = false }: { title?: string; eyebrow?: string; recordAction?: boolean }) {
  const [params, setParams] = useSearchParams();
  const [filters, setFilters] = useState({ from: addDays(todayIso(), -60), to: todayIso(), q: "", method: "" as ReceiptQuery["method"], status: "" as ReceiptQuery["status"] });
  const q = useDebounced(filters.q);
  const query: ReceiptQuery = { from: filters.from, to: filters.to, q, method: filters.method, status: filters.status };
  const list = useAsync(() => api.receipts.ledger(query), [filters.from, filters.to, q, filters.method, filters.status]);
  const [recording, setRecording] = useState(false);
  const openId = Number(params.get("receipt")) || null;
  const movedForm = params.get("moved") === "form";
  const data = list.data;
  const badRange = filters.from > filters.to;
  const setParam = (key: string, value: string | null) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value); else next.delete(key);
    setParams(next, { replace: true });
  };

  return (
    <>
      <PageHeader eyebrow={eyebrow} title={title} description="Every payment gets an official receipt, numbered per year without gaps (OR-YYYY-NNNNNN). Receipts are issued by the server and can't be edited; a voided receipt keeps its number."
        actions={recordAction && <Can permission="mark_bill_paid"><Button icon="hand-coin-line" onClick={() => setRecording(true)}>Record a payment</Button></Can>} />
      {movedForm && (
        <div className="mb-4" role="status">
          <Notice tone="warn">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span>Official receipts moved to this screen. The form you sent from the old page was <b>not saved</b> (nothing was voided); please do it here.</span>
              <Button size="sm" variant="ghost" onClick={() => setParam("moved", null)}>Dismiss</Button>
            </div>
          </Notice>
        </div>
      )}
      <div className="mb-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard tone="hero" icon="hand-coin-line" label="Collected in range" value={data ? peso(data.collected) : "—"}
          hint={data ? `${data.total - data.voidCount} receipt(s)${data.voidCount ? ` · ${data.voidCount} void, not counted` : ""}` : undefined} />
        {(["CASH", "CHECK", "ONLINE"] as const).map((m) => <StatCard key={m} icon={{ CASH: "money-dollar-circle-line", CHECK: "bank-card-2-line", ONLINE: "global-line" }[m]} label={m === "ONLINE" ? "Online / transfer" : m[0] + m.slice(1).toLowerCase()} value={data ? peso(data.byMethod[m]) : "—"} />)}
      </div>
      <Card>
        <div className="flex flex-wrap items-end gap-3 border-b border-ink-100 p-4">
          <Field label="From">{(id) => <input id={id} type="date" className="input" value={filters.from} max={filters.to} onChange={(e) => setFilters({ ...filters, from: e.target.value })} />}</Field>
          <Field label="To" error={badRange && "Must be on or after From."}>{(id) => <input id={id} type="date" className="input" value={filters.to} min={filters.from} onChange={(e) => setFilters({ ...filters, to: e.target.value })} />}</Field>
          <Field label="Method" className="w-36">{(id) => (
            <select id={id} className="input" value={filters.method} onChange={(e) => setFilters({ ...filters, method: e.target.value as ReceiptQuery["method"] })}>
              <option value="">All</option><option value="CASH">Cash</option><option value="CHECK">Check</option><option value="ONLINE">Online</option>
            </select>)}</Field>
          <Field label="Status" className="w-36">{(id) => (
            <select id={id} className="input" value={filters.status} onChange={(e) => setFilters({ ...filters, status: e.target.value as ReceiptQuery["status"] })}>
              <option value="">All</option><option value="valid">Valid</option><option value="void">Void</option>
            </select>)}</Field>
          <div className="flex-1" />
          <SearchInput value={filters.q} onChange={(v) => setFilters({ ...filters, q: v })} placeholder="OR no., unit or reference" />
        </div>
        {data?.truncated && <div className="border-b border-ink-100 px-4 py-2"><Notice tone="warn">Showing the newest 1,000 of {data.total.toLocaleString()} receipts. Narrow the dates to see the rest; the totals above cover all of them.</Notice></div>}
        <DataTable rows={data ? data.receipts : null} loading={list.loading} error={list.error} onRetry={list.reload} rowKey={(r) => r.id} onRowClick={(r) => setParam("receipt", String(r.id))}
          empty={{ icon: "receipt-line", title: "No receipts match", text: "Try another date range, method or search." }}
          columns={[
            { key: "o", header: "OR No.", cell: (r) => <><b className={cx("tabular", r.voided ? "text-ink-400 line-through" : "text-ink-900")}>{r.receiptNo}</b>{r.voided && <Badge tone="bad" dot={false}>VOID</Badge>}</> },
            { key: "d", header: "Date", cell: (r) => dateLabel(r.date) },
            { key: "u", header: "Unit", cell: (r) => r.unitNo },
            { key: "f", header: "Paid for", cell: (r) => <span className="line-clamp-1">{r.items.map(itemLabel).join(", ")}</span> },
            { key: "m", header: "Method", cell: (r) => <>{r.method}{r.reference && <span className="block text-[12px] text-ink-500">Ref. {r.reference}</span>}</> },
            { key: "b", header: "Received by", cell: (r) => <>{r.receivedBy}{r.backfilled && <Badge tone="neutral" dot={false}>backfilled</Badge>}</> },
            { key: "a", header: "Amount", align: "right", cell: (r) => <b className={cx(r.voided && "text-ink-400 line-through")}>{peso(r.amount)}</b> },
          ]} />
      </Card>
      <ReceiptDrawer receiptId={openId} onClose={() => setParam("receipt", null)} onChanged={list.reload} />
      <Drawer open={recording} onClose={() => setRecording(false)} title="Record a payment" subtitle="Find the unit's bill, then record what was received." width="max-w-2xl">
        {recording && <PaymentFinder onDone={() => { setRecording(false); list.reload(); }} />}
      </Drawer>
    </>
  );
}

function ReceiptDrawer({ receiptId, onClose, onChanged }: { receiptId: number | null; onClose: () => void; onChanged: () => void }) {
  const detail = useAsync(() => (receiptId ? api.receipts.detail(receiptId) : Promise.resolve(null)), [receiptId]);
  const [voiding, setVoiding] = useState(false);
  const [reason, setReason] = useState("");
  const [token, setToken] = useState(newFormToken);
  const [error, setError] = useState("");
  const { busy, act } = useAction();
  const toast = useToast();
  const docRef = useRef<HTMLDivElement>(null);
  const d = detail.data;
  if (!receiptId) return null;

  async function downloadPdf() {
    if (!d || !docRef.current) return;
    try {
      await act(async () => {
        const html2pdf = (await import("html2pdf.js")).default;   // bundled locally, loaded only when needed
        await html2pdf().set({ margin: 10, filename: `${d.receipt.receiptNo}.pdf`, html2canvas: { scale: 2, backgroundColor: "#ffffff" }, jsPDF: { unit: "mm", format: "a5", orientation: "landscape" } })
          .from(docRef.current!).save();
      });
    } catch {
      toast({ tone: "error", title: "Couldn't create the PDF" });
    }
  }

  async function confirmVoid() {
    if (!d || busy) return;
    if (reason.trim().length < 10) { setError("Give the reason (at least 10 characters). It is printed on the receipt and kept in the audit log."); return; }
    try {
      const next = await act(() => api.receipts.void(d.receipt.id, reason.trim(), token));
      detail.setData(next); onChanged(); setVoiding(false); setReason(""); setToken(newFormToken());
      toast({ tone: "success", title: `${next.receipt.receiptNo} voided`, message: "Its payments were reversed and the bill balances went back up. The receipt keeps its number." });
    } catch (err) {
      setError((err as Error).message);
    }
  }

  return (
    <Drawer open onClose={() => { if (!busy) onClose(); }} width="max-w-3xl" title={d?.receipt.receiptNo ?? "Official receipt"}
      subtitle={d ? `Unit ${d.unit.unitNo} · ${dateLabel(d.receipt.date)}${d.receipt.voided ? " · VOID" : ""}` : undefined}
      footer={d && !voiding && <div className="flex w-full flex-wrap justify-end gap-2">
        <Button variant="secondary" icon="file-download-line" loading={busy} onClick={downloadPdf}>Download PDF</Button>
        {d.canVoid && <Button variant="danger" icon="close-circle-line" disabled={d.closed} title={d.closed ? "The books are closed for this month" : undefined} onClick={() => { setVoiding(true); setError(""); }}>Void receipt</Button>}
      </div>}>
      {detail.error ? <ErrorState title={detail.error.status === 404 ? "Receipt not found" : "Couldn't load the receipt"} message={detail.error.message} onRetry={detail.error.status === 404 ? undefined : detail.reload} />
        : !d ? <Skeleton rows={6} />
        : (
          <div className="space-y-4">
            {voiding && (
              <div className="space-y-3 rounded-xl border border-red-200 bg-red-50 p-4">
                <p className="font-semibold text-red-800">Void {d.receipt.receiptNo} ({peso(d.receipt.amount)})?</p>
                <p className="text-[13px] text-red-800">The receipt keeps its number and is marked VOID. What it paid is reversed: the bill and water balances go back up, and an advance payment it created is cancelled. This can't be undone; record the payment again if it was voided by mistake.</p>
                <Field label="Reason (printed on the receipt, kept in the audit log)" error={error}>{(id) => (
                  <textarea id={id} rows={2} maxLength={500} className="input h-auto bg-white py-2" data-autofocus value={reason} aria-invalid={Boolean(error) || undefined} onChange={(e) => { setReason(e.target.value); setError(""); }} />)}</Field>
                <div className="flex justify-end gap-2">
                  <Button variant="secondary" disabled={busy} onClick={() => { setVoiding(false); setError(""); }}>Cancel</Button>
                  <Button variant="danger" icon="close-circle-line" loading={busy} onClick={confirmVoid}>Void and reverse payments</Button>
                </div>
              </div>
            )}
            {d.closed && !d.receipt.voided && d.canVoid && <Notice tone="warn" icon="lock-line">The books are closed for this receipt's month, so it can't be voided.</Notice>}
            {d.receipt.voided && d.voidedBy && <Notice tone="warn">Voided by {d.voidedBy}{d.voidedAt ? ` on ${dateTimeLabel(d.voidedAt)}` : ""}.</Notice>}
            <div ref={docRef}><ReceiptDocument corporation={d.corporation || CORPORATION} address={d.address} unitNo={d.unit.unitNo} receivedFrom={d.receivedFrom} receipt={d.receipt} /></div>
          </div>
        )}
    </Drawer>
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
// Live: /api/advances. Prepaid condo dues: split evenly per month and applied automatically to the
// unit's bills from the start month. Recording one issues an official receipt (server-numbered).
const EMPTY_ADV = (): AdvanceInput => ({ unitId: 0, amount: "", startMonth: currentMonth(), months: 3, method: "CASH", reference: "", date: todayIso(), remarks: "", formToken: newFormToken() });
type AdvStatus = "" | "open" | "used" | "reversed";

export function AdvancesWorkspace({ embedded = false }: { embedded?: boolean }) {
  const { can } = useAuth();
  const [params, setParams] = useSearchParams();
  const [status, setStatus] = useState<AdvStatus>("");
  const [q, setQ] = useState("");
  const unit = Number(params.get("unit")) || undefined;
  const list = useAsync(() => api.advances.list({ unit, status }), [unit, status]);
  const [open, setOpen] = useState(false);
  const movedForm = params.get("moved") === "form";
  const portal = useLocation().pathname.split("/")[1];
  const navigate = useNavigate();
  const data = list.data;
  const needle = q.trim().toLowerCase();
  const rows = (data?.advances ?? []).filter((a) => !needle || a.unitNo.toLowerCase().includes(needle) || (a.payerName ?? "").toLowerCase().includes(needle) || a.receiptNo.toLowerCase().includes(needle));
  const clear = (k: string) => { const n = new URLSearchParams(params); n.delete(k); setParams(n, { replace: true }); };
  const receiptsPath = portal === "accounting" ? "collections" : "payments";

  return (
    <>
      {!embedded && <PageHeader eyebrow="Property & Billing" title="Advance Payments" description="Prepaid condo dues. The amount is split evenly per month and applied automatically to future bills (condo dues only). Each advance gets an official receipt."
        actions={can("advance_payments") && <Button icon="add-line" onClick={() => setOpen(true)}>Record advance payment</Button>} />}
      {movedForm && (
        <div className="mb-4" role="status"><Notice tone="warn">
          <div className="flex flex-wrap items-center justify-between gap-2"><span>Advance Payments moved to this screen. The form you sent from the old page was <b>not saved</b> (no payment was recorded); please record it here.</span>
            <Button size="sm" variant="ghost" onClick={() => clear("moved")}>Dismiss</Button></div>
        </Notice></div>
      )}
      <div className="mb-6 grid gap-4 sm:grid-cols-3">
        <StatCard icon="calendar-check-line" label="Advances on record" value={data ? data.totals.count : "—"} hint={unit ? "For the selected unit" : undefined} />
        <StatCard icon="hand-coin-line" label="Total received" value={data ? peso(data.totals.received) : "—"} />
        <StatCard tone="copper" icon="hourglass-line" label="Not yet applied" value={data ? peso(data.totals.remaining) : "—"} hint="Will reduce future condo dues" />
      </div>
      <Card>
        <div className="flex flex-wrap items-center gap-3 border-b border-ink-100 p-4">
          <div className="max-w-full"><Tabs value={status} onChange={setStatus} tabs={[{ key: "", label: "All" }, { key: "open", label: "Still to apply" }, { key: "used", label: "Used up" }, { key: "reversed", label: "Reversed" }]} /></div>
          {unit && <Button size="sm" variant="ghost" icon="close-circle-line" onClick={() => clear("unit")}>All units</Button>}
          <div className="flex-1" />
          <SearchInput value={q} onChange={setQ} placeholder="Unit, name or OR no." />
          {embedded && can("advance_payments") && <Button size="sm" icon="add-line" onClick={() => setOpen(true)}>Record advance payment</Button>}
        </div>
        <DataTable rows={data ? rows : null} loading={list.loading} error={list.error} onRetry={list.reload} rowKey={(a) => a.id}
          empty={{ icon: "calendar-check-line", title: data?.advances.length ? "No advances match" : "No advance payments yet" }}
          columns={[
            { key: "d", header: "Date", cell: (a) => dateLabel(a.date) },
            { key: "u", header: "Unit", cell: (a) => <><b>{a.unitNo}</b>{a.payerName && <span className="block text-[12px] text-ink-500">{a.payerName}</span>}</> },
            { key: "c", header: "Covers", cell: (a) => <>{a.months} month(s)<span className="block text-[12px] text-ink-500">{monthLabel(a.startMonth, "short")}{a.endMonth && a.endMonth !== a.startMonth ? ` – ${monthLabel(a.endMonth, "short")}` : ""} · {peso(a.monthlyAmount)}/mo</span></> },
            { key: "o", header: "OR No.", cell: (a) => a.receiptNo ? <button type="button" className="font-semibold text-brand-600 hover:underline" onClick={() => a.receiptId && navigate(`/${portal}/${receiptsPath}?receipt=${a.receiptId}`)}>{a.receiptNo}</button> : "—" },
            { key: "a", header: "Amount", align: "right", cell: (a) => <span className={cx(a.reversed && "text-ink-400 line-through")}>{peso(a.amount)}</span> },
            { key: "p", header: "Applied", align: "right", cell: (a) => peso(a.applied) },
            { key: "r", header: "Remaining", align: "right", cell: (a) => a.reversed ? <Badge tone="bad" dot={false}>Reversed</Badge> : <b>{peso(a.remaining)}</b> },
          ]} />
      </Card>
      <Drawer open={open} onClose={() => setOpen(false)} title="Record advance payment" subtitle="Issues an official receipt and applies the credit to the unit's condo dues month by month." width="max-w-lg">
        {open && <AdvanceForm presetUnit={unit} onDone={() => { setOpen(false); list.reload(); }} />}
      </Drawer>
    </>
  );
}

function AdvanceForm({ presetUnit, onDone }: { presetUnit?: number; onDone: () => void }) {
  const options = useAsync(() => api.advances.options(), []);
  const [form, setForm] = useState(() => ({ ...EMPTY_ADV(), unitId: presetUnit ?? 0 }));
  const [touched, setTouched] = useState(false);
  const [serverFields, setServerFields] = useState<Record<string, string>>({});
  const [serverError, setServerError] = useState("");
  const [result, setResult] = useState<{ receiptNo: string; advance: AdvancePayment } | null>(null);
  const { busy, act } = useAction();
  const unit = (options.data?.units ?? []).find((u) => u.id === form.unitId);
  const errors: Record<string, string> = {
    unitId: !form.unitId ? "Choose the unit." : serverFields.unitId ?? "",
    amount: !isAmount(form.amount) ? "Enter the amount received." : serverFields.amount ?? "",
    months: !Number.isInteger(form.months) || form.months < 1 || form.months > 120 ? "Between 1 and 120 months." : serverFields.months ?? "",
    startMonth: !/^\d{4}-(0[1-9]|1[0-2])$/.test(form.startMonth) ? "Choose the first month." : serverFields.startMonth ?? "",
    reference: form.method !== "CASH" && !form.reference.trim() ? "Required for check and online payments." : serverFields.reference ?? "",
    date: serverFields.date ?? serverFields.period ?? "",
  };
  const show = (k: string) => (touched || serverFields[k] ? errors[k] : "");
  const monthly = isAmount(form.amount) && form.months > 0 ? fromCents(Math.round(toCents(form.amount) / form.months)) : null;
  const set = (p: Partial<AdvanceInput>) => { setForm({ ...form, ...p }); setServerFields({}); setServerError(""); };

  async function submit() {
    setTouched(true);
    if (busy || Object.values(errors).some(Boolean)) return;
    try {
      const r = await act(() => api.advances.record(form));
      setResult({ receiptNo: r.receiptNo, advance: r.advance });
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) { setServerError("This advance was already recorded, so it was not recorded twice."); return; }
      if (err instanceof ApiError && Object.keys(err.fields).length) setServerFields(err.fields);
      else setServerError((err as Error).message);
    }
  }

  if (result) {
    const a = result.advance;
    return (
      <div className="space-y-4 text-center">
        <div className="mx-auto grid size-14 place-items-center rounded-full bg-emerald-50 text-3xl text-emerald-600"><Icon name="checkbox-circle-fill" /></div>
        <div><p className="text-ink-500">Official receipt issued</p><p className="font-display text-[26px] font-semibold text-ink-900">{result.receiptNo}</p></div>
        <p>{peso(a.monthlyAmount)} per month for {a.months} month(s) from {monthLabel(a.startMonth)}{toCents(a.applied) > 0 ? `; ${peso(a.applied)} already applied to an issued bill` : ""}.</p>
        <Button onClick={onDone}>Done</Button>
      </div>
    );
  }
  return (
    <form className="grid gap-4 sm:grid-cols-2" onSubmit={(e) => { e.preventDefault(); void submit(); }} noValidate>
      {serverError && <div className="sm:col-span-2"><Notice tone="warn">{serverError}</Notice></div>}
      <Field label="Unit" className="sm:col-span-2" error={show("unitId")}>{(id) => (
        <select id={id} className="input" value={form.unitId} onChange={(e) => set({ unitId: Number(e.target.value) })}>
          <option value={0}>{options.data ? "Choose a unit…" : "Loading units…"}</option>
          {(options.data?.units ?? []).map((u) => <option key={u.id} value={u.id}>{u.unitNo}{u.payerName ? ` — ${u.payerName}` : ""}</option>)}
        </select>)}</Field>
      <Field label="Amount received (₱)" error={show("amount")} hint={unit && `Monthly condo dues for ${unit.unitNo}: ${peso(unit.monthlyDues)}`}>{(id) => <input id={id} inputMode="decimal" className="input tabular" value={form.amount} onChange={(e) => set({ amount: e.target.value })} />}</Field>
      <Field label="Months covered" error={show("months")}>{(id) => <input id={id} type="number" min={1} max={120} className="input" value={form.months} onChange={(e) => set({ months: Number(e.target.value) })} />}</Field>
      <Field label="First billing month" error={show("startMonth")}>{(id) => <input id={id} type="month" className="input" value={form.startMonth} onChange={(e) => set({ startMonth: e.target.value })} />}</Field>
      <Field label="Payment date" error={show("date")}>{(id) => <input id={id} type="date" className="input" value={form.date} onChange={(e) => set({ date: e.target.value })} />}</Field>
      <Field label="Method">{(id) => <select id={id} className="input" value={form.method} onChange={(e) => set({ method: e.target.value as PaymentMethod })}><option value="CASH">Cash</option><option value="CHECK">Check</option><option value="ONLINE">Online / bank transfer</option></select>}</Field>
      <Field label={form.method === "CASH" ? "Reference no. (optional)" : "Reference no. (required)"} error={show("reference")}>{(id) => <input id={id} className="input" maxLength={100} value={form.reference} onChange={(e) => set({ reference: e.target.value })} />}</Field>
      <Field label="Remarks" className="sm:col-span-2">{(id) => <input id={id} className="input" maxLength={300} value={form.remarks} onChange={(e) => set({ remarks: e.target.value })} />}</Field>
      {monthly && <div className="sm:col-span-2"><Notice tone="info" icon="calendar-2-line">{peso(monthly)} per month will be applied to condo dues from {monthLabel(form.startMonth)} for {form.months} month(s). The OR number is assigned by the server.</Notice></div>}
      <div className="flex justify-end gap-2 sm:col-span-2"><Button type="submit" icon="save-3-line" loading={busy}>Record & issue OR</Button></div>
    </form>
  );
}

export function ReceiptQuickList({ limit = 6 }: { limit?: number }) {
  const { data } = useAsync(() => api.receipts.ledger({ from: addDays(todayIso(), -45), to: todayIso(), q: "", method: "", status: "valid" }), []);
  const rows = data?.receipts ?? [];
  return (
    <ul className="divide-y divide-ink-100">
      {rows.slice(0, limit).map((r) => (
        <li key={r.id} className="flex items-center justify-between gap-3 px-5 py-2.5">
          <div className="min-w-0"><p className="font-semibold text-ink-900 tabular">{r.receiptNo}</p><p className="truncate text-[12.5px] text-ink-500">Unit {r.unitNo} · {r.method} · {dateLabel(r.date)}</p></div>
          <b className="tabular">{peso(r.amount)}</b>
        </li>
      ))}
      {data && rows.length === 0 && <li className="px-5 py-6 text-center text-ink-500"><Icon name="inbox-line" /> No receipts in the last 45 days.</li>}
    </ul>
  );
}
