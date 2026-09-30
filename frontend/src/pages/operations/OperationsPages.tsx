// Front desk & community: gate passes, move certificates (Bug D1), expenses, maintenance,
// vendors, documents, announcements.
import { useMemo, useState } from "react";
import { Can, useAuth } from "../../auth/AuthContext";
import { DataTable } from "../../components/base/DataTable";
import { Drawer, useToast } from "../../components/base/overlays";
import { Badge, Button, Card, CardHeader, EmptyState, ErrorState, Field, Icon, Notice, PageHeader, SearchInput, Skeleton, StatCard, StatusBadge, Tabs } from "../../components/base/ui";
import { useAction, useAsync } from "../../hooks/useAsync";
import { addDays, currentMonth, dateLabel, dateTimeLabel, monthLabel, todayIso } from "../../lib/format";
import { fromCents, isAmount, peso, toCents } from "../../lib/money";
import { api } from "../../services/api";
import { ApiError } from "../../services/http";
import type { AdminGatePass, CertificateInput, GatePassInput, GatePassType, MaintenanceTicket, MoveType, TicketStatus } from "../../services/types";
import { MonthPicker } from "../property/Billing";

// ------------------------------------------------------------------ Gate passes
export function GatePassesPage() {
  const { can } = useAuth();
  const { data, error, loading, reload } = useAsync(() => api.gatePasses.list(), []);
  const [tab, setTab] = useState<"requests" | "today" | "all">("requests");
  const [issuing, setIssuing] = useState(false);
  const [reviewing, setReviewing] = useState<AdminGatePass | null>(null);
  const today = todayIso();
  const requests = (data ?? []).filter((p) => p.status === "Requested");
  const rows = tab === "requests" ? requests : tab === "today" ? (data ?? []).filter((p) => p.date === today && p.status === "Issued") : data ?? [];
  return (
    <>
      <PageHeader eyebrow="Front Desk" title="Gate Passes" description="Issue passes for visitors, deliveries and moving, and approve the requests residents send from the portal."
        actions={<Button icon="add-line" onClick={() => setIssuing(true)}>Issue gate pass</Button>} />
      <div className="mb-6 grid gap-4 sm:grid-cols-3">
        <StatCard tone={requests.length ? "copper" : "default"} icon="inbox-archive-line" label="Waiting for approval" value={requests.length} hint="Requests from residents" />
        <StatCard icon="passport-line" label="Valid today" value={(data ?? []).filter((p) => p.date === today && p.status === "Issued").length} />
        <StatCard icon="truck-line" label="Moves in the next 7 days" value={(data ?? []).filter((p) => (p.type === "Move-in" || p.type === "Move-out") && p.status === "Issued" && (p.date ?? "") >= today && (p.date ?? "") <= addDays(today, 7)).length} />
      </div>
      <Card>
        <div className="border-b border-ink-100 p-4"><Tabs value={tab} onChange={setTab} tabs={[{ key: "requests", label: "Resident requests", count: requests.length }, { key: "today", label: "Valid today" }, { key: "all", label: "All passes", count: data?.length }]} /></div>
        <DataTable rows={data ? rows : null} loading={loading} error={error} onRetry={reload} rowKey={(p) => p.id}
          empty={{ icon: "passport-line", title: tab === "requests" ? "No requests waiting" : "No passes", text: tab === "requests" ? "New requests from residents appear here." : undefined }}
          columns={[
            { key: "d", header: "Date", cell: (p) => <b className="text-ink-900">{dateLabel(p.date)}</b> },
            { key: "t", header: "Type", cell: (p) => <Badge tone={p.type.startsWith("Move") ? "copper" : "info"} dot={false}>{p.type}</Badge> },
            { key: "u", header: "Unit", cell: (p) => p.unitNo },
            { key: "n", header: "Name / details", cell: (p) => <><span className="font-medium">{p.name}</span><span className="block max-w-xs truncate text-[12px] text-ink-500">{p.purpose}</span></> },
            { key: "s", header: "Source", cell: (p) => (p.source === "resident" ? <span className="text-[12.5px]">Resident · {p.requestedBy}<span className="block text-ink-400">{dateTimeLabel(p.requestedAt)}</span></span> : <span className="text-[12.5px] text-ink-500">Office</span>) },
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
  const [rejectTried, setRejectTried] = useState(false);
  const { busy, act } = useAction();
  const toast = useToast();
  async function decide(decision: "approve" | "reject") {
    if (!pass) return;
    if (decision === "reject" && !note.trim()) { setRejectTried(true); return; }
    try {
      await act(() => api.gatePasses.review(pass.id, decision, note));
      toast({ tone: "success", title: decision === "approve" ? "Pass approved" : "Request rejected", message: decision === "approve" ? `${pass.type} pass for unit ${pass.unitNo} on ${dateLabel(pass.date)}.` : "The resident will see your reason." });
      setNote(""); setRejectTried(false);
      onDone();
    } catch (err) {
      toast({ tone: "error", title: "Couldn't save the decision", message: (err as Error).message });
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
          <Field label="Note to the resident" error={rejectTried && !note.trim() && "A reason is required when rejecting."} hint="Optional when approving, required when rejecting.">
            {(id) => <textarea id={id} rows={3} maxLength={300} className="input" value={note} onChange={(e) => setNote(e.target.value)} placeholder="e.g. Please use the service elevator; bring a valid ID." />}
          </Field>
        </div>
      )}
    </Drawer>
  );
}

function IssuePassDrawer({ open, onClose, onDone }: { open: boolean; onClose: () => void; onDone: () => void }) {
  const [form, setForm] = useState<GatePassInput>({ type: "Visitor", date: todayIso(), unitNo: "", name: "", purpose: "" });
  const [touched, setTouched] = useState(false);
  const { busy, act } = useAction();
  const toast = useToast();
  const bad = { unitNo: !form.unitNo.trim(), name: !form.name.trim(), purpose: !form.purpose.trim() };
  async function submit() {
    setTouched(true);
    if (bad.unitNo || bad.name || bad.purpose) return;
    try {
      const p = await act(() => api.gatePasses.issue(form));
      toast({ tone: "success", title: "Gate pass issued", message: `${p.type} · unit ${p.unitNo} · ${dateLabel(p.date)}` });
      setForm({ type: "Visitor", date: todayIso(), unitNo: "", name: "", purpose: "" }); setTouched(false);
      onDone();
    } catch (err) {
      toast({ tone: "error", title: "Pass not issued", message: (err as Error).message });
    }
  }
  return (
    <Drawer open={open} onClose={onClose} title="Issue gate pass" subtitle="Passes issued by the office are valid immediately." width="max-w-md"
      footer={<><Button variant="secondary" onClick={onClose}>Cancel</Button><Button icon="passport-line" loading={busy} onClick={submit}>Issue pass</Button></>}>
      <form className="grid gap-4 sm:grid-cols-2" onSubmit={(e) => { e.preventDefault(); void submit(); }} noValidate>
        <Field label="Type">{(id) => <select id={id} className="input" value={form.type} onChange={(e) => setForm({ ...form, type: e.target.value as GatePassType })}>{["Visitor", "Delivery", "Move-in", "Move-out"].map((t) => <option key={t}>{t}</option>)}</select>}</Field>
        <Field label="Date">{(id) => <input id={id} type="date" className="input" value={form.date} onChange={(e) => setForm({ ...form, date: e.target.value })} />}</Field>
        <Field label="Unit no." className="sm:col-span-2" error={touched && bad.unitNo && "Enter the unit, e.g. 12-01."}>{(id) => <input id={id} className="input" placeholder="e.g. 12-01" value={form.unitNo} onChange={(e) => setForm({ ...form, unitNo: e.target.value })} />}</Field>
        <Field label={form.type.startsWith("Move") ? "Mover / trucking company" : form.type === "Delivery" ? "Courier / company" : "Visitor name"} className="sm:col-span-2" error={touched && bad.name && "Enter a name."}>{(id) => <input id={id} className="input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />}</Field>
        <Field label="Purpose" className="sm:col-span-2" error={touched && bad.purpose && "Enter the purpose."}>{(id) => <input id={id} className="input" maxLength={300} value={form.purpose} onChange={(e) => setForm({ ...form, purpose: e.target.value })} />}</Field>
      </form>
    </Drawer>
  );
}

// ------------------------------------------------------------------ Move In / Out certificates (Bug D1)
export function CertificatesPage() {
  const list = useAsync(() => api.certificates.list(), []);
  const units = useAsync(() => api.units.list(), []);
  const [form, setForm] = useState<CertificateInput>({ unitId: 0, personType: "Tenant", personId: 0, moveType: "Move In", certificateDate: todayIso() });
  const people = useAsync(() => (form.unitId ? api.units.people(form.unitId) : Promise.resolve([])), [form.unitId]);
  const [failure, setFailure] = useState<ApiError | null>(null);
  const [touched, setTouched] = useState(false);
  const { busy, act } = useAction();
  const toast = useToast();
  const residential = (units.data ?? []).filter((u) => !["PARKING", "STORAGE"].includes(u.type));
  const candidates = (people.data ?? []).filter((p) => p.type === form.personType);
  const person = candidates.find((p) => p.id === form.personId);
  const unit = residential.find((u) => u.id === form.unitId);

  async function generate() {
    setTouched(true);
    setFailure(null);
    if (!form.unitId || !form.personId) return;
    try {
      const c = await act(() => api.certificates.generate(form));
      toast({ tone: "success", title: `Certificate ${c.certificateNo} generated`, message: `${c.moveType} · ${c.personName} · unit ${c.unitNo}` });
      list.reload();
    } catch (err) {
      const e = err instanceof ApiError ? err : new ApiError(0, String(err));
      if (e.status >= 500) setFailure(e); // Bug D1: the server crashes while saving the certificate
      else toast({ tone: "error", title: "Certificate not generated", message: e.message });
    }
  }

  return (
    <>
      <PageHeader eyebrow="Front Desk" title="Move In / Out Certificates" description="Pick the unit and the owner or tenant, then generate a numbered certificate. Move dates come from the owner/tenant record." />
      <div className="grid gap-6 xl:grid-cols-[420px_1fr]">
        <Card className="self-start">
          <CardHeader title="New certificate" />
          <form className="grid gap-4 p-5" onSubmit={(e) => { e.preventDefault(); void generate(); }} noValidate>
            <div className="grid grid-cols-2 gap-2">
              {(["Move In", "Move Out"] as MoveType[]).map((m) => (
                <button key={m} type="button" onClick={() => setForm({ ...form, moveType: m })}
                  className={`flex items-center justify-center gap-2 rounded-lg border px-3 py-2.5 font-semibold transition ${form.moveType === m ? "border-brand-600 bg-brand-50 text-brand-700" : "border-ink-200 text-ink-600 hover:bg-ink-50"}`}>
                  <Icon name={m === "Move In" ? "login-box-line" : "logout-box-line"} />{m}
                </button>
              ))}
            </div>
            <Field label="Unit" error={touched && !form.unitId && "Choose the unit."}>{(id) => (
              <select id={id} className="input" value={form.unitId} onChange={(e) => setForm({ ...form, unitId: Number(e.target.value), personId: 0 })}>
                <option value={0}>Choose a unit…</option>{residential.map((u) => <option key={u.id} value={u.id}>{u.unitNo}</option>)}
              </select>)}</Field>
            <Field label="Person">{(id) => (
              <select id={id} className="input" value={form.personType} onChange={(e) => setForm({ ...form, personType: e.target.value as "Owner" | "Tenant", personId: 0 })}>
                <option>Tenant</option><option>Owner</option>
              </select>)}</Field>
            <Field label={form.personType} error={touched && !form.personId && `Choose the ${form.personType.toLowerCase()}.`} hint={form.unitId && !candidates.length && !people.loading ? `No ${form.personType.toLowerCase()} recorded for this unit.` : undefined}>{(id) => (
              <select id={id} className="input" value={form.personId} disabled={!form.unitId} onChange={(e) => setForm({ ...form, personId: Number(e.target.value) })}>
                <option value={0}>Choose…</option>{candidates.map((p) => <option key={p.id} value={p.id}>{p.name} ({p.status})</option>)}
              </select>)}</Field>
            <Field label="Certificate date">{(id) => <input id={id} type="date" className="input" value={form.certificateDate} onChange={(e) => setForm({ ...form, certificateDate: e.target.value })} />}</Field>
            {person && <p className="rounded-lg bg-ink-50 px-3 py-2 text-[13px] text-ink-600">{form.moveType === "Move In" ? "Move-in" : "Move-out"} date on record: <b>{dateLabel(form.moveType === "Move In" ? person.moveIn : person.moveOut)}</b></p>}
            <Button type="submit" icon="file-paper-2-line" loading={busy}>Generate certificate</Button>
          </form>
        </Card>
        <div className="space-y-6">
          {failure && (
            <div className="animate-pop-in rounded-xl border border-red-200 bg-white shadow-card" role="alert">
              <div className="flex items-start gap-3 border-b border-red-100 bg-red-50 px-5 py-4 text-red-800">
                <Icon name="error-warning-fill" className="mt-0.5 text-2xl" />
                <div>
                  <p className="font-semibold">The certificate could not be generated</p>
                  <p className="text-[13px]">The server hit a known problem (reference <b>Bug D1</b>) while saving the certificate. Nothing was saved and no certificate number was used.</p>
                </div>
              </div>
              <div className="space-y-3 px-5 py-4 text-[13.5px]">
                <p className="font-semibold text-ink-900">What to do now</p>
                <ol className="list-decimal space-y-1 pl-5 text-ink-600">
                  <li>Keep these details; the form still has them: <b>{form.moveType}</b> · unit <b>{unit?.unitNo}</b> · <b>{person?.name}</b> · {dateLabel(form.certificateDate)}.</li>
                  <li>Let the move proceed if the gate pass is approved; the certificate can be issued once the fix is in.</li>
                  <li>Tell the system administrator it's Bug D1 ({failure.status} · {failure.message}).</li>
                </ol>
                <div className="flex gap-2 pt-1"><Button size="sm" variant="secondary" icon="refresh-line" loading={busy} onClick={generate}>Try again</Button><Button size="sm" variant="ghost" onClick={() => setFailure(null)}>Dismiss</Button></div>
              </div>
            </div>
          )}
          <Card>
            <CardHeader title="Issued certificates" />
            <DataTable rows={list.data} loading={list.loading} error={list.error} onRetry={list.reload} rowKey={(c) => c.id} empty={{ icon: "file-paper-2-line", title: "No certificates yet" }}
              columns={[
                { key: "n", header: "Certificate", cell: (c) => <b className="whitespace-nowrap text-ink-900">{c.certificateNo}</b> },
                { key: "t", header: "Type", cell: (c) => <Badge tone={c.moveType === "Move In" ? "ok" : "copper"} dot={false}>{c.moveType}</Badge> },
                { key: "p", header: "Person", cell: (c) => <>{c.personName}<span className="block text-[12px] text-ink-500">{c.personType} · unit {c.unitNo}</span></> },
                { key: "m", header: "Move date", cell: (c) => <span className="whitespace-nowrap">{dateLabel(c.moveDate)}</span> },
                { key: "d", header: "Issued", cell: (c) => <>{dateLabel(c.certificateDate)}<span className="block text-[12px] text-ink-500">by {c.issuedBy}</span></> },
              ]} />
          </Card>
        </div>
      </div>
    </>
  );
}

// ------------------------------------------------------------------ Expenses
const CATEGORIES = ["Utilities", "Security", "Repairs", "Supplies", "Salaries", "Professional fees", "Other"];
export function ExpensesPage() {
  const [month, setMonth] = useState(currentMonth());
  const { data, error, loading, reload } = useAsync(() => api.expenses.list(month), [month]);
  const [form, setForm] = useState({ date: todayIso(), category: "Utilities", description: "", amount: "" });
  const [touched, setTouched] = useState(false);
  const { busy, act } = useAction();
  const toast = useToast();
  const total = (data ?? []).reduce((s, e) => s + toCents(e.amount), 0);
  const bad = { description: !form.description.trim(), amount: !isAmount(form.amount) };
  async function add() {
    setTouched(true);
    if (bad.description || bad.amount) return;
    try {
      await act(() => api.expenses.add(form));
      toast({ tone: "success", title: "Expense recorded", message: `${form.category} · ${peso(form.amount)}` });
      setForm({ ...form, description: "", amount: "" }); setTouched(false);
      if (form.date.startsWith(month)) reload(); else setMonth(form.date.slice(0, 7));
    } catch (err) {
      toast({ tone: "error", title: "Expense not recorded", message: (err as Error).message });
    }
  }
  return (
    <>
      <PageHeader eyebrow="Records" title="Expense Logs" description="Building expenses by date and category." actions={<MonthPicker value={month} onChange={setMonth} max={currentMonth()} />} />
      <div className="grid gap-6 xl:grid-cols-[380px_1fr]">
        <Card className="self-start">
          <CardHeader title="Record expense" />
          <form className="grid gap-4 p-5" onSubmit={(e) => { e.preventDefault(); void add(); }} noValidate>
            <div className="grid grid-cols-2 gap-4">
              <Field label="Date">{(id) => <input id={id} type="date" className="input" max={todayIso()} value={form.date} onChange={(e) => setForm({ ...form, date: e.target.value })} />}</Field>
              <Field label="Category">{(id) => <select id={id} className="input" value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })}>{CATEGORIES.map((c) => <option key={c}>{c}</option>)}</select>}</Field>
            </div>
            <Field label="Description" error={touched && bad.description && "Describe the expense."}>{(id) => <input id={id} className="input" maxLength={300} value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />}</Field>
            <Field label="Amount (₱)" error={touched && bad.amount && "Enter an amount greater than zero."}>{(id) => <input id={id} inputMode="decimal" className="input tabular" value={form.amount} onChange={(e) => setForm({ ...form, amount: e.target.value })} />}</Field>
            <Button type="submit" icon="add-line" loading={busy}>Record expense</Button>
          </form>
        </Card>
        <Card>
          <CardHeader title={`Expenses · ${monthLabel(month)}`} subtitle={`Total ${peso(fromCents(total))}`} />
          <DataTable rows={data} loading={loading} error={error} onRetry={reload} rowKey={(e) => e.id} empty={{ icon: "wallet-3-line", title: "No expenses this month" }}
            columns={[
              { key: "d", header: "Date", cell: (e) => dateLabel(e.date) },
              { key: "c", header: "Category", cell: (e) => <Badge tone="neutral" dot={false}>{e.category}</Badge> },
              { key: "x", header: "Description", cell: (e) => e.description },
              { key: "a", header: "Amount", align: "right", cell: (e) => <b>{peso(e.amount)}</b> },
            ]} />
        </Card>
      </div>
    </>
  );
}

