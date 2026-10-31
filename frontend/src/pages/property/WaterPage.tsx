// Water Readings (Superadmin, Admin). Live: /api/water. Monthly meter readings, corrections and
// water payments. The server applies the rules (current >= previous, default rate, the month's bill
// follows the reading, closed periods, one payment per form) and issues the official receipts.
import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useAuth } from "../../auth/AuthContext";
import { DataTable } from "../../components/base/DataTable";
import { Drawer, useToast } from "../../components/base/overlays";
import { Button, Card, CardHeader, Field, Icon, Notice, PageHeader, StatCard, StatusBadge, Tabs, cx } from "../../components/base/ui";
import { focusFirstInvalid } from "../../components/feature/AccountFields";
import { BulkWaterReadingUpload } from "../../components/feature/BulkWaterReadingUpload";
import { useAction, useAsync } from "../../hooks/useAsync";
import { addMonths, currentMonth, monthLabel, todayIso } from "../../lib/format";
import { fromCents, isAmount, peso, toCents } from "../../lib/money";
import { api } from "../../services/api";
import { ApiError } from "../../services/http";
import type { CommercialWaterReadingAdmin, PaymentMethod, WaterMonth, WaterPaymentInput, WaterReadingAdmin } from "../../services/types";
import { MonthPicker, newFormToken } from "./Billing";

const MONTH_RE = /^\d{4}-(0[1-9]|1[0-2])$/;
const READING_RE = /^\d{1,8}(\.\d{1,3})?$/;
const RATE_RE = /^\d{1,6}(\.\d{1,4})?$/;
const defaultReadingPeriod = (month: string) => ({ readingFrom: `${addMonths(month, -1)}-16`, readingTo: `${month}-15` });

