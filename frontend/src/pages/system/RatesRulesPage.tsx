// Rates & Rules (Superadmin). Live: /api/admin/rates. The server validates and saves; this page
// shows what changed, checks obvious mistakes before sending, and maps the server's field messages.
// Saving changes only the settings rows. As on the classic screen, issued bills keep their stored condo dues,
// parking and water (each reading keeps its rate); the penalty rule and the storage cut-off month are read when
// a statement is shown, so they also affect unpaid bills. Payments and receipts never change.
import { useEffect, useState } from "react";
import { ConfirmDialog, useToast } from "../../components/base/overlays";
import { Button, Card, CardHeader, ErrorState, Field, Notice, PageHeader, Skeleton, cx } from "../../components/base/ui";
import { useAction, useAsync } from "../../hooks/useAsync";
import { monthLabel } from "../../lib/format";
import { api } from "../../services/api";
import { ApiError } from "../../services/http";
import type { RatesAndRules, RatesAndRulesUpdate } from "../../services/types";

const RATE = /^\d{1,6}(\.\d{1,4})?$/;
const MONTH = /^\d{4}-(0[1-9]|1[0-2])$/;
const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
const NEVER = "9999-12";

// Field key (same as the server's error keys) -> label, used for errors and the "what changed" list.
const LABELS: Record<string, string> = {
  corporationName: "Corporation name", address: "Address",
  "ratesPerSqm.studio": "Studio rate", "ratesPerSqm.oneBed": "1 bedroom rate", "ratesPerSqm.twoBed": "2 bedroom rate", "ratesPerSqm.threeBed": "3 bedroom rate",
  parkingRatePerSqm: "Parking rate", storageRatePerSqm: "Storage rate", storageInTotalFrom: "Storage in SOA totals from",
  booksClosedThrough: "Books closed through", waterRate: "Water rate", waterAutoCompute: "Automatic water computation",
  penaltyRate: "Penalty rate", "penaltyIncludes.condo": "Penalty on condo dues", "penaltyIncludes.parking": "Penalty on parking",
  "penaltyIncludes.storage": "Penalty on storage", "penaltyIncludes.water": "Penalty on water",
  onlinePaymentUrl: "Online payment link", onlinePaymentInstructions: "Payment instructions",
  smtpHost: "SMTP host", smtpPort: "SMTP port", smtpSender: "Sender email", smtpUsername: "SMTP username", smtpPassword: "SMTP password",
};

function flatten(v: RatesAndRulesUpdate): Record<string, string | boolean> {
  const out: Record<string, string | boolean> = {};
  for (const [k, val] of Object.entries(v)) {
    if (val && typeof val === "object") for (const [sub, x] of Object.entries(val)) out[`${k}.${sub}`] = x as string | boolean;
    else out[k] = val as string | boolean;
  }
  return out;
}

function toForm(saved: RatesAndRules): RatesAndRulesUpdate {
  const { smtpPasswordSet: _set, dueDay: _due, ...rest } = saved;
  return { ...rest, smtpPassword: "", smtpPasswordClear: false };
}

function clientErrors(f: RatesAndRulesUpdate): Record<string, string> {
  const e: Record<string, string> = {};
  const rate = (key: string, value: string, max = 100000) => {
    const v = value.trim();
    if (!RATE.test(v)) e[key] = "Enter a number with up to 4 decimals, e.g. 85.00.";
    else if (Number(v) > max) e[key] = `Can't be more than ${max.toLocaleString()}.`;
  };
  (["studio", "oneBed", "twoBed", "threeBed"] as const).forEach((k) => rate(`ratesPerSqm.${k}`, f.ratesPerSqm[k]));
  rate("parkingRatePerSqm", f.parkingRatePerSqm);
  rate("storageRatePerSqm", f.storageRatePerSqm);
  rate("waterRate", f.waterRate);
  rate("penaltyRate", f.penaltyRate, 100);
  if (!f.corporationName.trim()) e.corporationName = "Required: it is printed on every SOA.";
  if (!MONTH.test(f.storageInTotalFrom)) e.storageInTotalFrom = "Choose a month.";
  if (f.booksClosedThrough && !MONTH.test(f.booksClosedThrough)) e.booksClosedThrough = "Choose a month, or leave it empty.";
  if (f.onlinePaymentUrl.trim() && !/^https?:\/\/\S+$/.test(f.onlinePaymentUrl.trim())) e.onlinePaymentUrl = "Must start with http:// or https://.";
  if (f.smtpPort.trim() && !(/^\d+$/.test(f.smtpPort.trim()) && Number(f.smtpPort) >= 1 && Number(f.smtpPort) <= 65535)) e.smtpPort = "A number from 1 to 65535, e.g. 587.";
  if (f.smtpSender.trim() && !EMAIL.test(f.smtpSender.trim())) e.smtpSender = "Enter a valid email address.";
  for (const k of ["corporationName", "address", "onlinePaymentUrl", "onlinePaymentInstructions", "smtpHost", "smtpSender", "smtpUsername"] as const)
    if (f[k].length > 255) e[k] = "Keep it under 255 characters.";
  return e;
}

