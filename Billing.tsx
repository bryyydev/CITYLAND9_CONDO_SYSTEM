// Billing & SOA: monthly bills, generate bills, bill detail (SOA), record payment, Edit SOA,
// recalculate, email. Live: /api/billing. Amounts, statuses and OR numbers come from the server
// (one implementation of the money rules); this page explains them and prevents obvious mistakes.
import { useEffect, useMemo, useRef, useState } from "react";
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import { useSearchParams } from "react-router-dom";
import { Can, useAuth } from "../../auth/AuthContext";
import { DataTable } from "../../components/base/DataTable";
import { ConfirmDialog, Drawer, useToast } from "../../components/base/overlays";
import { Badge, Button, Card, CardHeader, ErrorState, Field, Icon, Notice, PageHeader, SearchInput, Skeleton, StatusBadge, Tabs, cx } from "../../components/base/ui";
import { downloadSoaDocument, printSoaDocument, printSoaDocuments, SoaDocument } from "../../components/feature/Documents";
import { focusFirstInvalid } from "../../components/feature/AccountFields";
import { legacyUrl } from "../../components/feature/ModuleRoute";
import { useAction, useAsync } from "../../hooks/useAsync";
import { addMonths, currentMonth, dateLabel, monthLabel, todayIso } from "../../lib/format";
import { fromCents, isAmount, peso, toCents } from "../../lib/money";
import { IS_MOCK, api } from "../../services/api";
import { ApiError } from "../../services/http";
import type { BillDetail, BillRow, ChargePayer, Month, PartyTransaction, PaymentInput, PaymentMethod, RecordPaymentResult, SoaAmounts, SoaCorrection } from "../../services/types";

export const CORPORATION = "CITYLAND 9 CONDOMINIUM CORPORATION";
type StatusTab = "All" | "Pending" | "Paid" | "Partially Paid" | "Overdue";
const MONTH_RE = /^\d{4}-(0[1-9]|1[0-2])$/;

