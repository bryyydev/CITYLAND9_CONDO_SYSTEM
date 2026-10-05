// Printable Statement of Account and Official Receipt. Always white paper colours, so the PDF
// and the printout look the same in any theme. Figures come from the server as-is.
import { forwardRef, type ReactNode } from "react";
import { dateLabel, monthLabel } from "../../lib/format";
import { peso, toCents } from "../../lib/money";
import type { ChargeBasis, ReceiptItem, SoaDetail } from "../../services/types";
import { StatusBadge } from "../base/ui";

const sheet = "print-sheet mx-auto max-w-[860px] rounded-xl border border-ink-200 bg-white p-5 text-ink-800 shadow-card sm:p-8";
const sectionTitle = "mt-6 mb-2 text-[12px] font-bold tracking-[0.12em] text-brand-700";

/** A receipt line (Official Receipt document). */
function Line({ label, detail, amount, negative }: { label: string; detail?: string; amount: string; negative?: boolean }) {
  return (
    <tr>
      <td className="border-b border-ink-100 py-2.5 pr-4">{label}{detail && <span className="block text-[12px] text-ink-500">{detail}</span>}</td>
      <td className="border-b border-ink-100 py-2.5 text-right tabular">{negative ? "− " : ""}{peso(amount)}</td>
    </tr>
  );
}

function Meta({ items }: { items: [string, string][] }) {
  return (
    <div className="my-5 grid grid-cols-2 gap-2.5 sm:grid-cols-4">
      {items.map(([k, v]) => (
        <div key={k} className="rounded-lg bg-ink-50 px-3 py-2">
          <div className="text-[10.5px] font-semibold tracking-[0.1em] text-ink-500">{k}</div>
          <div className="font-semibold text-ink-900 tabular">{v}</div>
        </div>
      ))}
    </div>
  );
}

const basisText = (b: ChargeBasis | null, recorded: boolean) => {
  if (!b) return recorded ? "" : "Basis not recorded for this statement";
  const where = b.unitNo ? `${b.unitNo}: ` : "";
  return b.basis === "manual" ? `${where}fixed monthly amount` : `${where}${Number(b.area).toLocaleString("en-PH", { minimumFractionDigits: 2 })} sqm × ₱${b.rate}/sqm`;
};

function ChargeRow({ label, basis, amount, minus, strong, shade }: { label: string; basis?: string; amount: string; minus?: boolean; strong?: boolean; shade?: boolean }) {
  return (
    <tr className={shade ? "bg-brand-50/70" : undefined}>
      <td className={`border-b border-ink-100 py-2.5 pr-3 pl-2 ${strong ? "font-semibold text-ink-900" : ""}`}>{label}</td>
      <td className="border-b border-ink-100 py-2.5 pr-3 text-[12.5px] text-ink-500">{basis}</td>
      <td className={`border-b border-ink-100 py-2.5 pr-2 text-right whitespace-nowrap tabular ${strong ? "font-semibold text-ink-900" : ""}`}>{minus && toCents(amount) ? "− " : ""}{peso(amount.replace("-", ""))}</td>
    </tr>
  );
}

/** The Statement of Account, as the office and residents see it on screen and print it. The PDF
 * download (server) has the same sections and figures. */
