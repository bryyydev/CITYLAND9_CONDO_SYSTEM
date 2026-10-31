// Small bar chart: one or two series, grouped or stacked, hover/focus tooltip on every mark.
// Colours: --color-chart-1 (teal) and --color-chart-2 (copper), validated for colour-vision
// deficiency; a legend is always shown for two series, and values are in the tooltip and in the
// table each page shows next to its chart (identity is never colour alone).
import { type ReactNode, useState } from "react";
import { cx } from "./ui";

export interface Series { key: string; label: string; color: "chart-1" | "chart-2" | "muted" }
export interface BarDatum { label: string; values: Record<string, number>; detail?: ReactNode }

const FILL = { "chart-1": "bg-chart-1", "chart-2": "bg-chart-2", muted: "bg-ink-300" } as const;

function niceMax(max: number) {
  if (max <= 0) return 1;
  const pow = 10 ** Math.floor(Math.log10(max));
  return [1, 2, 2.5, 5, 10].map((m) => m * pow).find((v) => v >= max) ?? max;
}

export function BarChart({ data, series, stacked = false, format = (v) => String(v), height = 220, ariaLabel }:
  { data: BarDatum[]; series: Series[]; stacked?: boolean; format?: (v: number) => string; height?: number; ariaLabel: string }) {
  const [active, setActive] = useState<number | null>(null);
  const totals = data.map((d) => (stacked ? series.reduce((s, x) => s + (d.values[x.key] ?? 0), 0) : Math.max(...series.map((x) => d.values[x.key] ?? 0))));
  const top = niceMax(Math.max(...totals, 0));
  const shown = active === null ? null : data[active];

  return (
    <figure aria-label={ariaLabel} className="m-0">
      {series.length > 1 && (
        <figcaption className="mb-3 flex flex-wrap gap-4 text-[12.5px] text-ink-600">
          {series.map((s) => <span key={s.key} className="inline-flex items-center gap-1.5"><span className={cx("size-2.5 rounded-sm", FILL[s.color])} />{s.label}</span>)}
        </figcaption>
      )}
      <div className="relative pb-6 pl-14" style={{ height }} onMouseLeave={() => setActive(null)}>
        <div className="pointer-events-none absolute inset-0 bottom-6 left-14 flex flex-col justify-between" aria-hidden="true">
          {[top, top / 2, 0].map((v, i) => (
            <div key={i} className={cx("relative border-t", i === 2 ? "border-ink-300" : "border-ink-100")}>
              <span className="absolute top-[-8px] right-[calc(100%+8px)] text-[11px] whitespace-nowrap text-ink-500 tabular">{format(v)}</span>
            </div>
          ))}
        </div>
        <div className="relative flex h-full items-stretch gap-0.5">
          {data.map((d, i) => (
            <button key={d.label} type="button"
              className={cx("relative flex min-w-0 flex-1 cursor-default items-end justify-center rounded-md px-1 outline-none", active === i && "bg-brand-50")}
              onMouseEnter={() => setActive(i)} onFocus={() => setActive(i)} onBlur={() => setActive(null)}
              aria-label={`${d.label}: ${series.map((s) => `${s.label} ${format(d.values[s.key] ?? 0)}`).join(", ")}`}>
              {stacked ? (
                <span className="flex h-full w-full max-w-10 flex-col-reverse gap-0.5">
                  {series.map((s) => (
                    <span key={s.key} className={cx("block w-full first:rounded-b-none last:rounded-t", FILL[s.color])}
                      style={{ height: `${((d.values[s.key] ?? 0) / top) * 100}%` }} />
                  ))}
                </span>
              ) : (
                <span className="flex h-full w-full max-w-16 items-end justify-center gap-0.5">
                  {series.map((s) => (
                    <span key={s.key} className={cx("block min-h-0.5 w-full max-w-7 rounded-t", FILL[s.color])}
                      style={{ height: `${((d.values[s.key] ?? 0) / top) * 100}%` }} />
                  ))}
                </span>
              )}
              <span className="absolute top-[calc(100%+6px)] text-[11.5px] whitespace-nowrap text-ink-500">{d.label}</span>
            </button>
          ))}
        </div>
        {shown && active !== null && (
          <div role="status" className="pointer-events-none absolute top-0 z-10 -translate-x-1/2 rounded-lg border border-ink-200 bg-white px-3 py-2 text-[12.5px] shadow-pop"
            style={{ left: `calc(3.5rem + (100% - 3.5rem) * ${(active + 0.5) / data.length})` }}>
            <p className="font-semibold text-ink-900">{shown.detail ?? shown.label}</p>
            {series.map((s) => (
              <p key={s.key} className="flex items-center gap-1.5 whitespace-nowrap text-ink-600">
                <span className={cx("size-2 rounded-sm", FILL[s.color])} />{s.label}: <b className="text-ink-900 tabular">{format(shown.values[s.key] ?? 0)}</b>
              </p>
            ))}
          </div>
        )}
      </div>
    </figure>
  );
}