/** A one-time token for one payment form: a second submit (double click, retry) is refused by the server. */
export const newFormToken = () => (crypto.randomUUID?.() ?? `${Date.now()}${Math.random()}`).replace(/[^A-Za-z0-9]/g, "").slice(0, 40).padEnd(16, "0");

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
  const toast = useToast();
  const [params, setParams] = useSearchParams();
  const paramMonth = params.get("month") ?? "";
  const [month, setMonthState] = useState<Month>(MONTH_RE.test(paramMonth) ? paramMonth : currentMonth());
  const [tab, setTab] = useState<StatusTab>("All");
  const [q, setQ] = useState("");
  const [type, setType] = useState("");
  const [generating, setGenerating] = useState(false);
  const [emailing, setEmailing] = useState(false);
  const [printingAll, setPrintingAll] = useState(false);
  const [printingPayer, setPrintingPayer] = useState<ChargePayer | null>(null);
  const [selectedBillIds, setSelectedBillIds] = useState<Set<number>>(new Set());
  const [deleteMode, setDeleteMode] = useState<"selected" | "all" | null>(null);
  const { busy: deletingBills, act: deleteAction } = useAction();
  const bills = useAsync(() => api.billing.list(month), [month]);
  const openBill = Number(params.get("bill")) || null;
  const movedForm = params.get("moved") === "form";

  const setParam = (key: string, value: string | null) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value); else next.delete(key);
    setParams(next, { replace: true });
  };
  const setMonth = (m: Month) => { setMonthState(m); setParam("month", m === currentMonth() ? null : m); };
  useEffect(() => setSelectedBillIds(new Set()), [month]);

  async function deleteGeneratedBills() {
    if (!deleteMode || deletingBills) return;
    try {
      const ids = deleteMode === "selected" ? [...selectedBillIds] : undefined;
      const result = await deleteAction(() => api.billing.deleteGenerated(month, ids));
      setSelectedBillIds(new Set());
      bills.reload();
      toast({ tone: "success", title: `${result.deleted} SOA(s) deleted`, message: `Generated SOAs for ${monthLabel(month)} were removed and recorded in the Audit Logs.` });
    } catch (err) {
      toast({ tone: "error", title: "SOAs not deleted", message: (err as Error).message });
    } finally {
      setDeleteMode(null);
    }
  }
  const printAll = async (payerType?: ChargePayer) => {
    if (printingAll) return;
    setPrintingAll(true);
    let printRoot: ReturnType<typeof createRoot> | null = null;
    const printContainer = document.createElement("div");
    printContainer.style.cssText = "position:fixed;left:-100000px;top:0;width:210mm";
    document.body.append(printContainer);
    try {
      const statements = await api.billing.printStatements(month, payerType);
      printRoot = createRoot(printContainer);
      flushSync(() => printRoot?.render(
        <>{statements.map((statement) => <SoaDocument key={statement.id} statement={statement} />)}</>,
      ));
      const documents = [...printContainer.querySelectorAll<HTMLElement>(".print-sheet")];
      await printSoaDocuments(documents, `SOA_${month}_${payerType ?? "all"}`);
    } catch (err) {
      toast({ tone: "error", title: "Couldn't print the SOAs", message: (err as Error).message });
    } finally {
      printRoot?.unmount();
      printContainer.remove();
      setPrintingAll(false);
      setPrintingPayer(null);
    }
  };

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
  const types = [...new Set((bills.data ?? []).map((b) => b.unitType).filter(Boolean))].sort();
  const partyStatementsAvailable = (bills.data ?? []).some((bill) => bill.partyLedgerEnabled);
  const printAllForPayer = (payerType: ChargePayer) => {
    setPrintingPayer(payerType);
    void printAll(payerType);
  };

  return (
    <>
      {!embedded && (
        <PageHeader eyebrow="Property & Billing" title="Billing & SOA" description="Monthly bills per unit, with separate statements for parking and storage assets. Print all SOAs together or open a bill to record a payment, correct or recalculate it, or email its SOA."
          actions={<>
            <Can permission="billing_email"><Button variant="secondary" icon="mail-send-line" onClick={() => setEmailing(true)}>Email SOAs</Button></Can>
            {!IS_MOCK && bills.data && bills.data.length > 0 && <>
              {partyStatementsAvailable
                ? (["Tenant", "Owner"] as const).map((payer) => (
                  <Button key={payer} variant="secondary" icon="printer-line" loading={printingPayer === payer} disabled={printingAll} onClick={() => printAllForPayer(payer)}>
                    {printingPayer === payer ? `Preparing ${payer} SOAs…` : `Print all SOA for ${payer}`}
                  </Button>
                ))
                : <Button variant="secondary" icon="printer-line" loading={printingAll} disabled={printingAll} onClick={() => void printAll()}>{printingAll ? "Preparing SOAs…" : "Print all SOAs"}</Button>}
            </>}
            {can("billing") && <Button icon="file-add-line" onClick={() => setGenerating(true)}>Generate bills</Button>}
            {can("delete_generated_soa") && <Button variant="danger" icon="delete-bin-line" onClick={() => setDeleteMode("all")} disabled={!bills.data?.some((bill) => bill.generated)}>Delete all generated SOAs</Button>}
          </>} />
      )}
      {movedForm && (
        <div className="mb-4" role="status">
          <Notice tone="warn">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span>Billing moved to this screen. The form you sent from the old page was <b>not saved</b> (no payment was recorded); please do it here.</span>
              <Button size="sm" variant="ghost" onClick={() => setParam("moved", null)}>Dismiss</Button>
            </div>
          </Notice>
        </div>
      )}
      <Card>
        <div className="flex flex-wrap items-center gap-3 border-b border-ink-100 p-4">
          <MonthPicker value={month} onChange={setMonth} />
          <div className="max-w-full"><Tabs value={tab} onChange={setTab} tabs={(["All", "Pending", "Paid", "Partially Paid", "Overdue"] as StatusTab[]).map((k) => ({ key: k, label: k, count: counts[k] }))} /></div>
          <div className="flex-1" />
          <select className="input sm:w-40" aria-label="Unit type" value={type} onChange={(e) => setType(e.target.value)}>
            <option value="">All unit types</option>{types.map((t) => <option key={t}>{t}</option>)}
          </select>
          <SearchInput value={q} onChange={setQ} placeholder="Search unit or name" />
          {can("delete_generated_soa") && selectedBillIds.size > 0 && <Button variant="danger" size="sm" icon="delete-bin-line" onClick={() => setDeleteMode("selected")} disabled={deletingBills}>Delete selected ({selectedBillIds.size})</Button>}
          {embedded && can("delete_generated_soa") && <Button variant="danger" size="sm" icon="delete-bin-line" onClick={() => setDeleteMode("all")} disabled={!bills.data?.some((bill) => bill.generated)}>Delete all generated SOAs</Button>}
          {embedded && !IS_MOCK && bills.data && bills.data.length > 0 && <>
            {partyStatementsAvailable
              ? (["Tenant", "Owner"] as const).map((payer) => (
                <Button key={payer} variant="secondary" icon="printer-line" loading={printingPayer === payer} disabled={printingAll} onClick={() => printAllForPayer(payer)}>
                  {printingPayer === payer ? `Preparing ${payer} SOAs…` : `Print all SOA for ${payer}`}
                </Button>
              ))
              : <Button variant="secondary" icon="printer-line" loading={printingAll} disabled={printingAll} onClick={() => void printAll()}>{printingAll ? "Preparing SOAs…" : "Print all SOAs"}</Button>}
          </>}
          {embedded && can("billing") && <Button icon="file-add-line" onClick={() => setGenerating(true)}>Generate bills</Button>}
        </div>
        <DataTable rows={bills.data ? rows : null} loading={bills.loading} error={bills.error} onRetry={bills.reload} rowKey={(b) => b.id} onRowClick={(b) => setParam("bill", String(b.id))}
          empty={{ icon: "file-list-3-line", title: bills.data?.length ? "No bills match these filters" : `No bills for ${monthLabel(month)}`, text: bills.data?.length ? "Try another status tab or search." : can("billing") ? "Generate the month's bills to create one SOA per residential unit." : undefined }}
          columns={[
            ...(can("delete_generated_soa") ? [{ key: "select", header: "Select", className: "w-16", cell: (b: BillRow) => <input type="checkbox" aria-label={`Select generated SOA for unit ${b.unitNo}`} title={b.generated ? undefined : "Only SOAs generated by this system can be removed"} disabled={!b.generated} checked={selectedBillIds.has(b.id)}
              onClick={(event) => event.stopPropagation()} onChange={(event) => setSelectedBillIds((current) => {
                const next = new Set(current);
                if (event.target.checked) next.add(b.id); else next.delete(b.id);
                return next;
              })} /> }] : []),
            { key: "u", header: "Unit", cell: (b) => <><b className="text-ink-900">{b.unitNo}</b><span className="block text-[12px] text-ink-500">{b.unitType}</span></> },
            { key: "n", header: "Billed to", cell: (b) => <>{b.payerName}{b.manualOverride && <span className="ml-2 rounded bg-copper-50 px-1.5 py-0.5 text-[11px] font-semibold text-copper-700" title="Corrected by hand (Edit SOA)">Corrected</span>}</> },
            { key: "d", header: "Due", cell: (b) => dateLabel(b.dueDate) },
            { key: "t", header: "Total", align: "right", cell: (b) => peso(b.total) },
            { key: "p", header: "Paid", align: "right", cell: (b) => peso(b.amountPaid) },
            { key: "b", header: "Balance", align: "right", cell: (b) => <b>{peso(b.balance)}</b> },
            { key: "s", header: "Status", cell: (b) => <StatusBadge status={b.status} /> },
            { key: "a", header: "", cell: () => <Icon name="arrow-right-s-line" className="text-ink-400" /> },
          ]}
          footer={rows.length > 0 && (
            <tr className="bg-ink-50 font-semibold"><td className="td" colSpan={can("delete_generated_soa") ? 4 : 3}>{rows.length} bill(s)</td><td className="td num">{peso(fromCents(totals.total))}</td><td className="td num">{peso(fromCents(totals.paid))}</td><td className="td num">{peso(fromCents(totals.balance))}</td><td className="td" colSpan={2} /></tr>
          )} />
      </Card>
      <BillDrawer billId={openBill} onClose={() => setParam("bill", null)} onChanged={bills.reload} />
      <GenerateBillsDrawer open={generating} initialMonth={month} onClose={() => setGenerating(false)} onDone={(m) => { setMonth(m); bills.reload(); }} />
      <SoaEmailDrawer open={emailing} month={month} onClose={() => setEmailing(false)} />
      <ConfirmDialog open={deleteMode !== null}
        title={deleteMode === "all" ? `Delete all SOAs for ${monthLabel(month)}?` : `Delete ${selectedBillIds.size} selected SOA(s)?`}
        confirmLabel="Delete SOAs" tone="danger" busy={deletingBills} onClose={() => { if (!deletingBills) setDeleteMode(null); }} onConfirm={() => void deleteGeneratedBills()}
        message={<>This permanently removes only SOAs generated by this system. The request is refused if the month is closed or any selected SOA is paid, has a payment, or has an advance allocation. No SOAs are deleted if any selected record is blocked. The deletion is recorded in the Audit Logs.</>} />
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
  const [mode, setMode] = useState<"view" | "pay" | "edit">("view");
  const [confirmRecalc, setConfirmRecalc] = useState(false);
  const [confirmEmail, setConfirmEmail] = useState(false);
  const { busy, act } = useAction();
  const toast = useToast();
  const docRef = useRef<HTMLElement>(null);
  const partyDocRef = useRef<HTMLElement>(null);
  const d = detail.data;
  const [pdfBusy, setPdfBusy] = useState(false);
  const [printBusy, setPrintBusy] = useState(false);
  const [partySoa, setPartySoa] = useState<BillDetail | null>(null);
  const [partyDocumentBusy, setPartyDocumentBusy] = useState<ChargePayer | null>(null);
  const [partyActionBusy, setPartyActionBusy] = useState<"print" | "download" | null>(null);

  useEffect(() => setMode("view"), [billId]);

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

  async function email() {
    if (!d) return;
    try {
      const r = await act(() => api.billing.emailBill(d.id));
      toast(r.failed ? { tone: "error", title: `${r.failed} email(s) failed`, message: r.errors[0] } : { tone: "success", title: `SOA emailed to ${r.sent} recipient(s)` });
    } catch (err) {
      toast({ tone: "error", title: "SOA not emailed", message: (err as Error).message });
    } finally {
      setConfirmEmail(false);
    }
  }

  async function downloadPdf() {
    if (!d || pdfBusy) return;
    setPdfBusy(true);
    try {
      const fresh = await api.billing.detail(d.id);
      flushSync(() => detail.setData(fresh));
      const name = `SOA_${fresh.unitNo}_${fresh.billingMonth}.pdf`;
      await downloadSoaDocument(docRef.current, name);
      toast({ tone: "success", title: "SOA downloaded", message: name });
    } catch (err) {
      toast({ tone: "error", title: "Couldn't download the SOA PDF", message: (err as Error).message });
    } finally {
      setPdfBusy(false);
    }
  }

  async function printSoa() {
    if (!d || printBusy) return;
    setPrintBusy(true);
    try {
      const fresh = await api.billing.detail(d.id);
      flushSync(() => detail.setData(fresh));
      await printSoaDocument(docRef.current, `SOA_${fresh.unitNo}_${fresh.billingMonth}`);
    } catch (err) {
      toast({ tone: "error", title: "Couldn't print the SOA", message: (err as Error).message });
    } finally {
      setPrintBusy(false);
    }
  }

  async function viewPartySoa(payerType: ChargePayer) {
    if (!d || partyDocumentBusy) return;
    setPartyDocumentBusy(payerType);
    try {
      const fresh = await api.billing.detail(d.id, payerType);
      setPartySoa(fresh);
    } catch (err) {
      toast({ tone: "error", title: `Couldn't view the ${payerType} SOA`, message: (err as Error).message });
    } finally {
      setPartyDocumentBusy(null);
    }
  }

  async function printPartySoa() {
    if (!partySoa || partyActionBusy) return;
    setPartyActionBusy("print");
    try {
      await printSoaDocument(partyDocRef.current, `SOA_${partySoa.unitNo}_${partySoa.billingMonth}_${partySoa.payerType ?? "Party"}`);
    } catch (err) {
      toast({ tone: "error", title: `Couldn't print the ${partySoa.payerType ?? "party"} SOA`, message: (err as Error).message });
    } finally {
      setPartyActionBusy(null);
    }
  }

  async function downloadPartySoa() {
    if (!partySoa || partyActionBusy) return;
    const name = `SOA_${partySoa.unitNo}_${partySoa.billingMonth}_${partySoa.payerType ?? "Party"}.pdf`;
    setPartyActionBusy("download");
    try {
      await downloadSoaDocument(partyDocRef.current, name);
      toast({ tone: "success", title: `${partySoa.payerType ?? "Party"} SOA downloaded`, message: name });
    } catch (err) {
      toast({ tone: "error", title: `Couldn't download the ${partySoa.payerType ?? "party"} SOA`, message: (err as Error).message });
    } finally {
      setPartyActionBusy(null);
    }
  }

  if (billId === null) return null;
  const locked = Boolean(d?.closed);
  return (
    <>
    <Drawer open onClose={() => { if (!busy) onClose(); }} width="max-w-3xl"
      title={d ? `Unit ${d.unitNo} · ${monthLabel(d.billingMonth)}` : "Bill"} subtitle={d ? `Billed to ${d.payerName}${d.unitType ? ` · ${d.unitType}` : ""}` : undefined}
      footer={d && mode === "view" && <div className="flex w-full flex-wrap justify-end gap-2">
        {d.partyLedgerEnabled && (["Owner", "Tenant"] as const).map((payer) => (
          <Button key={payer} variant="secondary" icon="eye-line" loading={partyDocumentBusy === payer}
            disabled={partyDocumentBusy !== null} onClick={() => void viewPartySoa(payer)}>
            {partyDocumentBusy === payer ? `Loading ${payer} SOA…` : `View ${payer} SOA`}
          </Button>
        ))}
        <Button variant="secondary" icon="printer-line" loading={printBusy} disabled={printBusy} onClick={printSoa}>Print</Button>
        <Button variant="secondary" icon="file-download-line" loading={pdfBusy} disabled={pdfBusy} onClick={downloadPdf}>{pdfBusy ? "Preparing PDF…" : "Download PDF"}</Button>
        {can("email_bill") && <Button variant="secondary" icon="mail-send-line" disabled={!d.emailRecipients?.length && !IS_MOCK} title={d.emailRecipients?.length ? undefined : "No owner or tenant has an email opted in for SOAs"} onClick={() => setConfirmEmail(true)}>Email</Button>}
        {can("edit_soa") && <Button variant="secondary" icon="edit-line" disabled={locked} title={locked ? "The books are closed for this month" : undefined} onClick={() => setMode("edit")}>Edit SOA</Button>}
        {can("recalculate_soa") && <Button variant="secondary" icon="refresh-line" onClick={() => setConfirmRecalc(true)} disabled={d.manualOverride || locked} title={d.manualOverride ? "Corrected by hand: use Edit SOA" : undefined}>Recalculate</Button>}
        {can("mark_bill_paid") && <Button icon="hand-coin-line" onClick={() => setMode("pay")}>Record payment</Button>}
      </div>}>
      {detail.error ? <ErrorState title={detail.error.status === 404 ? "Bill not found" : "Couldn't load the bill"} message={detail.error.message} onRetry={detail.error.status === 404 ? undefined : detail.reload} />
        : !d ? <Skeleton rows={8} />
        : mode === "pay" ? <RecordPaymentForm bill={d} onCancel={() => setMode("view")} onDone={() => { setMode("view"); detail.reload(); onChanged(); }} />
        : mode === "edit" ? <EditSoaForm bill={d} onCancel={() => setMode("view")} onSaved={(next) => { detail.setData(next); setMode("view"); onChanged(); toast({ tone: "success", title: "SOA corrected", message: "Saved and recorded in the Audit Logs." }); }} />
        : (
          <div className="space-y-4">
            {locked && <Notice tone="warn" icon="lock-line" title="Books closed for this month">Corrections and recalculation are refused. Payments dated in an open month can still be recorded.</Notice>}
            {d.manualOverride && <Notice tone="warn">This SOA was corrected by hand{d.note ? `: “${d.note}”` : ""}. It is never re-priced automatically.</Notice>}
            {d.partyLedgerEnabled && <PartyTransactionAssignment billId={d.id} />}
            {d.previousUnpaid && d.previousUnpaid.length > 0 && (
              <Notice tone="copper" title="Earlier months still unpaid">{d.previousUnpaid.map((p) => `${monthLabel(p.month)} (${peso(p.balance)})`).join(" · ")}. They are carried into this SOA's previous balance.</Notice>
            )}
            <SoaDocument ref={docRef} statement={d} />
            <div className="grid gap-3 sm:grid-cols-2">
              {d.contact && <div className="rounded-xl border border-ink-200 p-4 text-[13px]"><p className="font-semibold text-ink-900">{d.contact.name} <span className="font-normal text-ink-500">· {d.contact.kind}</span></p><p className="text-ink-600">{[d.contact.contactNo, d.contact.email].filter(Boolean).join(" · ") || "No contact details"}</p></div>}
              {d.qrUrl && !IS_MOCK && <div className="flex items-center gap-3 rounded-xl border border-ink-200 p-4 text-[13px]"><img src={legacyUrl(d.qrUrl)} alt="Payment QR code for this SOA" className="size-20" /><span className="text-ink-600">Payment QR for this SOA (amount = current balance).</span></div>}
            </div>
          </div>
        )}
      <ConfirmDialog open={confirmRecalc} title="Recalculate this SOA?" confirmLabel="Recalculate" busy={busy} onClose={() => setConfirmRecalc(false)} onConfirm={recalc}
        message="Issued bills keep their amounts. Recalculating re-prices condo dues on residential SOAs or the fee on a parking/storage asset SOA from the CURRENT rates. The change is recorded in the Audit Logs." />
      <ConfirmDialog open={confirmEmail} title="Email this SOA?" confirmLabel="Send email" busy={busy} onClose={() => setConfirmEmail(false)} onConfirm={email}
        message={<>Sends the SOA to: {d?.emailRecipients?.map((r) => `${r.name} <${r.email}>`).join(", ") || "the opted-in owners and tenants"}.</>} />
    </Drawer>
    <Drawer open={partySoa !== null} onClose={() => setPartySoa(null)}
      title={partySoa ? `${partySoa.payerType ?? "Party"} SOA · Unit ${partySoa.unitNo}` : "SOA"}
      subtitle={partySoa ? monthLabel(partySoa.billingMonth) : undefined} width="max-w-4xl"
      footer={partySoa && <>
        <Button variant="secondary" icon="printer-line" loading={partyActionBusy === "print"} disabled={partyActionBusy !== null} onClick={() => void printPartySoa()}>Print</Button>
        <Button icon="file-download-line" loading={partyActionBusy === "download"} disabled={partyActionBusy !== null} onClick={() => void downloadPartySoa()}>Download PDF</Button>
      </>}>
      {partySoa && <SoaDocument ref={partyDocRef} statement={partySoa} />}
    </Drawer>
    </>
  );
}

