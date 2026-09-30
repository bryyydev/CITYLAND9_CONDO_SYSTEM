// Philippine payroll PREVIEW (monthly): SSS, PhilHealth, Pag-IBIG and BIR withholding tax.
//
// This is a planning tool only. The official payroll is computed by the server from the
// rules saved under Payroll Rules; figures here must never be posted as payroll.
// Rates below follow the 2025 schedules (SSS Circular 2024-006, PhilHealth 5% premium,
// Pag-IBIG HDMF Circular 460, BIR TRAIN withholding table effective 2023). Verify them
// against the latest circulars before relying on a preview.
import { fromCents, toCents } from "./money";
import type { Money } from "../services/types";

export const RULES = {
  sss: { rate: 0.15, employeeRate: 0.05, employerRate: 0.10, minMsc: 5_000, maxMsc: 35_000, step: 500, ecLow: 10, ecHigh: 30, ecThreshold: 15_000 },
  philhealth: { rate: 0.05, floor: 10_000, ceiling: 100_000 },
  pagibig: { employeeRateLow: 0.01, lowThreshold: 1_500, employeeRate: 0.02, employerRate: 0.02, maxBase: 10_000 },
  // Monthly withholding table (TRAIN, 2023 onward): [over, base tax, rate on excess]
  bir: [
    [666_667, 183_541.80, 0.35],
    [166_667, 33_541.80, 0.30],
    [66_667, 8_541.80, 0.25],
    [33_333, 1_875, 0.20],
    [20_833, 0, 0.15],
  ] as const,
} as const;

/** SSS Monthly Salary Credit: nearest ₱500 bracket between ₱5,000 and ₱35,000. */
export function sssMsc(monthly: number): number {
  const { minMsc, maxMsc, step } = RULES.sss;
  if (monthly < minMsc + step / 2) return minMsc;
  return Math.min(maxMsc, Math.round(monthly / step) * step);
}

export interface PayrollInput {
  basicMonthly: number;
  overtimePay: number;
  taxableAllowances: number;
  nonTaxableAllowances: number;
  lateUndertimeDeduction: number;
  loanDeductions: number;
}

export interface PayrollPreview {
  gross: Money;
  sss: { employee: Money; employer: Money; msc: number };
  philhealth: { employee: Money; employer: Money };
  pagibig: { employee: Money; employer: Money };
  taxableIncome: Money;
  withholdingTax: Money;
  totalDeductions: Money;
  netPay: Money;
}

const c = (pesos: number) => Math.round(pesos * 100);

export function previewPayroll(input: PayrollInput): PayrollPreview {
  const basic = Math.max(input.basicMonthly - input.lateUndertimeDeduction, 0);
  const taxableGross = basic + input.overtimePay + input.taxableAllowances;
  const gross = taxableGross + input.nonTaxableAllowances;

  // Contributions are based on the monthly basic salary.
  const msc = sssMsc(input.basicMonthly);
  const sssEe = c(msc * RULES.sss.employeeRate);
  const sssEr = c(msc * RULES.sss.employerRate + (msc < RULES.sss.ecThreshold ? RULES.sss.ecLow : RULES.sss.ecHigh));

  const phBase = Math.min(Math.max(input.basicMonthly, RULES.philhealth.floor), RULES.philhealth.ceiling);
  const phTotal = c(phBase * RULES.philhealth.rate);
  const phEe = Math.floor(phTotal / 2);
  const phEr = phTotal - phEe;

  const hdmfBase = Math.min(input.basicMonthly, RULES.pagibig.maxBase);
  const hdmfEeRate = input.basicMonthly <= RULES.pagibig.lowThreshold ? RULES.pagibig.employeeRateLow : RULES.pagibig.employeeRate;
  const hdmfEe = c(hdmfBase * hdmfEeRate);
  const hdmfEr = c(hdmfBase * RULES.pagibig.employerRate);

  // Mandatory employee contributions are excluded from taxable compensation.
  const taxable = Math.max(c(taxableGross) - sssEe - phEe - hdmfEe, 0);
  const taxablePesos = taxable / 100;
  const bracket = RULES.bir.find(([over]) => taxablePesos >= over);
  const tax = bracket ? c(bracket[1] + (taxablePesos - bracket[0]) * bracket[2]) : 0;

  const deductions = sssEe + phEe + hdmfEe + tax + c(input.loanDeductions);
  return {
    gross: fromCents(c(gross)),
    sss: { employee: fromCents(sssEe), employer: fromCents(sssEr), msc },
    philhealth: { employee: fromCents(phEe), employer: fromCents(phEr) },
    pagibig: { employee: fromCents(hdmfEe), employer: fromCents(hdmfEr) },
    taxableIncome: fromCents(taxable),
    withholdingTax: fromCents(tax),
    totalDeductions: fromCents(deductions),
    netPay: fromCents(c(gross) - deductions),
  };
}

export const netOf = (p: PayrollPreview) => toCents(p.netPay);