export default function WaterPage() {
  const [params, setParams] = useSearchParams();
  const pm = params.get("month") ?? "";
  const [month, setMonthState] = useState(MONTH_RE.test(pm) ? pm : currentMonth());
  const [tab, setTab] = useState<"month" | "unpaid">("month");
  const view = useAsync(() => api.water.month(month), [month]);
  const [editing, setEditing] = useState<WaterReadingAdmin | null>(null);
  const [editingCommercial, setEditingCommercial] = useState<CommercialWaterReadingAdmin | null>(null);
  const [paying, setPaying] = useState<WaterReadingAdmin | null>(null);
  const [bulkOpen, setBulkOpen] = useState(false);
  const { can } = useAuth();
  const v = view.data;
  const movedForm = params.get("moved") === "form";
  const setParam = (k: string, val: string | null) => { const n = new URLSearchParams(params); if (val) n.set(k, val); else n.delete(k); setParams(n, { replace: true }); };
  const setMonth = (m: string) => { setMonthState(m); setParam("month", m === currentMonth() ? null : m); };

  const actions = (r: WaterReadingAdmin) => (
    <div className="flex justify-end gap-1.5 whitespace-nowrap">
      {can("edit_water") && <Button size="sm" variant="secondary" icon="edit-line" onClick={() => setEditing(r)}>Correct</Button>}
      {can("mark_water_paid") && r.status !== "Paid" && toCents(r.balance ?? r.amount) > 0 && <Button size="sm" icon="hand-coin-line" onClick={() => setPaying(r)}>Payment</Button>}
    </div>
  );
  const columns = (withMonth: boolean) => [
    { key: "u", header: "Unit", cell: (r: WaterReadingAdmin) => <><b className="whitespace-nowrap text-ink-900">{r.unitNo}</b>{r.payerName && <span className="block text-[12px] text-ink-500">{r.payerName}</span>}</> },
    ...(withMonth ? [{ key: "m", header: "Month", cell: (r: WaterReadingAdmin) => monthLabel(r.month) }] : []),
    { key: "p", header: "Previous", align: "right" as const, cell: (r: WaterReadingAdmin) => r.previous },
    { key: "c", header: "Current", align: "right" as const, cell: (r: WaterReadingAdmin) => r.current },
    { key: "x", header: "Usage", align: "right" as const, cell: (r: WaterReadingAdmin) => <b>{r.usage} m³</b> },
    { key: "a", header: "Amount", align: "right" as const, cell: (r: WaterReadingAdmin) => <>{peso(r.amount)}<span className="block text-[12px] text-ink-500">@ {peso(r.rate)}/m³</span></> },
    { key: "s", header: "Status", cell: (r: WaterReadingAdmin) => <><StatusBadge status={r.status ?? (r.paid ? "Paid" : "Unpaid")} />{toCents(r.paidAmount ?? "0") > 0 && r.status !== "Paid" && <span className="block text-[12px] text-ink-500">{peso(r.balance ?? "0")} left</span>}</> },
    { key: "act", header: "", cell: actions },
  ];

  return (
    <>
      <PageHeader eyebrow="Property & Billing" title="Water Readings"
        description="Enter the dates covered by each meter reading. The reading period appears on the SOA. Shared commercial water charges are allocated to unit SOAs by area."
        actions={<div className="flex flex-wrap items-center gap-2">
          {can("water") && <Button variant="secondary" icon="upload-2-line" onClick={() => setBulkOpen(true)}>Bulk upload</Button>}
          <MonthPicker value={month} onChange={setMonth} />
        </div>} />
      {movedForm && (
        <div className="mb-4" role="status"><Notice tone="warn">
          <div className="flex flex-wrap items-center justify-between gap-2"><span>Water Readings moved to this screen. The form you sent from the old page was <b>not saved</b>; please enter it here.</span>
            <Button size="sm" variant="ghost" onClick={() => setParam("moved", null)}>Dismiss</Button></div>
        </Notice></div>
      )}
      {v && !v.autoCompute && <div className="mb-4"><Notice tone="warn">Automatic water computation is turned off in Rates &amp; Rules, so new bills get no water charge from these readings.</Notice></div>}
      <div className="mb-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard tone="hero" icon="drop-line" label={`Read for ${monthLabel(month)}`} value={v ? `${v.totals.read} / ${v.totals.read + v.totals.toRead}` : "—"} hint={v ? `${v.missing.length} unit(s) · ${v.commercialMissing.length} shared type(s) still to read` : undefined} />
        <StatCard icon="water-flash-line" label="Usage this month" value={v ? `${v.totals.usage} m³` : "—"} />
        <StatCard icon="money-dollar-circle-line" label="Water charges this month" value={v ? peso(v.totals.amount) : "—"} />
        <StatCard tone="copper" icon="hourglass-line" label="Unpaid individual water (all months)" value={v ? peso(v.totals.unpaid) : "—"} />
      </div>
      <div className="grid gap-6 2xl:grid-cols-[360px_minmax(0,1fr)]">
        {can("water") && (
          <Card className="self-start 2xl:sticky 2xl:top-4">
            <CardHeader title="Meter reading tool" subtitle={v ? `${v.missing.length} unit(s) still to read for ${monthLabel(month)}` : undefined} />
            {v && <div className="px-5 pt-4"><div className="h-2 overflow-hidden rounded-full bg-ink-100"><div className="h-full rounded-full bg-chart-1 transition-all" style={{ width: `${v.readings.length + v.missing.length ? (v.readings.length / (v.readings.length + v.missing.length)) * 100 : 0}%` }} /></div></div>}
            {v && <ReadingTool key={month} month={month} view={v} onSaved={view.reload} />}
          </Card>
        )}
        <Card>
          <div className="border-b border-ink-100 p-4"><Tabs value={tab} onChange={setTab} tabs={[{ key: "month", label: monthLabel(month), count: v?.readings.length }, { key: "unpaid", label: "Unpaid meter charges", count: v?.unpaid.length }]} /></div>
          {tab === "month"
            ? <DataTable rows={v ? v.readings : null} loading={view.loading} error={view.error} onRetry={view.reload} rowKey={(r) => r.id}
                empty={{ icon: "drop-line", title: `No readings yet for ${monthLabel(month)}`, text: "Use the meter reading tool to enter them." }} columns={columns(false)} />
            : <DataTable rows={v ? v.unpaid : null} loading={view.loading} error={view.error} onRetry={view.reload} rowKey={(r) => r.id}
                empty={{ icon: "checkbox-circle-line", title: "No unpaid individual water readings", text: "Shared commercial charges are paid through each unit's SOA." }} columns={columns(true)} />}
        </Card>
      </div>
      {v && v.commercialTypes.length > 0 && (
        <Card className="mt-6">
          <CardHeader title="Shared commercial water readings"
            subtitle="One meter reading per commercial type and month; the shared charge is allocated to unit SOAs by area." />
          <div className="px-5 pt-4"><Notice>Shared commercial water charges are included in unit SOAs; do not record a separate water payment here.</Notice></div>
          {can("water") && <CommercialReadingTool key={month} month={month} view={v} onSaved={view.reload} />}
          <DataTable rows={v.commercialReadings} loading={view.loading} error={view.error} onRetry={view.reload}
            rowKey={(r) => r.id}
            empty={{ icon: "drop-line", title: `No shared readings for ${monthLabel(month)}`, text: "Enter one reading for each commercial type above." }}
            columns={[
              { key: "type", header: "Commercial type", cell: (r) => <b className="text-ink-900">{r.commercialType}</b> },
              { key: "p", header: "Previous", align: "right", cell: (r) => r.previous },
              { key: "c", header: "Current", align: "right", cell: (r) => r.current },
              { key: "u", header: "Shared usage", align: "right", cell: (r) => <b>{r.usage} m³</b> },
              { key: "area", header: "Area allocated", align: "right", cell: (r) => <>{r.totalArea.toLocaleString()} m²<span className="block text-[12px] text-ink-500">{r.unitCount} units</span></> },
              { key: "amount", header: "Shared charge", align: "right", cell: (r) => <>{peso(r.amount)}<span className="block text-[12px] text-ink-500">@ {peso(r.rate)}/m³</span></> },
              { key: "act", header: "", cell: (r) => can("edit_water") ? <div className="flex justify-end"><Button size="sm" variant="secondary" icon="edit-line" onClick={() => setEditingCommercial(r)}>Correct</Button></div> : null },
            ]} />
        </Card>
      )}
      {editing && <CorrectDrawer reading={editing} defaultRate={v?.defaultRate ?? ""} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); view.reload(); }} />}
      {editingCommercial && <CorrectCommercialDrawer reading={editingCommercial} defaultRate={v?.defaultRate ?? ""}
        onClose={() => setEditingCommercial(null)} onSaved={() => { setEditingCommercial(null); view.reload(); }} />}
      {paying && <WaterPaymentDrawer reading={paying} onClose={() => setPaying(null)} onDone={() => { setPaying(null); view.reload(); }} />}
      <BulkWaterReadingUpload open={bulkOpen} onClose={() => setBulkOpen(false)} onCreated={view.reload} />
    </>
  );
}

