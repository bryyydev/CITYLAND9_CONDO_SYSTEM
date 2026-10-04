// Front desk & community: gate passes, move in/out certificates, expenses, maintenance,
// vendors, documents, announcements. APIs: backend/app/routes/operations.py and community.py.
import { Fragment, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Can, useAuth } from "../../auth/AuthContext";
import { DataTable } from "../../components/base/DataTable";
import { Drawer, useToast } from "../../components/base/overlays";
import { Badge, Button, Card, CardHeader, EmptyState, ErrorState, Field, Icon, Notice, PageHeader, SearchInput, Skeleton, StatCard, StatusBadge, Tabs } from "../../components/base/ui";
import { useAction, useAsync } from "../../hooks/useAsync";
import { addDays, currentMonth, dateLabel, dateTimeLabel, monthLabel, todayIso } from "../../lib/format";
import { isAmount, peso } from "../../lib/money";
import { api } from "../../services/api";
import { ApiError } from "../../services/http";
import type { AdminGatePass, CertificateInput, DocumentAudience, DocumentInput, GatePassInput, GatePassType, MaintenanceBoard, MaintenanceTicket, MoveType, TicketInput, TicketStatus, Vendor, VendorInput } from "../../services/types";
import { MonthPicker } from "../property/Billing";

/** Per-field messages from a 400 response (empty for other errors). */
const serverFields = (err: unknown): Record<string, string> => (err instanceof ApiError ? err.fields : {});

/** Shown after a classic page redirected here with ?moved=form: the old form was not saved. */
function MovedFormNotice({ screen }: { screen: string }) {
  const [params, setParams] = useSearchParams();
  if (params.get("moved") !== "form") return null;
  const dismiss = () => { const next = new URLSearchParams(params); next.delete("moved"); setParams(next, { replace: true }); };
  return (
    <div className="mb-4" role="status"><Notice tone="warn">
      <div className="flex flex-wrap items-center justify-between gap-2"><span>{screen} moved to this screen. The form you sent from the old page was <b>not saved</b>; please enter it here.</span>
        <Button size="sm" variant="ghost" onClick={dismiss}>Dismiss</Button></div>
    </Notice></div>
  );
}

// ------------------------------------------------------------------ Gate passes
export function GatePassesPage() {
  const { can } = useAuth();
  const { data, error, loading, reload } = useAsync(() => api.gatePasses.list(), []);
  const [tab, setTab] = useState<"requests" | "today" | "all">("requests");
  const [q, setQ] = useState("");
  const [issuing, setIssuing] = useState(false);
  const [reviewing, setReviewing] = useState<AdminGatePass | null>(null);
  const today = todayIso();
  const all = data ?? [];
  const requests = all.filter((p) => p.status === "Requested");
  const validToday = all.filter((p) => p.date === today && p.status === "Issued");
  const rows = useMemo(() => {
    const base = tab === "requests" ? requests : tab === "today" ? validToday : all;
    const n = q.trim().toLowerCase();
    return n ? base.filter((p) => [p.unitNo, p.name, p.purpose, p.type].some((v) => v.toLowerCase().includes(n))) : base;
  }, [tab, q, requests, validToday, all]);
  return (
    <>
      <PageHeader eyebrow="Front Desk" title="Gate Passes" description="Issue passes for visitors, deliveries and moving, and approve the requests residents send from the portal."
        actions={<Button icon="add-line" onClick={() => setIssuing(true)}>Issue gate pass</Button>} />
      <MovedFormNotice screen="Gate Passes" />
      <div className="mb-6 grid gap-4 sm:grid-cols-3">
        <StatCard tone={requests.length ? "copper" : "default"} icon="inbox-archive-line" label="Waiting for approval" value={requests.length} hint="Requests from residents" />
        <StatCard icon="passport-line" label="Valid today" value={validToday.length} />
        <StatCard icon="truck-line" label="Moves in the next 7 days" value={all.filter((p) => (p.type === "Move-in" || p.type === "Move-out") && p.status === "Issued" && (p.date ?? "") >= today && (p.date ?? "") <= addDays(today, 7)).length} />
      </div>
      <Card>
        <div className="flex flex-wrap items-center gap-3 border-b border-ink-100 p-4">
          <Tabs value={tab} onChange={setTab} tabs={[{ key: "requests", label: "Resident requests", count: requests.length }, { key: "today", label: "Valid today" }, { key: "all", label: "All passes", count: data?.length }]} />
          <div className="flex-1" /><SearchInput value={q} onChange={setQ} placeholder="Unit, name or purpose" />
        </div>
        <DataTable rows={data ? rows : null} loading={loading} error={error} onRetry={reload} rowKey={(p) => p.id}
          empty={{ icon: "passport-line", title: tab === "requests" ? "No requests waiting" : "No passes", text: tab === "requests" ? "New requests from residents appear here." : undefined }}
          columns={[
            { key: "d", header: "Date", cell: (p) => <b className="whitespace-nowrap text-ink-900">{dateLabel(p.date)}</b> },
            { key: "t", header: "Type", cell: (p) => <Badge tone={p.type.startsWith("Move") ? "copper" : "info"} dot={false}>{p.type}</Badge> },
            { key: "u", header: "Unit", cell: (p) => p.unitNo },
            { key: "n", header: "Name / details", cell: (p) => <><span className="font-medium">{p.name}</span><span className="block max-w-xs truncate text-[12px] text-ink-500">{p.purpose}</span></> },
            { key: "s", header: "Source", cell: (p) => (p.source === "resident" ? <span className="text-[12.5px]">Resident · {p.requestedBy}<span className="block text-ink-400">{dateTimeLabel(p.requestedAt)}</span></span> : <span className="text-[12.5px] text-ink-500">Office{p.reviewedBy ? ` · ${p.reviewedBy}` : ""}</span>) },
            { key: "st", header: "Status", cell: (p) => <><StatusBadge status={p.status} />{p.reviewNote && <span className="mt-1 block max-w-[14rem] truncate text-[12px] text-ink-500" title={p.reviewNote}>{p.reviewNote}</span>}</> },
            { key: "a", header: "", cell: (p) => p.status === "Requested" && can("gate_pass_review") && <Button size="sm" onClick={() => setReviewing(p)}>Review</Button> },
          ]} />
      </Card>
      <IssuePassDrawer open={issuing} onClose={() => setIssuing(false)} onDone={() => { setIssuing(false); reload(); }} />
      <ReviewDrawer pass={reviewing} onClose={() => setReviewing(null)} onDone={() => { setReviewing(null); reload(); }} />
    </>
  );
}