function PartyTransactionAssignment({ billId }: { billId: number }) {
  const transactions = useAsync(() => api.billing.partyTransactions(billId), [billId]);
  const [selected, setSelected] = useState<Record<string, ChargePayer | "">>({});
  const [error, setError] = useState("");
  const { busy, act } = useAction();
  const toast = useToast();
  const unassigned = (transactions.data?.transactions ?? []).filter((item) => !item.payerType);
  if (transactions.error) return <Notice tone="warn" title="Couldn't verify payer assignments">{transactions.error.message}</Notice>;
  if (!unassigned.length) return null;

  async function assign(item: PartyTransaction) {
    const key = `${item.kind}-${item.id}`;
    const payerType = selected[key];
    if (!payerType || busy) return;
    setError("");
    try {
      await act(() => api.billing.assignPartyTransaction(billId, item.kind, item.id, payerType));
      toast({ tone: "success", title: "Transaction assigned", message: `${item.receiptNo || "Transaction"} is now in the ${payerType} ledger.` });
      transactions.reload();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  return (
    <Notice tone="warn" title="Assign earlier transactions before issuing separate SOAs">
      <div className="space-y-3">
        <p>These post-cutover payments or advances do not have a recorded payer. Assign each to the correct ledger; the system will not infer it.</p>
        {error && <p role="alert" className="text-red-700">{error}</p>}
        {unassigned.map((item) => {
          const key = `${item.kind}-${item.id}`;
          return (
            <div key={key} className="flex flex-wrap items-center gap-2 rounded-lg border border-amber-200 bg-white p-3 text-sm">
              <span className="min-w-48 flex-1">{item.date} · {item.kind === "advance" ? "Advance" : item.kind === "water" ? `Water payment (${item.month})` : `Bill payment (${item.month})`} · {peso(item.amount)}{item.receiptNo ? ` · ${item.receiptNo}` : ""}</span>
              <select className="input w-32" aria-label={`Assign ${item.receiptNo || item.kind} to ledger`} value={selected[key] ?? ""} onChange={(event) => setSelected({ ...selected, [key]: event.target.value as ChargePayer | "" })}>
                <option value="">Choose…</option><option value="Owner">Owner</option><option value="Tenant">Tenant</option>
              </select>
              <Button size="sm" disabled={!selected[key] || busy} loading={busy} onClick={() => void assign(item)}>Assign</Button>
            </div>
          );
        })}
      </div>
    </Notice>
  );
}

// ------------------------------------------------------------------ record payment
export function RecordPaymentForm({ bill, onCancel, onDone }: { bill: BillDetail | BillRow; onCancel: () => void; onDone: (r: RecordPaymentResult) => void }) {
  const partyLedgerEnabled = Boolean(bill.partyLedgerEnabled);
  const payerRequired = Boolean(bill.payerRequired ?? partyLedgerEnabled);
  const [form, setForm] = useState<PaymentInput>(() => ({ amount: payerRequired ? "" : bill.balance, method: "CASH", type: "FULL", reference: "", date: todayIso(), remarks: "", payerType: payerRequired ? "" : undefined, formToken: newFormToken() }));
  const [partyBalance, setPartyBalance] = useState<string | null>(null);
  const [partyBalanceError, setPartyBalanceError] = useState("");
  const [touched, setTouched] = useState(false);
  const [serverFields, setServerFields] = useState<Record<string, string>>({});
  const [serverError, setServerError] = useState("");
  const [result, setResult] = useState<RecordPaymentResult | null>(null);
  const [duplicate, setDuplicate] = useState(false);
  const { busy, act } = useAction();
  const toast = useToast();
  const balance = toCents(payerRequired ? partyBalance : bill.balance);
  const amount = toCents(form.amount);
  const refNeeded = form.method !== "CASH";
  const errors = {
    amount: !isAmount(form.amount) ? "Enter an amount greater than zero, e.g. 5000 or 5000.50." : serverFields.amount ?? "",
    reference: refNeeded && !form.reference.trim() ? "A reference number is required for check and online payments." : serverFields.reference ?? "",
    date: !form.date ? "Choose the payment date." : serverFields.date ?? serverFields.period ?? "",
    payerType: payerRequired && !form.payerType ? "Choose Owner or Tenant." : serverFields.payerType ?? "",
  };
  const set = (p: Partial<PaymentInput>) => { setForm({ ...form, ...p }); setServerFields({}); setServerError(""); };

  useEffect(() => {
    if (!payerRequired || !form.payerType) {
      setPartyBalance(null);
      setPartyBalanceError("");
      return;
    }
    let active = true;
    setPartyBalance(null);
    setPartyBalanceError("");
    api.billing.partyBalance(bill.id, form.payerType, form.date).then(({ balance }) => {
      if (!active) return;
      setPartyBalance(balance);
      setForm((current) => ({ ...current, amount: current.amount || balance }));
    }).catch((err: unknown) => {
      if (active) setPartyBalanceError((err as Error).message);
    });
    return () => { active = false; };
  }, [bill.id, form.date, form.payerType, payerRequired]);

  async function submit() {
    setTouched(true);
    if (busy || errors.amount || errors.reference || errors.date || errors.payerType || partyBalance === null && payerRequired) return;
    try {
      // The same token is sent if this is retried (e.g. after a network error): the server records it once.
      const r = await act(() => api.billing.recordPayment(bill.id, { ...form, type: amount >= balance ? "FULL" : "PARTIAL" }));
      setResult(r);
      toast({ tone: "success", title: `Payment recorded · ${r.receiptNo}`, message: toCents(r.excessToAdvance) > 0 ? `${peso(r.excessToAdvance)} moved to advance payment.` : undefined });
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) { setDuplicate(true); return; }
      if (err instanceof ApiError && Object.keys(err.fields).length) { setServerFields(err.fields); setServerError(Object.values(err.fields)[0]); }
      else setServerError((err as Error).message);
    }
  }

  if (duplicate) {
    return (
      <div className="space-y-4 text-center">
        <Notice tone="warn" title="This payment was already recorded">The server had already saved this payment (for example after a double click or a lost connection), so it was not recorded twice.</Notice>
        <Button onClick={() => onDone({ receiptNo: "", appliedToBill: "0.00", excessToAdvance: "0.00", status: bill.status })}>Show the bill</Button>
      </div>
    );
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
        <div className="flex justify-center gap-2">
          {result.receiptId && !IS_MOCK && <a className="inline-flex h-10 items-center gap-2 rounded-lg border border-ink-200 bg-white px-4 font-semibold shadow-sm hover:bg-ink-50" href={legacyUrl(`/receipts/${result.receiptId}`)} target="_blank" rel="noreferrer"><Icon name="printer-line" />Print receipt</a>}
          <Button onClick={() => onDone(result)}>Done</Button>
        </div>
      </div>
    );
  }

  const applied = Math.min(amount, Math.max(balance, 0));
  const excess = Math.max(amount - Math.max(balance, 0), 0);
  const show = (k: keyof typeof errors) => (touched || serverFields[k] ? errors[k] : "");
  return (
    <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); void submit(); }} noValidate>
      <div className="flex items-center justify-between rounded-xl bg-ink-50 px-4 py-3">
        <span className="text-ink-600">Balance due{payerRequired && form.payerType ? ` · ${form.payerType} ledger` : ""} · Unit {"unitNo" in bill ? bill.unitNo : ""} · {monthLabel(bill.billingMonth)}</span>
        <b className="font-display text-[18px] tabular">{payerRequired ? partyBalance === null ? "Loading…" : peso(partyBalance) : peso(bill.balance)}</b>
      </div>
      {serverError && <Notice tone="warn">{serverError}</Notice>}
      {partyBalanceError && <Notice tone="warn" title="Couldn't load this ledger balance">{partyBalanceError}</Notice>}
      {payerRequired && <Field label="Payer ledger" error={show("payerType")}>{(id) => (
        <select id={id} className="input" value={form.payerType} aria-invalid={Boolean(show("payerType")) || undefined}
          onChange={(e) => { set({ payerType: e.target.value as ChargePayer | "" }); setPartyBalance(null); }}>
          <option value="">Choose…</option><option value="Owner">Owner</option><option value="Tenant">Tenant</option>
        </select>)}</Field>}
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Amount received (₱)" error={show("amount")}>{(id) => <input id={id} inputMode="decimal" className="input tabular" data-autofocus aria-invalid={Boolean(show("amount")) || undefined} value={form.amount} onChange={(e) => set({ amount: e.target.value })} />}</Field>
        <Field label="Payment date" error={show("date")}>{(id) => <input id={id} type="date" className="input" value={form.date} aria-invalid={Boolean(show("date")) || undefined} onChange={(e) => set({ date: e.target.value })} />}</Field>
        <Field label="Method">{(id) => (
          <select id={id} className="input" value={form.method} onChange={(e) => set({ method: e.target.value as PaymentMethod })}>
            <option value="CASH">Cash</option><option value="CHECK">Check</option><option value="ONLINE">Online / bank transfer</option>
          </select>)}</Field>
        <Field label={refNeeded ? "Reference no. (required)" : "Reference no. (optional)"} error={show("reference")}>{(id) => <input id={id} className="input" maxLength={120} placeholder={form.method === "CHECK" ? "Check number" : form.method === "ONLINE" ? "Transaction reference" : ""} aria-invalid={Boolean(show("reference")) || undefined} value={form.reference} onChange={(e) => set({ reference: e.target.value })} />}</Field>
        <Field label="Remarks" className="sm:col-span-2">{(id) => <input id={id} className="input" maxLength={300} value={form.remarks} onChange={(e) => set({ remarks: e.target.value })} />}</Field>
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
        <Button type="button" variant="secondary" onClick={onCancel} disabled={busy}>Back</Button>
        <Button type="submit" icon="hand-coin-line" loading={busy} disabled={payerRequired && partyBalance === null}>Record payment & issue OR</Button>
      </div>
    </form>
  );
}