// ------------------------------------------------------------------ reading tool
function ReadingTool({ month, view, onSaved }: { month: string; view: WaterMonth; onSaved: () => void }) {
  const first = view.missing[0];
  const [unitId, setUnitId] = useState(first?.unitId ?? 0);
  const [previous, setPrevious] = useState(first ? String(first.previous) : "");
  const [current, setCurrent] = useState("");
  const [rate, setRate] = useState(view.defaultRate);
  const [period, setPeriod] = useState(() => defaultReadingPeriod(month));
  const [touched, setTouched] = useState(false);
  const [serverFields, setServerFields] = useState<Record<string, string>>({});
  const { busy, act } = useAction();
  const toast = useToast();
  const ref = useRef<HTMLFormElement>(null);
  const units = [...view.missing.map((u) => ({ id: u.unitId, label: `${u.unitNo}${u.payerName ? ` — ${u.payerName}` : ""}`, previous: u.previous, done: false })),
    ...view.readings.map((r) => ({ id: r.unitId, label: `${r.unitNo} ✓ read (correct it)`, previous: r.previous, done: true }))];

  function pick(id: number) {
    setUnitId(id); setCurrent(""); setTouched(false); setServerFields({});
    const u = units.find((x) => x.id === id);
    setPrevious(u ? String(u.previous) : "");
  }
  const prev = Number(previous), cur = Number(current);
  const errors: Record<string, string> = {
    unitId: !unitId ? "Choose a unit." : "",
    previous: previous !== "" && !READING_RE.test(previous) ? "Enter a reading, up to 3 decimals." : "",
    current: current === "" ? "Enter the current reading." : !READING_RE.test(current) ? "Enter a reading, up to 3 decimals." : cur < prev ? "Can't be lower than the previous reading." : "",
    rate: rate !== "" && !RATE_RE.test(rate) ? "Enter the rate per m³." : "",
    readingFrom: !period.readingFrom ? "Choose the start date." : period.readingTo && period.readingFrom > period.readingTo ? "Start date must be on or before the end date." : "",
    readingTo: !period.readingTo ? "Choose the end date." : "",
  };
  const err = (k: string) => (touched ? errors[k] : "") || serverFields[k] || "";
  const usage = !errors.current && !errors.previous && current !== "" ? Math.round((cur - prev) * 1000) / 1000 : null;
  const amount = usage !== null && RATE_RE.test(rate || view.defaultRate) ? peso(fromCents(Math.round(usage * toCents(rate || view.defaultRate)))) : "—";

  async function save() {
    setTouched(true);
    if (busy) return;
    if (Object.values(errors).some(Boolean)) { focusFirstInvalid(ref); return; }
    try {
      const r = await act(() => api.water.save({ unitId, month, previous, current, rate, ...period }));
      toast({ tone: "success", title: `Saved ${r.reading.unitNo}: ${r.reading.usage} m³ · ${peso(r.reading.amount)}`, message: r.billUpdated ? `The ${monthLabel(month)} bill was updated.` : undefined });
      const next = view.missing.find((u) => u.unitId !== unitId);
      pick(next?.unitId ?? 0);
      onSaved();
    } catch (e) {
      if (e instanceof ApiError && Object.keys(e.fields).length) { setServerFields(e.fields); focusFirstInvalid(ref); }
      else toast({ tone: "error", title: "Reading not saved", message: (e as Error).message });
    }
  }

  if (units.length === 0) return <p className="p-5 text-ink-500">No residential units.</p>;
  return (
    <form ref={ref} className="grid gap-4 p-5 sm:grid-cols-2" onSubmit={(e) => { e.preventDefault(); void save(); }} noValidate>
      <Field label="Unit" className="sm:col-span-2" error={err("unitId")} hint={view.missing.length ? `${view.missing.length} unit(s) still to read` : "All units read for this month"}>{(id) => (
        <select id={id} className="input" value={unitId} onChange={(e) => pick(Number(e.target.value))}>
          <option value={0}>Choose a unit…</option>
          {units.map((u) => <option key={u.id} value={u.id}>{u.label}</option>)}
        </select>)}</Field>
      <Field label="Previous reading" error={err("previous")} hint="Carried over from last month">{(id) => <input id={id} inputMode="decimal" className="input tabular" value={previous} aria-invalid={Boolean(err("previous")) || undefined} onChange={(e) => setPrevious(e.target.value)} />}</Field>
      <Field label="Current reading" error={err("current")}>{(id) => <input id={id} inputMode="decimal" className="input tabular" data-autofocus value={current} aria-invalid={Boolean(err("current")) || undefined} onChange={(e) => { setCurrent(e.target.value); setServerFields({}); }} />}</Field>
      <Field label="Rate (₱ per m³)" error={err("rate")} hint={`Rates & Rules: ₱${view.defaultRate}`}>{(id) => <input id={id} inputMode="decimal" className="input tabular" value={rate} onChange={(e) => setRate(e.target.value)} />}</Field>
      <Field label="Reading period from" error={err("readingFrom")}>{(id) => <input id={id} type="date" className="input" value={period.readingFrom} onChange={(e) => setPeriod({ ...period, readingFrom: e.target.value })} />}</Field>
      <Field label="Reading period to" error={err("readingTo")}>{(id) => <input id={id} type="date" className="input" value={period.readingTo} onChange={(e) => setPeriod({ ...period, readingTo: e.target.value })} />}</Field>
      <div className="flex items-center justify-between rounded-xl bg-brand-50 px-4 py-3 sm:col-span-2">
        <span className="text-brand-800">Usage {usage === null ? "—" : `${usage} m³`}</span>
        <b className="font-display text-[18px] text-brand-900 tabular">{amount}</b>
      </div>
      <div className="flex justify-end sm:col-span-2"><Button type="submit" icon="save-3-line" loading={busy}>Save reading</Button></div>
    </form>
  );
}

