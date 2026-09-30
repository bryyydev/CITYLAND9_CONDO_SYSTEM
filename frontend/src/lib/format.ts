// Date display helpers. The server stores timestamps in UTC; people read Philippine time.
import type { IsoDate, IsoDateTime, Month } from "../services/types";

export const TIME_ZONE = "Asia/Manila";

const ymd = new Intl.DateTimeFormat("en-CA", { timeZone: TIME_ZONE, year: "numeric", month: "2-digit", day: "2-digit" });

/** Today's date in Manila as YYYY-MM-DD. */
export const todayIso = (): IsoDate => ymd.format(new Date());
export const currentMonth = (): Month => todayIso().slice(0, 7);

export const addDays = (iso: IsoDate, days: number): IsoDate => {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
};

export const addMonths = (month: Month, n: number): Month => {
  const [y, m] = month.split("-").map(Number);
  const d = new Date(Date.UTC(y, m - 1 + n, 1));
  return d.toISOString().slice(0, 7);
};

export function monthLabel(month: Month | null | undefined, style: "long" | "short" = "long"): string {
  if (!month) return "—";
  const [y, m] = month.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, 1)).toLocaleDateString("en-PH", { month: style, year: "numeric", timeZone: "UTC" });
}

export function shortMonth(month: Month): string {
  const [y, m] = month.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, 1)).toLocaleDateString("en-PH", { month: "short", timeZone: "UTC" });
}

export function dateLabel(iso: IsoDate | null | undefined): string {
  if (!iso) return "—";
  return new Date(`${iso.slice(0, 10)}T00:00:00Z`).toLocaleDateString("en-PH", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });
}

/** UTC timestamp from the server -> "Oct 1, 2026, 9:54 AM" in Manila time. */
export function dateTimeLabel(iso: IsoDateTime | null | undefined): string {
  if (!iso) return "—";
  const utc = /[zZ]|[+-]\d\d:\d\d$/.test(iso) ? iso : `${iso}Z`;
  return new Date(utc).toLocaleString("en-PH", { timeZone: TIME_ZONE, month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit" });
}

export const initials = (name: string): string =>
  name.split(/[\s._-]+/).filter(Boolean).slice(0, 2).map((p) => p[0]!.toUpperCase()).join("");