function ReviewDrawer({ pass, onClose, onDone }: { pass: AdminGatePass | null; onClose: () => void; onDone: () => void }) {
  const [note, setNote] = useState("");
  const [noteError, setNoteError] = useState("");
  const { busy, act } = useAction();
  const toast = useToast();
  async function decide(decision: "approve" | "reject") {
    if (!pass) return;
    if (decision === "reject" && !note.trim()) { setNoteError("A reason is required when rejecting."); return; }
    try {
      await act(() => api.gatePasses.review(pass.id, decision, note.trim()));
      toast({ tone: "success", title: decision === "approve" ? "Pass approved" : "Request rejected", message: decision === "approve" ? `${pass.type} pass for unit ${pass.unitNo} on ${dateLabel(pass.date)}.` : "The resident will see your reason." });
      setNote(""); setNoteError("");
      onDone();
    } catch (err) {
      if (serverFields(err).note) setNoteError(serverFields(err).note);
      else toast({ tone: "error", title: "Couldn't save the decision", message: (err as Error).message });
      if (err instanceof ApiError && err.status === 409) onDone(); // someone else handled it: refresh the list
    }
  }
  return (
    <Drawer open={!!pass} onClose={onClose} title="Review request" subtitle={pass ? `Unit ${pass.unitNo} · sent by ${pass.requestedBy} · ${dateTimeLabel(pass.requestedAt)}` : ""} width="max-w-md"
      footer={<><Button variant="danger" icon="close-circle-line" loading={busy} onClick={() => decide("reject")}>Reject</Button><Button icon="checkbox-circle-line" loading={busy} onClick={() => decide("approve")}>Approve</Button></>}>
      {pass && (
        <div className="space-y-4">
          <dl className="divide-y divide-ink-100 rounded-xl border border-ink-200">
            {([["Type", pass.type], ["Date needed", dateLabel(pass.date)], ["Name", pass.name], ["Details", pass.purpose]] as const).map(([k, v]) => (
              <div key={k} className="grid grid-cols-[110px_1fr] gap-3 px-4 py-2.5"><dt className="text-ink-500">{k}</dt><dd className="font-medium text-ink-900">{v}</dd></div>
            ))}
          </dl>
          <Field label="Note to the resident" error={noteError} hint="Optional when approving, required when rejecting.">
            {(id) => <textarea id={id} rows={3} maxLength={300} className="input h-auto py-2" value={note} onChange={(e) => { setNote(e.target.value); setNoteError(""); }} placeholder="e.g. Please use the service elevator; bring a valid ID." />}
          </Field>
        </div>
      )}
    </Drawer>
  );
}

const EMPTY_PASS: GatePassInput = { type: "Visitor", date: todayIso(), unitNo: "", name: "", purpose: "" };
function IssuePassDrawer({ open, onClose, onDone }: { open: boolean; onClose: () => void; onDone: () => void }) {
  const [form, setForm] = useState<GatePassInput>(EMPTY_PASS);
  const [touched, setTouched] = useState(false);
  const [server, setServer] = useState<Record<string, string>>({});
  const { busy, act } = useAction();
  const toast = useToast();
  const set = (patch: Partial<GatePassInput>) => { setForm({ ...form, ...patch }); setServer({}); };
  const bad = { unitNo: !form.unitNo.trim(), name: !form.name.trim(), purpose: !form.purpose.trim() };
  async function submit() {
    setTouched(true);
    if (bad.unitNo || bad.name || bad.purpose) return;
    try {
      const p = await act(() => api.gatePasses.issue(form));
      toast({ tone: "success", title: "Gate pass issued", message: `${p.type} · unit ${p.unitNo} · ${dateLabel(p.date)}` });
      setForm({ ...EMPTY_PASS, date: todayIso() }); setTouched(false);
      onDone();
    } catch (err) {
      setServer(serverFields(err));
      if (!Object.keys(serverFields(err)).length) toast({ tone: "error", title: "Pass not issued", message: (err as Error).message });
    }
  }
  return (
    <Drawer open={open} onClose={onClose} title="Issue gate pass" subtitle="Passes issued by the office are valid immediately." width="max-w-md"
      footer={<><Button variant="secondary" onClick={onClose}>Cancel</Button><Button icon="passport-line" loading={busy} onClick={submit}>Issue pass</Button></>}>
      <form className="grid gap-4 sm:grid-cols-2" onSubmit={(e) => { e.preventDefault(); void submit(); }} noValidate>
        <Field label="Type">{(id) => <select id={id} className="input" value={form.type} onChange={(e) => set({ type: e.target.value as GatePassType })}>{["Visitor", "Delivery", "Move-in", "Move-out"].map((t) => <option key={t}>{t}</option>)}</select>}</Field>
        <Field label="Date" error={server.date}>{(id) => <input id={id} type="date" className="input" value={form.date} onChange={(e) => set({ date: e.target.value })} />}</Field>
        <Field label="Unit no." className="sm:col-span-2" error={server.unitNo || (touched && bad.unitNo && "Enter the unit number.")}>{(id) => <input id={id} className="input" placeholder="e.g. 12-01" value={form.unitNo} onChange={(e) => set({ unitNo: e.target.value })} />}</Field>
        <Field label={form.type.startsWith("Move") ? "Mover / trucking company" : form.type === "Delivery" ? "Courier / company" : "Visitor name"} className="sm:col-span-2" error={server.name || (touched && bad.name && "Enter a name.")}>{(id) => <input id={id} className="input" maxLength={200} value={form.name} onChange={(e) => set({ name: e.target.value })} />}</Field>
        <Field label="Purpose" className="sm:col-span-2" error={server.purpose || (touched && bad.purpose && "Enter the purpose.")}>{(id) => <input id={id} className="input" maxLength={300} value={form.purpose} onChange={(e) => set({ purpose: e.target.value })} />}</Field>
      </form>
    </Drawer>
  );
}

// ------------------------------------------------------------------ Move In / Out certificates
export function CertificatesPage() {
  const [params, setParams] = useSearchParams();
  const openId = Number(params.get("certificate")) || null;
  const show = (id: number | null) => {
    const next = new URLSearchParams(params);
    if (id) next.set("certificate", String(id)); else next.delete("certificate");
    next.delete("moved");
    setParams(next);
  };
  return openId ? <CertificateView id={openId} onBack={() => show(null)} /> : <CertificateWorkspace onOpen={show} />;
}