function CommercialReadingTool({ month, view, onSaved }: { month: string; view: WaterMonth; onSaved: () => void }) {
  const first = view.commercialMissing[0];
  const [commercialType, setCommercialType] = useState<"2F" | "UG" | "LG" | "">(first?.commercialType ?? "");
  const [previous, setPrevious] = useState(first ? String(first.previous) : "");
  const [current, setCurrent] = useState("");
  const [rate, setRate] = useState(view.defaultRate);
  const [period, setPeriod] = useState(() => defaultReadingPeriod(month));
  const [touched, setTouched] = useState(false);
  const [serverFields, setServerFields] = useState<Record<string, string>>({});
  const { busy, act } = useAction();
  const toast = useToast();
  const ref = useRef<HTMLFormElement>(null);
  const selected = view.commercialMissing.find((x) => x.commercialType === commercialType);
  const errors: Record<string, string> = {
    commercialType: !commercialType ? "Choose a commercial type." : "",
    previous: previous !== "" && !READING_RE.test(previous) ? "Enter a reading, up to 3 decimals." : "",
    current: current === "" ? "Enter the current reading." : !READING_RE.test(current) ? "Enter a reading, up to 3 decimals." : Number(current) < Number(previous || 0) ? "Can't be lower than the previous reading." : "",
    rate: rate !== "" && !RATE_RE.test(rate) ? "Enter the rate per m³." : "",
    readingFrom: !period.readingFrom ? "Choose the start date." : period.readingTo && period.readingFrom > period.readingTo ? "Start date must be on or before the end date." : "",
    readingTo: !period.readingTo ? "Choose the end date." : "",
  };
  const err = (key: string) => (touched ? errors[key] : "") || serverFields[key] || "";
  const usage = !errors.current && !errors.previous && current !== "" ? Math.round((Number(current) - Number(previous || 0)) * 1000) / 1000 : null;
  const amount = usage !== null && RATE_RE.test(rate || view.defaultRate) ? peso(fromCents(Math.round(usage * toCents(rate || view.defaultRate)))) : "—";

  async function save() {
    setTouched(true);
    if (busy) return;
    if (Object.values(errors).some(Boolean)) { focusFirstInvalid(ref); return; }
    if (!commercialType) { focusFirstInvalid(ref); return; }
    try {
      const result = await act(() => api.water.saveCommercial({
        commercialType, month, previous, current, rate, ...period,
      }));
      toast({ tone: "success", title: `Saved ${commercialType}: ${result.reading.usage} m³ · ${peso(result.reading.amount)}`,
        message: result.billUpdated ? "Matching commercial SOAs were updated." : "The shared charge will be allocated to matching commercial SOAs." });
      const next = view.commercialMissing.find((group) => group.commercialType !== commercialType);
      const nextType = next?.commercialType ?? "";
      setCommercialType(nextType);
      setPrevious(next ? String(next.previous) : "");
      setCurrent("");
      setTouched(false);
      setServerFields({});
      onSaved();
    } catch (e) {
      if (e instanceof ApiError && Object.keys(e.fields).length) { setServerFields(e.fields); focusFirstInvalid(ref); }
      else toast({ tone: "error", title: "Shared reading not saved", message: (e as Error).message });
    }
  }

  if (!view.commercialMissing.length) {
    return <p className="px-5 py-4 text-sm text-ink-500">All active commercial types have a shared reading for {monthLabel(month)}.</p>;
  }
  return (
    <form ref={ref} className="grid gap-4 border-b border-ink-100 p-5 sm:grid-cols-2 lg:grid-cols-6"
      onSubmit={(e) => { e.preventDefault(); void save(); }} noValidate>
      <Field label="Commercial type" className="lg:col-span-2" error={err("commercialType")}
        hint={selected ? `${selected.unitCount} units · ${selected.totalArea.toLocaleString()} m² total area` : undefined}>{(id) => (
          <select id={id} className="input" value={commercialType} onChange={(e) => {
            const value = e.target.value as typeof commercialType;
            const group = view.commercialMissing.find((x) => x.commercialType === value);
            setCommercialType(value); setPrevious(group ? String(group.previous) : ""); setCurrent(""); setServerFields({});
          }}>
            <option value="">Choose a type…</option>
            {view.commercialMissing.map((group) => <option key={group.commercialType} value={group.commercialType}>{group.commercialType}</option>)}
          </select>)}</Field>
      <Field label="Previous reading" error={err("previous")} hint="Carried over from the last shared reading">{(id) => <input id={id} inputMode="decimal" className="input tabular" value={previous} onChange={(e) => setPrevious(e.target.value)} />}</Field>
      <Field label="Current reading" error={err("current")}>{(id) => <input id={id} inputMode="decimal" className="input tabular" data-autofocus value={current} onChange={(e) => { setCurrent(e.target.value); setServerFields({}); }} />}</Field>
      <Field label="Rate (₱ per m³)" error={err("rate")} hint={`Rates & Rules: ₱${view.defaultRate}`}>{(id) => <input id={id} inputMode="decimal" className="input tabular" value={rate} onChange={(e) => setRate(e.target.value)} />}</Field>
      <Field label="Reading period from" error={err("readingFrom")}>{(id) => <input id={id} type="date" className="input" value={period.readingFrom} onChange={(e) => setPeriod({ ...period, readingFrom: e.target.value })} />}</Field>
      <Field label="Reading period to" error={err("readingTo")}>{(id) => <input id={id} type="date" className="input" value={period.readingTo} onChange={(e) => setPeriod({ ...period, readingTo: e.target.value })} />}</Field>
      <div className="flex items-center justify-between rounded-xl bg-brand-50 px-4 py-3 sm:col-span-2 lg:col-span-4">
        <span className="text-brand-800">Shared usage {usage === null ? "—" : `${usage} m³`}</span>
        <b className="font-display text-[18px] text-brand-900 tabular">{amount}</b>
      </div>
      <div className="flex items-center justify-end sm:col-span-2 lg:col-span-2"><Button type="submit" icon="save-3-line" loading={busy}>Save shared reading</Button></div>
    </form>
  );
}