// ------------------------------------------------------------------ Edit SOA (manual correction)
const SOA_LABELS: [keyof SoaAmounts, string][] = [
  ["condoDues", "Condo dues"], ["parking", "Parking"], ["storage", "Storage"], ["water", "Water"], ["other", "Other charges"],
  ["adjustment", "Adjustment"], ["penalty", "Penalty"], ["previousBalance", "Previous balance"],
];

function EditSoaForm({ bill, onCancel, onSaved }: { bill: BillDetail; onCancel: () => void; onSaved: (b: BillDetail) => void }) {
  const stored = bill.stored ?? { condoDues: bill.charges.condoDues, parking: bill.charges.parking, storage: bill.charges.storage, water: bill.charges.water,
    other: bill.charges.other ?? "0.00", adjustment: bill.charges.adjustment ?? "0.00", penalty: bill.charges.penalty, previousBalance: bill.charges.previousBalance };
  const initialOtherItems = bill.charges.otherItems?.length
    ? bill.charges.otherItems
    : toCents(stored.other) > 0
      ? [{ description: "Other charges", amount: stored.other, payerType: bill.otherPayerType ?? "Owner" as const }]
      : [];
  const [form, setForm] = useState<SoaCorrection>({ ...stored, otherItems: initialOtherItems, dueDate: bill.dueDate ?? "", note: "" });
  const [touched, setTouched] = useState(false);
  const [serverFields, setServerFields] = useState<Record<string, string>>({});
  const [serverError, setServerError] = useState("");
  const { busy, act } = useAction();
  const ref = useRef<HTMLFormElement>(null);
  const amountOk = (v: string, negative = false) => v.trim() === "" || (negative ? /^-?\d{1,9}(\.\d{1,2})?$/ : /^\d{1,9}(\.\d{1,2})?$/).test(v.replace(/,/g, "").trim());
  const errors: Record<string, string> = Object.fromEntries([
    ...SOA_LABELS.filter(([k]) => k !== "other").map(([k, label]) => [k, !amountOk(form[k], k === "adjustment") ? `${label}: enter an amount, e.g. 1500.00${k === "adjustment" ? " (or -200.00 for a credit)" : ""}.` : serverFields[k] ?? ""]),
    ["otherItems", form.otherItems.some((item) => !item.description.trim() || item.description.length > 100 || !item.amount.trim() || !amountOk(item.amount)) ? "Enter a name and valid amount for every other-charge line." : serverFields.otherItems ?? ""],
    ["note", !form.note.trim() ? "Explain the correction (shown on the SOA, kept in the audit log)." : serverFields.note ?? ""],
    ["dueDate", serverFields.dueDate ?? ""],
  ]);
  const err = (k: string) => (touched ? errors[k] : "") || serverFields[k] || "";
  const total = SOA_LABELS.reduce((s, [k]) => s + toCents(form[k] || "0"), 0);
  const updateOtherItems = (otherItems: SoaCorrection["otherItems"]) => {
    const other = fromCents(otherItems.reduce((sum, item) => sum + toCents(item.amount), 0));
    setForm({ ...form, otherItems, other });
    setServerFields({});
  };

  async function submit() {
    setTouched(true); setServerError("");
    if (busy) return;
    if (Object.values(errors).some(Boolean)) { focusFirstInvalid(ref); return; }
    try {
      onSaved(await act(() => api.billing.correctSoa(bill.id, form)));
    } catch (e) {
      if (e instanceof ApiError && Object.keys(e.fields).length) { setServerFields(e.fields); setServerError(Object.values(e.fields)[0]); focusFirstInvalid(ref); }
      else setServerError((e as Error).message);
    }
  }

  return (
    <form ref={ref} className="space-y-4" noValidate onSubmit={(e) => { e.preventDefault(); void submit(); }}>
      <Notice tone="warn" title="Manual correction">The amounts you enter replace the SOA's figures and the SOA is marked “corrected by hand”: it won't be re-priced from rates or recalculated afterwards. The change and your reason are recorded in the Audit Logs.</Notice>
      {serverError && <Notice tone="warn">{serverError}</Notice>}
      <div className="grid gap-3 sm:grid-cols-3">
        {SOA_LABELS.filter(([k]) => k !== "other").map(([k, label]) => (
          <Field key={k} label={`${label} (₱)`} error={err(k)} hint={k === "adjustment" ? "Negative = credit/discount" : k === "previousBalance" && bill.partyLedgerEnabled ? "Calculated from the separate party ledgers" : undefined}>{(id) => <input id={id} inputMode="decimal" className="input tabular" value={form[k]} disabled={k === "previousBalance" && bill.partyLedgerEnabled} aria-invalid={Boolean(err(k)) || undefined}
            onChange={(e) => { setForm({ ...form, [k]: e.target.value }); setServerFields({}); }} />}</Field>
        ))}
        <Field label="Due date" error={err("dueDate")}>{(id) => <input id={id} type="date" className="input" value={form.dueDate} onChange={(e) => setForm({ ...form, dueDate: e.target.value })} />}</Field>
      </div>
      <section className="space-y-3 rounded-xl border border-ink-200 p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div><h3 className="font-semibold text-ink-900">Named other charges</h3><p className="text-xs text-ink-500">Choose who is responsible for each item. The SOA total is calculated from these lines.</p></div>
          <Button type="button" size="sm" variant="secondary" icon="add-line" onClick={() => updateOtherItems([...form.otherItems, { description: "", amount: "", payerType: bill.otherPayerType ?? "Owner" }])}>Add charge</Button>
        </div>
        {err("otherItems") && <p className="text-sm text-red-700" role="alert">{err("otherItems")}</p>}
        {form.otherItems.map((item, index) => (
          <div key={index} className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_9rem_9rem_auto]">
            <Field label="Charge name">{(id) => <input id={id} className="input" maxLength={100} value={item.description} onChange={(e) => updateOtherItems(form.otherItems.map((row, i) => i === index ? { ...row, description: e.target.value } : row))} />}</Field>
            <Field label="Amount (₱)">{(id) => <input id={id} inputMode="decimal" className="input tabular" value={item.amount} onChange={(e) => updateOtherItems(form.otherItems.map((row, i) => i === index ? { ...row, amount: e.target.value } : row))} />}</Field>
            <Field label="Payer">{(id) => <select id={id} className="input" value={item.payerType} onChange={(e) => updateOtherItems(form.otherItems.map((row, i) => i === index ? { ...row, payerType: e.target.value as "Owner" | "Tenant" } : row))}><option>Owner</option><option>Tenant</option></select>}</Field>
            <div className="flex items-end"><Button type="button" size="sm" variant="ghost" icon="delete-bin-line" aria-label={`Remove ${item.description || "other charge"}`} onClick={() => updateOtherItems(form.otherItems.filter((_, i) => i !== index))}>Remove</Button></div>
          </div>
        ))}
        <div className="flex justify-end gap-2 border-t border-ink-100 pt-3 text-sm"><span className="text-ink-600">Other-charge total</span><b className="tabular">{peso(form.other)}</b></div>
      </section>
      <div className={cx("flex items-center justify-between rounded-xl px-4 py-3", "bg-ink-50")}>
        <span className="text-ink-600">New SOA total (before payments and advance)</span><b className="font-display text-[18px] tabular">{peso(fromCents(total))}</b>
      </div>
      <Field label="Reason for the correction" error={err("note")} hint="Shown on the SOA, e.g. “Water meter misread; corrected per inspection 12 Oct”.">{(id) => (
        <textarea id={id} rows={2} maxLength={500} className="input h-auto py-2" value={form.note} aria-invalid={Boolean(err("note")) || undefined} onChange={(e) => { setForm({ ...form, note: e.target.value }); setServerFields({}); }} />)}</Field>
      <div className="flex justify-end gap-2">
        <Button type="button" variant="secondary" onClick={onCancel} disabled={busy}>Cancel</Button>
        <Button type="submit" icon="save-3-line" loading={busy}>Save correction</Button>
      </div>
    </form>
  );
}

