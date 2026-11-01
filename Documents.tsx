// Printable Statement of Account and Official Receipt. Always white paper colours, so the PDF
// and the printout look the same in any theme. Figures come from the server as-is.
import { forwardRef } from "react";
import { dateLabel, longDateLabel, monthLabel } from "../../lib/format";
import { fromCents, peso, toCents } from "../../lib/money";
import type { ChargeBasis, ReceiptItem, SoaDetail } from "../../services/types";

const sheet = "print-sheet mx-auto max-w-[860px] rounded-xl border border-ink-200 bg-white p-5 text-ink-800 shadow-card sm:p-8";

export async function printSoaDocument(element: HTMLElement | null, title: string) {
  if (!element) throw new Error("The SOA document is not ready to print.");
  await printSoaDocuments([element], title);
}

export async function printSoaDocuments(elements: HTMLElement[], title: string) {
  if (!elements.length) throw new Error("There are no SOA documents to print.");
  const frame = document.createElement("iframe");
  frame.title = title;
  frame.style.cssText = "position:fixed;left:-10000px;top:0;width:210mm;height:297mm;border:0";
  document.body.append(frame);

  const printWindow = frame.contentWindow;
  const printDocument = frame.contentDocument;
  if (!printWindow || !printDocument) {
    frame.remove();
    throw new Error("The print document could not be created.");
  }

  printDocument.open();
  printDocument.write("<!doctype html><html><head><meta charset=\"utf-8\"><title></title></head><body></body></html>");
  printDocument.close();
  printDocument.title = title;

  try {
    const stylesheetLoads = [...document.querySelectorAll<HTMLLinkElement>('link[rel="stylesheet"]')].map((source) => new Promise<void>((resolve, reject) => {
      const link = printDocument.createElement("link");
      link.rel = "stylesheet";
      link.href = source.href;
      link.onload = () => resolve();
      link.onerror = () => reject(new Error(`Couldn't load print stylesheet: ${source.href}`));
      printDocument.head.append(link);
    }));
    document.querySelectorAll("style").forEach((source) => printDocument.head.append(source.cloneNode(true)));
    const printStyles = printDocument.createElement("style");
    printStyles.textContent = `
      @page { size: A4 portrait; margin: 10mm; }
      html, body { height: auto !important; min-height: 0 !important; margin: 0 !important; padding: 0 !important; overflow: visible !important; }
      body { background: #fff !important; }
      body * { visibility: visible !important; animation: none !important; transform: none !important; }
      .print-sheet { position: static !important; display: block !important; width: 100% !important; max-width: none !important; box-sizing: border-box !important; margin: 0 !important; padding: 2rem !important; box-shadow: none !important; overflow: visible !important; -webkit-print-color-adjust: exact !important; print-color-adjust: exact !important; }
      .print-sheet:not(:last-child) { break-after: page !important; page-break-after: always !important; }
      .print-sheet *, .print-sheet *::before, .print-sheet *::after { box-sizing: border-box !important; }
      .print-sheet thead { display: table-header-group !important; }
      .soa-reminders li, .print-sheet tr { break-inside: avoid !important; page-break-inside: avoid !important; }
    `;
    printDocument.head.append(printStyles);
    elements.forEach((element) => printDocument.body.append(element.cloneNode(true)));

    await Promise.all(stylesheetLoads);
    await printDocument.fonts.ready;

    printWindow.addEventListener("afterprint", () => frame.remove(), { once: true });
    printWindow.focus();
    printWindow.print();
  } catch (error) {
    frame.remove();
    throw error;
  }
}