function CorrectCommercialDrawer({ reading, defaultRate, onClose, onSaved }: {
  reading: CommercialWaterReadingAdmin; defaultRate: string; onClose: () => void; onSaved: () => void;
}) {
  const [form, setForm] = useState({ previous: String(reading.previous), current: String(reading.current), rate: String(reading.rate),
    ...defaultReadingPeriod(reading.month), readingFrom: reading.readingFrom ?? defaultReadingPeriod(reading.month).readingFrom,
    readingTo: reading.readingTo ?? reading.readingDate ?? defaultReadingPeriod(reading.month).readingTo });
  const [serverError, setServerError] = useState("");
  const [serverFields, setServerFields] = useState<Record<string, string>>({});
  const { busy, act } = useAction();
  const toast = useToast();
  const errors: Record<string, string> = {
    previous: !READING_RE.test(form.previous) ? "Enter a reading." : "",
    current: !READING_RE.test(form.current) ? "Enter a reading." : Number(form.current) < Number(form.previous) ? "Can't be lower than the previous reading." : "",
    rate: form.rate !== "" && !RATE_RE.test(form.rate) ? "Enter the rate per m³." : "",
    readingFrom: !form.readingFrom ? "Choose the start date." : form.readingTo && form.readingFrom > form.readingTo ? "Start date must be on or before the end date." : "",
    readingTo: !form.readingTo ? "Choose the end date." : "",
  };
  const err = (key: string) => errors[key] || serverFields[key] || "";
  async function save() {
    if (busy || Object.values(errors).some(Boolean)) return;
    try {
      const result = await act(() => api.water.correctCommercial(reading.id, form));
      toast({ tone: "success", title: `${reading.commercialType} ${monthLabel(reading.month)} corrected`,
        message: `${result.reading.usage} m³ · ${peso(result.reading.amount)}${result.billUpdated ? " · matching SOAs were updated" : ""}` });
      onSaved();
    } catch (e) {
      if (e instanceof ApiError && Object.keys(e.fields).length) setServerFields(e.fields);
      else setServerError((e as Error).message);
    }
  }
  return (
    <Drawer open onClose={() => { if (!busy) onClose(); }} width="max-w-md"
      title={`Correct shared reading · ${reading.commercialType}`} subtitle={monthLabel(reading.month)}
      footer={<><Button variant="secondary" onClick={onClose} disabled={busy}>Cancel</Button><Button icon="save-3-line" loading={busy} onClick={save}>Save correction</Button></>}>
      <div className="space-y-4">
        {serverError && <Notice tone="warn">{serverError}</Notice>}
        <Notice>The shared charge is reallocated among the active units of this commercial type by area.</Notice>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Previous reading" error={err("previous")}>{(id) => <input id={id} inputMode="decimal" className="input tabular" value={form.previous} onChange={(e) => setForm({ ...form, previous: e.target.value })} />}</Field>
          <Field label="Current reading" error={err("current")}>{(id) => <input id={id} inputMode="decimal" className="input tabular" value={form.current} onChange={(e) => setForm({ ...form, current: e.target.value })} />}</Field>
          <Field label="Rate (₱ per m³)" error={err("rate")} hint={`Empty = Rates & Rules (₱${defaultRate})`}>{(id) => <input id={id} inputMode="decimal" className="input tabular" value={form.rate} onChange={(e) => setForm({ ...form, rate: e.target.value })} />}</Field>
          <Field label="Reading period from" error={err("readingFrom")}>{(id) => <input id={id} type="date" className="input" value={form.readingFrom} onChange={(e) => setForm({ ...form, readingFrom: e.target.value })} />}</Field>
          <Field label="Reading period to" error={err("readingTo")}>{(id) => <input id={id} type="date" className="input" value={form.readingTo} onChange={(e) => setForm({ ...form, readingTo: e.target.value })} />}</Field>
        </div>
      </div>
    </Drawer>
  );
}