// ------------------------------------------------------------------ Maintenance
const STATUSES: TicketStatus[] = ["Open", "In Progress", "Resolved", "Closed"];
export function MaintenancePage() {
  const { data, error, loading, reload } = useAsync(() => api.maintenance.list(), []);
  const [status, setStatus] = useState<TicketStatus | "Active">("Active");
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<MaintenanceTicket | null>(null);
  const rows = useMemo(() => {
    const n = q.trim().toLowerCase();
    return (data ?? []).filter((t) => (status === "Active" ? t.status === "Open" || t.status === "In Progress" : t.status === status) &&
      (!n || [t.ticketNo, t.unitNo, t.title].some((v) => v.toLowerCase().includes(n))));
  }, [data, status, q]);
  const count = (s: TicketStatus | "Active") => (data ?? []).filter((t) => (s === "Active" ? t.status === "Open" || t.status === "In Progress" : t.status === s)).length;
  return (
    <>
      <PageHeader eyebrow="Community" title="Maintenance Tickets" description="Requests from residents and the office. Status updates and notes are visible to the resident." />
      <Card>
        <div className="flex flex-wrap items-center gap-3 border-b border-ink-100 p-4">
          <Tabs value={status} onChange={setStatus} tabs={(["Active", ...STATUSES] as const).map((s) => ({ key: s, label: s, count: count(s) }))} />
          <div className="flex-1" /><SearchInput value={q} onChange={setQ} placeholder="Ticket, unit or title" />
        </div>
        <DataTable rows={data ? rows : null} loading={loading} error={error} onRetry={reload} rowKey={(t) => t.id} onRowClick={setOpen} empty={{ icon: "tools-line", title: "No tickets here" }}
          columns={[
            { key: "n", header: "Ticket", cell: (t) => <><b className="text-ink-900">{t.title}</b><span className="block text-[12px] text-ink-500">{t.ticketNo} · {t.category}</span></> },
            { key: "u", header: "Unit", cell: (t) => t.unitNo },
            { key: "p", header: "Priority", cell: (t) => <Badge tone={t.priority === "Urgent" ? "bad" : t.priority === "High" ? "warn" : "neutral"} dot={false}>{t.priority}</Badge> },
            { key: "a", header: "Assigned", cell: (t) => t.assignedTo || t.vendor || <span className="text-ink-400">Unassigned</span> },
            { key: "r", header: "Requested", cell: (t) => dateTimeLabel(t.requestedAt) },
            { key: "s", header: "Status", cell: (t) => <StatusBadge status={t.status} /> },
          ]} />
      </Card>
      <TicketDrawer ticket={open} onClose={() => setOpen(null)} onSaved={() => { setOpen(null); reload(); }} />
    </>
  );
}