function CertificateWorkspace({ onOpen }: { onOpen: (id: number) => void }) {
  const list = useAsync(() => api.certificates.list(), []);
  const options = useAsync(() => api.certificates.options(), []);
  const [form, setForm] = useState<CertificateInput>({ unitId: 0, personType: "Tenant", personId: 0, moveType: "Move In", certificateDate: todayIso() });
  const [touched, setTouched] = useState(false);
  const [server, setServer] = useState<Record<string, string>>({});
  const { busy, act } = useAction();
  const toast = useToast();
  const set = (patch: Partial<CertificateInput>) => { setForm({ ...form, ...patch }); setServer({}); };
  const unit = (options.data ?? []).find((u) => u.id === form.unitId);
  const candidates = (unit?.people ?? []).filter((p) => p.type === form.personType);
  const person = candidates.find((p) => p.id === form.personId);
  const moveDate = person ? (form.moveType === "Move In" ? person.moveIn : person.moveOut) : null;

  async function generate() {
    setTouched(true);
    if (!form.unitId || !form.personId) return;
    try {
      const c = await act(() => api.certificates.generate(form));
      toast({ tone: "success", title: `Certificate ${c.certificateNo} generated`, message: `${c.moveType} · ${c.personName} · unit ${c.unitNo}` });
      onOpen(c.id);
    } catch (err) {
      setServer(serverFields(err));
      if (!Object.keys(serverFields(err)).length) toast({ tone: "error", title: "Certificate not generated", message: (err as Error).message });
    }
  }

  return (
    <>
      <PageHeader eyebrow="Front Desk" title="Move In / Out Certificates" description="Pick the unit and the owner or tenant, then generate a numbered certificate. Move dates come from the owner/tenant record on the unit page." />
      <MovedFormNotice screen="Move In / Out Certificates" />
      <div className="grid gap-6 xl:grid-cols-[400px_1fr]">
        <Card className="self-start">
          <CardHeader title="New certificate" />
          {options.error ? <ErrorState message={options.error.message} onRetry={options.reload} /> : (
            <form className="grid gap-4 p-5" onSubmit={(e) => { e.preventDefault(); void generate(); }} noValidate>
              <div className="grid grid-cols-2 gap-2" role="group" aria-label="Certificate type">
                {(["Move In", "Move Out"] as MoveType[]).map((m) => (
                  <button key={m} type="button" aria-pressed={form.moveType === m} onClick={() => set({ moveType: m })}
                    className={`flex items-center justify-center gap-2 rounded-lg border px-3 py-2.5 font-semibold transition ${form.moveType === m ? "border-brand-600 bg-brand-50 text-brand-700" : "border-ink-200 text-ink-600 hover:bg-ink-50"}`}>
                    <Icon name={m === "Move In" ? "login-box-line" : "logout-box-line"} />{m}
                  </button>
                ))}
              </div>
              <Field label="Unit" error={server.unitId || (touched && !form.unitId && "Choose the unit.")}>{(id) => (
                <select id={id} className="input" value={form.unitId} disabled={options.loading} onChange={(e) => set({ unitId: Number(e.target.value), personId: 0 })}>
                  <option value={0}>{options.loading ? "Loading units…" : "Choose a unit…"}</option>{(options.data ?? []).map((u) => <option key={u.id} value={u.id}>{u.unitNo}</option>)}
                </select>)}</Field>
              <Field label="Person">{(id) => (
                <select id={id} className="input" value={form.personType} onChange={(e) => set({ personType: e.target.value as "Owner" | "Tenant", personId: 0 })}>
                  <option>Tenant</option><option>Owner</option>
                </select>)}</Field>
              <Field label={form.personType} error={server.personId || (touched && !form.personId && `Choose the ${form.personType.toLowerCase()}.`)}
                hint={form.unitId && !candidates.length ? `No ${form.personType.toLowerCase()} recorded for this unit. Add them on the unit page first.` : "Current and past owners/tenants are listed."}>{(id) => (
                <select id={id} className="input" value={form.personId} disabled={!form.unitId} onChange={(e) => set({ personId: Number(e.target.value) })}>
                  <option value={0}>Choose…</option>{candidates.map((p) => <option key={p.id} value={p.id}>{p.name} ({p.status})</option>)}
                </select>)}</Field>
              <Field label="Certificate date" error={server.certificateDate}>{(id) => <input id={id} type="date" className="input" value={form.certificateDate} onChange={(e) => set({ certificateDate: e.target.value })} />}</Field>
              {person && (
                <p className={`rounded-lg px-3 py-2 text-[13px] ${moveDate ? "bg-ink-50 text-ink-600" : "bg-amber-50 text-amber-900"}`}>
                  {form.moveType === "Move In" ? "Move-in" : "Move-out"} date on record: <b>{moveDate ? dateLabel(moveDate) : "not set"}</b>
                  {!moveDate && <span className="block">The certificate will say “Not specified”. Set the date on the unit page if you have it.</span>}
                </p>
              )}
              <Button type="submit" icon="file-paper-2-line" loading={busy}>Generate certificate</Button>
            </form>
          )}
        </Card>
        <Card>
          <CardHeader title="Issued certificates" subtitle="Open one to view or print it." />
          <DataTable rows={list.data} loading={list.loading} error={list.error} onRetry={list.reload} rowKey={(c) => c.id} onRowClick={(c) => onOpen(c.id)} empty={{ icon: "file-paper-2-line", title: "No certificates yet" }}
            columns={[
              { key: "n", header: "Certificate", cell: (c) => <b className="whitespace-nowrap text-ink-900">{c.certificateNo}</b> },
              { key: "t", header: "Type", cell: (c) => <Badge tone={c.moveType === "Move In" ? "ok" : "copper"} dot={false}>{c.moveType}</Badge> },
              { key: "p", header: "Person", cell: (c) => <>{c.personName}<span className="block text-[12px] text-ink-500">{c.personType} · unit {c.unitNo}</span></> },
              { key: "m", header: "Move date", cell: (c) => <span className="whitespace-nowrap">{c.moveDate ? dateLabel(c.moveDate) : "—"}</span> },
              { key: "d", header: "Issued", cell: (c) => <span className="whitespace-nowrap">{dateLabel(c.certificateDate)}<span className="block text-[12px] text-ink-500">by {c.issuedBy}</span></span> },
            ]} />
        </Card>
      </div>
    </>
  );
}

