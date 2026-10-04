// Printable Statement of Account and Official Receipt. Always white paper colours, so the PDF
// and the printout look the same in any theme. Figures come from the server as-is.
import { forwardRef } from "react";
import { dateLabel, monthLabel } from "../../lib/format";
import { peso, toCents } from "../../lib/money";
import type { ReceiptItem, SoaDetail } from "../../services/types";
import { StatusBadge } from "../base/ui";

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

const sheet = "print-sheet mx-auto max-w-[860px] rounded-xl border border-ink-200 bg-white p-6 text-ink-800 shadow-card sm:p-9";

export const SoaDocument = forwardRef<HTMLElement, { corporation: string; unitNo: string; statement: SoaDetail }>(function SoaDocument({ corporation, unitNo, statement: s }, ref) {
  const c = s.charges;
  const month = monthLabel(s.billingMonth).toUpperCase();
  return (
    <article ref={ref} className={sheet} aria-label="Statement of account">
      <header className="flex items-start justify-between gap-4 border-b-[3px] border-brand-600 pb-3">
        <div><h2 className="text-[18px] font-bold text-ink-900">{corporation}</h2><p className="text-ink-500">Statement of Account</p></div>
        <StatusBadge status={s.status} />
      </header>
      <Meta items={[["UNIT", unitNo], ["BILLING PERIOD", monthLabel(s.billingMonth)], ["DUE DATE", dateLabel(s.dueDate)], ["BALANCE", peso(s.balance)]]} />
      <h3 className="mt-6 mb-1 text-[11.5px] font-bold tracking-[0.12em] text-brand-600">CHARGES</h3>
      <table className="w-full text-[13.5px]">
        <tbody>
          <Line label={`${month} CONDO DUES`} amount={c.condoDues} />
          {toCents(c.parking) > 0 && <Line label="Parking" amount={c.parking} />}
          {toCents(c.storage) > 0 && <Line label="Storage" amount={c.storage} detail={c.storageIncluded ? undefined : `Not included in this total (storage is charged from ${monthLabel(c.storageFrom)})`} />}
          <Line label={`${month} WATER`} amount={c.water}
            detail={c.waterPaidSeparately ? "Paid separately, not included in the total" : c.waterUsage !== null ? `${c.waterUsage} m³ × ${peso(c.waterRate)}/m³` : "No reading for this period"} />
          {toCents(c.other ?? "0") !== 0 && <Line label="Other charges" amount={c.other!} />}
          {toCents(c.adjustment ?? "0") > 0 && <Line label="Adjustment" amount={c.adjustment!} />}
          {toCents(c.adjustment ?? "0") < 0 && <Line label="Adjustment" detail="Credit" amount={c.adjustment!.replace("-", "")} negative />}
          {toCents(c.previousBalance) > 0 && <Line label="Previous balance" detail="Unpaid from earlier statements" amount={c.previousBalance} />}
          {toCents(c.penalty) > 0 && <Line label="Penalty" detail="Late payment" amount={c.penalty} />}
          {toCents(c.advanceApplied) > 0 && <Line label="Advance payment applied" amount={c.advanceApplied} negative />}
          <tr className="bg-brand-50 font-bold"><td className="border-t-2 border-ink-800 px-2 py-2.5">TOTAL AMOUNT DUE</td><td className="border-t-2 border-ink-800 px-2 py-2.5 text-right tabular">{peso(s.total)}</td></tr>
          <Line label="Amount paid" amount={s.amountPaid} negative />
          <tr className="text-[16px] font-bold"><td className="py-2.5">BALANCE</td><td className="py-2.5 text-right tabular">{peso(s.balance)}</td></tr>
        </tbody>
      </table>
      {s.note && <p className="mt-2 text-[12.5px] text-ink-500">Note: {s.note}</p>}
      <h3 className="mt-6 mb-1 text-[11.5px] font-bold tracking-[0.12em] text-brand-600">PAYMENTS</h3>
      {s.payments.length === 0 ? <p className="text-ink-500">No payments recorded for this statement.</p> : (
        <table className="w-full text-[13px]">
          <thead><tr className="text-left text-[11px] tracking-wide text-ink-500"><th className="py-1.5">DATE</th><th>OR NO.</th><th>REFERENCE</th><th>METHOD</th><th className="text-right">AMOUNT</th></tr></thead>
          <tbody>
            {s.payments.map((p, i) => (
              <tr key={i} className="border-t border-ink-100"><td className="py-2">{dateLabel(p.date)}</td><td>{p.receiptNo ?? "—"}</td><td>{p.reference || "—"}</td><td>{p.type} · {p.method}</td><td className="text-right tabular">{p.reversed ? <><s>{peso(p.amount)}</s> <b className="text-red-700">REVERSED</b></> : peso(p.amount)}</td></tr>
            ))}
          </tbody>
        </table>
      )}
      <p className="mt-6 text-[12px] text-ink-500">Pay at the Admin Office by cash, check or online transfer. For questions about this statement, please contact the Admin Office.</p>
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