function TicketDrawer({ ticket, onClose, onSaved }: { ticket: MaintenanceTicket | null; onClose: () => void; onSaved: () => void }) {
  const vendors = useAsync(() => api.vendors.list().catch(() => []), []);
  const [form, setForm] = useState({ status: "Open" as TicketStatus, priority: "Normal", assignedTo: "", vendor: "", resolution: "" });
  const [loadedFor, setLoadedFor] = useState<number | null>(null);
  const { busy, act } = useAction();
  const toast = useToast();
  if (ticket && loadedFor !== ticket.id) {
    setLoadedFor(ticket.id);
    setForm({ status: ticket.status, priority: ticket.priority, assignedTo: ticket.assignedTo, vendor: ticket.vendor, resolution: ticket.resolution });
  }
  async function save() {
    if (!ticket) return;
    try {
      await act(() => api.maintenance.update(ticket.id, form));
      toast({ tone: "success", title: `Ticket ${ticket.ticketNo} updated` });
      onSaved();
    } catch (err) {
      toast({ tone: "error", title: "Ticket not updated", message: (err as Error).message });
    }
  }
  return (
    <Drawer open={!!ticket} onClose={onClose} title={ticket?.title ?? ""} subtitle={ticket ? `${ticket.ticketNo} · unit ${ticket.unitNo} · ${ticket.category}` : ""}
      footer={<Can permission="maintenance_update"><Button icon="save-3-line" loading={busy} onClick={save}>Save update</Button></Can>}>
      {ticket && (
        <div className="space-y-5">
          <p className="rounded-xl bg-ink-50 p-4 whitespace-pre-line text-ink-700">{ticket.description}</p>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Status">{(id) => <select id={id} className="input" value={form.status} onChange={(e) => setForm({ ...form, status: e.target.value as TicketStatus })}>{STATUSES.map((s) => <option key={s}>{s}</option>)}</select>}</Field>
            <Field label="Priority">{(id) => <select id={id} className="input" value={form.priority} onChange={(e) => setForm({ ...form, priority: e.target.value })}>{["Low", "Normal", "High", "Urgent"].map((s) => <option key={s}>{s}</option>)}</select>}</Field>
            <Field label="Assigned to (staff)">{(id) => <input id={id} className="input" value={form.assignedTo} onChange={(e) => setForm({ ...form, assignedTo: e.target.value })} />}</Field>
            <Field label="Vendor">{(id) => <select id={id} className="input" value={form.vendor} onChange={(e) => setForm({ ...form, vendor: e.target.value })}><option value="">None</option>{(vendors.data ?? []).filter((v) => v.status === "Active").map((v) => <option key={v.id}>{v.name}</option>)}</select>}</Field>
            <Field label="Update for the resident" className="sm:col-span-2" hint="Shown to the resident in their portal.">{(id) => <textarea id={id} rows={3} className="input" value={form.resolution} onChange={(e) => setForm({ ...form, resolution: e.target.value })} />}</Field>
          </div>
        </div>
      )}
    </Drawer>
  );
}

