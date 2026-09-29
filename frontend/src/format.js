// Display helpers. Amounts arrive from the server as strings ("1234.50") and are
// only formatted here; the server's figures are the authoritative ones.

const pesoFormat = new Intl.NumberFormat("en-PH", { style: "currency", currency: "PHP" });

export const peso = (amount) => pesoFormat.format(Number(amount || 0));

export function monthLabel(yyyyMm) {
  if (!yyyyMm) return "—";
  const [y, m] = yyyyMm.split("-").map(Number);
  return new Date(y, m - 1, 1).toLocaleDateString("en-PH", { month: "long", year: "numeric" });
}

export function dateLabel(iso) {
  if (!iso) return "—";
  return new Date(`${iso.slice(0, 10)}T00:00:00`).toLocaleDateString("en-PH", { month: "short", day: "numeric", year: "numeric" });
}

const BADGE = { Paid: "b-paid", "Partially Paid": "b-partial", Overdue: "b-overdue", Unpaid: "b-unpaid",
  Open: "b-unpaid", "In Progress": "b-partial", Resolved: "b-paid", Closed: "b-paid" };

export const badgeClass = (status) => `badge ${BADGE[status] || "b-unpaid"}`;