export default function RatesRulesPage() {
  const { data, error, reload, setData } = useAsync(() => api.admin.rates(), []);
  if (error) return <><PageHeader eyebrow="Administration" title="Rates & Rules" /><ErrorState title="Couldn't load Rates & Rules" message={error.message} onRetry={reload} /></>;
  if (!data) return <><PageHeader eyebrow="Administration" title="Rates & Rules" /><Card><Skeleton rows={10} /></Card></>;
  return <RatesForm saved={data} onSaved={setData} />;
}

function RatesForm({ saved, onSaved }: { saved: RatesAndRules; onSaved: (r: RatesAndRules) => void }) {
  const [form, setForm] = useState<RatesAndRulesUpdate>(() => toForm(saved));
  const [touched, setTouched] = useState(false);
  const [serverFields, setServerFields] = useState<Record<string, string>>({});
  const [serverError, setServerError] = useState("");
  const [confirming, setConfirming] = useState(false);
  const { busy, act } = useAction();
  const toast = useToast();

  useEffect(() => setForm(toForm(saved)), [saved]);

  const initial = flatten(toForm(saved));
  const current = flatten(form);
  const changed = Object.keys(current).filter((k) => !["smtpPassword", "smtpPasswordClear"].includes(k) && current[k] !== initial[k]);
  const smtpChange = form.smtpPasswordClear ? "remove" : form.smtpPassword ? "replace" : null;
  const dirty = changed.length > 0 || smtpChange !== null;
  // Read live by the billing engine (not frozen on issued bills): see the note at the top of this file.
  const affectsOpenBills = changed.some((k) => k === "penaltyRate" || k.startsWith("penaltyIncludes.") || k === "storageInTotalFrom");
  const local = clientErrors(form);
  const err = (k: string) => (touched ? local[k] : "") || serverFields[k] || "";
  const hasErrors = Object.keys(local).length > 0;

  // Warn before leaving the page with unsaved changes (browser refresh / close).
  useEffect(() => {
    if (!dirty) return;
    const h = (e: BeforeUnloadEvent) => e.preventDefault();
    window.addEventListener("beforeunload", h);
    return () => window.removeEventListener("beforeunload", h);
  }, [dirty]);

  const patch = (p: Partial<RatesAndRulesUpdate>, keys: string[]) => {
    setForm({ ...form, ...p });
    if (keys.some((k) => serverFields[k])) setServerFields(Object.fromEntries(Object.entries(serverFields).filter(([k]) => !keys.includes(k))));
  };

  function askToSave() {
    setTouched(true);
    setServerError("");
    if (!dirty || busy) return;
    if (hasErrors) {
      requestAnimationFrame(() => document.querySelector<HTMLElement>("[aria-invalid='true']")?.focus());
      return;
    }
    setConfirming(true);
  }

  async function save() {
    try {
      const result = await act(() => api.admin.saveRates(form));
      setConfirming(false); setTouched(false); setServerFields({});
      onSaved(result);
      toast({ tone: "success", title: "Rates & Rules saved", message: affectsOpenBills ? "Penalty and storage settings also apply to unpaid bills' statements. Recorded payments and receipts don't change." : "New rates apply to bills generated from now on. Issued bills, payments and receipts don't change." });
    } catch (e) {
      setConfirming(false);
      if (e instanceof ApiError && Object.keys(e.fields).length) {
        setServerFields(e.fields);
        setServerError("Some values weren't accepted, so nothing was saved. Check the highlighted fields.");
        requestAnimationFrame(() => document.querySelector<HTMLElement>("[aria-invalid='true']")?.focus());
      } else setServerError((e as Error).message);
    }
  }

  const numberField = (key: string, label: string, value: string, onChange: (v: string) => void, suffix?: string) => (
    <Field label={label} error={err(key)}>{(id) => (
      <div className="relative">
        <input id={id} inputMode="decimal" className={cx("input tabular", suffix && "pr-14")} value={value} aria-invalid={Boolean(err(key)) || undefined}
          onChange={(e) => onChange(e.target.value)} />
        {suffix && <span className="pointer-events-none absolute top-1/2 right-3 -translate-y-1/2 text-[12px] text-ink-500">{suffix}</span>}
      </div>)}</Field>
  );
  const textField = (key: keyof RatesAndRulesUpdate & string, label: string, opts: { hint?: string; type?: string; placeholder?: string; className?: string; autoComplete?: string } = {}) => (
    <Field label={label} error={err(key)} hint={opts.hint} className={opts.className}>{(id) => (
      <input id={id} type={opts.type ?? "text"} className="input" placeholder={opts.placeholder} autoComplete={opts.autoComplete} maxLength={300}
        value={form[key] as string} aria-invalid={Boolean(err(key)) || undefined} onChange={(e) => patch({ [key]: e.target.value } as Partial<RatesAndRulesUpdate>, [key])} />)}</Field>
  );
  const check = (key: string, label: string, checked: boolean, onChange: (v: boolean) => void) => (
    <label key={key} className={cx("flex min-h-11 cursor-pointer items-center gap-2.5 rounded-lg border px-3 py-2 transition focus-within:ring-2 focus-within:ring-brand-300",
      checked ? "border-brand-500 bg-brand-50" : "border-ink-200 hover:bg-ink-50")}>
      <input type="checkbox" className="size-4 accent-brand-600" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <span className="font-medium text-ink-900">{label}</span>
    </label>
  );

  const storageNever = form.storageInTotalFrom === NEVER;
  return (
    <>
      <PageHeader eyebrow="Administration" title="Rates & Rules"
        description="Dues and water rates, the penalty rule, payment instructions and SOA email. New dues, parking and water rates apply to bills generated from now on; issued bills keep theirs."
        actions={<>
          {dirty && <span className="text-[12.5px] font-semibold text-copper-700" role="status">Unsaved changes</span>}
          <Button variant="secondary" icon="arrow-go-back-line" disabled={!dirty || busy} onClick={() => { setForm(toForm(saved)); setTouched(false); setServerFields({}); setServerError(""); }}>Discard</Button>
          <Button icon="save-3-line" loading={busy} disabled={!dirty} title={dirty ? undefined : "Nothing changed yet"} onClick={askToSave}>Save changes</Button>
        </>} />

      {serverError && <div className="mb-4" role="alert"><Notice tone="warn">{serverError}</Notice></div>}
      {touched && hasErrors && !serverError && <div className="mb-4" role="alert"><Notice tone="warn">Fix the highlighted fields before saving.</Notice></div>}

      <form className="grid gap-6 xl:grid-cols-2" noValidate onSubmit={(e) => { e.preventDefault(); askToSave(); }}>
        <Card><CardHeader title="Condo dues" subtitle="Monthly rate per square metre, by unit type. A unit can also have a manual monthly amount (Units Directory)." />
          <div className="grid gap-4 p-5 sm:grid-cols-2">
            {numberField("ratesPerSqm.studio", "Studio", form.ratesPerSqm.studio, (v) => patch({ ratesPerSqm: { ...form.ratesPerSqm, studio: v } }, ["ratesPerSqm.studio"]), "₱/m²")}
            {numberField("ratesPerSqm.oneBed", "1 bedroom", form.ratesPerSqm.oneBed, (v) => patch({ ratesPerSqm: { ...form.ratesPerSqm, oneBed: v } }, ["ratesPerSqm.oneBed"]), "₱/m²")}
            {numberField("ratesPerSqm.twoBed", "2 bedroom", form.ratesPerSqm.twoBed, (v) => patch({ ratesPerSqm: { ...form.ratesPerSqm, twoBed: v } }, ["ratesPerSqm.twoBed"]), "₱/m²")}
            {numberField("ratesPerSqm.threeBed", "3 bedroom", form.ratesPerSqm.threeBed, (v) => patch({ ratesPerSqm: { ...form.ratesPerSqm, threeBed: v } }, ["ratesPerSqm.threeBed"]), "₱/m²")}
          </div></Card>

        <Card><CardHeader title="Parking & storage" />
          <div className="grid gap-4 p-5 sm:grid-cols-2">
            {numberField("parkingRatePerSqm", "Parking rate", form.parkingRatePerSqm, (v) => patch({ parkingRatePerSqm: v }, ["parkingRatePerSqm"]), "₱/m²")}
            {numberField("storageRatePerSqm", "Storage rate", form.storageRatePerSqm, (v) => patch({ storageRatePerSqm: v }, ["storageRatePerSqm"]), "₱/m²")}
            <div className="space-y-2 sm:col-span-2">
              <Field label="Add storage to SOA totals from" error={err("storageInTotalFrom")}
                hint={storageNever ? "Not yet: storage is shown on SOAs but not added to the total." : `Bills for ${monthLabel(form.storageInTotalFrom)} and later, including ones already issued, add storage to the total. Earlier bills don't.`}>{(id) => (
                <input id={id} type="month" className="input" disabled={storageNever} value={storageNever ? "" : form.storageInTotalFrom} aria-invalid={Boolean(err("storageInTotalFrom")) || undefined}
                  onChange={(e) => patch({ storageInTotalFrom: e.target.value }, ["storageInTotalFrom"])} />)}</Field>
              <label className="flex items-center gap-2 text-[13px] text-ink-700">
                <input type="checkbox" className="size-4 accent-brand-600" checked={storageNever}
                  onChange={(e) => patch({ storageInTotalFrom: e.target.checked ? NEVER : (saved.storageInTotalFrom !== NEVER ? saved.storageInTotalFrom : new Date().toISOString().slice(0, 7)) }, ["storageInTotalFrom"])} />
                Don't add storage to SOA totals yet
              </label>
            </div>
          </div></Card>

        <Card><CardHeader title="Water" />
          <div className="grid gap-4 p-5 sm:grid-cols-2">
            {numberField("waterRate", "Water rate", form.waterRate, (v) => patch({ waterRate: v }, ["waterRate"]), "₱/m³")}
            <fieldset>
              <legend className="mb-1.5 text-[12.5px] font-semibold text-ink-700">Computation</legend>
              {check("waterAutoCompute", "Compute automatically", form.waterAutoCompute, (v) => patch({ waterAutoCompute: v }, ["waterAutoCompute"]))}
            </fieldset>
            <p className="text-[12.5px] text-ink-500 sm:col-span-2">{form.waterAutoCompute
              ? "Water = (current reading − previous reading) × water rate. A reading can still use an adjusted rate."
              : "Off: the water amount is entered manually on each reading."}</p>
          </div></Card>

        <Card><CardHeader title="Penalty rule" subtitle={`Bills fall due on day ${saved.dueDay} of the month (fixed).`} />
          <div className="grid gap-4 p-5">
            <div className="sm:max-w-xs">{numberField("penaltyRate", "Penalty rate", form.penaltyRate, (v) => patch({ penaltyRate: v }, ["penaltyRate"]), "%")}</div>
            <fieldset>
              <legend className="mb-1.5 text-[12.5px] font-semibold text-ink-700">Charge the penalty on unpaid</legend>
              <div className="grid gap-2 sm:grid-cols-2">
                {(["condo", "parking", "storage", "water"] as const).map((k) => check(`penaltyIncludes.${k}`, k === "condo" ? "Condo dues" : k[0].toUpperCase() + k.slice(1), form.penaltyIncludes[k],
                  (v) => patch({ penaltyIncludes: { ...form.penaltyIncludes, [k]: v } }, [`penaltyIncludes.${k}`])))}
              </div>
            </fieldset>
            <p className="text-[12.5px] text-ink-500">Penalty = unpaid balance of the selected charges from earlier months × the penalty rate. It is worked out when a statement is shown, so a change here also applies to unpaid bills.</p>
          </div></Card>

        <Card><CardHeader title="Corporation & payment instructions" subtitle="Printed on every SOA." />
          <div className="grid gap-4 p-5">
            {textField("corporationName", "Corporation name")}
            {textField("address", "Address")}
            {textField("onlinePaymentUrl", "Online payment link (optional)", { hint: "Shown as a QR code on printed SOAs.", placeholder: "https://" })}
            <Field label="Payment instructions" error={err("onlinePaymentInstructions")} hint={`${form.onlinePaymentInstructions.length}/255 characters`}>{(id) => (
              <textarea id={id} rows={3} maxLength={300} className="input h-auto py-2" value={form.onlinePaymentInstructions} aria-invalid={Boolean(err("onlinePaymentInstructions")) || undefined}
                onChange={(e) => patch({ onlinePaymentInstructions: e.target.value }, ["onlinePaymentInstructions"])} />)}</Field>
          </div></Card>

        <Card><CardHeader title="Closing the books" subtitle="Optional. Protects finished months from changes." />
          <div className="grid gap-4 p-5">
            <Field label="Books closed through" error={err("booksClosedThrough")}
              hint={form.booksClosedThrough ? `Payments, voids, bill generation and SOA corrections dated ${monthLabel(form.booksClosedThrough)} or earlier are refused.` : "Empty: no month is closed."}>{(id) => (
              <div className="flex gap-2">
                <input id={id} type="month" className="input" value={form.booksClosedThrough} aria-invalid={Boolean(err("booksClosedThrough")) || undefined}
                  onChange={(e) => patch({ booksClosedThrough: e.target.value as RatesAndRulesUpdate["booksClosedThrough"] }, ["booksClosedThrough"])} />
                {form.booksClosedThrough && <Button type="button" variant="ghost" onClick={() => patch({ booksClosedThrough: "" }, ["booksClosedThrough"])}>Clear</Button>}
              </div>)}</Field>
            <p className="text-[12.5px] text-ink-500">Every change to this date is recorded in the Audit Logs.</p>
          </div></Card>

        <Card className="xl:col-span-2"><CardHeader title="SOA email (SMTP)" subtitle="Optional. Used only to email statements to owners and tenants." />
          <div className="grid gap-4 p-5 sm:grid-cols-2 lg:grid-cols-4">
            {textField("smtpHost", "SMTP host", { placeholder: "smtp.example.com" })}
            {textField("smtpPort", "Port", { placeholder: "587" })}
            {textField("smtpSender", "Sender email", { type: "email", placeholder: "billing@example.com" })}
            {textField("smtpUsername", "Username", { autoComplete: "off" })}
            <div className="space-y-2 sm:col-span-2">
              <Field label="Password" error={err("smtpPassword")}
                hint={saved.smtpPasswordSet ? "A password is saved (encrypted, never shown). Type a new one only to replace it." : "No password saved."}>{(id) => (
                <input id={id} type="password" className="input" autoComplete="new-password" disabled={form.smtpPasswordClear}
                  placeholder={saved.smtpPasswordSet ? "•••••••• saved" : ""} value={form.smtpPassword}
                  onChange={(e) => patch({ smtpPassword: e.target.value }, ["smtpPassword"])} />)}</Field>
              {saved.smtpPasswordSet && (
                <label className="flex items-center gap-2 text-[13px] text-ink-700">
                  <input type="checkbox" className="size-4 accent-brand-600" checked={form.smtpPasswordClear}
                    onChange={(e) => patch({ smtpPasswordClear: e.target.checked, smtpPassword: "" }, ["smtpPassword"])} />
                  Remove the saved password
                </label>
              )}
            </div>
          </div></Card>
        <button type="submit" hidden />
      </form>

      {/* Long form: keep Save reachable while editing further down. */}
      {dirty && (
        <div className="sticky bottom-0 z-10 -mx-4 mt-6 flex flex-wrap items-center justify-end gap-2 border-t border-ink-200 bg-white/95 px-4 py-3 backdrop-blur sm:-mx-6 sm:px-6">
          <span className="mr-auto text-[12.5px] font-semibold text-copper-700">{changed.length + (smtpChange ? 1 : 0)} unsaved change{changed.length + (smtpChange ? 1 : 0) === 1 ? "" : "s"}</span>
          <Button variant="secondary" disabled={busy} onClick={() => { setForm(toForm(saved)); setTouched(false); setServerFields({}); setServerError(""); }}>Discard</Button>
          <Button icon="save-3-line" loading={busy} onClick={askToSave}>Save changes</Button>
        </div>
      )}

      <ConfirmDialog open={confirming} busy={busy} title="Save Rates & Rules?" confirmLabel="Save changes"
        onClose={() => { if (!busy) setConfirming(false); }} onConfirm={save}
        message={<div className="space-y-3">
          <p>These settings change:</p>
          <ul className="list-disc space-y-0.5 pl-5 text-[13px]">
            {changed.map((k) => <li key={k}>{LABELS[k] ?? k}</li>)}
            {smtpChange && <li>SMTP password ({smtpChange === "remove" ? "removed" : "replaced"})</li>}
          </ul>
          {affectsOpenBills
            ? <Notice tone="warn">The penalty rule and the storage month are applied when a statement is shown, so they also change the totals of <b>unpaid bills</b> (as on the classic screen). Recorded payments and receipts don't change.</Notice>
            : <p className="text-[13px] text-ink-500">Bills generated from now on use the new rates. Issued bills, payments and receipts don't change.</p>}
          <p className="text-[13px] text-ink-500">The change is recorded in the audit log.</p>
        </div>} />
    </>
  );
}