export const SoaDocument = forwardRef<HTMLElement, { statement: SoaDetail; corporation?: string; unitNo?: string }>(function SoaDocument({ statement: s }, ref) {
  const c = s.charges, a = s.account, pen = s.penaltyInfo;
  const period = monthLabel(s.billingMonth);
  const balance = toCents(s.balance);
  const facts: [string, ReactNode][] = [
    ["Unit", a.unitNo], ["Billing period", `${dateLabel(s.periodStart)} – ${dateLabel(s.periodEnd)}`], ["Statement date", dateLabel(s.issueDate)],
    ["Due date", dateLabel(s.dueDate)], ["Status", <StatusBadge key="st" status={s.status} />], [balance < 0 ? "Credit" : "Balance", peso(s.balance.replace("-", ""))],
  ];
  const account = ([["Bill to", a.billTo], ["Owner", a.ownerName], ...(a.tenantName ? [["Tenant", a.tenantName]] : []),
    ["Unit type", [a.unitType, a.floor && `Floor ${a.floor}`].filter(Boolean).join(" · ")]] as [string, string][]);
  return (
    <article ref={ref} className={sheet} aria-label="Statement of account">
      <header className="flex flex-col gap-2 border-b-2 border-brand-600 pb-3 sm:flex-row sm:items-start sm:justify-between sm:gap-4">
        <div className="min-w-0 flex-1">
          <h2 className="text-[17px] leading-tight font-bold text-[#0f2a3d]">{s.property.name}</h2>
          {s.property.address && <p className="mt-0.5 text-[12.5px] text-ink-500">{s.property.address}</p>}
        </div>
        <div className="sm:shrink-0 sm:text-right">
          <p className="text-[12px] font-bold tracking-[0.12em] text-brand-700">STATEMENT OF ACCOUNT</p>
          <p className="text-[13px] text-ink-700 tabular">No. {s.statementNo}</p>
        </div>
      </header>

      <dl className="mt-4 grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-ink-100 bg-ink-100 sm:grid-cols-3">
        {facts.map(([k, v]) => (
          <div key={k} className="bg-ink-50 px-3.5 py-2.5">
            <dt className="text-[11px] font-semibold tracking-[0.08em] text-ink-500 uppercase">{k}</dt>
            <dd className="mt-0.5 text-[14px] font-semibold text-ink-900 tabular">{v}</dd>
          </div>
        ))}
      </dl>

      <h3 className={sectionTitle}>ACCOUNT</h3>
      <dl className="grid gap-x-8 gap-y-1 text-[13.5px] sm:grid-cols-2">
        {account.map(([k, v]) => (
          <div key={k} className="flex gap-3"><dt className="w-20 shrink-0 text-ink-500">{k}</dt><dd className="font-medium text-ink-900">{v || "—"}</dd></div>
        ))}
      </dl>

      <h3 className={sectionTitle}>CHARGES</h3>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[520px] text-[14px]">
          <thead><tr className="border-b-2 border-[#0f2a3d] text-left text-[11px] tracking-[0.08em] text-ink-500"><th className="py-1.5 pl-2 font-semibold">DESCRIPTION</th><th className="py-1.5 font-semibold">BASIS</th><th className="py-1.5 pr-2 text-right font-semibold">AMOUNT</th></tr></thead>
          <tbody>
            <ChargeRow label={`Condo dues · ${period}`} basis={basisText(s.bases.condo, s.bases.recorded)} amount={c.condoDues} />
            {(toCents(c.parking) !== 0 || s.bases.parking) && <ChargeRow label="Parking" basis={basisText(s.bases.parking, s.bases.recorded)} amount={c.parking} />}
            {(toCents(c.storage) !== 0 || s.bases.storage) && <ChargeRow label="Storage" amount={c.storageIncluded ? c.storage : "0.00"}
              basis={[basisText(s.bases.storage, s.bases.recorded), c.storageIncluded ? "" : `not included in this total (storage is charged from ${monthLabel(c.storageFrom)})`].filter(Boolean).join("; ")} />}
            <ChargeRow label={`Water · ${period}`} amount={c.waterPaidSeparately ? "0.00" : c.water}
              basis={c.waterPaidSeparately ? "paid separately; not included in this total" : c.waterUsage !== null ? `${c.waterUsage} m³ × ${peso(c.waterRate)}/m³` : "no reading for this period"} />
            {toCents(c.other ?? "0") !== 0 && <ChargeRow label="Other charges" amount={c.other!} />}
            {toCents(c.adjustment ?? "0") !== 0 && <ChargeRow label={toCents(c.adjustment!) < 0 ? "Adjustment (credit)" : "Adjustment"} amount={c.adjustment!} minus={toCents(c.adjustment!) < 0} />}
            <ChargeRow label="Current charges" amount={c.currentCharges} strong shade />
            {toCents(c.previousBalance) !== 0 && <ChargeRow label="Previous unpaid balance" amount={c.previousBalance} strong shade
              basis={`carried from earlier statements when issued${s.previousUnpaid.length ? `; still unpaid now: ${s.previousUnpaid.map((p) => `${monthLabel(p.month)} ${peso(p.balance)}`).join("; ")}` : "; since paid"}`} />}
            {toCents(c.penalty) !== 0 && <ChargeRow label={`Late payment penalty (${pen.rate}%)`} basis={`on ${peso(pen.base)} unpaid ${pen.eligible.join(", ")}`} amount={c.penalty} />}
            {toCents(c.advanceApplied) !== 0 && <ChargeRow label="Advance payment applied" basis="prepaid condo dues" amount={c.advanceApplied} minus />}
          </tbody>
        </table>
      </div>

      <div className="mt-4 flex flex-col items-end gap-3 break-inside-avoid sm:flex-row sm:items-start sm:justify-between">
        <p className="max-w-md text-[12.5px] leading-relaxed text-ink-600"><b className="text-ink-800">Penalty.</b> {pen.explanation}
          {s.correctedByHand && <span className="block">This statement was corrected by the Admin Office.</span>}
          {s.note && <span className="block">Note: {s.note}</span>}</p>
        <dl className="w-full shrink-0 overflow-hidden rounded-lg border border-ink-200 text-[14px] sm:w-80">
          {s.issuedAmount !== null && s.issuedAmount !== s.total && <div className="flex justify-between px-4 py-1.5 text-[12.5px] text-ink-500"><dt>Amount due as first issued</dt><dd className="tabular">{peso(s.issuedAmount)}</dd></div>}
          <div className="flex justify-between px-4 py-2"><dt className="font-semibold">Total amount due</dt><dd className="font-semibold tabular">{peso(s.total)}</dd></div>
          <div className="flex justify-between px-4 py-2"><dt>Payments received</dt><dd className="tabular">{toCents(s.amountPaid) ? "− " : ""}{peso(s.amountPaid)}</dd></div>
          <div className="flex items-baseline justify-between border-t-2 border-[#0f2a3d] bg-brand-50 px-4 py-3">
            <dt className="font-bold text-[#0f2a3d]">{balance < 0 ? "Credit (overpaid)" : "Balance due"}</dt>
            <dd className="font-display text-[20px] font-bold text-[#0f2a3d] tabular">{peso(s.balance.replace("-", ""))}</dd>
          </div>
        </dl>
      </div>

      <h3 className={sectionTitle}>PAYMENTS FOR THIS STATEMENT</h3>
      {s.payments.length === 0 ? <p className="text-[13.5px] text-ink-500">No payments recorded for this statement yet.</p> : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[520px] text-[13.5px]">
            <thead><tr className="border-b-2 border-[#0f2a3d] text-left text-[11px] tracking-[0.08em] text-ink-500"><th className="py-1.5 pl-2 font-semibold">DATE</th><th className="font-semibold">OR NO.</th><th className="font-semibold">REFERENCE</th><th className="font-semibold">METHOD</th><th className="pr-2 text-right font-semibold">AMOUNT</th></tr></thead>
            <tbody>
              {s.payments.map((p, i) => (
                <tr key={i} className="border-b border-ink-100"><td className="py-2 pl-2 whitespace-nowrap">{dateLabel(p.date)}</td><td className="whitespace-nowrap">{p.receiptNo ?? "—"}</td><td>{p.reference || "—"}</td>
                  <td>{[p.type, p.method].filter(Boolean).map((x) => x.charAt(0) + x.slice(1).toLowerCase()).join(" · ")}</td>
                  <td className="pr-2 text-right whitespace-nowrap tabular">{p.reversed ? <><s>{peso(p.amount)}</s> <b className="text-red-700">REVERSED</b></> : peso(p.amount)}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <h3 className={sectionTitle}>HOW TO PAY</h3>
      <p className="text-[13.5px] text-ink-700">{s.payment.instructions}</p>
      <p className="mt-1 text-[12.5px] text-ink-500">Quote statement no. <b className="text-ink-700">{s.statementNo}</b> and unit <b className="text-ink-700">{a.unitNo}</b>. For questions about this statement, contact the Admin Office.</p>
    </article>
  );
});

export function itemLabel(item: ReceiptItem) {
  if (item.kind === "bill") return `Statement of Account — ${monthLabel(item.month)}`;
  if (item.kind === "water") return `Water — ${monthLabel(item.month)}`;
  return `Advance condo dues from ${monthLabel(item.month)}`;
}

export function ReceiptDocument({ corporation, address, unitNo, receivedFrom, receipt: r }: {
  corporation: string; address: string; unitNo: string; receivedFrom: string;
  receipt: { receiptNo: string; date: string; amount: string; method: string; reference: string; remarks?: string; receivedBy?: string; backfilled?: boolean; voided?: boolean; voidReason?: string; items: ReceiptItem[] };
}) {
  return (
    <article className={sheet} aria-label="Official receipt">
      {r.voided && <p className="mb-4 rounded-lg border border-red-200 bg-red-50 px-4 py-2 font-semibold text-red-800">VOID — {r.voidReason || "this receipt was cancelled"}. The payment it recorded was reversed.</p>}
      <header className="flex items-start justify-between gap-4 border-b-[3px] border-brand-600 pb-3">
        <div><h2 className="text-[18px] font-bold text-ink-900">{corporation}</h2>{address && <p className="text-ink-500">{address}</p>}</div>
        <div className="text-right"><p className="text-[11px] font-semibold tracking-[0.12em] text-ink-500">OFFICIAL RECEIPT</p><p className="font-display text-[18px] font-bold whitespace-nowrap text-ink-900">{r.receiptNo}</p></div>
      </header>
      <Meta items={[["RECEIVED FROM", receivedFrom || "—"], ["UNIT", unitNo], ["DATE", dateLabel(r.date)], ["AMOUNT", peso(r.amount)]]} />
      <p>Payment method: <b>{r.method}</b>{r.reference && <> · Ref. {r.reference}</>}</p>
      {r.remarks && <p className="text-ink-500">Remarks: {r.remarks}</p>}
      <h3 className="mt-6 mb-1 text-[11.5px] font-bold tracking-[0.12em] text-brand-600">IN PAYMENT OF</h3>
      <table className="w-full text-[13.5px]">
        <tbody>
          {r.items.map((it, i) => <Line key={i} label={itemLabel(it)} amount={it.amount} />)}
          <tr className="bg-brand-50 font-bold"><td className="border-t-2 border-ink-800 px-2 py-2.5">TOTAL</td><td className="border-t-2 border-ink-800 px-2 py-2.5 text-right tabular">{peso(r.amount)}</td></tr>
        </tbody>
      </table>
      <p className="mt-6 text-[12px] text-ink-500">Received by: {r.receivedBy || "—"}{r.backfilled && " · Receipt created for a payment recorded before official receipts were introduced."}</p>
    </article>
  );
}