function CertificateView({ id, onBack }: { id: number; onBack: () => void }) {
  const { data, error, reload } = useAsync(() => api.certificates.detail(id), [id]);
  const back = <Button variant="ghost" icon="arrow-left-line" onClick={onBack}>All certificates</Button>;
  if (error) return <><PageHeader eyebrow="Front Desk" title="Certificate" actions={back} /><ErrorState title={error.status === 404 ? "Certificate not found" : "Couldn't load the certificate"} message={error.message} onRetry={error.status === 404 ? undefined : reload} /></>;
  if (!data) return <Card><Skeleton rows={8} /></Card>;
  const { certificate: c, unit, person } = data;
  const row = (cells: [string, string][], wide = false) => (
    <tr>{cells.map(([k, v]) => <Fragment key={k}><th className="w-[18%] border border-ink-300 bg-ink-50 px-3 py-2 text-left font-semibold">{k}</th><td colSpan={wide ? 3 : 1} className="border border-ink-300 px-3 py-2">{v}</td></Fragment>)}</tr>
  );
  return (
    <>
      <PageHeader eyebrow="Front Desk" title={c.certificateNo} description={`${c.moveType} certificate · ${c.personName} · unit ${c.unitNo}`}
        actions={<>{back}<Button icon="printer-line" onClick={() => window.print()}>Print</Button></>} />
      <article className="print-sheet mx-auto max-w-[860px] rounded-xl border border-ink-200 bg-white p-8 leading-relaxed text-ink-800 shadow-card sm:p-10" aria-label="Certificate">
        <header className="border-b-2 border-ink-900 pb-4 text-center">
          <p className="text-right text-[12px]">Certificate No.: <b>{c.certificateNo}</b></p>
          <p className="text-[12px] font-semibold tracking-[0.14em] text-ink-500">{data.corporation}</p>
          {data.address && <p className="text-[12px] text-ink-500">{data.address}</p>}
          <h2 className="mt-2 font-display text-[26px] font-bold text-ink-900">{c.moveType.toUpperCase()} CERTIFICATE</h2>
          <p>Official Unit Occupancy / Turnover Record</p>
        </header>
        <div className="space-y-5 pt-6">
          <p>This is to certify that <b>{c.personName}</b>, identified as the <b>{c.personType}</b> of Unit <b>{unit.unitNo}</b>{unit.type ? `, ${unit.type}` : ""}, is recorded by the Property Management Office as follows:</p>
          <table className="w-full border-collapse text-[13.5px]">
            <tbody>
              {row([["Unit Number", unit.unitNo], ["Type of Unit", unit.type || "—"]])}
              {row([["Area", unit.areaSqm != null ? `${unit.areaSqm.toLocaleString("en-PH", { maximumFractionDigits: 2 })} sqm` : "—"], ["Person Type", c.personType]])}
              {row([["Name", c.personName]], true)}
              {row([["Contact", person.contactNo || "—"], ["Email", person.email || "—"]])}
              {row([[`${c.moveType} Date`, c.moveDate ? dateLabel(c.moveDate) : "Not specified"], ["Certificate Date", dateLabel(c.certificateDate)]])}
              {row([["Issued By", c.issuedBy || "—"]], true)}
            </tbody>
          </table>
          {c.personType === "Tenant" && person.representative && <p><b>Representative:</b> Yes</p>}
          <p>This certificate is issued upon request for property management and administrative purposes.</p>
          <div className="grid grid-cols-2 gap-16 pt-16 text-center">
            <div><div className="mb-2 border-t border-ink-900" /><b>Property Management Representative</b></div>
            <div><div className="mb-2 border-t border-ink-900" /><b>{c.personType}</b></div>
          </div>
        </div>
      </article>
    </>
  );
}

// ------------------------------------------------------------------ Expenses
const DEFAULT_CATEGORIES = ["Utilities", "Security", "Repairs", "Supplies", "Salaries", "Professional fees", "Other"];
export function ExpensesPage() {
  const [month, setMonth] = useState(currentMonth());
  const { data, error, loading, reload } = useAsync(() => api.expenses.list(month), [month]);
  const [form, setForm] = useState({ date: todayIso(), category: "Utilities", description: "", amount: "" });
  const [touched, setTouched] = useState(false);
  const [server, setServer] = useState<Record<string, string>>({});
  const { busy, act } = useAction();
  const toast = useToast();
  const set = (patch: Partial<typeof form>) => { setForm({ ...form, ...patch }); setServer({}); };
  const categories = [...new Set([...DEFAULT_CATEGORIES, ...(data?.categories ?? [])])];
  const bad = { category: !form.category.trim(), description: !form.description.trim(), amount: !isAmount(form.amount) };
  async function add() {
    setTouched(true);
    if (bad.category || bad.description || bad.amount) return;
    try {
      const e = await act(() => api.expenses.add(form));
      toast({ tone: "success", title: "Expense recorded", message: `${e.category} · ${peso(e.amount)} · ${dateLabel(e.date)}` });
      setForm({ ...form, description: "", amount: "" }); setTouched(false);
      if (e.date.startsWith(month)) reload(); else setMonth(e.date.slice(0, 7));
    } catch (err) {
      setServer(serverFields(err));
      if (!Object.keys(serverFields(err)).length) toast({ tone: "error", title: "Expense not recorded", message: (err as Error).message });
    }
  }
  const byCategory = Object.entries(data?.byCategory ?? {});
  return (
    <>
      <PageHeader eyebrow="Records" title="Expense Logs" description="Building expenses by date and category." actions={<MonthPicker value={month} onChange={setMonth} max={currentMonth()} />} />
      <MovedFormNotice screen="Expense Logs" />
      <div className="grid gap-6 xl:grid-cols-[380px_1fr]">
        <Card className="self-start">
          <CardHeader title="Record expense" />
          <form className="grid gap-4 p-5" onSubmit={(e) => { e.preventDefault(); void add(); }} noValidate>
            <div className="grid grid-cols-2 gap-4">
              <Field label="Date" error={server.date}>{(id) => <input id={id} type="date" className="input" max={todayIso()} value={form.date} onChange={(e) => set({ date: e.target.value })} />}</Field>
              <Field label="Category" error={server.category || (touched && bad.category && "Choose a category.")}>{(id) => <select id={id} className="input" value={form.category} onChange={(e) => set({ category: e.target.value })}>{categories.map((c) => <option key={c}>{c}</option>)}</select>}</Field>
            </div>
            <Field label="Description" error={server.description || (touched && bad.description && "Describe the expense.")}>{(id) => <input id={id} className="input" maxLength={300} value={form.description} onChange={(e) => set({ description: e.target.value })} />}</Field>
            <Field label="Amount (₱)" error={server.amount || (touched && bad.amount && "Enter an amount greater than zero.")}>{(id) => <input id={id} inputMode="decimal" className="input tabular" value={form.amount} onChange={(e) => set({ amount: e.target.value })} />}</Field>
            <Button type="submit" icon="add-line" loading={busy}>Record expense</Button>
          </form>
        </Card>
        <Card>
          <CardHeader title={`Expenses · ${monthLabel(month)}`} subtitle={data ? `Total ${peso(data.total)}` : undefined} />
          {byCategory.length > 0 && (
            <div className="flex flex-wrap gap-2 border-b border-ink-100 px-5 py-3">
              {byCategory.map(([cat, amt]) => <span key={cat} className="rounded-full bg-ink-50 px-3 py-1 text-[12.5px] text-ink-600">{cat} <b className="text-ink-900">{peso(amt)}</b></span>)}
            </div>
          )}
          <DataTable rows={data?.expenses ?? null} loading={loading} error={error} onRetry={reload} rowKey={(e) => e.id} empty={{ icon: "wallet-3-line", title: "No expenses this month" }}
            columns={[
              { key: "d", header: "Date", cell: (e) => <span className="whitespace-nowrap">{dateLabel(e.date)}</span> },
              { key: "c", header: "Category", cell: (e) => <Badge tone="neutral" dot={false}>{e.category}</Badge> },
              { key: "x", header: "Description", cell: (e) => e.description },
              { key: "a", header: "Amount", align: "right", cell: (e) => <b className="whitespace-nowrap">{peso(e.amount)}</b> },
            ]} />
        </Card>
      </div>
    </>
  );
}

