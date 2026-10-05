// Prototype (mock) payroll: a copy of the SERVER's computation (backend/legacy_app.py _v1040_calc and
// _v1039_calc_payroll) so the prototype shows the same figures the live system saves. Live pages never
// use this: they ask the server, which computes with the Payroll Rules saved there.
import type { Statutory } from "../services/types";

/** Payroll Rules defaults (legacy HR_SETTING_DEFAULTS). */
export const RULE_DEFAULTS: Record<string, string> = {
  hr_working_days: "26", hr_work_hours_per_day: "8", hr_grace_minutes: "5",
  hr_sss_employee_rate: "5", hr_sss_employer_rate: "10", hr_sss_max_msc: "35000",
  hr_sss_ec_threshold: "14500", hr_sss_ec_low: "10", hr_sss_ec_high: "30",
  hr_philhealth_rate: "5", hr_philhealth_min_base: "10000", hr_philhealth_max_base: "100000", hr_philhealth_employee_share: "50",
  hr_pagibig_max_base: "5000", hr_pagibig_low_rate: "1", hr_pagibig_high_rate: "2", hr_pagibig_employer_rate: "2",
  hr_bir_exempt_threshold: "20833", hr_bir_bracket2: "33332", hr_bir_bracket3: "66666", hr_bir_bracket4: "166666", hr_bir_bracket5: "666666",
  hr_bir_rate2: "15", hr_bir_rate3: "20", hr_bir_rate4: "25", hr_bir_rate5: "30", hr_bir_rate6: "35", hr_13th_month_ceiling: "90000",
};

const round2 = (n: number) => Math.round((n + Number.EPSILON) * 100) / 100;
const m = (n: number) => round2(n).toFixed(2);

export function statutory(rules: Record<string, string>, grossIn: number, basicIn: number, other = 0, absences = 0, lateUndertime = 0): Statutory {
  const r = (k: string) => Number(rules[k] ?? RULE_DEFAULTS[k]);
  const gross = Math.max(grossIn, 0), basic = Math.max(basicIn, 0);
  const msc = Math.min(gross, r("hr_sss_max_msc"));
  const sssEe = round2(msc * r("hr_sss_employee_rate") / 100), sssEr = round2(msc * r("hr_sss_employer_rate") / 100);
  const sssEc = msc <= r("hr_sss_ec_threshold") ? r("hr_sss_ec_low") : r("hr_sss_ec_high");
  const phBase = Math.min(Math.max(gross, r("hr_philhealth_min_base")), r("hr_philhealth_max_base"));
  const phTotal = round2(phBase * r("hr_philhealth_rate") / 100);
  const phEe = round2(phTotal * r("hr_philhealth_employee_share") / 100), phEr = round2(phTotal - phEe);
  const pib = Math.min(gross, r("hr_pagibig_max_base"));
  const piEe = round2(pib * (pib <= 1500 ? r("hr_pagibig_low_rate") : r("hr_pagibig_high_rate")) / 100);
  const piEr = round2(pib * r("hr_pagibig_employer_rate") / 100);
  const taxable = Math.max(gross - sssEe - phEe - piEe, 0);
  const [exempt, b2, b3, b4, b5] = ["hr_bir_exempt_threshold", "hr_bir_bracket2", "hr_bir_bracket3", "hr_bir_bracket4", "hr_bir_bracket5"].map(r);
  let tax = 0;
  if (taxable <= exempt) tax = 0;
  else if (taxable <= b2) tax = (taxable - exempt) * r("hr_bir_rate2") / 100;
  else if (taxable <= b3) tax = 1875 + (taxable - b2) * r("hr_bir_rate3") / 100;
  else if (taxable <= b4) tax = 8541.8 + (taxable - b3) * r("hr_bir_rate4") / 100;
  else if (taxable <= b5) tax = 33541.8 + (taxable - b4) * r("hr_bir_rate5") / 100;
  else tax = 183541.8 + (taxable - b5) * r("hr_bir_rate6") / 100;
  tax = round2(Math.max(tax, 0));
  const ded = round2(sssEe + phEe + piEe + tax + other + absences + lateUndertime);
  return {
    sssEmployee: m(sssEe), sssEmployer: m(sssEr), sssEcEmployer: m(sssEc), philhealthEmployee: m(phEe), philhealthEmployer: m(phEr),
    pagibigEmployee: m(piEe), pagibigEmployer: m(piEr), withholdingTax: m(tax), thirteenthMonth: m(basic / 12),
    grossPay: m(gross), totalEmployeeDeductions: m(ded), totalEmployerCost: m(sssEr + sssEc + phEr + piEr), netPay: m(gross - ded),
  };
}

/** Absence and late/undertime deductions for a period (server: _v1039_calc_payroll). */
export function attendanceDeductions(rules: Record<string, string>, monthly: number, rows: { status: string; timeIn: string | null; timeOut: string | null }[]) {
  const r = (k: string) => Number(rules[k] ?? RULE_DEFAULTS[k]);
  const days = Math.max(r("hr_working_days"), 1), hours = Math.max(r("hr_work_hours_per_day"), 1), grace = Math.max(r("hr_grace_minutes"), 0);
  const absent = rows.filter((x) => x.status.toUpperCase() === "ABSENT").length;
  const hourly = monthly / days / hours;
  let minutes = 0;
  for (const x of rows) minutes += lateMinutes(x, grace) + undertimeMinutes(x);
  return { absences: round2((monthly / days) * absent), lateUndertime: round2(hourly * minutes / 60) };
}

const toMin = (t: string) => Number(t.slice(0, 2)) * 60 + Number(t.slice(3, 5));
export function lateMinutes(x: { status: string; timeIn: string | null }, grace: number) {
  return x.timeIn && ["LATE", "LATE/UNDERTIME", "PRESENT"].includes(x.status.toUpperCase()) ? Math.max(toMin(x.timeIn) - 8 * 60 - grace, 0) : 0;
}
export function undertimeMinutes(x: { status: string; timeOut: string | null }) {
  return x.timeOut && ["UNDERTIME", "LATE/UNDERTIME"].includes(x.status.toUpperCase()) ? Math.max(17 * 60 - toMin(x.timeOut), 0) : 0;
}
