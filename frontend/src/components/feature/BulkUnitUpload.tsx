import { useRef, useState } from "react";
import { Drawer, useToast } from "../base/overlays";
import { Button, Icon, Notice } from "../base/ui";
import { useAction } from "../../hooks/useAsync";
import { api } from "../../services/api";
import type { BulkUnitPreview } from "../../services/types";

export function BulkUnitUpload({ open, onClose, onCreated }: { open: boolean; onClose: () => void; onCreated: () => void }) {
  const input = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<BulkUnitPreview | null>(null);
  const [error, setError] = useState("");
  const { busy, act } = useAction();
  const toast = useToast();

  function close() {
    if (busy) return;
    setFile(null);
    setPreview(null);
    setError("");
    if (input.current) input.current.value = "";
    onClose();
  }

  async function downloadTemplate() {
    setError("");
    try {
      await act(() => api.units.downloadBulkTemplate());
    } catch (err) {
      setError((err as Error).message);
    }
  }

  async function check() {
    if (!file || busy) return;
    setPreview(null);
    setError("");
    try {
      setPreview(await act(() => api.units.checkBulkImport(file)));
    } catch (err) {
      setError((err as Error).message);
    }
  }

  async function create() {
    if (!file || !preview || preview.invalid > 0 || preview.valid === 0 || busy) return;
    setError("");
    try {
      const result = await act(() => api.units.createBulk(file));
      toast({
        tone: "success",
        title: `${result.created} units created`,
        message: result.skipped
          ? `The new units and any listed owners or tenants were saved. ${result.skipped} duplicate unit names were skipped.`
          : "The new units and any listed owners or tenants were saved.",
      });
      close();
      onCreated();
    } catch (err) {
      setError((err as Error).message);
      const latest = (err as { details?: { preview?: BulkUnitPreview } }).details?.preview;
      if (latest) setPreview(latest);
    }
  }

  return (
    <Drawer open={open} onClose={close} width="max-w-4xl" title="Bulk upload units"
      subtitle="Upload multiple new units at once. The system checks every row before creating anything."
      footer={<>
        <Button variant="secondary" onClick={close} disabled={busy}>Cancel</Button>
        <Button icon="search-eye-line" variant="secondary" loading={busy} disabled={!file} onClick={check}>Check workbook</Button>
        <Button icon="add-line" loading={busy} disabled={!preview || preview.invalid > 0 || preview.valid === 0} onClick={create}>
          Create {preview?.valid ?? 0} units
        </Button>
      </>}>
      <div className="space-y-4">
        <Notice tone="warn" title="Creates new records">
          This dedicated upload creates new units; it does not modify existing ones. If any row has a problem, the system creates none. For updates, use the existing database export and Settings import instead.
        </Notice>
        <div className="flex flex-wrap items-center gap-3">
          <Button variant="secondary" icon="file-excel-2-line" loading={busy} onClick={() => void downloadTemplate()}>Download unit template</Button>
          <span className="text-[12px] text-ink-500">Enter up to 3,000 units. Repeated unit numbers and units already in CITYLAND9 are skipped. Optional owner and tenant names are stored whole, exactly as entered, including punctuation.</span>
        </div>
        <label className="flex cursor-pointer flex-col items-center gap-1.5 rounded-xl border-2 border-dashed border-ink-200 px-4 py-5 text-center hover:bg-ink-50">
          <Icon name="upload-2-line" className="text-2xl text-brand-600" />
          <span className="font-semibold text-ink-900">{file?.name ?? "Choose a completed unit template"}</span>
          <span className="text-[12px] text-ink-500">{file ? `${(file.size / 1024 / 1024).toFixed(1)} MB` : "Excel .xlsx only"}</span>
          <input ref={input} type="file" accept=".xlsx" className="sr-only" onChange={(event) => {
            setFile(event.target.files?.[0] ?? null);
            setPreview(null);
            setError("");
          }} />
        </label>
        {error && <div role="alert"><Notice tone="warn" title="Bulk upload not completed">{error}</Notice></div>}
        {preview && (
          <section className="space-y-3" aria-live="polite">
            <Notice tone={preview.invalid ? "warn" : "copper"} title={preview.invalid ? "Fix the listed rows before creating units" : "Workbook is ready"}>
              {preview.valid} valid · {preview.skipped} duplicate names skipped · {preview.invalid} with issues · {preview.total} total. No records have been created yet.
            </Notice>
            <div className="max-h-[45vh] overflow-auto rounded-lg border border-ink-200">
              <table className="w-full border-collapse text-left text-[12px]">
                <thead className="sticky top-0 bg-ink-50 text-ink-600">
                  <tr><th className="p-2">Row</th><th className="p-2">Unit</th><th className="p-2">Type</th><th className="p-2">Area</th><th className="p-2">Owner / Tenant</th><th className="p-2">Check</th></tr>
                </thead>
                <tbody>
                  {preview.rows.map((row) => (
                    <tr key={row.row} className="border-t border-ink-100 align-top">
                      <td className="p-2 tabular">{row.row}</td>
                      <td className="p-2 font-semibold">{row.unitNo || "—"}</td>
                      <td className="p-2">{row.type || "—"}</td>
                      <td className="p-2 tabular">{row.areaSqm || "—"}</td>
                      <td className="p-2">{[row.ownerName, row.tenantName].filter(Boolean).join(" / ") || "—"}</td>
                      <td className={`p-2 ${row.errors.length ? "text-red-700" : row.skipped ? "text-ink-500" : "text-emerald-700"}`}>
                        {row.errors.length ? row.errors.join(" ") : row.skipped ? "Duplicate name — skipped" : "Ready"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        )}
      </div>
    </Drawer>
  );
}