// ------------------------------------------------------------------ Maintenance
export function MaintenancePage() {
  const { can } = useAuth();
  const { data, error, loading, reload } = useAsync(() => api.maintenance.board(), []);
  const [status, setStatus] = useState<TicketStatus | "Active">("Active");
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<MaintenanceTicket | null>(null);
  const [filing, setFiling] = useState(false);
  const tickets = data?.tickets ?? [];
  const matches = (t: MaintenanceTicket, s: TicketStatus | "Active") => (s === "Active" ? t.status === "Open" || t.status === "In Progress" : t.status === s);
  const rows = useMemo(() => {
    const n = q.trim().toLowerCase();
    return tickets.filter((t) => matches(t, status) && (!n || [t.ticketNo, t.unitNo, t.title, t.vendor, t.assignedTo].some((v) => v.toLowerCase().includes(n))));
  }, [tickets, status, q]);
  const statuses: TicketStatus[] = data?.statuses ?? ["Open", "In Progress", "Resolved", "Closed"];
  return (
    <>
      <PageHeader eyebrow="Community" title="Maintenance Tickets" description="Requests from residents and the office. Status updates and notes are visible to the resident."
        actions={<Button icon="add-line" disabled={!data} onClick={() => setFiling(true)}>New ticket</Button>} />
      <MovedFormNotice screen="Maintenance Tickets" />
      <Card>
        <div className="flex flex-wrap items-center gap-3 border-b border-ink-100 p-4">
          <Tabs value={status} onChange={setStatus} tabs={(["Active", ...statuses] as const).map((s) => ({ key: s, label: s, count: tickets.filter((t) => matches(t, s)).length }))} />
          <div className="flex-1" /><SearchInput value={q} onChange={setQ} placeholder="Ticket, unit, title or vendor" />
        </div>
        <DataTable rows={data ? rows : null} loading={loading} error={error} onRetry={reload} rowKey={(t) => t.id} onRowClick={setOpen} empty={{ icon: "tools-line", title: "No tickets here" }}
          columns={[
            { key: "n", header: "Ticket", cell: (t) => <><b className="text-ink-900">{t.title}</b><span className="block text-[12px] whitespace-nowrap text-ink-500">{t.ticketNo} · {t.category}</span></> },
            { key: "u", header: "Unit", cell: (t) => t.unitNo },
            { key: "p", header: "Priority", cell: (t) => <Badge tone={t.priority === "Urgent" ? "bad" : t.priority === "High" ? "warn" : "neutral"} dot={false}>{t.priority}</Badge> },
            { key: "a", header: "Assigned", cell: (t) => (t.assignedTo || t.vendor ? <>{t.assignedTo}{t.vendor && <span className="block text-[12px] text-ink-500">{t.vendor}</span>}</> : <span className="text-ink-400">Unassigned</span>) },
            { key: "r", header: "Requested", cell: (t) => <span className="text-[12.5px]">{dateTimeLabel(t.requestedAt)}<span className="block text-ink-400">{t.source === "resident" ? "Resident" : "Office"}</span></span> },
            { key: "s", header: "Status", cell: (t) => <StatusBadge status={t.status} /> },
          ]} />
      </Card>
      {data && <TicketDrawer board={data} ticket={open} canUpdate={can("maintenance_update")} onClose={() => setOpen(null)} onSaved={() => { setOpen(null); reload(); }} />}
      {data && <NewTicketDrawer board={data} open={filing} onClose={() => setFiling(false)} onDone={() => { setFiling(false); setStatus("Active"); reload(); }} />}
    </>
  );
}

function NewTicketDrawer({ board, open, onClose, onDone }: { board: MaintenanceBoard; open: boolean; onClose: () => void; onDone: () => void }) {
  const blank: TicketInput = { unitId: 0, category: board.categories[0] ?? "General", priority: "Normal", title: "", description: "" };
  const [form, setForm] = useState<TicketInput>(blank);
  const [touched, setTouched] = useState(false);
  const [server, setServer] = useState<Record<string, string>>({});
  const { busy, act } = useAction();
  const toast = useToast();
  const set = (patch: Partial<TicketInput>) => { setForm({ ...form, ...patch }); setServer({}); };
  const bad = { unitId: !form.unitId, title: !form.title.trim(), description: !form.description.trim() };
  async function submit() {
    setTouched(true);
    if (bad.unitId || bad.title || bad.description) return;
    try {
      const t = await act(() => api.maintenance.create(form));
      toast({ tone: "success", title: `Ticket ${t.ticketNo} filed`, message: `${t.title} · unit ${t.unitNo}` });
      setForm(blank); setTouched(false);
      onDone();
    } catch (err) {
      setServer(serverFields(err));
      if (!Object.keys(serverFields(err)).length) toast({ tone: "error", title: "Ticket not filed", message: (err as Error).message });
    }
  }
  return (
    <Drawer open={open} onClose={onClose} title="New maintenance ticket" subtitle="For problems reported at the desk or found by staff. The unit's residents see it in their portal." width="max-w-lg"
      footer={<><Button variant="secondary" onClick={onClose}>Cancel</Button><Button icon="tools-line" loading={busy} onClick={submit}>File ticket</Button></>}>
      <form className="grid gap-4 sm:grid-cols-2" onSubmit={(e) => { e.preventDefault(); void submit(); }} noValidate>
        <Field label="Unit" className="sm:col-span-2" error={server.unitId || (touched && bad.unitId && "Choose the unit.")}>{(id) => (
          <select id={id} className="input" value={form.unitId} onChange={(e) => set({ unitId: Number(e.target.value) })}>
            <option value={0}>Choose a unit…</option>{board.units.map((u) => <option key={u.id} value={u.id}>{u.unitNo}</option>)}
          </select>)}</Field>
        <Field label="Category" error={server.category}>{(id) => <select id={id} className="input" value={form.category} onChange={(e) => set({ category: e.target.value })}>{board.categories.map((c) => <option key={c}>{c}</option>)}</select>}</Field>
        <Field label="Priority" error={server.priority}>{(id) => <select id={id} className="input" value={form.priority} onChange={(e) => set({ priority: e.target.value })}>{board.priorities.map((c) => <option key={c}>{c}</option>)}</select>}</Field>
        <Field label="Title" className="sm:col-span-2" error={server.title || (touched && bad.title && "Enter a short title.")}>{(id) => <input id={id} className="input" maxLength={200} placeholder="e.g. Leaking pipe under the sink" value={form.title} onChange={(e) => set({ title: e.target.value })} />}</Field>
        <Field label="Description" className="sm:col-span-2" error={server.description || (touched && bad.description && "Describe the problem.")}>{(id) => <textarea id={id} rows={4} maxLength={4000} className="input h-auto py-2" value={form.description} onChange={(e) => set({ description: e.target.value })} />}</Field>
      </form>
    </Drawer>
  );
}

