// "Check only" (dry-run) report for a data-migration workbook. The server wrote nothing; every issue
// names a sheet, row, column and code only, never a value from the workbook. Guide:
// CITYLAND9_IMPORT_SAFEGUARDS.md.
import { useMemo, useState } from "react";
import { Badge, Button, Card, CardHeader, Notice, cx, type Tone } from "../base/ui";
import type { ImportCheckReport as Report, ImportIssueSeverity } from "../../services/types";

const SEVERITY: Record<ImportIssueSeverity, { label: string; tone: Tone }> = {
  blocked: { label: "Blocked", tone: "bad" },
  conflict: { label: "Conflict", tone: "warn" },
  unresolved: { label: "Needs a decision", tone: "copper" },
  warning: { label: "Note", tone: "neutral" },
};
const AREA_LABEL: Record<string, string> = {
  properties: "Units (properties)", people: "Owners and tenants", records: "Records", financial: "Bills and payments",
  file: "File", import: "Importing",
};
const FORMAT_LABEL: Record<Report["format"], string> = {
  properties: "Property list", cityland_export: "CITYLAND9 export workbook", unknown: "Not recognised",
};
const PAGE = 100;
const AREA_ORDER = ["file", "properties", "records", "people", "financial", "import"];

function csvCell(value: string | number | null) {
  const text = value === null ? "" : String(value);
  return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

export function ImportCheckReport({ report, onClose }: { report: Report; onClose: () => void }) {
  const [filter, setFilter] = useState<ImportIssueSeverity | "all">("all");
  const [shown, setShown] = useState(PAGE);
  const codes = useMemo(() => Object.entries(report.issueCounts)
    .sort(([, a], [, b]) => ["blocked", "conflict", "unresolved", "warning"].indexOf(a.severity) - ["blocked", "conflict", "unresolved", "warning"].indexOf(b.severity) || b.count - a.count), [report]);
  const issues = useMemo(() => report.issues.filter((i) => filter === "all" || i.severity === filter), [report, filter]);
  const totals = Object.values(report.sheets).reduce((t, s) => ({ rows: t.rows + s.rows, valid: t.valid + s.valid }), { rows: 0, valid: 0 });

  function downloadCsv() {
    const lines = [["sheet", "row", "column", "code", "severity", "message"].join(",")]
      .concat(report.issues.map((i) => [i.sheet, i.row, i.column, i.code, i.severity, i.message].map(csvCell).join(",")));
    const url = URL.createObjectURL(new Blob([lines.join("\n")], { type: "text/csv" }));
    const a = Object.assign(document.createElement("a"), { href: url, download: `check-${report.file.sha256.slice(0, 12)}.csv` });
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <Card>
      <CardHeader title="Check only: result" actions={<Button size="sm" variant="ghost" icon="close-line" onClick={onClose}>Close</Button>} />
      <div className="space-y-5 p-5 text-[13.5px] text-ink-700">
        <Notice title="Nothing was changed">
          This was a check only: no records, backup, audit entry or account were created.
          {report.mode === "migration" && <>{" "}Importing data from another system is <b>not enabled</b> yet; this report shows what must be fixed or decided first.</>}
        </Notice>
        <section aria-labelledby="ic-gate" className={cx("rounded-lg border px-4 py-3", report.importGate.allowed ? "border-emerald-200 bg-emerald-50/60" : "border-red-200 bg-red-50/50")}>
          <h3 id="ic-gate" className="flex flex-wrap items-center gap-2 font-semibold text-ink-900">
            Import workbook: <Badge tone={report.importGate.allowed ? "ok" : "bad"}>{report.importGate.allowed ? "Allowed for this file" : "Locked for this file"}</Badge>
          </h3>
          {report.importGate.allowed
            ? <p className="mt-1 text-[12.5px] text-ink-600">This is CITYLAND9's own export of this database and every row safely updates an existing record. Importing it changes only what was edited in the file.</p>
            : <ul className="mt-1 list-disc pl-5 text-[12.5px] text-ink-600">{report.importGate.blockers.map((b) => <li key={b.code}>{b.message}{b.count > 1 ? ` (${b.count.toLocaleString()} rows)` : ""}</li>)}</ul>}
        </section>
        {report.mode === "migration" && !report.configLoaded && (
          <Notice tone="warn">No migration decisions file was found on the server (<code>migration_data/import_config.json</code>), so every rule that needs an approved decision is reported as unresolved.</Notice>
        )}

        <dl className="grid gap-3 sm:grid-cols-4">
          <div><dt className="text-[12px] text-ink-500">File</dt><dd className="break-all font-medium text-ink-900">{report.file.name}</dd></div>
          <div><dt className="text-[12px] text-ink-500">Layout</dt><dd className="font-medium text-ink-900">{FORMAT_LABEL[report.format]}</dd></div>
          <div><dt className="text-[12px] text-ink-500">Rows checked</dt><dd className="font-medium tabular text-ink-900">{totals.rows.toLocaleString()} ({totals.valid.toLocaleString()} valid)</dd></div>
          <div><dt className="text-[12px] text-ink-500">File fingerprint</dt><dd className="font-mono text-[12px] text-ink-700" title={report.file.sha256}>{report.file.sha256.slice(0, 16)}…</dd></div>
        </dl>

        <section aria-labelledby="ic-ready">
          <h3 id="ic-ready" className="mb-2 font-semibold text-ink-900">Readiness</h3>
          <ul className="divide-y divide-ink-100 rounded-lg border border-ink-200">
            {Object.entries(report.readiness).sort(([a], [b]) => AREA_ORDER.indexOf(a) - AREA_ORDER.indexOf(b)).map(([area, r]) => (
              <li key={area} className="flex flex-col gap-1 px-3 py-2 sm:flex-row sm:items-start sm:gap-3">
                <span className="w-44 shrink-0 font-medium text-ink-900">{AREA_LABEL[area] ?? area}</span>
                <Badge tone={r.ready ? "ok" : "bad"}>{r.ready ? "Ready" : "Not ready"}</Badge>
                {!r.ready && <span className="text-[12.5px] text-ink-600">{r.blockers.map((b) => b.message).join(" ")}</span>}
              </li>
            ))}
          </ul>
          {report.configProblems.length > 0 && (
            <ul className="mt-2 list-disc pl-5 text-[12.5px] text-ink-600">{report.configProblems.map((p) => <li key={p.code}>{p.message}</li>)}</ul>
          )}
        </section>

        <div className="grid gap-5 lg:grid-cols-2">
          <section aria-labelledby="ic-sheets" className="overflow-x-auto">
            <h3 id="ic-sheets" className="mb-2 font-semibold text-ink-900">Rows per sheet</h3>
            <table className="w-full border-collapse text-[12.5px]">
              <thead><tr className="text-left text-ink-500"><th className="py-1 font-semibold">Sheet</th><th className="py-1 text-right font-semibold">Rows</th><th className="py-1 text-right font-semibold">Valid</th><th className="py-1 text-right font-semibold">Blocked</th><th className="py-1 text-right font-semibold">Conflict</th><th className="py-1 text-right font-semibold">Decision</th></tr></thead>
              <tbody>{Object.entries(report.sheets).map(([sheet, s]) => (
                <tr key={sheet} className="border-t border-ink-100"><td className="py-1">{sheet}</td><td className="py-1 text-right tabular">{s.rows}</td><td className="py-1 text-right tabular">{s.valid}</td><td className="py-1 text-right tabular">{s.blocked}</td><td className="py-1 text-right tabular">{s.conflict}</td><td className="py-1 text-right tabular">{s.unresolved}</td></tr>
              ))}</tbody>
            </table>
          </section>
          <section aria-labelledby="ic-proposals" className="overflow-x-auto">
            <h3 id="ic-proposals" className="mb-2 font-semibold text-ink-900">Would be added or changed</h3>
            {Object.keys(report.proposals).length === 0
              ? <p className="text-[12.5px] text-ink-500">Nothing, until the open decisions are made. Proposals are only shown where the matching rule is confirmed.</p>
              : <table className="w-full border-collapse text-[12.5px]">
                  <thead><tr className="text-left text-ink-500"><th className="py-1 font-semibold">Record</th><th className="py-1 text-right font-semibold">New</th><th className="py-1 text-right font-semibold">Linked</th><th className="py-1 text-right font-semibold">Updated</th><th className="py-1 text-right font-semibold">Unchanged</th></tr></thead>
                  <tbody>{Object.entries(report.proposals).map(([entity, p]) => (
                    <tr key={entity} className="border-t border-ink-100"><td className="py-1">{entity.replace("_", " ")}</td><td className="py-1 text-right tabular">{p.insert}</td><td className="py-1 text-right tabular">{p.link}</td><td className="py-1 text-right tabular">{p.update}</td><td className="py-1 text-right tabular">{p.unchanged}</td></tr>
                  ))}</tbody>
                </table>}
          </section>
        </div>

        <section aria-labelledby="ic-codes" className="overflow-x-auto">
          <h3 id="ic-codes" className="mb-2 font-semibold text-ink-900">Issues by type</h3>
          {codes.length === 0 ? <p className="text-[12.5px] text-ink-500">No issues found.</p> : (
            <table className="w-full border-collapse text-[12.5px]">
              <tbody>{codes.map(([code, c]) => (
                <tr key={code} className="border-t border-ink-100 align-top">
                  <td className="py-1.5 pr-3"><Badge tone={SEVERITY[c.severity].tone}>{SEVERITY[c.severity].label}</Badge></td>
                  <td className="py-1.5 pr-3 text-right tabular font-semibold">{c.count.toLocaleString()}</td>
                  <td className="py-1.5">{c.message} <span className="font-mono text-[11px] text-ink-400">{code}</span></td>
                </tr>
              ))}</tbody>
            </table>
          )}
        </section>

        {report.issues.length > 0 && (
          <section aria-labelledby="ic-rows">
            <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
              <h3 id="ic-rows" className="font-semibold text-ink-900">Where (sheet, row, column)</h3>
              <div className="flex flex-wrap items-center gap-2">
                <label className="sr-only" htmlFor="ic-filter">Show</label>
                <select id="ic-filter" className="input h-8 w-auto py-0 text-[13px]" value={filter}
                  onChange={(e) => { setFilter(e.target.value as ImportIssueSeverity | "all"); setShown(PAGE); }}>
                  <option value="all">All issues</option>
                  {(Object.keys(SEVERITY) as ImportIssueSeverity[]).map((s) => <option key={s} value={s}>{SEVERITY[s].label}</option>)}
                </select>
                <Button size="sm" variant="secondary" icon="download-2-line" onClick={downloadCsv}>Download list (CSV)</Button>
              </div>
            </div>
            <div className="max-h-96 overflow-auto rounded-lg border border-ink-200">
              <table className="w-full border-collapse text-[12.5px]">
                <thead className="sticky top-0 bg-ink-50"><tr className="text-left text-ink-500"><th className="px-2 py-1 font-semibold">Sheet</th><th className="px-2 py-1 text-right font-semibold">Row</th><th className="px-2 py-1 font-semibold">Column</th><th className="px-2 py-1 font-semibold">Issue</th></tr></thead>
                <tbody>{issues.slice(0, shown).map((i, k) => (
                  <tr key={k} className={cx("border-t border-ink-100", i.severity === "blocked" && "bg-red-50/40")}>
                    <td className="px-2 py-1">{i.sheet ?? "-"}</td><td className="px-2 py-1 text-right tabular">{i.row ?? "-"}</td>
                    <td className="px-2 py-1">{i.column ?? "-"}</td><td className="px-2 py-1"><span className="font-mono text-[11px]">{i.code}</span></td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
            {issues.length > shown && <Button size="sm" variant="ghost" className="mt-2" onClick={() => setShown(shown + PAGE)}>Show {Math.min(PAGE, issues.length - shown)} more of {issues.length - shown}</Button>}
            {report.issuesTruncated && <p className="mt-2 text-[12px] text-ink-500">Only the first {report.issues.length.toLocaleString()} issues are listed; the counts above include all of them. The full list is available with the command-line check (database\import_check.py --details).</p>}
          </section>
        )}
        {report.notes.length > 0 && <ul className="list-disc pl-5 text-[12.5px] text-ink-500">{report.notes.map((n, k) => <li key={k}>{n}</li>)}</ul>}
      </div>
    </Card>
  );
}
