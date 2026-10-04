// Audit Logs (Superadmin, Accounting). Live: /api/admin/audit-logs. Read-only: the server has no
// route that edits or deletes entries. Filters run on the server; the CSV export covers the whole
// filtered result, not just the page on screen.
import { Fragment, useEffect, useState } from "react";
import { useToast } from "../../components/base/overlays";
import { Button, Card, EmptyState, ErrorState, Field, Icon, IconButton, PageHeader, Skeleton, cx } from "../../components/base/ui";
import { useDebounced } from "../../components/feature/AccountFields";
import { useAsync } from "../../hooks/useAsync";
import { TIME_ZONE, dateTimeLabel } from "../../lib/format";
import { api } from "../../services/api";
import type { AuditLog } from "../../services/types";

const PER_PAGE = 50;
const EMPTY = { q: "", user: "", from: "", to: "" };

/** Before/after values as a small table when the entry recorded them that way; otherwise formatted JSON. */
function Details({ entry }: { entry: AuditLog }) {
  const d = entry.details as Record<string, unknown> | null | undefined;
  const pairs = d && typeof d === "object" && !Array.isArray(d)
    ? Object.entries(d).filter(([, v]) => v && typeof v === "object" && "before" in (v as object) && "after" in (v as object)) as [string, { before: unknown; after: unknown }][]
    : [];
  const show = (v: unknown) => (v === "" || v === null || v === undefined ? "—" : typeof v === "object" ? JSON.stringify(v) : String(v));
  return (
    <div className="space-y-2 text-[12.5px]">
      {entry.reason && <p><span className="font-semibold text-ink-700">Note:</span> {entry.reason}</p>}
      {entry.entityType && <p className="text-ink-500">Record: {entry.entityType}{entry.entityId ? ` #${entry.entityId}` : ""}</p>}
      {d && "before" in d && "after" in d && <pre className="overflow-x-auto rounded-lg bg-ink-50 p-3 text-[12px] whitespace-pre-wrap">{JSON.stringify(d, null, 2)}</pre>}
      {pairs.length > 0 && (
        <table className="w-full max-w-2xl border-collapse">
          <thead><tr><th className="py-1 pr-3 text-left font-semibold text-ink-600">Field</th><th className="py-1 pr-3 text-left font-semibold text-ink-600">Before</th><th className="py-1 text-left font-semibold text-ink-600">After</th></tr></thead>
          <tbody>{pairs.map(([k, v]) => (
            <tr key={k} className="border-t border-ink-100"><td className="py-1 pr-3 font-mono text-[12px]">{k}</td><td className="py-1 pr-3 break-all">{show(v.before)}</td><td className="py-1 break-all">{show(v.after)}</td></tr>
          ))}</tbody>
        </table>
      )}
      {d && !("before" in d) && pairs.length === 0 && <pre className="overflow-x-auto rounded-lg bg-ink-50 p-3 text-[12px] whitespace-pre-wrap">{JSON.stringify(d, null, 2)}</pre>}
    </div>
  );
}

const hasMore = (e: AuditLog) => Boolean(e.reason || e.details || e.entityType);