function TicketDrawer({ board, ticket, canUpdate, onClose, onSaved }: { board: MaintenanceBoard; ticket: MaintenanceTicket | null; canUpdate: boolean; onClose: () => void; onSaved: () => void }) {
  const [form, setForm] = useState({ status: "Open" as TicketStatus, priority: "Normal", assignedTo: "", vendorId: null as number | null, resolution: "" });
  const [loadedFor, setLoadedFor] = useState<number | null>(null);
  const [server, setServer] = useState<Record<string, string>>({});
  const { busy, act } = useAction();
  const toast = useToast();
  if (ticket && loadedFor !== ticket.id) {
    setLoadedFor(ticket.id);
    setForm({ status: ticket.status, priority: ticket.priority, assignedTo: ticket.assignedTo, vendorId: ticket.vendorId, resolution: ticket.resolution });
    setServer({});
  }
  // A vendor that was deactivated after being assigned stays selectable on its ticket.
  const vendorChoices = ticket?.vendorId && !board.vendors.some((v) => v.id === ticket.vendorId) ? [...board.vendors, { id: ticket.vendorId, name: `${ticket.vendor} (inactive)`, serviceType: "" }] : board.vendors;
  async function save() {
    if (!ticket) return;
    try {
      await act(() => api.maintenance.update(ticket.id, form));
      toast({ tone: "success", title: `Ticket ${ticket.ticketNo} updated` });
      setLoadedFor(null);
      onSaved();
    } catch (err) {
      setServer(serverFields(err));
      if (!Object.keys(serverFields(err)).length) toast({ tone: "error", title: "Ticket not updated", message: (err as Error).message });
    }
  }
  const ro = !canUpdate;
  return (
    <Drawer open={!!ticket} onClose={onClose} title={ticket?.title ?? ""} subtitle={ticket ? `${ticket.ticketNo} · unit ${ticket.unitNo} · ${ticket.category} · ${ticket.source === "resident" ? "from the resident" : "filed by the office"}` : ""}
      footer={<Can permission="maintenance_update"><Button icon="save-3-line" loading={busy} onClick={save}>Save update</Button></Can>}>
      {ticket && (
        <div className="space-y-5">
          <p className="rounded-xl bg-ink-50 p-4 whitespace-pre-line text-ink-700">{ticket.description}</p>
          <p className="text-[12.5px] text-ink-500">Requested {dateTimeLabel(ticket.requestedAt)}</p>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Status" error={server.status}>{(id) => <select id={id} className="input" disabled={ro} value={form.status} onChange={(e) => setForm({ ...form, status: e.target.value as TicketStatus })}>{board.statuses.map((s) => <option key={s}>{s}</option>)}</select>}</Field>
            <Field label="Priority" error={server.priority}>{(id) => <select id={id} className="input" disabled={ro} value={form.priority} onChange={(e) => setForm({ ...form, priority: e.target.value })}>{board.priorities.map((s) => <option key={s}>{s}</option>)}</select>}</Field>
            <Field label="Assigned to (staff)" error={server.assignedTo}>{(id) => <input id={id} className="input" disabled={ro} maxLength={120} value={form.assignedTo} onChange={(e) => setForm({ ...form, assignedTo: e.target.value })} />}</Field>
            <Field label="Vendor" error={server.vendorId}>{(id) => (
              <select id={id} className="input" disabled={ro} value={form.vendorId ?? 0} onChange={(e) => setForm({ ...form, vendorId: Number(e.target.value) || null })}>
                <option value={0}>None</option>{vendorChoices.map((v) => <option key={v.id} value={v.id}>{v.name}{v.serviceType ? ` · ${v.serviceType}` : ""}</option>)}
              </select>)}</Field>
            <Field label="Update for the resident" className="sm:col-span-2" error={server.resolution} hint="Shown to the resident in their portal.">{(id) => <textarea id={id} rows={3} maxLength={4000} disabled={ro} className="input h-auto py-2" value={form.resolution} onChange={(e) => setForm({ ...form, resolution: e.target.value })} />}</Field>
          </div>
        </div>
      )}
    </Drawer>
  );
}

// ------------------------------------------------------------------ Vendors
const EMPTY_VENDOR: VendorInput = { name: "", serviceType: "", contactPerson: "", contactNo: "", email: "", address: "", notes: "", status: "Active" };
export function VendorsPage() {
  const { data, error, loading, reload } = useAsync(() => api.vendors.list(), []);
  const [q, setQ] = useState("");
  const [showInactive, setShowInactive] = useState(false);
  const [editing, setEditing] = useState<Vendor | "new" | null>(null);
  const rows = useMemo(() => {
    const n = q.trim().toLowerCase();
    return (data ?? []).filter((v) => (showInactive || v.status === "Active") && (!n || [v.name, v.serviceType, v.contactPerson].some((x) => x.toLowerCase().includes(n))));
  }, [data, q, showInactive]);
  return (
    <>
      <PageHeader eyebrow="Community" title="Vendor Directory" description="Contractors and suppliers the building works with. Active vendors can be assigned to maintenance tickets."
        actions={<Button icon="add-line" onClick={() => setEditing("new")}>Add vendor</Button>} />
      <MovedFormNotice screen="Vendor Directory" />
      <Card>
        <div className="flex flex-wrap items-center gap-3 border-b border-ink-100 p-4">
          <SearchInput value={q} onChange={setQ} placeholder="Vendor, service or contact" />
          <label className="flex items-center gap-2 text-[13px] text-ink-600"><input type="checkbox" checked={showInactive} onChange={(e) => setShowInactive(e.target.checked)} />Show inactive</label>
        </div>
        <DataTable rows={data ? rows : null} loading={loading} error={error} onRetry={reload} rowKey={(v) => v.id} onRowClick={setEditing} empty={{ icon: "store-2-line", title: "No vendors", text: "Add the contractors and suppliers you work with." }} columns={[
          { key: "n", header: "Vendor", cell: (v) => <><b className="text-ink-900">{v.name}</b><span className="block text-[12px] text-ink-500">{v.serviceType || "—"}</span></> },
          { key: "c", header: "Contact person", cell: (v) => v.contactPerson || "—" },
          { key: "p", header: "Phone", cell: (v) => <span className="whitespace-nowrap">{v.contactNo || "—"}</span> },
          { key: "e", header: "Email", cell: (v) => v.email || "—" },
          { key: "s", header: "Status", cell: (v) => <StatusBadge status={v.status} /> },
        ]} />
      </Card>
      <VendorDrawer vendor={editing} onClose={() => setEditing(null)} onDone={() => { setEditing(null); reload(); }} />
    </>
  );
}

