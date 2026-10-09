// Settings (Superadmin): backup status, Excel export and Excel import. Live: /api/admin/system;
// the export is the server's existing download (/database/export.xlsx). The import is the classic
// importer: a database backup is taken first and nothing is imported if that fails. "Check only" is a
// separate dry run for migration workbooks: it writes nothing (CITYLAND9_IMPORT_SAFEGUARDS.md).
import { useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { ConfirmDialog, useToast } from "../../components/base/overlays";
import { Badge, Button, Card, CardHeader, ErrorState, Icon, Notice, PageHeader, Skeleton, cx } from "../../components/base/ui";
import { ImportCheckReport } from "../../components/feature/ImportCheckReport";
import { legacyUrl } from "../../components/feature/ModuleRoute";
import { useAction, useAsync } from "../../hooks/useAsync";
import { IS_MOCK, api } from "../../services/api";
import type { ImportCheckReport as CheckReport, ImportResult } from "../../services/types";

export default function SettingsPage() {
  const { data, error, reload } = useAsync(() => api.admin.system(), []);
  const [file, setFile] = useState<File | null>(null);
  const [fileError, setFileError] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [result, setResult] = useState<ImportResult | null>(null);
  const [importError, setImportError] = useState("");
  const [check, setCheck] = useState<CheckReport | null>(null);
  const [checkError, setCheckError] = useState("");
  const [params, setParams] = useSearchParams();
  const movedForm = params.get("moved") === "form";
  const input = useRef<HTMLInputElement>(null);
  const { busy, act } = useAction();
  const toast = useToast();

  if (error) return <><PageHeader eyebrow="Administration" title="Settings" /><ErrorState title="Couldn't load Settings" message={error.message} onRetry={reload} /></>;
  if (!data) return <><PageHeader eyebrow="Administration" title="Settings" /><Card><Skeleton rows={6} /></Card></>;

  function choose(f: File | null) {
    setResult(null); setImportError(""); setCheck(null); setCheckError("");
    if (!f) { setFile(null); setFileError(""); return; }
    if (!/\.(xlsx|xlsm)$/i.test(f.name)) { setFile(null); setFileError("Choose an Excel workbook (.xlsx or .xlsm)."); return; }
    if (f.size > data!.import.maxUploadMb * 1024 * 1024) { setFile(null); setFileError(`That file is larger than ${data!.import.maxUploadMb} MB.`); return; }
    setFile(f); setFileError("");
  }

  async function runCheck() {
    if (!file || busy) return;
    setCheck(null); setCheckError(""); setResult(null); setImportError("");
    try {
      setCheck(await act(() => api.admin.checkImport(file)));
      setTimeout(() => document.getElementById("import-check-report")?.scrollIntoView({ behavior: "smooth", block: "start" }), 50);
    } catch (err) {
      setCheckError((err as Error).message);
    }
  }

  async function runImport() {
    if (!file || busy) return;
    try {
      const r = await act(() => api.admin.importDatabase(file));
      setConfirming(false); setResult(r); setFile(null); setCheck(null);
      if (input.current) input.current.value = "";
      toast({ tone: "success", title: "Import completed", message: "The changes are saved and recorded in the audit log." });
      reload();
    } catch (err) {
      setConfirming(false);
      setImportError((err as Error).message);
    }
  }

  const b = data.backup;
  return (
    <>
      <PageHeader eyebrow="Administration" title="Settings" description={`Database backup, export and import for the local server (${data.database.engine}). All data stays on the office PC.`} />
      {movedForm && (
        <div className="mb-4" role="status">
          <Notice tone="warn">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span>The import moved to this screen. The file you sent from the old page was <b>not imported</b>; please import it here.</span>
              <Button size="sm" variant="ghost" onClick={() => setParams({}, { replace: true })}>Dismiss</Button>
            </div>
          </Notice>
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-3">
        <Card><CardHeader title="Backup" actions={<Badge tone={b.ok ? "ok" : "warn"}>{b.ok ? "Up to date" : "Needs attention"}</Badge>} />
          <div className="space-y-3 p-5 text-[13.5px] text-ink-600">
            <p>{b.ageHours !== null
              ? <>Last scheduled backup: <b className="text-ink-900">{b.status === "ok" ? "succeeded" : b.status ?? "unknown"}</b>, {b.ageHours < 1 ? "less than an hour" : `${b.ageHours} hours`} ago.</>
              : <>No scheduled backup has been recorded yet.</>}</p>
            {!b.ok && <Notice tone="warn">{b.ageHours !== null && b.detail ? `${b.detail}. ` : ""}Backups should run at least every {b.maxAgeHours} hours. Set up the daily backup task: docs\run-guide\07-OPERATIONS.md.</Notice>}
            <p>Backups are written to <code className="rounded bg-ink-100 px-1">database/backups/</code> on the server PC: by the daily scheduled task (if it is set up, see the operations guide) and automatically before every import and schema upgrade.</p>
            <p>To make one now, run <code className="rounded bg-ink-100 px-1">.venv\Scripts\python.exe database\backup.py</code> on the server.</p>
          </div></Card>

        <Card><CardHeader title="Export to Excel" />
          <div className="space-y-4 p-5 text-[13.5px] text-ink-600">
            <p>Download units, owners, tenants, billing, payments, water readings, employees and expenses as one Excel workbook, for safekeeping or reporting.</p>
            {IS_MOCK
              ? <Button variant="secondary" icon="file-excel-2-line" onClick={() => toast({ tone: "info", title: "Excel export", message: "In the live system this downloads the workbook from the server." })}>Download workbook</Button>
              : <a href={legacyUrl(data.export.url)} download className="inline-flex h-10 items-center gap-2 rounded-lg border border-ink-200 bg-white px-4 font-semibold shadow-sm hover:bg-ink-50"><Icon name="file-excel-2-line" />Download workbook</a>}
          </div></Card>

        <Card><CardHeader title="Import from Excel" />
          <div className="space-y-4 p-5 text-[13.5px] text-ink-600">
            <Notice>Run <b>Check only</b> first: it validates the workbook and changes nothing.</Notice>
            <Notice tone="warn"><b>Import workbook is locked</b> to CITYLAND9's own export of this database, edited and uploaded again. It is allowed only after Check only passes for the same file: no new records, no id pointing to another record, and no change to a closed month or an issued or hand-corrected bill. Blank cells keep the saved values. Data from another system can only be checked here. A database backup is taken before every import.</Notice>
            {!data.import.allowed ? <p>Your role can't import data.</p> : (
              <>
                <label className={cx("flex cursor-pointer flex-col items-center gap-1.5 rounded-xl border-2 border-dashed px-4 py-5 text-center transition focus-within:ring-2 focus-within:ring-brand-300",
                  fileError ? "border-red-300 bg-red-50" : file ? "border-brand-400 bg-brand-50" : "border-ink-200 hover:bg-ink-50")}>
                  <Icon name="upload-2-line" className="text-2xl text-brand-600" />
                  <span className="font-semibold text-ink-900">{file ? file.name : "Choose an Excel workbook"}</span>
                  <span className="text-[12px] text-ink-500">{file ? `${(file.size / 1024 / 1024).toFixed(1)} MB` : `.xlsx or .xlsm, up to ${data.import.maxUploadMb} MB, ${data.import.maxRowsPerSheet.toLocaleString()} rows per sheet`}</span>
                  <input ref={input} type="file" accept=".xlsx,.xlsm" className="sr-only" aria-invalid={Boolean(fileError) || undefined}
                    onChange={(e) => choose(e.target.files?.[0] ?? null)} />
                </label>
                {fileError && <p className="text-[12px] text-red-600" role="alert">{fileError}</p>}
                <div className="flex flex-wrap gap-2">
                  <Button icon="search-eye-line" disabled={!file} loading={busy} onClick={runCheck}>Check only</Button>
                  <Button variant="secondary" icon="upload-2-line" disabled={!file || busy || !check?.importGate.allowed}
                    title={check?.importGate.allowed ? undefined : "Run Check only first; importing is allowed only when the check passes for this file."}
                    onClick={() => setConfirming(true)}>Import workbook</Button>
                </div>
                {file && !check && <p className="text-[12px] text-ink-500">Import workbook unlocks after Check only passes for this file.</p>}
                {check && !check.importGate.allowed && <p className="text-[12px] text-red-700">Import workbook is locked for this file: see the result below.</p>}
              </>
            )}
            {checkError && <div role="alert"><Notice tone="warn" title="Couldn't check this file">{checkError}</Notice></div>}
            {check && <p role="status" className="text-[12.5px] text-ink-600">Check finished: see the result below. Nothing was changed.</p>}
            {importError && <div role="alert"><Notice tone="warn" title="Import failed: nothing was changed">{importError}</Notice></div>}
            {result && (
              <div role="status" className="space-y-2">
                <Notice title="Import completed">{result.message}</Notice>
                {Object.keys(result.counts).length > 0 && (
                  <table className="w-full border-collapse text-[12.5px]">
                    <thead><tr><th className="py-1 text-left font-semibold">Sheet</th><th className="py-1 text-right font-semibold">New</th><th className="py-1 text-right font-semibold">Updated</th></tr></thead>
                    <tbody>{Object.entries(result.counts).map(([sheet, c]) => (
                      <tr key={sheet} className="border-t border-ink-100"><td className="py-1">{sheet}</td><td className="py-1 text-right tabular">{c.inserted}</td><td className="py-1 text-right tabular">{c.updated}</td></tr>
                    ))}</tbody>
                  </table>
                )}
              </div>
            )}
          </div></Card>
      </div>

      {check && <div id="import-check-report" className="mt-6 scroll-mt-4"><ImportCheckReport report={check} onClose={() => setCheck(null)} /></div>}

      <ConfirmDialog open={confirming} tone="danger" busy={busy} title="Import this workbook?" confirmLabel="Back up and import"
        onClose={() => { if (!busy) setConfirming(false); }} onConfirm={runImport}
        message={<div className="space-y-2">
          <p>The changes edited in <b>{file?.name}</b> will be saved to the existing records. No record is added.</p>
          <p className="text-[13px] text-ink-500">The server checks the file again and backs up the database first. If anything is rejected, nothing is saved. This can take a minute for large files.</p>
        </div>} />
    </>
  );
}