export async function downloadSoaDocument(element: HTMLElement | null, filename: string) {
  if (!element) throw new Error("The SOA document is not ready to download.");
  const html2pdf = (await import("html2pdf.js")).default;
  await html2pdf().set({
    margin: 10,
    filename,
    image: { type: "jpeg", quality: 0.98 },
    html2canvas: { scale: 2, backgroundColor: "#ffffff", useCORS: true },
    jsPDF: { unit: "mm", format: "a4", orientation: "portrait" },
  }).from(element).save();
}

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
    <div className="my-4 grid grid-cols-3 gap-px overflow-hidden rounded-md border border-ink-100 bg-ink-100">
      {items.map(([k, v]) => (
        <div key={k} className="min-w-0 bg-[#f4f7f8] px-3 py-2">
          <div className="text-[10.5px] font-bold text-brand-700 uppercase">{k}</div>
          <div className="mt-0.5 font-semibold text-ink-900 tabular">{v}</div>
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

/** The Statement of Account used for viewing, printing, and downloading by residents and staff. */
export const SoaDocument = forwardRef<HTMLElement, { statement: SoaDetail; corporation?: string; unitNo?: string }>(function SoaDocument({ statement: s }, ref) {
  const c = s.charges, a = s.account;
  const period = monthLabel(s.billingMonth);
  const waterReadingPeriod = c.waterReadingMonth ? monthLabel(c.waterReadingMonth) : period;
  const waterDateRange = c.waterReadingFrom && c.waterReadingTo
    ? `Water reading: ${longDateLabel(c.waterReadingFrom)} to ${longDateLabel(c.waterReadingTo)}`
    : `Water reading: ${waterReadingPeriod}`;
  const waterUsage = c.waterUsage === null ? "" : String(Number(c.waterUsage.toFixed(2)));
  const waterMeasure = waterUsage && c.waterRate !== null ? `${waterUsage} m³ × ${peso(c.waterRate)}/m³` : "";
  const waterAllocation = c.waterAllocation;
  const waterBasis = c.waterReadingMonth
    ? waterAllocation
      ? `Shared ${waterAllocation.commercialType} reading · ${waterDateRange}: ${waterUsage} m³ × ${peso(c.waterRate ?? "0")}/m³ = ${peso(waterAllocation.sharedAmount)} · ${waterAllocation.unitArea} of ${waterAllocation.totalArea} sqm; ${peso(waterAllocation.unitAmount)} allocated`
      : `${waterDateRange}${waterMeasure ? ` · ${waterMeasure}` : ""}${c.waterPaidSeparately ? "; paid separately; not included in this total" : ""}`
    : "no reading for this period";
  const currentRows: { label: string; detail: string; amount: string; negative?: boolean }[] = [
    { label: "Association Dues", detail: `${dateLabel(s.periodStart)} – ${dateLabel(s.periodEnd)} · ${basisText(s.bases.condo, s.bases.recorded)}`, amount: c.condoDues },
    ...(toCents(c.parking) !== 0 || s.bases.parking ? [{ label: "Parking", detail: basisText(s.bases.parking, s.bases.recorded), amount: c.parking }] : []),
    ...(toCents(c.storage) !== 0 || s.bases.storage ? [{ label: "Storage", detail: [basisText(s.bases.storage, s.bases.recorded), c.storageIncluded ? "" : `not included in this total (storage is charged from ${monthLabel(c.storageFrom)})`].filter(Boolean).join("; "), amount: c.storageIncluded ? c.storage : "0.00" }] : []),
    { label: `Water Consumption`, detail: waterBasis, amount: c.waterPaidSeparately ? "0.00" : c.water },
    ...(toCents(c.other ?? "0") !== 0 ? [{ label: "Other charges", detail: "", amount: c.other! }] : []),
    ...(toCents(c.adjustment ?? "0") !== 0 ? [{ label: toCents(c.adjustment!) < 0 ? "Adjustment (credit)" : "Adjustment", detail: "", amount: c.adjustment! }] : []),
    ...(toCents(c.vat) !== 0 ? [{ label: `VAT (${c.vatRate}%)`, detail: `on ${c.vatEligible.join(", ")} · taxable amount ${peso(c.vatBase)}`, amount: c.vat }] : []),
    ...(toCents(c.advanceApplied) !== 0 ? [{ label: "Advance payment applied", detail: "Prepaid condo dues", amount: c.advanceApplied, negative: true }] : []),
  ];
  const overdueRows: { label: string; detail: string; amount: string }[] = [
    ...s.previousUnpaid.map((p) => ({
      label: "Prior SOA balance",
      detail: `${monthLabel(p.month)} · ${p.statementNo} · due ${dateLabel(p.dueDate)}`,
      amount: p.balance,
    })),
    ...(toCents(c.previousBalance) !== s.previousUnpaid.reduce((sum, p) => sum + toCents(p.balance), 0)
      ? [{ label: "Unitemized carried balance", detail: "No matching earlier SOA is available in the records to break down this amount.", amount: fromCents(toCents(c.previousBalance) - s.previousUnpaid.reduce((sum, p) => sum + toCents(p.balance), 0)) }]
      : []),
    ...(toCents(c.penalty) !== 0 ? [{ label: "Account Penalty", detail: `Late payment charge (${s.penaltyInfo.rate}%)`, amount: c.penalty }] : []),
  ];
  const totalCurrent = toCents(c.currentCharges) - toCents(c.advanceApplied);
  const totalOverdue = toCents(c.previousBalance) + toCents(c.penalty);
  return (
    <article ref={ref} className={sheet} aria-label="Statement of account">
      <header className="border-b-[3px] border-[#0f2a3d] pb-3">
        <div className="flex items-start justify-between gap-4">
          <div className="flex min-w-0 flex-1 items-start gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded bg-[#0f2a3d] text-xs font-black text-white">C9</div>
            <div className="min-w-0">
              <p className="text-[18px] font-black leading-tight text-[#0f2a3d]">{s.property.name}</p>
              {s.property.address && <p className="mt-1 text-[10px] leading-snug text-ink-500">{s.property.address}</p>}
            </div>
          </div>
          <div className="min-w-[255px] border border-[#d7e2e8] bg-[#edf3f7] px-3 py-2 text-right">
            <p className="text-[11px] font-extrabold tracking-[0.18em] text-[#0f2a3d] uppercase">Billing Invoice</p>
            <p className="mt-1 text-[9px] font-medium uppercase tracking-[0.18em] text-ink-500">Association Dues and Water</p>
          </div>
        </div>
      </header>

      <div className="mt-4 grid gap-3 sm:grid-cols-[1.1fr_0.9fr]">
        <div className="rounded-md border border-ink-200 bg-[#f7f9fa] p-3">
          <p className="text-[11px] font-extrabold tracking-[0.18em] text-[#0f2a3d] uppercase">Billed to:</p>
          <div className="mt-2 space-y-0.5 text-[12px] text-ink-800">
            <div className="font-bold">{a.billTo || "—"}</div>
            <div>{a.unitNo || "—"}</div>
            <div>{s.property.name}</div>
            <div>{s.property.address || "—"}</div>
          </div>
        </div>

        <div className="overflow-hidden rounded-md border border-ink-200 bg-[#f8f8f8]">
          <div className="grid grid-cols-2 gap-px bg-ink-200 text-[11px] text-ink-700">
            <div className="bg-[#f8f8f8] px-2 py-1.5 font-bold uppercase tracking-[0.08em]">Invoice No.</div>
            <div className="bg-[#f8f8f8] px-2 py-1.5 text-right tabular">{s.statementNo}</div>
            <div className="bg-[#f8f8f8] px-2 py-1.5 font-bold uppercase tracking-[0.08em]">Billing Period</div>
            <div className="bg-[#f8f8f8] px-2 py-1.5 text-right tabular">{period}</div>
            <div className="bg-[#f8f8f8] px-2 py-1.5 font-bold uppercase tracking-[0.08em]">Unit Area</div>
            <div className="bg-[#f8f8f8] px-2 py-1.5 text-right tabular">{a.unitType || "—"}</div>
            <div className="bg-[#f8f8f8] px-2 py-1.5 font-bold uppercase tracking-[0.08em]">Payment Due</div>
            <div className="bg-[#f8f8f8] px-2 py-1.5 text-right tabular">{dateLabel(s.dueDate)}</div>
          </div>
        </div>
      </div>

      <div className="mt-6 overflow-hidden rounded-md border border-ink-300">
        <div className="bg-[#e5e7e9] px-3 py-2 text-[11px] font-extrabold tracking-[0.18em] text-ink-900 uppercase">Current Period Charges</div>
        <table className="w-full border-separate border-spacing-0 text-[12px]">
          <thead>
            <tr className="bg-[#f1f2f3] text-left text-[10px] font-bold tracking-[0.12em] text-ink-700 uppercase">
              <th className="px-3 py-2">Particulars / Description</th>
              <th className="px-3 py-2">Meter / Details</th>
              <th className="px-3 py-2 text-right">Amount (PHP)</th>
            </tr>
          </thead>
          <tbody>
            {currentRows.map((row, index) => (
              <tr key={`${row.label}-${index}`} className={`${index % 2 === 0 ? "bg-white" : "bg-ink-50"} align-top`}>
                <td className="border-t border-ink-100 px-3 py-2 font-medium text-ink-800">{row.label}</td>
                <td className="border-t border-ink-100 px-3 py-2 text-[11px] text-ink-600">{row.detail}</td>
                <td className="border-t border-ink-100 px-3 py-2 text-right font-semibold tabular text-ink-900">{row.negative && toCents(row.amount) ? "− " : ""}{peso(row.amount)}</td>
              </tr>
            ))}
            <tr className="bg-[#e5e7e9] text-[12px] font-bold text-ink-900">
              <td className="border-t border-ink-300 px-3 py-2">Total Current Charges</td>
              <td className="border-t border-ink-300 px-3 py-2"></td>
              <td className="border-t border-ink-300 px-3 py-2 text-right tabular">{peso(totalCurrent / 100)}</td>
            </tr>
          </tbody>
        </table>
      </div>

      {totalOverdue !== 0 && (
        <div className="mt-6 overflow-hidden rounded-md border border-ink-300">
          <div className="bg-[#e5e7e9] px-3 py-2 text-[11px] font-extrabold tracking-[0.18em] text-ink-900 uppercase">Overdue Balances &amp; Arrears</div>
          <table className="w-full border-separate border-spacing-0 text-[12px]">
            <thead>
              <tr className="bg-[#f1f2f3] text-left text-[10px] font-bold tracking-[0.12em] text-ink-700 uppercase">
                <th className="px-3 py-2">Prior Account Particulars</th>
                <th className="px-3 py-2">Arrears Period</th>
                <th className="px-3 py-2 text-right">Amount (PHP)</th>
              </tr>
            </thead>
            <tbody>
              {overdueRows.map((row, index) => (
                <tr key={`${row.label}-${row.detail}-${index}`} className={`${index % 2 === 0 ? "bg-white" : "bg-ink-50"} align-top`}>
                  <td className="border-t border-ink-100 px-3 py-2 font-medium text-ink-800">{row.label}</td>
                  <td className="border-t border-ink-100 px-3 py-2 text-[11px] text-ink-600">{row.detail}</td>
                  <td className="border-t border-ink-100 px-3 py-2 text-right font-semibold tabular text-ink-900">{peso(row.amount)}</td>
                </tr>
              ))}
              <tr className="bg-[#e5e7e9] text-[12px] font-bold text-ink-900">
                <td className="border-t border-ink-300 px-3 py-2">Total Overdue Balance</td>
                <td className="border-t border-ink-300 px-3 py-2"></td>
                <td className="border-t border-ink-300 px-3 py-2 text-right tabular">{peso(totalOverdue / 100)}</td>
              </tr>
            </tbody>
          </table>
        </div>
      )}

      <div className="mt-6 space-y-3">
        <div className="grid gap-2 text-[12px] font-semibold text-ink-900 sm:grid-cols-2">
          <div className="rounded-md border border-ink-300 bg-[#f1f2f3] px-3 py-2">Total Current Charges: <span className="float-right tabular">{peso(totalCurrent / 100)}</span></div>
          {totalOverdue !== 0 && <div className="rounded-md border border-ink-300 bg-[#f1f2f3] px-3 py-2">Total Overdue Balance &amp; Penalties: <span className="float-right tabular">{peso(totalOverdue / 100)}</span></div>}
        </div>
        <div className="rounded-md bg-[#e5e7e9] px-4 py-3 text-right text-[12px] font-extrabold uppercase tracking-[0.12em] text-ink-900">
          Total Amount Due: <span className="ml-3 text-[20px] font-black tabular">{peso(s.total)}</span>
        </div>
      </div>

      <section className="soa-reminders mt-6 border-t-2 border-[#0f2a3d] pt-4 text-[10.5px] leading-relaxed text-ink-700">
        <h3 className="mb-2 text-[12px] font-extrabold tracking-[0.12em] text-[#0f2a3d]">REMINDERS &amp; PAYMENT POLICIES</h3>
        <ol className="list-decimal space-y-1 pl-5">
          <li>Payments shall be accepted at the <b>Administration Office</b> located at the UG28, CITYLAND DELA ROSA CONDOMINIUM INCORPORATED.</li>
          <li>Full payment must be made <b>on or before every 8th of the month</b> to avoid a further <b>4% penalty per month</b>, wherein a fraction thereof shall be deemed equivalent to one month.</li>
          <li>Issue your check in favor of: <b>CITYLAND DELA ROSA CONDOMINIUM INCORPORATED.</b></li>
          <li>For cash payment, we encourage exact amount to expedite O.R. issuance and facilitate deposit. Please bring this statement of account when paying.</li>
          <li>Penalty will be collected for non-payment on due dates even if the reason is non-receipt or delayed receipt of the bill. If you fail to receive your bill on or before the <b>8th of every month</b>, call the <b>Admin Office at 02-8559-9048</b>.</li>
          <li>The Association shall not be liable for cash/check payments given to the guards.</li>
        </ol>
        <p className="mt-2 font-semibold">Any and all disconnection notice(s) received prior to this Statement of Account shall remain valid and effective unless and until the arrears indicated in said notice(s) are fully settled.</p>
      </section>
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