function VendorDrawer({ vendor, onClose, onDone }: { vendor: Vendor | "new" | null; onClose: () => void; onDone: () => void }) {
  const [form, setForm] = useState<VendorInput>(EMPTY_VENDOR);
  const [loadedFor, setLoadedFor] = useState<number | "new" | null>(null);
  const [touched, setTouched] = useState(false);
  const [server, setServer] = useState<Record<string, string>>({});
  const { busy, act } = useAction();
  const toast = useToast();
  const key = vendor === "new" ? "new" : vendor?.id ?? null;
  if (vendor && loadedFor !== key) {
    setLoadedFor(key);
    if (vendor === "new") setForm(EMPTY_VENDOR); else { const { id: _id, ...rest } = vendor; setForm(rest); }
    setTouched(false); setServer({});
  }
  const set = (patch: Partial<VendorInput>) => { setForm({ ...form, ...patch }); setServer({}); };
  const badName = !form.name.trim();
  async function save() {
    setTouched(true);
    if (badName || !vendor) return;
    try {
      const v = await act(() => (vendor === "new" ? api.vendors.add(form) : api.vendors.update(vendor.id, form)));
      toast({ tone: "success", title: vendor === "new" ? "Vendor added" : "Vendor updated", message: `${v.name}${v.status === "Inactive" ? " (inactive)" : ""}` });
      setLoadedFor(null);
      onDone();
    } catch (err) {
      setServer(serverFields(err));
      if (!Object.keys(serverFields(err)).length) toast({ tone: "error", title: "Vendor not saved", message: (err as Error).message });
    }
  }
  const text = (k: keyof VendorInput, label: string, max: number, extra: { className?: string; type?: string; placeholder?: string } = {}) => (
    <Field label={label} className={extra.className} error={server[k]}>{(id) => <input id={id} type={extra.type ?? "text"} className="input" maxLength={max} placeholder={extra.placeholder} value={form[k]} onChange={(e) => set({ [k]: e.target.value })} />}</Field>
  );
  return (
    <Drawer open={!!vendor} onClose={onClose} title={vendor === "new" ? "Add vendor" : vendor?.name ?? ""} width="max-w-lg"
      footer={<><Button variant="secondary" onClick={onClose}>Cancel</Button><Button icon="save-3-line" loading={busy} onClick={save}>Save vendor</Button></>}>
      <form className="grid gap-4 sm:grid-cols-2" onSubmit={(e) => { e.preventDefault(); void save(); }} noValidate>
        <Field label="Vendor name" className="sm:col-span-2" error={server.name || (touched && badName && "Enter the vendor's name.")}>{(id) => <input id={id} className="input" maxLength={200} value={form.name} onChange={(e) => set({ name: e.target.value })} />}</Field>
        {text("serviceType", "Service", 120, { placeholder: "e.g. Plumbing" })}
        <Field label="Status" error={server.status}>{(id) => <select id={id} className="input" value={form.status} onChange={(e) => set({ status: e.target.value as Vendor["status"] })}><option>Active</option><option>Inactive</option></select>}</Field>
        {text("contactPerson", "Contact person", 160)}
        {text("contactNo", "Phone", 80)}
        {text("email", "Email", 160, { className: "sm:col-span-2", type: "email" })}
        {text("address", "Address", 300, { className: "sm:col-span-2" })}
        <Field label="Notes" className="sm:col-span-2" error={server.notes}>{(id) => <textarea id={id} rows={3} maxLength={2000} className="input h-auto py-2" value={form.notes} onChange={(e) => set({ notes: e.target.value })} />}</Field>
      </form>
    </Drawer>
  );
}

// ------------------------------------------------------------------ Documents
const AUDIENCE_LABEL: Record<DocumentAudience, string> = { admin: "Office only", residents: "All residents", unit: "One unit's residents" };
export function DocumentsPage() {
  const { data, error, loading, reload } = useAsync(() => api.documents.list(), []);
  const [q, setQ] = useState("");
  const [adding, setAdding] = useState(false);
  const rows = useMemo(() => {
    const n = q.trim().toLowerCase();
    return (data?.documents ?? []).filter((d) => !n || [d.title, d.category, d.fileName, d.unitNo ?? ""].some((x) => x.toLowerCase().includes(n)));
  }, [data, q]);
  return (
    <>
      <PageHeader eyebrow="Community" title="Documents" description="Records of the building's documents: what each is, who may see it, and where the file is kept."
        actions={<Button icon="add-line" disabled={!data} onClick={() => setAdding(true)}>Add document</Button>} />
      <MovedFormNotice screen="Documents" />
      <div className="mb-4"><Notice tone="info">Files are not uploaded to the system; each record says where the file is kept (shared folder or cabinet). Records for residents appear in their portal under Announcements &amp; Notices.</Notice></div>
      <Card>
        <div className="border-b border-ink-100 p-4"><SearchInput value={q} onChange={setQ} placeholder="Title, category, file or unit" /></div>
        <DataTable rows={data ? rows : null} loading={loading} error={error} onRetry={reload} rowKey={(d) => d.id} empty={{ icon: "folder-3-line", title: "No documents recorded" }} columns={[
          { key: "t", header: "Title", cell: (d) => <><b className="text-ink-900">{d.title}</b>{d.description && <span className="block max-w-sm truncate text-[12px] text-ink-500" title={d.description}>{d.description}</span>}</> },
          { key: "c", header: "Category", cell: (d) => d.category },
          { key: "a", header: "Who can see it", cell: (d) => <Badge tone={d.audience === "admin" ? "neutral" : "info"} dot={false}>{d.audience === "unit" ? `Unit ${d.unitNo}` : AUDIENCE_LABEL[d.audience]}</Badge> },
          { key: "f", header: "File", cell: (d) => <span className="text-[12.5px]">{d.fileName || "—"}{d.filePath && <span className="block text-ink-500">{d.filePath}</span>}</span> },
          { key: "d", header: "Added", cell: (d) => <span className="whitespace-nowrap">{d.addedAt ? dateLabel(d.addedAt) : "—"}{d.addedBy && <span className="block text-[12px] text-ink-500">by {d.addedBy}</span>}</span> },
        ]} />
      </Card>
      {data && <DocumentDrawer units={data.units} open={adding} onClose={() => setAdding(false)} onDone={() => { setAdding(false); reload(); }} />}
    </>
  );
}