// ------------------------------------------------------------------ generate bills
export function GenerateBillsPanel({ onDone, compact = false, initialMonth }: { onDone?: (month: Month) => void; compact?: boolean; initialMonth?: Month }) {
  const [month, setMonth] = useState<Month>(initialMonth ?? currentMonth());
  const preview = useAsync(() => api.billing.preview(month), [month]);
  const [confirm, setConfirm] = useState(false);
  const { busy, act } = useAction();
  const toast = useToast();
  const p = preview.data;

  async function generate() {
    try {
      const r = await act(() => api.billing.generate(month));
      const notes = [
        r.skipped ? `${r.skipped} unit(s) were already billed and were skipped.` : "",
        r.deferredAssets?.length ? `${r.deferredAssets.length} asset SOA(s) were held back because existing residential SOAs already include those fees; no duplicate charge was created.` : "",
      ].filter(Boolean);
      toast({ tone: "success", title: `Generated ${r.created} bill(s) for ${monthLabel(r.month)}`, message: notes.join(" ") || undefined });
      preview.reload();
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
      {preview.error ? <ErrorState title="Couldn't check the month" message={preview.error.message} onRetry={preview.reload} /> : !p ? <Skeleton rows={4} /> : (
        <>
          {p.closed && <Notice tone="warn" icon="lock-line" title="Books closed">The books are closed through {monthLabel(p.closedThrough!)}. Bills for {monthLabel(month)} can't be generated.</Notice>}
          <ul className="space-y-3">
            {check(p.missingReadings.length === 0, `Water readings: ${p.readings} of ${p.residentialUnits} units`, p.missingReadings.length === 0 ? "All meters read for this month." : `Without a reading, water is billed ₱0.00: ${p.missingReadings.slice(0, 8).join(", ")}${p.missingReadings.length > 8 ? "…" : ""}. Enter readings first if possible.`)}
            {check(p.alreadyBilled === 0, p.alreadyBilled ? `${p.alreadyBilled} residential unit(s) already billed` : "No residential bills yet for this month", p.alreadyBilled ? "Already-billed units are skipped; only missing bills are created." : "One bill will be created per active residential or commercial unit.")}
            {(p.assetUnits > 0 || p.assetsAlreadyBilled > 0) && check(p.assetsAlreadyBilled === 0, p.assetsAlreadyBilled ? `${p.assetsAlreadyBilled} parking/storage asset(s) already billed` : `${p.assetsToCreate} parking/storage asset(s) will receive separate SOAs`, p.assetsAlreadyBilled ? "Already-billed assets are skipped; assigned fees will not also appear on residential SOAs." : "Assigned parking and storage fees are moved to the asset statements, preventing duplicate charges.")}
            {p.assetsDeferred.length > 0 && check(false, `${p.assetsDeferred.length} asset SOA(s) deferred to prevent double billing`, `An already-issued residential SOA still includes fees for: ${p.assetsDeferred.slice(0, 8).join(", ")}${p.assetsDeferred.length > 8 ? "…" : ""}. Existing SOAs are not changed; have accounting correct them before transferring these charges.`)}
            {p.zeroDuesUnits.length > 0 && check(false, `${p.zeroDuesUnits.length} unit(s) would get ₱0 condo dues`, `${p.zeroDuesUnits.slice(0, 8).join(", ")}: no rate for their type. Set a rate in Units Directory first.`)}
            {check(true, "Rules applied by the server", `Condo dues = area × rate (or the unit's manual amount); assigned parking/storage billed separately; previous balance, penalty and advance payments; due ${dateLabel(p.dueDate)}.`)}
          </ul>
          {!compact && <Notice tone="copper" icon="lock-line">Previously issued SOAs are unchanged. New bills keep their recorded amounts; later rate changes don't alter them unless someone recalculates a bill on purpose (audited).</Notice>}
          <Button icon="file-add-line" onClick={() => setConfirm(true)} disabled={p.closed || p.toCreate + p.assetsToCreate === 0}>Generate {p.toCreate + p.assetsToCreate} bill(s) for {monthLabel(month)}</Button>
        </>
      )}
      <ConfirmDialog open={confirm} title={`Generate bills for ${monthLabel(month)}?`} confirmLabel="Generate bills" busy={busy} onClose={() => setConfirm(false)} onConfirm={generate}
        message={<>This creates {(p?.toCreate ?? 0) + (p?.assetsToCreate ?? 0)} bill(s), including separate statements for parking and storage assets. Residents will see them in their portal.</>} />
    </div>
  );
}

function GenerateBillsDrawer({ open, initialMonth, onClose, onDone }: { open: boolean; initialMonth: Month; onClose: () => void; onDone: (m: Month) => void }) {
  return (
    <Drawer open={open} onClose={onClose} title="Generate monthly bills" subtitle="Creates SOAs for active residential/commercial units and parking/storage assets." width="max-w-lg">
      {open && <GenerateBillsPanel initialMonth={initialMonth} onDone={(m) => { onDone(m); onClose(); }} />}
    </Drawer>
  );
}

// ------------------------------------------------------------------ email the month's SOAs
function SoaEmailDrawer({ open, month, onClose }: { open: boolean; month: Month; onClose: () => void }) {
  const overview = useAsync(() => (open ? api.billing.emailOverview(month) : Promise.resolve(null)), [open, month]);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [confirm, setConfirm] = useState(false);
  const { busy, act } = useAction();
  const toast = useToast();
  const { can } = useAuth();
  const o = overview.data;
  const reachable = (o?.rows ?? []).filter((r) => r.recipients.length > 0);

  useEffect(() => { if (o) setSelected(new Set(o.rows.filter((r) => r.recipients.length > 0).map((r) => r.billId))); }, [o]);
  if (!open) return null;

  async function send() {
    try {
      const r = await act(() => api.billing.emailSoas(month, [...selected]));
      toast(r.failed ? { tone: "error", title: `${r.sent} sent, ${r.failed} failed`, message: r.errors[0] }
        : { tone: "success", title: `${r.sent} SOA email(s) sent`, message: r.skipped ? `${r.skipped} unit(s) skipped: no opted-in email.` : undefined });
      onClose();
    } catch (err) {
      toast({ tone: "error", title: "SOAs not sent", message: (err as Error).message });
    } finally {
      setConfirm(false);
    }
  }

  return (
    <Drawer open onClose={() => { if (!busy) onClose(); }} width="max-w-xl" title={`Email SOAs · ${monthLabel(month)}`} subtitle="Sent to owners and tenants who opted in under Units Directory."
      footer={o && <><Button variant="secondary" onClick={onClose} disabled={busy}>Close</Button>
        {can("send_billing_emails") && <Button icon="mail-send-line" loading={busy} disabled={!o.smtpConfigured || selected.size === 0} onClick={() => setConfirm(true)}>Send {selected.size} SOA(s)</Button>}</>}>
      {overview.error ? <ErrorState title="Couldn't load" message={overview.error.message} onRetry={overview.reload} /> : !o ? <Skeleton rows={6} /> : (
        <div className="space-y-4">
          {!o.smtpConfigured && <Notice tone="warn" title="Email isn't set up">Set the SMTP host and sender email under Rates &amp; Rules first.</Notice>}
          {o.rows.length === 0 ? <p className="text-ink-500">No bills for {monthLabel(month)} yet.</p> : (
            <>
              <p className="text-[13px] text-ink-600">{reachable.length} of {o.rows.length} units have an opted-in email ({o.optedIn} recipient(s)).</p>
              <ul className="divide-y divide-ink-100 rounded-xl border border-ink-200">
                {o.rows.map((r) => (
                  <li key={r.billId}>
                    <label className={cx("flex items-start gap-3 px-4 py-2.5", r.recipients.length ? "cursor-pointer hover:bg-ink-50" : "opacity-60")}>
                      <input type="checkbox" className="mt-1 size-4 accent-brand-600" disabled={!r.recipients.length} checked={selected.has(r.billId)}
                        onChange={(e) => { const n = new Set(selected); if (e.target.checked) n.add(r.billId); else n.delete(r.billId); setSelected(n); }} />
                      <span className="min-w-0 flex-1"><b className="text-ink-900">{r.unitNo}</b> <StatusBadge status={r.status} />
                        <span className="block text-[12.5px] break-words text-ink-500">{r.recipients.length ? r.recipients.map((x) => `${x.name} <${x.email}>`).join(", ") : "No opted-in email"}</span></span>
                    </label>
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      )}
      <ConfirmDialog open={confirm} title={`Send ${selected.size} SOA(s)?`} confirmLabel="Send emails" busy={busy} onClose={() => setConfirm(false)} onConfirm={send}
        message={`Each selected unit's SOA for ${monthLabel(month)} is emailed to its opted-in owners/tenants. Every email is recorded in the Audit Logs.`} />
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