// ------------------------------------------------------------------ Vendors, documents, announcements
export function VendorsPage() {
  const { data, error, loading, reload } = useAsync(() => api.vendors.list(), []);
  return (
    <>
      <PageHeader eyebrow="Community" title="Vendor Directory" description="Contractors and suppliers the building works with." />
      <Card>
        <DataTable rows={data} loading={loading} error={error} onRetry={reload} rowKey={(v) => v.id} columns={[
          { key: "n", header: "Vendor", cell: (v) => <><b className="text-ink-900">{v.name}</b><span className="block text-[12px] text-ink-500">{v.serviceType}</span></> },
          { key: "c", header: "Contact person", cell: (v) => v.contactPerson },
          { key: "p", header: "Phone", cell: (v) => v.contactNo },
          { key: "e", header: "Email", cell: (v) => v.email || "—" },
          { key: "s", header: "Status", cell: (v) => <StatusBadge status={v.status} /> },
        ]} />
      </Card>
    </>
  );
}

export function DocumentsPage() {
  const { data, error, loading, reload } = useAsync(() => api.documents.list(), []);
  return (
    <>
      <PageHeader eyebrow="Community" title="Documents" description="Document records (title, category, audience, file name or shared-folder path). Files themselves stay on the office network." />
      <Notice tone="info">Uploading files isn't part of the system yet; records point to where the file is kept.</Notice>
      <Card className="mt-4">
        <DataTable rows={data} loading={loading} error={error} onRetry={reload} rowKey={(d) => d.id} columns={[
          { key: "t", header: "Title", cell: (d) => <><b className="text-ink-900">{d.title}</b><span className="block text-[12px] text-ink-500">{d.fileName}</span></> },
          { key: "c", header: "Category", cell: (d) => d.category },
          { key: "a", header: "Audience", cell: (d) => <Badge tone={d.audience === "admin" ? "neutral" : "info"} dot={false}>{d.audience === "unit" ? `Unit ${d.unitNo}` : d.audience === "admin" ? "Admin only" : "Residents"}</Badge> },
          { key: "d", header: "Added", cell: (d) => dateLabel(d.addedAt) },
        ]} />
      </Card>
    </>
  );
}