// ------------------------------------------------------------------ correct a reading
function CorrectDrawer({ reading, defaultRate, onClose, onSaved }: { reading: WaterReadingAdmin; defaultRate: string; onClose: () => void; onSaved: () => void }) {
  const [form, setForm] = useState({ previous: String(reading.previous), current: String(reading.current), rate: String(reading.rate),
    readingFrom: reading.readingFrom ?? defaultReadingPeriod(reading.month).readingFrom,
    readingTo: reading.readingTo ?? reading.readingDate ?? defaultReadingPeriod(reading.month).readingTo });
  const [serverFields, setServerFields] = useState<Record<string, string>>({});
  const [serverError, setServerError] = useState("");
  const { busy, act } = useAction();
  const toast = useToast();
  const errors: Record<string, string> = {
    previous: !READING_RE.test(form.previous) ? "Enter a reading." : "",
    current: !READING_RE.test(form.current) ? "Enter a reading." : Number(form.current) < Number(form.previous) ? "Can't be lower than the previous reading." : "",
    rate: form.rate !== "" && !RATE_RE.test(form.rate) ? "Enter the rate per m³." : "",
    readingFrom: !form.readingFrom ? "Choose the start date." : form.readingTo && form.readingFrom > form.readingTo ? "Start date must be on or before the end date." : "",
    readingTo: !form.readingTo ? "Choose the end date." : "",
  };
  const err = (k: string) => errors[k] || serverFields[k] || "";
  async function save() {
    if (busy || Object.values(errors).some(Boolean)) return;
    try {
      const r = await act(() => api.water.correct(reading.id, form));
      toast({ tone: "success", title: `${reading.unitNo} ${monthLabel(reading.month)} corrected`, message: `${r.reading.usage} m³ · ${peso(r.reading.amount)}${r.billUpdated ? " · the bill was updated" : ""}` });
      onSaved();
    } catch (e) {
      if (e instanceof ApiError && Object.keys(e.fields).length) setServerFields(e.fields);
      else setServerError((e as Error).message);
    }
  }
  return (
    <Drawer open onClose={() => { if (!busy) onClose(); }} width="max-w-md" title={`Correct reading · ${reading.unitNo}`} subtitle={monthLabel(reading.month)}
      footer={<><Button variant="secondary" onClick={onClose} disabled={busy}>Cancel</Button><Button icon="save-3-line" loading={busy} onClick={save}>Save correction</Button></>}>
      <div className="space-y-4">
        {serverError && <Notice tone="warn">{serverError}</Notice>}
        {reading.billId && <Notice>The {monthLabel(reading.month)} bill was already generated: its water charge will follow this correction.</Notice>}
        {toCents(reading.paidAmount ?? "0") > 0 && <Notice tone="warn">{peso(reading.paidAmount ?? "0")} was already paid for this reading. Correcting it changes the amount due, not the payments.</Notice>}
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Previous reading" error={err("previous")}>{(id) => <input id={id} inputMode="decimal" className="input tabular" value={form.previous} onChange={(e) => setForm({ ...form, previous: e.target.value })} />}</Field>
          <Field label="Current reading" error={err("current")}>{(id) => <input id={id} inputMode="decimal" className="input tabular" value={form.current} onChange={(e) => setForm({ ...form, current: e.target.value })} />}</Field>
          <Field label="Rate (₱ per m³)" error={err("rate")} hint={`Empty = Rates & Rules (₱${defaultRate})`}>{(id) => <input id={id} inputMode="decimal" className="input tabular" value={form.rate} onChange={(e) => setForm({ ...form, rate: e.target.value })} />}</Field>
          <Field label="Reading period from" error={err("readingFrom")}>{(id) => <input id={id} type="date" className="input" value={form.readingFrom} onChange={(e) => setForm({ ...form, readingFrom: e.target.value })} />}</Field>
          <Field label="Reading period to" error={err("readingTo")}>{(id) => <input id={id} type="date" className="input" value={form.readingTo} onChange={(e) => setForm({ ...form, readingTo: e.target.value })} />}</Field>
        </div>
      </div>
    </Drawer>
  );
}