const EMPTY_DOC: DocumentInput = { title: "", category: "", description: "", audience: "admin", unitId: null, fileName: "", filePath: "" };
function DocumentDrawer({ units, open, onClose, onDone }: { units: { id: number; unitNo: string }[]; open: boolean; onClose: () => void; onDone: () => void }) {
  const [form, setForm] = useState<DocumentInput>(EMPTY_DOC);
  const [touched, setTouched] = useState(false);
  const [server, setServer] = useState<Record<string, string>>({});
  const { busy, act } = useAction();
  const toast = useToast();
  const set = (patch: Partial<DocumentInput>) => { setForm({ ...form, ...patch }); setServer({}); };
  const bad = { title: !form.title.trim(), unitId: form.audience === "unit" && !form.unitId };
  async function save() {
    setTouched(true);
    if (bad.title || bad.unitId) return;
    try {
      const d = await act(() => api.documents.add({ ...form, unitId: form.audience === "unit" ? form.unitId : null }));
      toast({ tone: "success", title: "Document recorded", message: `${d.title} · ${d.audience === "unit" ? `unit ${d.unitNo}` : AUDIENCE_LABEL[d.audience]}` });
      setForm(EMPTY_DOC); setTouched(false);
      onDone();
    } catch (err) {
      setServer(serverFields(err));
      if (!Object.keys(serverFields(err)).length) toast({ tone: "error", title: "Document not recorded", message: (err as Error).message });
    }
  }
  return (
    <Drawer open={open} onClose={onClose} title="Add document record" width="max-w-lg"
      footer={<><Button variant="secondary" onClick={onClose}>Cancel</Button><Button icon="save-3-line" loading={busy} onClick={save}>Save record</Button></>}>
      <form className="grid gap-4 sm:grid-cols-2" onSubmit={(e) => { e.preventDefault(); void save(); }} noValidate>
        <Field label="Title" className="sm:col-span-2" error={server.title || (touched && bad.title && "Enter the document's title.")}>{(id) => <input id={id} className="input" maxLength={200} value={form.title} onChange={(e) => set({ title: e.target.value })} />}</Field>
        <Field label="Category" error={server.category} hint="e.g. Policies, Contracts">{(id) => <input id={id} className="input" maxLength={100} value={form.category} onChange={(e) => set({ category: e.target.value })} />}</Field>
        <Field label="Who can see it" error={server.audience}>{(id) => (
          <select id={id} className="input" value={form.audience} onChange={(e) => set({ audience: e.target.value as DocumentAudience })}>
            {(Object.keys(AUDIENCE_LABEL) as DocumentAudience[]).map((a) => <option key={a} value={a}>{AUDIENCE_LABEL[a]}</option>)}
          </select>)}</Field>
        {form.audience === "unit" && (
          <Field label="Unit" className="sm:col-span-2" error={server.unitId || (touched && bad.unitId && "Choose the unit.")}>{(id) => (
            <select id={id} className="input" value={form.unitId ?? 0} onChange={(e) => set({ unitId: Number(e.target.value) || null })}>
              <option value={0}>Choose a unit…</option>{units.map((u) => <option key={u.id} value={u.id}>{u.unitNo}</option>)}
            </select>)}</Field>
        )}
        <Field label="File name" error={server.fileName}>{(id) => <input id={id} className="input" maxLength={300} placeholder="e.g. House Rules 2026.pdf" value={form.fileName} onChange={(e) => set({ fileName: e.target.value })} />}</Field>
        <Field label="Where the file is kept" error={server.filePath}>{(id) => <input id={id} className="input" maxLength={500} placeholder="Shared folder or cabinet" value={form.filePath} onChange={(e) => set({ filePath: e.target.value })} />}</Field>
        <Field label="Description" className="sm:col-span-2" error={server.description}>{(id) => <textarea id={id} rows={3} maxLength={4000} className="input h-auto py-2" value={form.description} onChange={(e) => set({ description: e.target.value })} />}</Field>
      </form>
    </Drawer>
  );
}

// ------------------------------------------------------------------ Announcements
const ANN_AUDIENCE: Record<string, string> = { residents: "Residents", staff: "Staff only", all: "Everyone" };
export function AnnouncementsPage() {
  const { data, error, loading, reload } = useAsync(() => api.announcements.list(), []);
  const [form, setForm] = useState({ title: "", message: "", audience: "residents" });
  const [touched, setTouched] = useState(false);
  const [server, setServer] = useState<Record<string, string>>({});
  const { busy, act } = useAction();
  const toast = useToast();
  const set = (patch: Partial<typeof form>) => { setForm({ ...form, ...patch }); setServer({}); };
  async function save(published: boolean) {
    setTouched(true);
    if (!form.title.trim() || !form.message.trim()) return;
    try {
      await act(() => api.announcements.publish({ ...form, published }));
      toast({ tone: "success", title: published ? "Announcement published" : "Draft saved",
        message: !published ? "It isn't shown to anyone yet." : form.audience === "staff" ? "Staff see it; residents don't." : "Residents see it in their portal right away." });
      setForm({ title: "", message: "", audience: "residents" }); setTouched(false);
      reload();
    } catch (err) {
      setServer(serverFields(err));
      if (!Object.keys(serverFields(err)).length) toast({ tone: "error", title: "Not saved", message: (err as Error).message });
    }
  }
  return (
    <>
      <PageHeader eyebrow="Communication" title="Announcements" description="Notices for residents appear on the resident portal as soon as they are published." />
      <MovedFormNotice screen="Announcements" />
      <div className="grid gap-6 xl:grid-cols-[420px_1fr]">
        <Card className="self-start">
          <CardHeader title="New announcement" />
          <form className="grid gap-4 p-5" onSubmit={(e) => { e.preventDefault(); void save(true); }} noValidate>
            <Field label="Title" error={server.title || (touched && !form.title.trim() && "Enter a title.")}>{(id) => <input id={id} className="input" maxLength={200} value={form.title} onChange={(e) => set({ title: e.target.value })} />}</Field>
            <Field label="Message" error={server.message || (touched && !form.message.trim() && "Write the message.")}>{(id) => <textarea id={id} rows={6} maxLength={10000} className="input h-auto py-2" value={form.message} onChange={(e) => set({ message: e.target.value })} />}</Field>
            <Field label="For" error={server.audience}>{(id) => <select id={id} className="input" value={form.audience} onChange={(e) => set({ audience: e.target.value })}>{Object.entries(ANN_AUDIENCE).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>}</Field>
            <div className="flex flex-wrap justify-end gap-2">
              <Button type="button" variant="secondary" loading={busy} onClick={() => save(false)}>Save as draft</Button>
              <Button type="submit" icon="megaphone-line" loading={busy}>Publish</Button>
            </div>
          </form>
        </Card>
        <Card>
          <CardHeader title="Announcements" />
          {error ? <ErrorState message={error.message} onRetry={reload} /> : loading && !data ? <Skeleton rows={5} /> : !data?.length ? <EmptyState icon="megaphone-line" title="No announcements yet" /> : (
            <ul className="divide-y divide-ink-100">
              {data.map((a) => (
                <li key={a.id} className="px-5 py-4">
                  <div className="flex flex-wrap items-baseline justify-between gap-2">
                    <h3 className="font-semibold text-ink-900">{a.title}</h3>
                    <span className="flex items-center gap-2 text-[12.5px] text-ink-400">
                      {!a.published && <Badge tone="warn" dot={false}>Draft</Badge>}
                      <Badge tone={a.audience === "staff" ? "neutral" : "info"} dot={false}>{ANN_AUDIENCE[a.audience] ?? a.audience}</Badge>
                      {dateLabel(a.publishDate)} · {a.createdBy}
                    </span>
                  </div>
                  <p className="mt-1 whitespace-pre-line text-ink-600">{a.message}</p>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </>
  );
}
