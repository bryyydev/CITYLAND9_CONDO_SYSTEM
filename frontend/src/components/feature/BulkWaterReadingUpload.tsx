import { useRef, useState } from "react";
import { ApiError } from "../../services/http";
import type { BulkWaterReadingPreview } from "../../services/types";
import { Drawer, useToast } from "../base/overlays";
import { Button, Icon, Notice } from "../base/ui";
import { useAction } from "../../hooks/useAsync";
import { api } from "../../services/api";

export function BulkWaterReadingUpload({ open, onClose, onCreated }: {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<BulkWaterReadingPreview | null>(null);
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
      await act(() => api.water.downloadBulkTemplate());
    } catch (err) {
      setError((err as Error).message);
    }
  }

  async function check() {
    if (!file || busy) return;
    setPreview(null);
    setError("");
    try {
      setPreview(await act(() => api.water.checkBulkImport(file)));
    } catch (err) {
      setError((err as Error).message);
    }
  }

  async function save() {
    if (!file || !preview || preview.invalid > 0 || preview.valid === 0 || busy) return;
    setError("");
    try {
      const result = await act(() => api.water.createBulk(file));
      toast({ tone: "success", title: `${result.created} water readings saved`, message: "The readings and any affected open bills were updated." });
      close();
      onCreated();
    } catch (err) {
      setError((err as Error).message);
      if (err instanceof ApiError) {
        const latest = err.details.preview as BulkWaterReadingPreview | undefined;
        if (latest) setPreview(latest);
      }
    }
  }

  return (
    <Drawer open={open} onClose={close} width="max-w-5xl" title="Bulk upload water readings"
      subtitle="Check every unit and shared commercial reading before saving. Any row issue blocks the whole upload."
      footer={<>
        <Button variant="secondary" onClick={close} disabled={busy}>Cancel</Button>
        <Button icon="search-eye-line" variant="secondary" loading={busy} disabled={!file} onClick={check}>Check workbook</Button>
        <Button icon="save-3-line" loading={busy} disabled={!preview || preview.invalid > 0 || preview.valid === 0} onClick={save}>
          Save {preview?.valid ?? 0} readings
        </Button>
      </>}>
      <div className="space-y-4">
        <Notice tone="warn" title="Creates missing readings only">
          Existing readings are never overwritten. Use <b>WaterReadings</b> for one row per residential unit and
          <b> CommercialWaterReadings</b> for one row per commercial type (2F, UG, or LG). This upload does not record payments.
        </Notice>
        <div className="flex flex-wrap items-center gap-3">
          <Button variant="secondary" icon="file-excel-2-line" loading={busy} onClick={() => void downloadTemplate()}>Download water template</Button>
          <span className="text-[12px] text-ink-500">Enter the reading period start and end dates, for example month 2026-10, from 2026-09-16 to 2026-10-15. Maximum 10,000 rows per workbook.</span>
        </div>
        <label className="flex cursor-pointer flex-col items-center gap-1.5 rounded-xl border-2 border-dashed border-ink-200 px-4 py-5 text-center hover:bg-ink-50">
          <Icon name="upload-2-line" className="text-2xl text-brand-600" />
          <span className="font-semibold text-ink-900">{file?.name ?? "Choose a completed water-reading template"}</span>
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
            <Notice tone={preview.invalid ? "warn" : "copper"} title={preview.invalid ? "Fix the listed rows before saving" : "Workbook is ready"}>
              {preview.valid} ready · {preview.invalid} with issues · {preview.total} total. No readings have been saved yet.
            </Notice>
            <div className="max-h-[45vh] overflow-auto rounded-lg border border-ink-200">
              <table className="w-full border-collapse text-left text-[12px]">
                <thead className="sticky top-0 bg-ink-50 text-ink-600">
                  <tr><th className="p-2">Sheet / row</th><th className="p-2">Unit / type</th><th className="p-2">Month</th>
                    <th className="p-2 text-right">Previous</th><th className="p-2 text-right">Current</th>
                    <th className="p-2">Reading from</th><th className="p-2">Reading to</th><th className="p-2">Check</th></tr>
                </thead>
                <tbody>
                  {preview.rows.map((row) => (
                    <tr key={`${row.sheet}-${row.row}`} className="border-t border-ink-100 align-top">
                      <td className="p-2">{row.sheet}<span className="block text-ink-500">Row {row.row}</span></td>
                      <td className="p-2 font-semibold">{row.unitNo || row.commercialType || "—"}</td>
                      <td className="p-2">{row.month || "—"}</td>
                      <td className="p-2 text-right tabular">{row.previous || "—"}</td>
                      <td className="p-2 text-right tabular">{row.current || "—"}</td>
                      <td className="p-2">{row.readingFrom || "—"}</td>
                      <td className="p-2">{row.readingTo || "—"}</td>
                      <td className={`p-2 ${row.errors.length ? "text-red-700" : "text-emerald-700"}`}>
                        {row.errors.length ? row.errors.join(" ") : "Ready"}
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
