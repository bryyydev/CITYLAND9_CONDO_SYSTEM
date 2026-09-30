import type { ReactNode } from "react";
import type { ApiError } from "../../services/http";
import { EmptyState, ErrorState, Skeleton, cx } from "./ui";

export interface Column<R> {
  key: string;
  header: ReactNode;
  cell: (row: R) => ReactNode;
  align?: "right";
  className?: string;
}

/** Table with the standard loading / error / empty states and a horizontally scrolling body on phones. */
export function DataTable<R>({ columns, rows, rowKey, loading, error, onRetry, empty, onRowClick, footer }: {
  columns: Column<R>[];
  rows: R[] | null;
  rowKey: (row: R) => string | number;
  loading?: boolean;
  error?: ApiError | null;
  onRetry?: () => void;
  empty?: { icon?: string; title: string; text?: string };
  onRowClick?: (row: R) => void;
  footer?: ReactNode;
}) {
  if (error) return <ErrorState title="Couldn't load this list" message={error.message} onRetry={onRetry} />;
  if (loading && !rows) return <Skeleton rows={6} />;
  if (!rows || rows.length === 0) return <EmptyState icon={empty?.icon} title={empty?.title ?? "Nothing here yet"}>{empty?.text}</EmptyState>;
  return (
    <div className={cx("overflow-x-auto", loading && "opacity-60 transition-opacity")}>
      <table className="w-full border-collapse text-[13.5px]">
        <thead>
          <tr>{columns.map((c) => <th key={c.key} className={cx("th", c.align === "right" && "text-right", c.className)}>{c.header}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={rowKey(r)} onClick={onRowClick ? () => onRowClick(r) : undefined}
              className={cx("transition-colors", onRowClick && "cursor-pointer hover:bg-brand-50/50")}>
              {columns.map((c) => <td key={c.key} className={cx("td", c.align === "right" && "num", c.className)}>{c.cell(r)}</td>)}
            </tr>
          ))}
        </tbody>
        {footer && <tfoot>{footer}</tfoot>}
      </table>
    </div>
  );
}