export default function AuditLogsPage() {
  const [filters, setFilters] = useState(EMPTY);
  const [page, setPage] = useState(1);
  const [open, setOpen] = useState<number | null>(null);
  const q = useDebounced(filters.q);
  const query = { q, user: filters.user, from: filters.from, to: filters.to };
  const list = useAsync(() => api.admin.auditLogs({ ...query, page, perPage: PER_PAGE }), [q, filters.user, filters.from, filters.to, page]);
  const toast = useToast();
  const data = list.data;
  const pages = data ? Math.max(Math.ceil(data.total / data.perPage), 1) : 1;
  const filtered = Boolean(filters.q || filters.user || filters.from || filters.to);
  const badRange = Boolean(filters.from && filters.to && filters.from > filters.to);

  useEffect(() => setPage(1), [q, filters.user, filters.from, filters.to]);

  async function exportCsv() {
    try {
      await api.admin.exportAuditLogs(query);
      toast({ tone: "success", title: "Export started", message: `${data?.total ?? 0} entr${data?.total === 1 ? "y" : "ies"}. The CSV is saved by your browser; the export itself is recorded in the audit log.` });
    } catch (err) {
      toast({ tone: "error", title: "Export failed", message: (err as Error).message });
    }
  }

  return (
    <>
      <PageHeader eyebrow="Controls" title="Audit Logs"
        description="A permanent, read-only record of important actions: sign-ins, account changes, bills, payments and receipts, rate changes and approvals. Entries can't be edited or deleted."
        actions={<Button variant="secondary" icon="download-2-line" onClick={exportCsv} disabled={!data?.total || badRange}>Export CSV</Button>} />
      <Card>
        <form className="flex flex-wrap items-end gap-3 border-b border-ink-100 p-4" role="search" onSubmit={(e) => e.preventDefault()}>
          <Field label="Search" className="min-w-[200px] flex-1 sm:max-w-xs">{(id) => (
            <div className="relative">
              <Icon name="search-line" className="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-ink-500" />
              <input id={id} type="search" className="input pl-9" placeholder="Action, user or note" value={filters.q} onChange={(e) => setFilters({ ...filters, q: e.target.value })} />
            </div>)}</Field>
          <Field label="User" className="w-[calc(50%-0.375rem)] sm:w-44">{(id) => (
            <select id={id} className="input" value={filters.user} onChange={(e) => setFilters({ ...filters, user: e.target.value })}>
              <option value="">Everyone</option>
              {(data?.users ?? []).map((u) => <option key={u} value={u}>{u}</option>)}
            </select>)}</Field>
          <Field label="From" className="w-[calc(50%-0.375rem)] sm:w-40">{(id) => (
            <input id={id} type="date" className="input" value={filters.from} max={filters.to || undefined} onChange={(e) => setFilters({ ...filters, from: e.target.value })} />)}</Field>
          <Field label="To" className="w-[calc(50%-0.375rem)] sm:w-40" error={badRange && "Must be on or after From."}>{(id) => (
            <input id={id} type="date" className="input" value={filters.to} min={filters.from || undefined} aria-invalid={badRange || undefined} onChange={(e) => setFilters({ ...filters, to: e.target.value })} />)}</Field>
          {filtered && <Button type="button" variant="ghost" icon="close-circle-line" onClick={() => setFilters(EMPTY)}>Reset</Button>}
          <p className="ml-auto self-center text-[12.5px] text-ink-500" aria-live="polite">
            <Icon name="lock-line" className="mr-1 align-[-2px]" />Read-only · Philippine time{data ? ` · ${data.total.toLocaleString()} entr${data.total === 1 ? "y" : "ies"}` : ""}
          </p>
        </form>

        {list.error ? <ErrorState title="Couldn't load the audit log" message={list.error.message} onRetry={list.reload} />
          : !data ? <Skeleton rows={8} />
          : data.entries.length === 0 ? (filtered
            ? <EmptyState icon="search-line" title="No entries match" action={<Button variant="secondary" onClick={() => setFilters(EMPTY)}>Reset filters</Button>}>Try another word, user or date range.</EmptyState>
            : <EmptyState icon="file-shield-2-line" title="No entries yet">Actions are recorded here as people use the system.</EmptyState>)
          : (
            <div className={cx(list.loading && "opacity-60 transition-opacity")} aria-busy={list.loading || undefined}>
              <div className="hidden overflow-x-auto md:block">
                <table className="w-full border-collapse text-[13.5px]">
                  <caption className="sr-only">Audit log entries, newest first</caption>
                  <thead><tr>
                    <th scope="col" className="th" title={`Shown in Philippine time (${TIME_ZONE}); stored in UTC`}>When</th>
                    <th scope="col" className="th">User</th>
                    <th scope="col" className="th">Action</th>
                    <th scope="col" className="th w-12"><span className="sr-only">Details</span></th>
                  </tr></thead>
                  <tbody>
                    {data.entries.map((e) => (
                      <Fragment key={e.id}>
                        <tr className="align-top hover:bg-ink-50/60">
                          <td className="border-b border-ink-100 px-4 py-2 whitespace-nowrap text-ink-600 tabular">{dateTimeLabel(e.at)}</td>
                          <td className="border-b border-ink-100 px-4 py-2 font-semibold text-ink-900">{e.username}</td>
                          <td className="border-b border-ink-100 px-4 py-2 break-words">{e.action}{e.reason && <span className="mt-0.5 block text-[12px] text-ink-500">Note: {e.reason}</span>}</td>
                          <td className="border-b border-ink-100 px-2 py-1">
                            {hasMore(e) && <IconButton icon={open === e.id ? "arrow-up-s-line" : "arrow-down-s-line"} label={open === e.id ? "Hide details" : "Show details"}
                              aria-expanded={open === e.id} onClick={() => setOpen(open === e.id ? null : e.id)} />}
                          </td>
                        </tr>
                        {open === e.id && <tr><td colSpan={4} className="border-b border-ink-100 bg-ink-50/40 px-4 py-3"><Details entry={e} /></td></tr>}
                      </Fragment>
                    ))}
                  </tbody>
                </table>
              </div>
              <ul className="divide-y divide-ink-100 md:hidden">
                {data.entries.map((e) => (
                  <li key={e.id} className="space-y-1 px-4 py-3">
                    <div className="flex items-start justify-between gap-3">
                      <span className="font-semibold text-ink-900">{e.username}</span>
                      <span className="text-[12px] whitespace-nowrap text-ink-500 tabular">{dateTimeLabel(e.at)}</span>
                    </div>
                    <p className="break-words text-[13.5px]">{e.action}</p>
                    {hasMore(e) && (
                      <button type="button" className="text-[12.5px] font-semibold text-brand-600" aria-expanded={open === e.id} onClick={() => setOpen(open === e.id ? null : e.id)}>
                        {open === e.id ? "Hide details" : "Show details"}
                      </button>
                    )}
                    {open === e.id && <div className="pt-1"><Details entry={e} /></div>}
                  </li>
                ))}
              </ul>
              <nav className="flex flex-wrap items-center justify-between gap-3 border-t border-ink-100 px-4 py-3 text-[13px] text-ink-600" aria-label="Pagination">
                <span>Showing {(data.page - 1) * data.perPage + 1}–{(data.page - 1) * data.perPage + data.entries.length} of {data.total.toLocaleString()}</span>
                <div className="flex items-center gap-1">
                  <IconButton icon="arrow-left-s-line" label="Newer entries" disabled={page <= 1} className="disabled:opacity-30" onClick={() => setPage(page - 1)} />
                  <span className="px-2">Page {data.page} of {pages}</span>
                  <IconButton icon="arrow-right-s-line" label="Older entries" disabled={page >= pages} className="disabled:opacity-30" onClick={() => setPage(page + 1)} />
                </div>
              </nav>
            </div>
          )}
      </Card>
    </>
  );
}
