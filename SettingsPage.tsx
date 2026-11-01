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
  const [backupError, setBackupError] = useState("");
  const [backupMessage, setBackupMessage] = useState("");
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

  async function runBackup() {
    if (busy || !data?.backup.manualAllowed) return;
    setBackupError(""); setBackupMessage("");
    if (IS_MOCK) {
      toast({ tone: "info", title: "Backup preview", message: "Prototype mode does not create a backup file." });
      return;
    }
    try {
      const backup = await act(() => api.admin.backupDatabase());
      setBackupMessage(`${backup.message} Saved as ${backup.file}.`);
      toast({ tone: "success", title: "Backup completed", message: backup.file });
      reload();
    } catch (err) {
      setBackupError((err as Error).message);
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

      <div className="grid gap-6 lg:grid-cols-2">
        <Card><CardHeader title="Backup" subtitle="Create a recoverable copy of the current database."
          actions={<Badge tone={b.ok ? "ok" : "warn"}>{b.ok ? "Up to date" : "Needs attention"}</Badge>} />
          <div className="space-y-4 p-5 text-[13.5px] text-ink-600">
            <div className="flex items-start gap-3">
              <Icon name="database-2-line" className="mt-0.5 text-xl text-brand-600" />
              <p>{b.ageHours !== null
                ? <>Last backup: <b className="text-ink-900">{b.status === "ok" ? "succeeded" : b.status ?? "unknown"}</b>, {b.ageHours < 1 ? "less than an hour" : `${b.ageHours} hours`} ago.</>
                : <>No backup has been recorded yet.</>}</p>
            </div>
            {!b.ok && <Notice tone="warn">{b.ageHours !== null && b.detail ? `${b.detail}. ` : ""}Backups should run at least every {b.maxAgeHours} hours. Set up the daily backup task using docs\run-guide\07-OPERATIONS.md.</Notice>}
            {backupError && <div role="alert"><Notice tone="warn" title="Backup failed">{backupError}</Notice></div>}
            {backupMessage && <div role="status"><Notice title="Backup ready">{backupMessage}</Notice></div>}
            <div className="flex flex-wrap items-center gap-3">
              <Button icon="save-3-line" loading={busy} disabled={!b.manualAllowed} onClick={runBackup}>Back up now</Button>
              {!b.manualAllowed && <span className="text-[12px] text-ink-500">A manual backup isn't available for this database configuration.</span>}
            </div>
            <p>Backup files are stored on the server in <code className="rounded bg-ink-100 px-1">database/backups/</code>. A separate backup is also attempted before every import and schema upgrade.</p>
          </div></Card>

        <Card><CardHeader title="Export to Excel" subtitle="Download the current database data as a workbook." />
          <div className="space-y-4 p-5 text-[13.5px] text-ink-600">
            <p>The workbook includes units, owners, tenants, billing, payments, water readings, employees and expenses.</p>
            <Notice>For a safe edit-and-import round trip, export from this database, preserve the record IDs, make only the intended changes, and upload that workbook below. An Excel export is not a database backup.</Notice>
            {IS_MOCK
              ? <Button variant="secondary" icon="file-excel-2-line" onClick={() => toast({ tone: "info", title: "Excel export", message: "In the live system this downloads the workbook from the server." })}>Download workbook</Button>
              : <a href={legacyUrl(data.export.url)} download className="inline-flex h-10 items-center gap-2 rounded-lg border border-ink-200 bg-white px-4 font-semibold shadow-sm hover:bg-ink-50"><Icon name="file-excel-2-line" />Download workbook</a>}
          </div></Card>

        <Card className="lg:col-span-2"><CardHeader title="Import from Excel" subtitle="Check the workbook first; the check never changes database records." />
          <div className="space-y-4 p-5 text-[13.5px] text-ink-600">
            <ol className="grid gap-3 sm:grid-cols-3">
              <li className="rounded-lg border border-ink-200 bg-ink-50/60 p-3"><b className="block text-ink-900">1. Choose the workbook</b><span className="text-[12.5px]">Use an .xlsx or .xlsm file exported from this same database for an update.</span></li>
              <li className="rounded-lg border border-ink-200 bg-ink-50/60 p-3"><b className="block text-ink-900">2. Check only</b><span className="text-[12.5px]">Review row-level issues and confirm the report is for the intended workbook.</span></li>
              <li className="rounded-lg border border-ink-200 bg-ink-50/60 p-3"><b className="block text-ink-900">3. Import if allowed</b><span className="text-[12.5px]">Only a passing check unlocks import. The server backs up first and checks again.</span></li>
            </ol>
            <div className="flex flex-wrap items-center gap-3">
              {IS_MOCK
                ? <Button variant="secondary" icon="file-excel-2-line" onClick={() => toast({ tone: "info", title: "Import template", message: "The live system downloads the template with the supported CITYLAND9 sheets and columns." })}>Download import template</Button>
                : <a href={legacyUrl(data.import.templateUrl)} download className="inline-flex h-10 items-center gap-2 rounded-lg border border-ink-200 bg-white px-4 font-semibold shadow-sm hover:bg-ink-50"><Icon name="file-excel-2-line" />Download import template</a>}
              <span className="text-[12px] text-ink-500">The template has the system’s sheet and column layout, but contains no data. Use a fresh database export for updates.</span>
            </div>
            <Notice tone="warn"><b>Safety lock:</b> imports can update existing records only. New records, mismatched IDs, and changes to closed months or issued/hand-corrected bills remain blocked. Blank cells keep saved values. Workbooks from another system are check-only here; they are not imported.</Notice>
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