export function AnnouncementsPage() {
  const { can } = useAuth();
  const { data, error, loading, reload } = useAsync(() => api.announcements.list(), []);
  const [form, setForm] = useState({ title: "", message: "", audience: "residents" });
  const [touched, setTouched] = useState(false);
  const { busy, act } = useAction();
  const toast = useToast();
  async function publish() {
    setTouched(true);
    if (!form.title.trim() || !form.message.trim()) return;
    try {
      await act(() => api.announcements.publish(form));
      toast({ tone: "success", title: "Announcement published", message: "Residents see it in their portal right away." });
      setForm({ title: "", message: "", audience: "residents" }); setTouched(false);
      reload();
    } catch (err) {
      toast({ tone: "error", title: "Not published", message: (err as Error).message });
    }
  }
  return (
    <>
      <PageHeader eyebrow="Communication" title="Announcements" description="Notices published here appear on the resident portal." />
      <div className="grid gap-6 xl:grid-cols-[420px_1fr]">
        {can("announcements") && (
          <Card className="self-start">
            <CardHeader title="New announcement" />
            <form className="grid gap-4 p-5" onSubmit={(e) => { e.preventDefault(); void publish(); }} noValidate>
              <Field label="Title" error={touched && !form.title.trim() && "Enter a title."}>{(id) => <input id={id} className="input" maxLength={200} value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} />}</Field>
              <Field label="Message" error={touched && !form.message.trim() && "Write the message."}>{(id) => <textarea id={id} rows={6} className="input" value={form.message} onChange={(e) => setForm({ ...form, message: e.target.value })} />}</Field>
              <Button type="submit" icon="megaphone-line" loading={busy}>Publish</Button>
            </form>
          </Card>
        )}
        <Card>
          <CardHeader title="Published" />
          {error ? <ErrorState message={error.message} onRetry={reload} /> : loading && !data ? <Skeleton rows={5} /> : !data?.length ? <EmptyState icon="megaphone-line" title="No announcements yet" /> : (
            <ul className="divide-y divide-ink-100">
              {data.map((a) => (
                <li key={a.id} className="px-5 py-4">
                  <div className="flex flex-wrap items-baseline justify-between gap-2"><h3 className="font-semibold text-ink-900">{a.title}</h3><span className="text-[12.5px] text-ink-400">{dateLabel(a.publishDate)} · {a.createdBy}</span></div>
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
