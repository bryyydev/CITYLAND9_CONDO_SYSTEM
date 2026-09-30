// Money helpers. Amounts are strings with two decimals ("1234.50") everywhere in the app.
// Arithmetic (only needed by the prototype data and previews) is done in whole centavos,
// never in floating-point pesos.
import type { Money } from "../services/types";

export const toCents = (value: Money | number | null | undefined): number => {
  if (value === null || value === undefined || value === "") return 0;
  const text = typeof value === "number" ? value.toFixed(2) : String(value).replace(/,/g, "").trim();
  if (!/^-?\d+(\.\d+)?$/.test(text)) return 0;
  const negative = text.startsWith("-");
  const [whole, frac = ""] = text.replace("-", "").split(".");
  const cents = Number(whole) * 100 + Number((frac + "00").slice(0, 2)) + (Number(frac[2] ?? 0) >= 5 ? 1 : 0);
  return negative ? -cents : cents;
};

export const fromCents = (cents: number): Money => {
  const sign = cents < 0 ? "-" : "";
  const abs = Math.abs(Math.round(cents));
  return `${sign}${Math.floor(abs / 100)}.${String(abs % 100).padStart(2, "0")}`;
};

/** Is `text` a valid positive peso amount with at most two decimals? */
export const isAmount = (text: string): boolean => /^\d{1,9}(\.\d{1,2})?$/.test(text.replace(/,/g, "").trim()) && toCents(text) > 0;

const pesoFormat = new Intl.NumberFormat("en-PH", { style: "currency", currency: "PHP" });

/** Format a server amount for display: "1234.5" -> "₱1,234.50". */
export const peso = (amount: Money | number | null | undefined): string => pesoFormat.format(toCents(amount ?? 0) / 100);

/** Compact form for charts and KPI tiles: ₱1.2M, ₱845K. */
export const pesoShort = (amount: Money | number): string => {
  const v = toCents(amount) / 100;
  if (Math.abs(v) >= 1_000_000) return `₱${(v / 1_000_000).toFixed(1)}M`;
  if (Math.abs(v) >= 10_000) return `₱${Math.round(v / 1000)}K`;
  return peso(amount);
};