// ------------------------------------------------------------------ water payment
function WaterPaymentDrawer({ reading, onClose, onDone }: { reading: WaterReadingAdmin; onClose: () => void; onDone: () => void }) {
  const balance = reading.balance ?? reading.amount;
  const [form, setForm] = useState<WaterPaymentInput>(() => ({ amount: balance, method: "CASH", type: "FULL", reference: "", date: todayIso(), payerType: reading.payerRequired ? "" : reading.chargePayer, formToken: newFormToken() }));
  const [touched, setTouched] = useState(false);
  const [serverError, setServerError] = useState("");
  const [result, setResult] = useState<{ receiptNo: string; applied: string } | null>(null);
  const { busy, act } = useAction();
  const errors = {
    amount: !isAmount(form.amount) ? "Enter the amount received." : "",
    reference: form.method !== "CASH" && !form.reference.trim() ? "Required for check and online payments." : "",
    payerType: reading.payerRequired && !form.payerType ? "Choose Owner or Tenant." : "",
  };
  useEffect(() => setServerError(""), [form]);
  async function submit() {
    setTouched(true);
    if (busy || errors.amount || errors.reference || errors.payerType) return;
    try {
      const r = await act(() => api.water.pay(reading.id, { ...form, type: toCents(form.amount) >= toCents(balance) ? "FULL" : "PARTIAL" }));
      setResult({ receiptNo: r.receiptNo, applied: r.applied });
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) { setServerError("This payment was already recorded, so it was not recorded twice."); return; }
      setServerError((e as Error).message);
    }
  }
  return (
    <Drawer open onClose={() => { if (!busy) (result ? onDone : onClose)(); }} width="max-w-md" title={`Water payment · ${reading.unitNo}`} subtitle={`${monthLabel(reading.month)} · balance ${peso(balance)}`}
      footer={result ? <Button onClick={onDone}>Done</Button> : <><Button variant="secondary" onClick={onClose} disabled={busy}>Cancel</Button><Button icon="hand-coin-line" loading={busy} onClick={submit}>Record payment & issue OR</Button></>}>
      {result ? (
        <div className="space-y-3 text-center">
          <div className="mx-auto grid size-14 place-items-center rounded-full bg-emerald-50 text-3xl text-emerald-600"><Icon name="checkbox-circle-fill" /></div>
          <p className="text-ink-500">Official receipt issued</p><p className="font-display text-[26px] font-semibold text-ink-900">{result.receiptNo}</p>
          <p>{peso(result.applied)} applied to the water charge.</p>
        </div>
      ) : (
        <form className="grid gap-4 sm:grid-cols-2" onSubmit={(e) => { e.preventDefault(); void submit(); }} noValidate>
          {serverError && <div className="sm:col-span-2"><Notice tone="warn">{serverError}</Notice></div>}
          {reading.payerRequired && <Field label="Payer ledger" className="sm:col-span-2" error={touched && errors.payerType}>{(id) => (
            <select id={id} className="input" value={form.payerType ?? ""} onChange={(e) => setForm({ ...form, payerType: e.target.value as "Owner" | "Tenant" })}>
              <option value="">Choose…</option><option value="Owner">Owner</option><option value="Tenant">Tenant</option>
            </select>)}</Field>}
          <Field label="Amount received (₱)" error={touched && errors.amount} hint="At most the balance is applied.">{(id) => <input id={id} inputMode="decimal" className="input tabular" value={form.amount} onChange={(e) => setForm({ ...form, amount: e.target.value })} />}</Field>
          <Field label="Payment date">{(id) => <input id={id} type="date" className="input" value={form.date} onChange={(e) => setForm({ ...form, date: e.target.value })} />}</Field>
          <Field label="Method">{(id) => <select id={id} className="input" value={form.method} onChange={(e) => setForm({ ...form, method: e.target.value as PaymentMethod })}><option value="CASH">Cash</option><option value="CHECK">Check</option><option value="ONLINE">Online / bank transfer</option></select>}</Field>
          <Field label={form.method === "CASH" ? "Reference no. (optional)" : "Reference no. (required)"} error={touched && errors.reference}>{(id) => <input id={id} className={cx("input")} maxLength={100} value={form.reference} onChange={(e) => setForm({ ...form, reference: e.target.value })} />}</Field>
          <button type="submit" hidden />
        </form>
      )}
    </Drawer>
  );
}
