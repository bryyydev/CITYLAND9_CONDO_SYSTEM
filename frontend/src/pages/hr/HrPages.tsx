// HR & Payroll workspace (Manager) + Staff attendance entry and Staff dashboard.
// API: backend/app/routes/hr.py. Payroll figures always come from the server (saved Payroll Rules).
import { Fragment, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useAuth, useUser } from "../../auth/AuthContext";
import { DataTable } from "../../components/base/DataTable";
import { ConfirmDialog, Drawer, useToast } from "../../components/base/overlays";
import { Badge, Button, Card, CardHeader, EmptyState, ErrorState, Field, Icon, Notice, PageHeader, SearchInput, Skeleton, StatCard, StatusBadge, Tabs } from "../../components/base/ui";
import { ROLES } from "../../config/roles";
import { useAction, useAsync } from "../../hooks/useAsync";
import { addDays, currentMonth, dateLabel, todayIso } from "../../lib/format";
import { peso, pesoShort, toCents } from "../../lib/money";
import { api } from "../../services/api";
import { ApiError } from "../../services/http";
import type {
  AttendanceInput, Employee, EmployeeInput, EmploymentStatus, HrLoan, LeaveRequest, LoanInput, OvertimeRequest, PayrollDetail, PayrollInput, PayrollPreview, PayrollRule, Statutory,
} from "../../services/types";
import { MonthPicker, newFormToken } from "../property/Billing";

const serverFields = (err: unknown): Record<string, string> => (err instanceof ApiError ? err.fields : {});
const ATT_TONE: Record<string, "ok" | "warn" | "bad" | "info" | "neutral"> = { PRESENT: "ok", LATE: "warn", UNDERTIME: "warn", "LATE/UNDERTIME": "warn", ABSENT: "bad", LEAVE: "info", "REST DAY": "neutral" };
const AttBadge = ({ status }: { status: string }) => <Badge tone={ATT_TONE[status] ?? "neutral"} dot={false}>{status.charAt(0) + status.slice(1).toLowerCase()}</Badge>;
const PAY_TONE: Record<string, "ok" | "warn" | "neutral"> = { Draft: "neutral", Final: "warn", Paid: "ok" };

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

// ------------------------------------------------------------------ HR dashboard
export function HrDashboard() {
  const user = useUser();
  const { data, error, reload } = useAsync(() => api.dashboards.hr(), []);
  const leave = useAsync(() => api.hr.leave(), []);
  const ot = useAsync(() => api.hr.overtime(), []);
  if (error) return <ErrorState message={error.message} onRetry={reload} />;
  if (!data) return <Card><Skeleton rows={6} /></Card>;
  const portal = ROLES[user.role].portal;
  const pending = [...(leave.data?.requests ?? []).filter((l) => l.status === "Pending").map((l) => ({ key: `l${l.id}`, who: l.employeeName, what: `${l.leaveType} leave · ${l.days} day(s) from ${dateLabel(l.from)}`, to: "leave" })),
    ...(ot.data?.requests ?? []).filter((o) => o.status === "Pending").map((o) => ({ key: `o${o.id}`, who: o.employeeName, what: `Overtime · ${o.hours}h on ${dateLabel(o.date)}`, to: "attendance?tab=overtime" }))];
  return (
    <>
      <PageHeader eyebrow={`Good day, ${user.displayName?.split(" ")[0] ?? user.username}`} title="People & payroll" description="Attendance today, requests waiting for you, and the monthly payroll." />
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard tone="hero" icon="team-line" label="Headcount" value={data.headcount} hint={`${data.onLeave} on leave`} />
        <StatCard icon="time-line" label="Present today" value={data.presentToday} hint={`${data.lateToday} late`} />
        <StatCard tone={pending.length ? "copper" : "default"} icon="inbox-archive-line" label="Waiting for approval" value={pending.length} hint={`${data.pendingLeave} leave · ${data.pendingOvertime} overtime`} />
        <StatCard icon="money-dollar-box-line" label="Monthly basic payroll" value={pesoShort(data.monthlyPayroll)} />
      </div>
      <Card className="mt-6">
        <CardHeader title="Requests waiting for you" />
        {pending.length === 0 ? <p className="px-5 py-8 text-center text-ink-500">Nothing to approve.</p> : (
          <ul className="divide-y divide-ink-100">
            {pending.map((p) => (
              <li key={p.key} className="flex items-center justify-between gap-3 px-5 py-3">
                <div><p className="font-semibold text-ink-900">{p.who}</p><p className="text-[12.5px] text-ink-500">{p.what}</p></div>
                <Link to={`/${portal}/${p.to}`} className="text-[13px] font-semibold text-brand-600 hover:underline">Review →</Link>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </>
  );
}

// ------------------------------------------------------------------ Employees
const EMPTY_EMPLOYEE: EmployeeInput = { employeeNo: "", fullName: "", position: "", department: "", status: "Active", contactNo: "", email: "", dateHired: null, monthlySalary: "", notes: "" };
export function EmployeesPage() {
  const [params, setParams] = useSearchParams();
  const { data, error, loading, reload } = useAsync(() => api.hr.employees(), []);
  const [q, setQ] = useState("");
  const [show, setShow] = useState<"current" | "all">("current");
  const [adding, setAdding] = useState(false);
  const openId = Number(params.get("employee")) || null;
  const editing = data?.employees.find((e) => e.id === openId) ?? null;
  const setOpen = (id: number | null) => { const p = new URLSearchParams(params); if (id) p.set("employee", String(id)); else p.delete("employee"); p.delete("moved"); setParams(p, { replace: true }); };
  const rows = useMemo(() => {
    const n = q.trim().toLowerCase();
    return (data?.employees ?? []).filter((e) => (show === "all" || e.status === "Active" || e.status === "On Leave") &&
      (!n || [e.fullName, e.employeeNo, e.position, e.department].some((v) => v.toLowerCase().includes(n))));
  }, [data, q, show]);
  const current = (data?.employees ?? []).filter((e) => e.status === "Active" || e.status === "On Leave");
  return (
    <>
      <PageHeader eyebrow="People" title="Employee Roster" description="Building employees, positions and monthly basic salary. Payroll uses the salary saved here."
        actions={<Button icon="user-add-line" onClick={() => setAdding(true)}>Add employee</Button>} />
      <MovedFormNotice screen="Employees" />
      <div className="mb-6 grid gap-4 sm:grid-cols-3">
        <StatCard tone="hero" icon="team-line" label="Current employees" value={current.length} hint={`${current.filter((e) => e.status === "On Leave").length} on leave`} />
        <StatCard icon="money-dollar-box-line" label="Monthly basic salaries" value={peso((current.reduce((s, e) => s + toCents(e.monthlySalary), 0) / 100).toFixed(2))} />
        <StatCard icon="user-unfollow-line" label="Inactive / separated" value={(data?.employees.length ?? 0) - current.length} />
      </div>
      <Card>
        <div className="flex flex-wrap items-center gap-3 border-b border-ink-100 p-4">
          <Tabs value={show} onChange={setShow} tabs={[{ key: "current", label: "Current", count: current.length }, { key: "all", label: "All", count: data?.employees.length }]} />
          <div className="flex-1" /><SearchInput value={q} onChange={setQ} placeholder="Name, number, position" />
        </div>
        <DataTable rows={data ? rows : null} loading={loading} error={error} onRetry={reload} rowKey={(e) => e.id} onRowClick={(e) => setOpen(e.id)} empty={{ icon: "team-line", title: "No employees", text: "Add the building's employees to start recording attendance and payroll." }} columns={[
          { key: "n", header: "Employee", cell: (e) => <><b className="text-ink-900">{e.fullName}</b><span className="block text-[12px] text-ink-500">{e.employeeNo}</span></> },
          { key: "p", header: "Position", cell: (e) => <>{e.position || "—"}<span className="block text-[12px] text-ink-500">{e.department}</span></> },
          { key: "h", header: "Hired", cell: (e) => <span className="whitespace-nowrap">{e.dateHired ? dateLabel(e.dateHired) : "—"}</span> },
          { key: "c", header: "Contact", cell: (e) => <>{e.contactNo || "—"}{e.email && <span className="block text-[12px] text-ink-500">{e.email}</span>}</> },
          { key: "s", header: "Monthly basic", align: "right", cell: (e) => <span className="whitespace-nowrap">{peso(e.monthlySalary)}</span> },
          { key: "st", header: "Status", cell: (e) => <StatusBadge status={e.status} /> },
        ]} />
      </Card>
      {data && <EmployeeDrawer open={adding || !!editing} employee={adding ? null : editing} statuses={data.statuses}
        onClose={() => { setAdding(false); setOpen(null); }} onDone={() => { setAdding(false); setOpen(null); reload(); }} />}
    </>
  );
}

function EmployeeDrawer({ open, employee, statuses, onClose, onDone }: { open: boolean; employee: Employee | null; statuses: EmploymentStatus[]; onClose: () => void; onDone: () => void }) {
  const [form, setForm] = useState<EmployeeInput>(EMPTY_EMPLOYEE);
  const [loadedFor, setLoadedFor] = useState<string | null>(null);
  const [server, setServer] = useState<Record<string, string>>({});
  const [confirmDelete, setConfirmDelete] = useState(false);
  const { busy, act } = useAction();
  const toast = useToast();
  const { can } = useAuth();
  const key = open ? (employee ? String(employee.id) : "new") : null;
  if (key !== loadedFor) {
    setLoadedFor(key);
    if (employee) { const { id: _i, hasRecords: _h, ...rest } = employee; setForm(rest); } else setForm(EMPTY_EMPLOYEE);
    setServer({});
  }
  const set = (patch: Partial<EmployeeInput>) => { setForm({ ...form, ...patch }); setServer({}); };
  async function save() {
    try {
      const e = await act(() => (employee ? api.hr.updateEmployee(employee.id, form) : api.hr.addEmployee(form)));
      toast({ tone: "success", title: employee ? "Employee updated" : "Employee added", message: `${e.fullName} · ${e.employeeNo}` });
      onDone();
    } catch (err) {
      setServer(serverFields(err));
      if (!Object.keys(serverFields(err)).length) toast({ tone: "error", title: "Not saved", message: (err as Error).message });
    }
  }
  async function remove() {
    if (!employee) return;
    try {
      await act(() => api.hr.deleteEmployee(employee.id));
      toast({ tone: "success", title: "Employee deleted" });
      setConfirmDelete(false);
      onDone();
    } catch (err) {
      setConfirmDelete(false);
      toast({ tone: "error", title: "Not deleted", message: (err as Error).message });
    }
  }
  const input = (k: keyof EmployeeInput, label: string, extra: { type?: string; max?: number; className?: string; disabled?: boolean } = {}) => (
    <Field label={label} className={extra.className} error={server[k]}>{(id) => <input id={id} type={extra.type ?? "text"} className="input" maxLength={extra.max} disabled={extra.disabled}
      value={(form[k] as string | null) ?? ""} onChange={(e) => set({ [k]: e.target.value || (k === "dateHired" ? null : "") })} />}</Field>
  );
  return (
    <Drawer open={open} onClose={onClose} title={employee ? employee.fullName : "Add employee"} subtitle={employee ? `${employee.employeeNo} · ${employee.position || "no position"}` : undefined} width="max-w-lg"
      footer={<div className="flex w-full flex-wrap justify-between gap-2">
        <span>{employee && can("delete_employee") && <Button variant="ghost" icon="delete-bin-line" disabled={employee.hasRecords} title={employee.hasRecords ? "Employees with records can't be deleted; set the status to Separated" : undefined} onClick={() => setConfirmDelete(true)}>Delete</Button>}</span>
        <span className="flex gap-2"><Button variant="secondary" onClick={onClose}>Cancel</Button><Button icon="save-3-line" loading={busy} onClick={save}>Save</Button></span>
      </div>}>
      <form className="grid gap-4 sm:grid-cols-2" onSubmit={(e) => { e.preventDefault(); void save(); }} noValidate>
        {input("employeeNo", "Employee No.", { max: 50, disabled: !!employee })}
        <Field label="Status" error={server.status}>{(id) => <select id={id} className="input" value={form.status} onChange={(e) => set({ status: e.target.value as EmploymentStatus })}>{statuses.map((s) => <option key={s}>{s}</option>)}</select>}</Field>
        {input("fullName", "Full name", { max: 200, className: "sm:col-span-2" })}
        {input("position", "Position", { max: 120 })}
        {input("department", "Department", { max: 120 })}
        {input("monthlySalary", "Monthly basic salary (₱)")}
        {input("dateHired", "Date hired", { type: "date" })}
        {input("contactNo", "Contact number", { max: 80 })}
        {input("email", "Email", { type: "email", max: 160 })}
        <Field label="Notes" className="sm:col-span-2" error={server.notes}>{(id) => <textarea id={id} rows={2} maxLength={500} className="input h-auto py-2" value={form.notes} onChange={(e) => set({ notes: e.target.value })} />}</Field>
      </form>
      {employee && <p className="mt-4 text-[12.5px] text-ink-500"><Link className="font-semibold text-brand-700 hover:underline" to={`../attendance?employee=${employee.id}`} relative="path">Attendance history →</Link></p>}
      <ConfirmDialog open={confirmDelete} title="Delete this employee?" message={employee ? `${employee.fullName} (${employee.employeeNo}) will be removed. This can't be undone.` : ""} confirmLabel="Delete" tone="danger" busy={busy}
        onClose={() => setConfirmDelete(false)} onConfirm={remove} />
    </Drawer>
  );
}

// ------------------------------------------------------------------ Attendance sheet (manager and staff)
type SheetRow = { status: string; timeIn: string; timeOut: string; remarks: string };
function AttendanceSheet({ date, onDateChange, title }: { date: string; onDateChange: (d: string) => void; title: string }) {
  const { data, error, loading, reload } = useAsync(() => api.hr.attendanceDay(date), [date]);
  const [rows, setRows] = useState<Record<number, SheetRow>>({});
  const [loadedFor, setLoadedFor] = useState<string | null>(null);
  const [problem, setProblem] = useState("");
  const { busy, act } = useAction();
  const toast = useToast();
  if (data && loadedFor !== `${data.date}:${data.employees.length}:${data.employees.map((e) => e.record?.id ?? 0).join(",")}`) {
    setLoadedFor(`${data.date}:${data.employees.length}:${data.employees.map((e) => e.record?.id ?? 0).join(",")}`);
    setRows(Object.fromEntries(data.employees.map((e) => [e.id, { status: e.record?.status ?? "", timeIn: e.record?.timeIn ?? "", timeOut: e.record?.timeOut ?? "", remarks: e.record?.remarks ?? "" }])));
    setProblem("");
  }
  const original = (id: number): SheetRow => { const r = data?.employees.find((e) => e.id === id)?.record; return { status: r?.status ?? "", timeIn: r?.timeIn ?? "", timeOut: r?.timeOut ?? "", remarks: r?.remarks ?? "" }; };
  const changed = Object.entries(rows).filter(([id, r]) => JSON.stringify(r) !== JSON.stringify(original(Number(id))));
  const set = (id: number, patch: Partial<SheetRow>) => {
    const next = { ...rows[id], ...patch };
    if (patch.status && ["ABSENT", "LEAVE", "REST DAY"].includes(patch.status)) { next.timeIn = ""; next.timeOut = ""; }
    if (patch.status && !rows[id].status && !rows[id].timeIn && ["PRESENT", "LATE", "UNDERTIME", "LATE/UNDERTIME"].includes(patch.status)) next.timeIn = data?.schedule.start ?? "08:00";
    setRows({ ...rows, [id]: next });
    setProblem("");
  };
  async function save() {
    const records: AttendanceInput[] = changed.map(([id, r]) => ({ employeeId: Number(id), status: r.status, timeIn: r.timeIn || null, timeOut: r.timeOut || null, remarks: r.remarks }));
    try {
      const res = await act(() => api.hr.saveAttendance(date, records));
      toast({ tone: "success", title: "Attendance saved", message: `${res.saved} record(s)${res.cleared ? `, ${res.cleared} cleared` : ""} for ${dateLabel(date)}` });
      reload();
    } catch (err) {
      setProblem(Object.values(serverFields(err))[0] ?? (err as Error).message);
    }
  }
  const recorded = data ? data.employees.filter((e) => e.record).length : 0;
  return (
    <Card>
      <div className="flex flex-wrap items-end justify-between gap-3 border-b border-ink-100 p-4">
        <div><h2 className="font-semibold text-ink-900">{title}</h2><p className="text-[12.5px] text-ink-500">{data ? `${recorded} of ${data.employees.length} recorded · schedule ${data.schedule.start}–${data.schedule.end}, ${data.schedule.graceMinutes} min grace` : "Loading…"}</p></div>
        <div className="flex flex-wrap items-end gap-2">
          <Field label="Date">{(id) => <input id={id} type="date" className="input w-44" max={todayIso()} value={date} onChange={(e) => e.target.value && onDateChange(e.target.value)} />}</Field>
          <Button icon="save-3-line" loading={busy} disabled={!changed.length} onClick={save}>Save {changed.length ? `${changed.length} change${changed.length === 1 ? "" : "s"}` : "attendance"}</Button>
        </div>
      </div>
      {problem && <div className="px-4 pt-4" role="alert"><Notice tone="warn">{problem}</Notice></div>}
      {error ? <ErrorState message={error.message} onRetry={reload} /> : !data ? <Skeleton rows={6} /> : !data.employees.length ? <EmptyState icon="team-line" title="No employees yet" /> : (
        <div className={`overflow-x-auto ${loading ? "opacity-60" : ""}`}>
          <table className="w-full min-w-[720px] text-[13.5px]">
            <thead><tr className="border-b border-ink-100 text-left text-[12px] text-ink-500 uppercase"><th className="px-4 py-2.5">Employee</th><th className="px-2 py-2.5">Status</th><th className="px-2 py-2.5">Time in</th><th className="px-2 py-2.5">Time out</th><th className="px-2 py-2.5">Remarks</th><th className="px-4 py-2.5 text-right">Late / undertime</th></tr></thead>
            <tbody className="divide-y divide-ink-100">
              {data.employees.map((e) => {
                const r = rows[e.id] ?? { status: "", timeIn: "", timeOut: "", remarks: "" };
                const noTimes = ["", "ABSENT", "LEAVE", "REST DAY"].includes(r.status);
                const dirty = JSON.stringify(r) !== JSON.stringify(original(e.id));
                return (
                  <tr key={e.id} className={dirty ? "bg-brand-50/40" : undefined}>
                    <td className="px-4 py-2"><b className="text-ink-900">{e.fullName}</b><span className="block text-[12px] text-ink-500">{e.employeeNo}{e.status !== "Active" ? ` · ${e.status}` : ""}</span></td>
                    <td className="px-2 py-2"><select aria-label={`Status of ${e.fullName}`} className="input h-9 w-40" value={r.status} onChange={(ev) => set(e.id, { status: ev.target.value })}><option value="">Not recorded</option>{data.statuses.map((s) => <option key={s} value={s}>{s.charAt(0) + s.slice(1).toLowerCase()}</option>)}</select></td>
                    <td className="px-2 py-2"><input aria-label={`Time in of ${e.fullName}`} type="time" className="input h-9 w-32" disabled={noTimes} value={r.timeIn} onChange={(ev) => set(e.id, { timeIn: ev.target.value })} /></td>
                    <td className="px-2 py-2"><input aria-label={`Time out of ${e.fullName}`} type="time" className="input h-9 w-32" disabled={noTimes} value={r.timeOut} onChange={(ev) => set(e.id, { timeOut: ev.target.value })} /></td>
                    <td className="px-2 py-2"><input aria-label={`Remarks for ${e.fullName}`} className="input h-9 w-44" maxLength={255} value={r.remarks} onChange={(ev) => set(e.id, { remarks: ev.target.value })} /></td>
                    <td className="px-4 py-2 text-right tabular text-ink-600">{e.record && !dirty ? [e.record.lateMinutes ? `${e.record.lateMinutes} min late` : "", e.record.undertimeMinutes ? `${e.record.undertimeMinutes} min under` : ""].filter(Boolean).join(" · ") || "—" : dirty ? <span className="text-[12px] text-brand-700">unsaved</span> : "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

// ------------------------------------------------------------------ Attendance & overtime (manager)
export function AttendancePage() {
  const [params, setParams] = useSearchParams();
  const tab = (["attendance", "overtime", "history"].includes(params.get("tab") ?? "") ? params.get("tab") : params.get("employee") ? "history" : "attendance") as "attendance" | "overtime" | "history";
  const setTab = (t: string) => { const p = new URLSearchParams(params); p.set("tab", t); p.delete("moved"); setParams(p, { replace: true }); };
  const date = params.get("date") || todayIso();
  const ot = useAsync(() => api.hr.overtime(), []);
  return (
    <>
      <PageHeader eyebrow="People" title="Attendance & Overtime" description="Daily time records, each employee's history, and overtime approvals. Approved overtime is added to payroll automatically." />
      <MovedFormNotice screen="Attendance" />
      <div className="mb-4"><Tabs value={tab} onChange={setTab} tabs={[{ key: "attendance", label: "Daily attendance" }, { key: "overtime", label: "Overtime", count: ot.data?.requests.filter((o) => o.status === "Pending").length }, { key: "history", label: "Employee history" }]} /></div>
      {tab === "attendance" && <AttendanceSheet date={date} title={`Attendance · ${dateLabel(date)}`} onDateChange={(d) => { const p = new URLSearchParams(params); p.set("date", d); setParams(p, { replace: true }); }} />}
      {tab === "overtime" && <OvertimePanel data={ot.data} error={ot.error} reload={ot.reload} />}
      {tab === "history" && <AttendanceHistoryPanel />}
    </>
  );
}

function AttendanceHistoryPanel() {
  const [params, setParams] = useSearchParams();
  const employees = useAsync(() => api.hr.employees(), []);
  const eid = Number(params.get("employee")) || 0;
  const [range, setRange] = useState({ from: `${currentMonth()}-01`, to: todayIso() });
  const hist = useAsync(() => (eid ? api.hr.attendanceHistory(eid, range.from, range.to) : Promise.resolve(null)), [eid, range.from, range.to]);
  const pick = (id: number) => { const p = new URLSearchParams(params); if (id) p.set("employee", String(id)); else p.delete("employee"); p.set("tab", "history"); setParams(p, { replace: true }); };
  return (
    <Card>
      <div className="flex flex-wrap items-end gap-3 border-b border-ink-100 p-4">
        <Field label="Employee">{(id) => <select id={id} className="input w-64" value={eid} onChange={(e) => pick(Number(e.target.value))}><option value={0}>Choose…</option>{(employees.data?.employees ?? []).map((e) => <option key={e.id} value={e.id}>{e.fullName}</option>)}</select>}</Field>
        <Field label="From">{(id) => <input id={id} type="date" className="input w-44" value={range.from} onChange={(e) => e.target.value && setRange({ ...range, from: e.target.value })} />}</Field>
        <Field label="To">{(id) => <input id={id} type="date" className="input w-44" value={range.to} onChange={(e) => e.target.value && setRange({ ...range, to: e.target.value })} />}</Field>
      </div>
      {!eid ? <EmptyState icon="user-search-line" title="Choose an employee" /> : hist.error ? <ErrorState message={hist.error.message} onRetry={hist.reload} /> : !hist.data ? <Skeleton rows={5} /> : (
        <>
          <div className="flex flex-wrap gap-2 border-b border-ink-100 px-4 py-3 text-[12.5px]">
            {Object.entries(hist.data.counts).map(([s, n]) => <span key={s} className="flex items-center gap-1"><AttBadge status={s} /> {n}</span>)}
            <span className="text-ink-500">· {hist.data.lateMinutes} min late · {hist.data.undertimeMinutes} min undertime</span>
          </div>
          <DataTable rows={hist.data.records} rowKey={(r) => r.id} empty={{ icon: "time-line", title: "No records in this period" }} columns={[
            { key: "d", header: "Date", cell: (r) => <span className="whitespace-nowrap">{dateLabel(r.date)}</span> },
            { key: "s", header: "Status", cell: (r) => <AttBadge status={r.status} /> },
            { key: "i", header: "In", cell: (r) => r.timeIn ?? "—" },
            { key: "o", header: "Out", cell: (r) => r.timeOut ?? "—" },
            { key: "l", header: "Late / under", align: "right", cell: (r) => [r.lateMinutes ? `${r.lateMinutes} min late` : "", r.undertimeMinutes ? `${r.undertimeMinutes} min under` : ""].filter(Boolean).join(" · ") || "—" },
            { key: "r", header: "Remarks", cell: (r) => r.remarks || "—" },
          ]} />
        </>
      )}
    </Card>
  );
}

function Approvals<R extends LeaveRequest | OvertimeRequest>({ rows, describe, onDecide }: { rows: R[]; describe: (r: R) => string; onDecide: (r: R, s: "Approved" | "Rejected") => Promise<void> }) {
  const [confirm, setConfirm] = useState<{ row: R; status: "Approved" | "Rejected" } | null>(null);
  const { busy, act } = useAction();
  return (
    <>
      <DataTable rows={rows} rowKey={(r) => r.id} empty={{ icon: "checkbox-circle-line", title: "No requests" }} columns={[
        { key: "e", header: "Employee", cell: (r) => <b className="text-ink-900">{r.employeeName}</b> },
        { key: "d", header: "Request", cell: (r) => <>{describe(r)}{r.reason && <span className="block text-[12px] text-ink-500">{r.reason}</span>}</> },
        { key: "s", header: "Status", cell: (r) => <><StatusBadge status={r.status} />{r.decidedBy && <span className="block text-[12px] text-ink-500">by {r.decidedBy}</span>}</> },
        { key: "a", header: "", cell: (r) => r.status === "Pending" && (
          <div className="flex justify-end gap-2">
            <Button size="sm" variant="secondary" onClick={() => setConfirm({ row: r, status: "Rejected" })}>Reject</Button>
            <Button size="sm" onClick={() => setConfirm({ row: r, status: "Approved" })}>Approve</Button>
          </div>) },
      ]} />
      <ConfirmDialog open={!!confirm} title={confirm ? `${confirm.status === "Approved" ? "Approve" : "Reject"} request?` : ""} busy={busy} tone={confirm?.status === "Rejected" ? "danger" : "primary"}
        confirmLabel={confirm?.status === "Approved" ? "Approve" : "Reject"} message={confirm && `${confirm.row.employeeName} · ${describe(confirm.row)}. This can't be changed afterwards.`}
        onClose={() => setConfirm(null)} onConfirm={async () => { if (confirm) await act(() => onDecide(confirm.row, confirm.status)); setConfirm(null); }} />
    </>
  );
}

function OvertimePanel({ data, error, reload }: { data: { requests: OvertimeRequest[]; multipliers: { value: number; label: string }[] } | null; error: Error | null; reload: () => void }) {
  const employees = useAsync(() => api.hr.employees(), []);
  const [filing, setFiling] = useState(false);
  const [show, setShow] = useState<"pending" | "all">("pending");
  const [form, setForm] = useState({ employeeId: 0, date: todayIso(), hours: "", multiplier: "1.25", reason: "" });
  const [server, setServer] = useState<Record<string, string>>({});
  const { busy, act } = useAction();
  const toast = useToast();
  async function file() {
    try {
      const o = await act(() => api.hr.fileOvertime(form));
      toast({ tone: "success", title: "Overtime filed", message: `${o.employeeName} · ${o.hours}h · ${peso(o.amount)} (pending approval)` });
      setForm({ ...form, hours: "", reason: "" }); setFiling(false); reload();
    } catch (err) {
      setServer(serverFields(err));
      if (!Object.keys(serverFields(err)).length) toast({ tone: "error", title: "Not filed", message: (err as Error).message });
    }
  }
  const rows = (data?.requests ?? []).filter((o) => show === "all" || o.status === "Pending");
  return (
    <Card>
      <div className="flex flex-wrap items-center gap-3 border-b border-ink-100 p-4">
        <Tabs value={show} onChange={setShow} tabs={[{ key: "pending", label: "Waiting", count: data?.requests.filter((o) => o.status === "Pending").length }, { key: "all", label: "All" }]} />
        <div className="flex-1" /><Button icon="add-line" onClick={() => setFiling(true)}>File overtime</Button>
      </div>
      {error ? <ErrorState message={error.message} onRetry={reload} /> : !data ? <Skeleton rows={4} /> : (
        <Approvals rows={rows} describe={(o) => `${o.hours} hour(s) on ${dateLabel(o.date)} × ${o.multiplier} = ${peso(o.amount)}`}
          onDecide={async (o, s) => { try { await api.hr.decideOvertime(o.id, s); toast({ tone: "success", title: `Overtime ${s.toLowerCase()}` }); } catch (err) { toast({ tone: "error", title: "Not saved", message: (err as Error).message }); } reload(); }} />
      )}
      <Drawer open={filing} onClose={() => setFiling(false)} title="File overtime" subtitle="Filed as pending; the amount uses the employee's hourly rate from the Payroll Rules." width="max-w-md"
        footer={<><Button variant="secondary" onClick={() => setFiling(false)}>Cancel</Button><Button icon="time-line" loading={busy} onClick={file}>File overtime</Button></>}>
        <form className="grid gap-4" onSubmit={(e) => { e.preventDefault(); void file(); }} noValidate>
          <Field label="Employee" error={server.employeeId}>{(id) => <select id={id} className="input" value={form.employeeId} onChange={(e) => { setForm({ ...form, employeeId: Number(e.target.value) }); setServer({}); }}><option value={0}>Choose…</option>{(employees.data?.employees ?? []).filter((e) => e.status === "Active").map((e) => <option key={e.id} value={e.id}>{e.fullName}</option>)}</select>}</Field>
          <div className="grid grid-cols-2 gap-4">
            <Field label="Date" error={server.date}>{(id) => <input id={id} type="date" className="input" value={form.date} onChange={(e) => setForm({ ...form, date: e.target.value })} />}</Field>
            <Field label="Hours" error={server.hours}>{(id) => <input id={id} inputMode="decimal" className="input tabular" value={form.hours} onChange={(e) => { setForm({ ...form, hours: e.target.value }); setServer({}); }} />}</Field>
          </div>
          <Field label="Day type" error={server.multiplier}>{(id) => <select id={id} className="input" value={form.multiplier} onChange={(e) => setForm({ ...form, multiplier: e.target.value })}>{(data?.multipliers ?? []).map((m) => <option key={m.value} value={String(m.value)}>{m.value.toFixed(2)} × {m.label}</option>)}</select>}</Field>
          <Field label="Reason" error={server.reason}>{(id) => <input id={id} className="input" maxLength={500} value={form.reason} onChange={(e) => setForm({ ...form, reason: e.target.value })} />}</Field>
        </form>
      </Drawer>
    </Card>
  );
}

// ------------------------------------------------------------------ Leave
export function LeavePage() {
  const { data, error, reload } = useAsync(() => api.hr.leave(), []);
  const employees = useAsync(() => api.hr.employees(), []);
  const [show, setShow] = useState<"pending" | "all">("pending");
  const [filing, setFiling] = useState(false);
  const [form, setForm] = useState({ employeeId: 0, leaveType: "VACATION", from: todayIso(), to: todayIso(), reason: "" });
  const [server, setServer] = useState<Record<string, string>>({});
  const { busy, act } = useAction();
  const toast = useToast();
  const set = (patch: Partial<typeof form>) => { setForm({ ...form, ...patch }); setServer({}); };
  async function file() {
    try {
      const l = await act(() => api.hr.fileLeave(form));
      toast({ tone: "success", title: "Leave filed", message: `${l.employeeName} · ${l.days} day(s) (pending approval)` });
      setFiling(false); setForm({ ...form, reason: "" }); reload();
    } catch (err) {
      setServer(serverFields(err));
      if (!Object.keys(serverFields(err)).length) toast({ tone: "error", title: "Not filed", message: (err as Error).message });
    }
  }
  const rows = (data?.requests ?? []).filter((l) => show === "all" || l.status === "Pending");
  return (
    <>
      <PageHeader eyebrow="People" title="Leave Management" description="Leave requests are filed as Pending and decided once. Overlapping leave for the same employee is refused."
        actions={<Button icon="add-line" onClick={() => setFiling(true)}>File leave</Button>} />
      <MovedFormNotice screen="Leave" />
      <Card>
        <div className="border-b border-ink-100 p-4"><Tabs value={show} onChange={setShow} tabs={[{ key: "pending", label: "Waiting", count: data?.requests.filter((l) => l.status === "Pending").length }, { key: "all", label: "All", count: data?.requests.length }]} /></div>
        {error ? <ErrorState message={error.message} onRetry={reload} /> : !data ? <Skeleton rows={5} /> : (
          <Approvals rows={rows} describe={(l) => `${l.leaveType.charAt(0) + l.leaveType.slice(1).toLowerCase()} leave · ${dateLabel(l.from)}${l.to !== l.from ? ` – ${dateLabel(l.to)}` : ""} · ${l.days} day(s)`}
            onDecide={async (l, s) => { try { await api.hr.decideLeave(l.id, s); toast({ tone: "success", title: `Leave ${s.toLowerCase()}` }); } catch (err) { toast({ tone: "error", title: "Not saved", message: (err as Error).message }); } reload(); }} />
        )}
      </Card>
      <Drawer open={filing} onClose={() => setFiling(false)} title="File leave" width="max-w-md"
        footer={<><Button variant="secondary" onClick={() => setFiling(false)}>Cancel</Button><Button icon="calendar-event-line" loading={busy} onClick={file}>File leave</Button></>}>
        <form className="grid gap-4" onSubmit={(e) => { e.preventDefault(); void file(); }} noValidate>
          <Field label="Employee" error={server.employeeId}>{(id) => <select id={id} className="input" value={form.employeeId} onChange={(e) => set({ employeeId: Number(e.target.value) })}><option value={0}>Choose…</option>{(employees.data?.employees ?? []).filter((e) => e.status === "Active" || e.status === "On Leave").map((e) => <option key={e.id} value={e.id}>{e.fullName}</option>)}</select>}</Field>
          <Field label="Leave type" error={server.leaveType}>{(id) => <select id={id} className="input" value={form.leaveType} onChange={(e) => set({ leaveType: e.target.value })}>{(data?.types ?? []).map((t) => <option key={t} value={t}>{t.charAt(0) + t.slice(1).toLowerCase()}</option>)}</select>}</Field>
          <div className="grid grid-cols-2 gap-4">
            <Field label="From" error={server.from}>{(id) => <input id={id} type="date" className="input" value={form.from} onChange={(e) => set({ from: e.target.value, to: form.to < e.target.value ? e.target.value : form.to })} />}</Field>
            <Field label="To" error={server.to}>{(id) => <input id={id} type="date" className="input" min={form.from} value={form.to} onChange={(e) => set({ to: e.target.value })} />}</Field>
          </div>
          <Field label="Reason" error={server.reason}>{(id) => <textarea id={id} rows={3} maxLength={500} className="input h-auto py-2" value={form.reason} onChange={(e) => set({ reason: e.target.value })} />}</Field>
        </form>
      </Drawer>
    </>
  );
}

// ------------------------------------------------------------------ Payroll
type PayTab = "runs" | "loans" | "13th" | "reports" | "calculator";
const monthEnd = (m: string) => { const [y, mm] = m.split("-").map(Number); return `${m}-${String(new Date(y, mm, 0).getDate()).padStart(2, "0")}`; };

export function PayrollPage() {
  const [params, setParams] = useSearchParams();
  const tab = (["runs", "loans", "13th", "reports", "calculator"].includes(params.get("tab") ?? "") ? params.get("tab") : "runs") as PayTab;
  const payslip = Number(params.get("payslip")) || null;
  const setParam = (k: string, v: string | null) => { const p = new URLSearchParams(params); if (v) p.set(k, v); else p.delete(k); p.delete("moved"); setParams(p, { replace: true }); };
  if (payslip) return <PayslipView id={payslip} onBack={() => setParam("payslip", null)} />;
  return (
    <>
      <PageHeader eyebrow="Payroll" title="Payroll Engine" description="Payroll per employee and period with Philippine statutory deductions, computed by the server from the saved Payroll Rules." />
      <MovedFormNotice screen="Payroll" />
      <div className="mb-4"><Tabs value={tab} onChange={(t) => setParam("tab", t)} tabs={[{ key: "runs", label: "Payroll runs" }, { key: "loans", label: "Loans & deductions" }, { key: "13th", label: "13th month" }, { key: "reports", label: "Reports" }, { key: "calculator", label: "Calculator" }]} /></div>
      {tab === "runs" && <PayrollRuns onOpen={(id) => setParam("payslip", String(id))} />}
      {tab === "loans" && <LoansPanel />}
      {tab === "13th" && <ThirteenthPanel />}
      {tab === "reports" && <PayrollReportPanel onOpen={(id) => setParam("payslip", String(id))} />}
      {tab === "calculator" && <PayrollCalculator />}
    </>
  );
}

function PayrollRuns({ onOpen }: { onOpen: (id: number) => void }) {
  const [month, setMonth] = useState(currentMonth());
  const [half, setHalf] = useState<"whole" | "first" | "second">("whole");
  const from = half === "second" ? `${month}-16` : `${month}-01`;
  const to = half === "first" ? `${month}-15` : monthEnd(month);
  const { data, error, loading, reload } = useAsync(() => api.hr.payroll(from, to), [from, to]);
  const [generating, setGenerating] = useState(false);
  const total = (data?.payroll ?? []).reduce((s, p) => ({ gross: s.gross + toCents(p.gross), net: s.net + toCents(p.netPay) }), { gross: 0, net: 0 });
  const missing = (data?.employees ?? []).filter((e) => !e.hasPayroll);
  return (
    <>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <MonthPicker value={month} onChange={setMonth} max={currentMonth()} />
        <Tabs value={half} onChange={setHalf} tabs={[{ key: "whole", label: "Whole month" }, { key: "first", label: "1st–15th" }, { key: "second", label: "16th–end" }]} />
        <div className="flex-1" />
        <Button icon="add-line" disabled={!data} onClick={() => setGenerating(true)}>Generate payroll</Button>
      </div>
      <Notice tone="info">Basic salary defaults to the full monthly salary and statutory contributions are computed monthly, as in the classic payroll. For a half-month run, enter the basic for the period when generating.</Notice>
      <div className="my-6 grid gap-4 sm:grid-cols-3">
        <StatCard icon="team-line" label="Payrolls in this period" value={data ? `${data.payroll.length} of ${data.employees.length}` : "—"} hint={missing.length ? `${missing.length} employee(s) without payroll` : undefined} />
        <StatCard icon="money-dollar-box-line" label="Gross pay" value={peso((total.gross / 100).toFixed(2))} />
        <StatCard tone="hero" icon="bank-line" label="Net pay" value={peso((total.net / 100).toFixed(2))} />
      </div>
      <Card>
        <CardHeader title={`Payroll · ${dateLabel(from)} – ${dateLabel(to)}`} subtitle="Open a row for the payslip, statutory breakdown and status." />
        <DataTable rows={data?.payroll ?? null} loading={loading} error={error} onRetry={reload} rowKey={(p) => p.id} onRowClick={(p) => onOpen(p.id)} empty={{ icon: "money-dollar-box-line", title: "No payroll for this period yet" }} columns={[
          { key: "e", header: "Employee", cell: (p) => <><b className="text-ink-900">{p.employeeName}</b><span className="block text-[12px] text-ink-500">{dateLabel(p.periodStart)} – {dateLabel(p.periodEnd)}</span></> },
          { key: "g", header: "Gross", align: "right", cell: (p) => <span className="whitespace-nowrap">{peso(p.gross)}</span> },
          { key: "d", header: "Absences / late", align: "right", cell: (p) => <span className="whitespace-nowrap">{peso((toCents(p.absences) + toCents(p.lateUndertime)) / 100 + "")}</span> },
          { key: "o", header: "Other deductions", align: "right", cell: (p) => <span className="whitespace-nowrap">{peso(p.deductions)}</span> },
          { key: "n", header: "Net pay", align: "right", cell: (p) => <b className="whitespace-nowrap">{peso(p.netPay)}</b> },
          { key: "s", header: "Status", cell: (p) => <Badge tone={PAY_TONE[p.status]} dot={false}>{p.status}</Badge> },
        ]} />
      </Card>
      {data && <GenerateDrawer open={generating} from={from} to={to} employees={missing} onClose={() => setGenerating(false)} onDone={(id) => { setGenerating(false); reload(); onOpen(id); }} />}
    </>
  );
}

function GenerateDrawer({ open, from, to, employees, onClose, onDone }: { open: boolean; from: string; to: string; employees: { id: number; fullName: string; monthlySalary: string }[]; onClose: () => void; onDone: (id: number) => void }) {
  const blank = (): PayrollInput => ({ employeeId: 0, from, to, basic: "", overtime: "", allowances: "", deductions: "", status: "DRAFT", remarks: "", formToken: newFormToken() });
  const [form, setForm] = useState<PayrollInput>(blank);
  const [preview, setPreview] = useState<PayrollPreview | null>(null);
  const [server, setServer] = useState<Record<string, string>>({});
  const [problem, setProblem] = useState("");
  const { busy, act } = useAction();
  const toast = useToast();
  useEffect(() => { if (open) { setForm(blank()); setPreview(null); setServer({}); setProblem(""); } }, [open, from, to]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!open || !form.employeeId) { setPreview(null); return; }
    const t = setTimeout(() => { api.hr.previewPayroll(form).then((p) => { setPreview(p); setServer({}); }).catch((err) => { setPreview(null); setServer(serverFields(err)); }); }, 350);
    return () => clearTimeout(t);
  }, [open, form.employeeId, form.basic, form.overtime, form.allowances, form.deductions]); // eslint-disable-line react-hooks/exhaustive-deps
  const set = (patch: Partial<PayrollInput>) => { setForm({ ...form, ...patch }); setProblem(""); };
  async function generate() {
    try {
      const p = await act(() => api.hr.generatePayroll(form));
      toast({ tone: "success", title: "Payroll generated", message: `${p.employeeName} · net ${peso(p.netPay)}` });
      onDone(p.id);
    } catch (err) {
      setServer(serverFields(err));
      setProblem(Object.keys(serverFields(err)).length ? "" : (err as Error).message);
      set({ formToken: newFormToken() });
    }
  }
  const s = preview?.statutory;
  const line = (label: string, v: string | undefined, minus = false, strong = false) => (
    <div className={`flex justify-between px-4 py-1.5 ${strong ? "font-semibold text-ink-900" : "text-ink-600"}`}><span>{label}</span><span className="tabular">{v ? `${minus ? "− " : ""}${peso(v)}` : "—"}</span></div>
  );
  return (
    <Drawer open={open} onClose={onClose} title="Generate payroll" subtitle={`${dateLabel(from)} – ${dateLabel(to)}`} width="max-w-2xl"
      footer={<><Button variant="secondary" onClick={onClose}>Cancel</Button><Button icon="money-dollar-box-line" loading={busy} disabled={!form.employeeId} onClick={generate}>Generate</Button></>}>
      <div className="grid gap-6 md:grid-cols-2">
        <form className="grid gap-4" onSubmit={(e) => { e.preventDefault(); void generate(); }} noValidate>
          <Field label="Employee" error={server.employeeId} hint={!employees.length ? "Every current employee already has a payroll for this period." : undefined}>{(id) => (
            <select id={id} className="input" value={form.employeeId} onChange={(e) => set({ employeeId: Number(e.target.value) })}><option value={0}>Choose…</option>{employees.map((e) => <option key={e.id} value={e.id}>{e.fullName} · {peso(e.monthlySalary)}</option>)}</select>)}</Field>
          <Field label="Basic for the period (₱)" error={server.basic} hint="Blank = the monthly salary.">{(id) => <input id={id} inputMode="decimal" className="input tabular" value={form.basic} onChange={(e) => set({ basic: e.target.value })} />}</Field>
          <Field label="Overtime pay (₱)" error={server.overtime} hint={preview ? `Blank = approved overtime of the period (${peso(preview.approvedOvertime)}).` : "Blank = approved overtime of the period."}>{(id) => <input id={id} inputMode="decimal" className="input tabular" value={form.overtime} onChange={(e) => set({ overtime: e.target.value })} />}</Field>
          <div className="grid grid-cols-2 gap-4">
            <Field label="Allowances (₱)" error={server.allowances}>{(id) => <input id={id} inputMode="decimal" className="input tabular" value={form.allowances} onChange={(e) => set({ allowances: e.target.value })} />}</Field>
            <Field label="Other deductions (₱)" error={server.deductions}>{(id) => <input id={id} inputMode="decimal" className="input tabular" value={form.deductions} onChange={(e) => set({ deductions: e.target.value })} />}</Field>
          </div>
          <Field label="Status">{(id) => <select id={id} className="input" value={form.status} onChange={(e) => set({ status: e.target.value as PayrollInput["status"] })}><option value="DRAFT">Draft</option><option value="FINAL">Final</option><option value="PAID">Paid</option></select>}</Field>
          <Field label="Remarks">{(id) => <input id={id} className="input" maxLength={255} value={form.remarks} onChange={(e) => set({ remarks: e.target.value })} />}</Field>
          {problem && <div role="alert"><Notice tone="warn">{problem}</Notice></div>}
        </form>
        <div className="rounded-xl border border-ink-200 text-[13.5px]">
          <p className="border-b border-ink-100 px-4 py-2.5 font-semibold text-ink-900">Preview (server computation)</p>
          {!preview ? <p className="px-4 py-6 text-center text-ink-500">Choose an employee to see the payroll before saving.</p> : (
            <div className="divide-y divide-ink-100 py-1">
              <div>{line("Basic", preview.basic)}{line("Overtime", preview.overtime)}{line("Allowances", preview.allowances)}{line("Gross pay", s?.grossPay, false, true)}</div>
              <div>{line("Absences", preview.absences, true)}{line("Late / undertime", preview.lateUndertime, true)}{line("Loan deductions", preview.loanDeductions, true)}{line("Other deductions", preview.deductions, true)}</div>
              <div>{line("SSS", s?.sssEmployee, true)}{line("PhilHealth", s?.philhealthEmployee, true)}{line("Pag-IBIG", s?.pagibigEmployee, true)}{line("Withholding tax", s?.withholdingTax, true)}</div>
              <div>{line("Net pay", s?.netPay, false, true)}</div>
            </div>
          )}
        </div>
      </div>
    </Drawer>
  );
}

const STAT_FIELDS: [keyof Statutory, string, "employee" | "employer" | "info"][] = [
  ["sssEmployee", "SSS", "employee"], ["philhealthEmployee", "PhilHealth", "employee"], ["pagibigEmployee", "Pag-IBIG", "employee"], ["withholdingTax", "Withholding tax", "employee"],
  ["sssEmployer", "SSS", "employer"], ["sssEcEmployer", "SSS EC", "employer"], ["philhealthEmployer", "PhilHealth", "employer"], ["pagibigEmployer", "Pag-IBIG", "employer"], ["thirteenthMonth", "13th month accrual", "info"],
];

function PayslipView({ id, onBack }: { id: number; onBack: () => void }) {
  const { data, error, reload, setData } = useAsync(() => api.hr.payslip(id), [id]);
  const [editing, setEditing] = useState(false);
  const [confirmNext, setConfirmNext] = useState(false);
  const { busy, act } = useAction();
  const toast = useToast();
  const back = <Button variant="ghost" icon="arrow-left-line" onClick={onBack}>All payroll</Button>;
  if (error) return <><PageHeader eyebrow="Payroll" title="Payslip" actions={back} /><ErrorState title={error.status === 404 ? "Payroll not found" : "Couldn't load the payslip"} message={error.message} onRetry={error.status === 404 ? undefined : reload} /></>;
  if (!data) return <Card><Skeleton rows={8} /></Card>;
  const p = data.payroll, s = p.statutory;
  const update = (np: PayrollDetail) => setData({ ...data, payroll: np });
  async function advance() {
    if (!p.nextStatus) return;
    try {
      const np = await act(() => api.hr.advancePayroll(p.id, p.nextStatus!));
      update(np); setConfirmNext(false);
      toast({ tone: "success", title: `Payroll marked ${np.status.toLowerCase()}` });
    } catch (err) {
      setConfirmNext(false);
      toast({ tone: "error", title: "Not changed", message: (err as Error).message });
    }
  }
  const row = (label: string, v: string, minus = false, strong = false) => (
    <tr className={strong ? "font-semibold text-ink-900" : undefined}><td className="py-1.5">{label}</td><td className="py-1.5 text-right tabular">{minus && toCents(v) ? "− " : ""}{peso(v)}</td></tr>
  );
  return (
    <>
      <PageHeader eyebrow="Payroll" title={`Payslip · ${p.employeeName}`} description={`${dateLabel(p.periodStart)} – ${dateLabel(p.periodEnd)}`}
        actions={<>{back}
          {p.editable && <Button variant="secondary" icon="edit-line" onClick={() => setEditing(true)}>Edit statutory amounts</Button>}
          {p.nextStatus && <Button variant="secondary" icon="checkbox-circle-line" onClick={() => setConfirmNext(true)}>Mark {p.nextStatus.toLowerCase()}</Button>}
          <Button icon="printer-line" onClick={() => window.print()}>Print</Button></>} />
      <article className="print-sheet mx-auto max-w-[860px] rounded-xl border border-ink-200 bg-white p-8 text-ink-800 shadow-card" aria-label="Payslip">
        <header className="flex flex-wrap items-start justify-between gap-4 border-b-[3px] border-brand-600 pb-3">
          <div><h2 className="text-[18px] font-bold text-ink-900">{data.corporation}</h2>{data.address && <p className="text-ink-500">{data.address}</p>}</div>
          <div className="text-right"><p className="text-[11px] font-semibold tracking-[0.12em] text-ink-500">PAYSLIP</p><Badge tone={PAY_TONE[p.status]} dot={false}>{p.status}</Badge></div>
        </header>
        <div className="grid gap-2 py-4 text-[13.5px] sm:grid-cols-2">
          <p>Employee: <b>{p.employeeName}</b>{p.employee && <> · {p.employee.employeeNo}</>}</p>
          <p className="sm:text-right">Period: <b>{dateLabel(p.periodStart)} – {dateLabel(p.periodEnd)}</b></p>
          {p.employee?.position && <p>Position: {p.employee.position}{p.employee.department ? ` · ${p.employee.department}` : ""}</p>}
        </div>
        <div className="grid gap-8 md:grid-cols-2">
          <table className="w-full self-start text-[13.5px]"><tbody>
            <tr><td colSpan={2} className="pb-1 text-[11.5px] font-bold tracking-[0.12em] text-brand-600">EARNINGS</td></tr>
            {row("Basic salary", p.basic)}{row("Overtime", p.overtime)}{row("Allowances", p.allowances)}{row("Gross pay", s.grossPay, false, true)}
          </tbody></table>
          <table className="w-full self-start text-[13.5px]"><tbody>
            <tr><td colSpan={2} className="pb-1 text-[11.5px] font-bold tracking-[0.12em] text-brand-600">DEDUCTIONS</td></tr>
            {row("SSS", s.sssEmployee, true)}{row("PhilHealth", s.philhealthEmployee, true)}{row("Pag-IBIG", s.pagibigEmployee, true)}{row("Withholding tax", s.withholdingTax, true)}
            {row("Absences", p.absences, true)}{row("Late / undertime", p.lateUndertime, true)}{row("Loans & other deductions", p.deductions, true)}{row("Total deductions", s.totalEmployeeDeductions, true, true)}
          </tbody></table>
        </div>
        <div className="mt-6 flex items-center justify-between rounded-xl bg-brand-50 px-5 py-4"><span className="font-semibold text-brand-800">NET PAY</span><span className="font-display text-[24px] font-bold text-brand-800 tabular">{peso(p.netPay)}</span></div>
        <p className="mt-4 text-[12.5px] text-ink-500">Employer share (not deducted): SSS {peso(s.sssEmployer)} + EC {peso(s.sssEcEmployer)}, PhilHealth {peso(s.philhealthEmployer)}, Pag-IBIG {peso(s.pagibigEmployer)} = {peso(s.totalEmployerCost)}. 13th-month accrual {peso(s.thirteenthMonth)}.</p>
        {p.remarks && <p className="mt-2 text-[12.5px] text-ink-500">Remarks: {p.remarks}</p>}
      </article>
      <StatutoryDrawer open={editing} payroll={p} onClose={() => setEditing(false)} onSaved={(np) => { update(np); setEditing(false); }} />
      <ConfirmDialog open={confirmNext} title={`Mark this payroll ${p.nextStatus?.toLowerCase()}?`} busy={busy} confirmLabel={`Mark ${p.nextStatus?.toLowerCase()}`}
        message={p.nextStatus === "Paid" ? "A paid payroll can't be edited any more." : "Status only moves forward: Draft → Final → Paid."} onClose={() => setConfirmNext(false)} onConfirm={advance} />
    </>
  );
}

function StatutoryDrawer({ open, payroll, onClose, onSaved }: { open: boolean; payroll: PayrollDetail; onClose: () => void; onSaved: (p: PayrollDetail) => void }) {
  const [form, setForm] = useState<Statutory>(payroll.statutory);
  const [server, setServer] = useState<Record<string, string>>({});
  const { busy, act } = useAction();
  const toast = useToast();
  useEffect(() => { if (open) { setForm(payroll.statutory); setServer({}); } }, [open, payroll.statutory]);
  async function save() {
    try {
      const p = await act(() => api.hr.editStatutory(payroll.id, form));
      toast({ tone: "success", title: "Statutory amounts saved", message: `Net pay is now ${peso(p.netPay)}` });
      onSaved(p);
    } catch (err) {
      setServer(serverFields(err));
      if (!Object.keys(serverFields(err)).length) toast({ tone: "error", title: "Not saved", message: (err as Error).message });
    }
  }
  return (
    <Drawer open={open} onClose={onClose} title="Edit statutory amounts" subtitle="Totals and net pay are recomputed from these amounts when you save." width="max-w-lg"
      footer={<><Button variant="secondary" onClick={onClose}>Cancel</Button><Button icon="save-3-line" loading={busy} onClick={save}>Save</Button></>}>
      {(["employee", "employer", "info"] as const).map((g) => (
        <Fragment key={g}>
          <p className="mt-2 mb-2 text-[11.5px] font-semibold tracking-wide text-ink-500 uppercase">{g === "employee" ? "Employee share (deducted)" : g === "employer" ? "Employer share" : "Other"}</p>
          <div className="mb-4 grid gap-3 sm:grid-cols-2">
            {STAT_FIELDS.filter(([, , grp]) => grp === g).map(([k, label]) => (
              <Field key={k} label={`${label} (₱)`} error={server[k]}>{(id) => <input id={id} inputMode="decimal" className="input tabular" value={form[k]} onChange={(e) => { setForm({ ...form, [k]: e.target.value }); setServer({}); }} />}</Field>
            ))}
          </div>
        </Fragment>
      ))}
    </Drawer>
  );
}

function LoansPanel() {
  const { data, error, loading, reload } = useAsync(() => api.hr.loans(), []);
  const employees = useAsync(() => api.hr.employees(), []);
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<HrLoan | null>(null);
  const blank: LoanInput = { employeeId: 0, loanType: "SSS", referenceNo: "", originalAmount: "", balance: "", monthlyDeduction: "", notes: "" };
  const [form, setForm] = useState<LoanInput>(blank);
  const [edit, setEdit] = useState({ status: "ACTIVE" as HrLoan["status"], monthlyDeduction: "", notes: "" });
  const [server, setServer] = useState<Record<string, string>>({});
  const { busy, act } = useAction();
  const toast = useToast();
  async function add() {
    try {
      const l = await act(() => api.hr.addLoan(form));
      toast({ tone: "success", title: "Loan added", message: `${l.employeeName} · ${peso(l.monthlyDeduction)} per payroll` });
      setAdding(false); setForm(blank); reload();
    } catch (err) {
      setServer(serverFields(err));
      if (!Object.keys(serverFields(err)).length) toast({ tone: "error", title: "Not saved", message: (err as Error).message });
    }
  }
  async function saveEdit() {
    if (!editing) return;
    try {
      await act(() => api.hr.updateLoan(editing.id, edit));
      toast({ tone: "success", title: "Loan updated" });
      setEditing(null); reload();
    } catch (err) {
      setServer(serverFields(err));
      if (!Object.keys(serverFields(err)).length) toast({ tone: "error", title: "Not saved", message: (err as Error).message });
    }
  }
  const STATUS_LABEL: Record<string, string> = { ACTIVE: "Active", HOLD: "On hold", PAID: "Paid" };
  return (
    <Card>
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-ink-100 p-4">
        <p className="text-[13px] text-ink-500">Active loans are deducted from each payroll (up to the balance) and the balance goes down automatically. Put a loan on hold to skip it.</p>
        <Button icon="add-line" onClick={() => { setAdding(true); setServer({}); }}>Add loan</Button>
      </div>
      <DataTable rows={data?.loans ?? null} loading={loading} error={error} onRetry={reload} rowKey={(l) => l.id} onRowClick={(l) => { setEditing(l); setEdit({ status: l.status, monthlyDeduction: l.monthlyDeduction, notes: l.notes }); setServer({}); }}
        empty={{ icon: "hand-coin-line", title: "No loans or recurring deductions" }} columns={[
          { key: "e", header: "Employee", cell: (l) => <><b className="text-ink-900">{l.employeeName}</b><span className="block text-[12px] text-ink-500">{l.loanType}{l.referenceNo ? ` · ${l.referenceNo}` : ""}</span></> },
          { key: "o", header: "Loan", align: "right", cell: (l) => peso(l.originalAmount) },
          { key: "m", header: "Per payroll", align: "right", cell: (l) => peso(l.monthlyDeduction) },
          { key: "b", header: "Balance", align: "right", cell: (l) => <b>{peso(l.balance)}</b> },
          { key: "s", header: "Status", cell: (l) => <Badge tone={l.status === "ACTIVE" ? "ok" : l.status === "HOLD" ? "warn" : "neutral"} dot={false}>{STATUS_LABEL[l.status]}</Badge> },
        ]} />
      <Drawer open={adding} onClose={() => setAdding(false)} title="Add loan or recurring deduction" width="max-w-md"
        footer={<><Button variant="secondary" onClick={() => setAdding(false)}>Cancel</Button><Button icon="save-3-line" loading={busy} onClick={add}>Save</Button></>}>
        <form className="grid gap-4 sm:grid-cols-2" onSubmit={(e) => { e.preventDefault(); void add(); }} noValidate>
          <Field label="Employee" className="sm:col-span-2" error={server.employeeId}>{(id) => <select id={id} className="input" value={form.employeeId} onChange={(e) => setForm({ ...form, employeeId: Number(e.target.value) })}><option value={0}>Choose…</option>{(employees.data?.employees ?? []).filter((e) => e.status === "Active" || e.status === "On Leave").map((e) => <option key={e.id} value={e.id}>{e.fullName}</option>)}</select>}</Field>
          <Field label="Type" error={server.loanType}>{(id) => <select id={id} className="input" value={form.loanType} onChange={(e) => setForm({ ...form, loanType: e.target.value })}>{(data?.types ?? []).map((t) => <option key={t}>{t}</option>)}</select>}</Field>
          <Field label="Reference no." error={server.referenceNo}>{(id) => <input id={id} className="input" maxLength={80} value={form.referenceNo} onChange={(e) => setForm({ ...form, referenceNo: e.target.value })} />}</Field>
          <Field label="Loan amount (₱)" error={server.originalAmount}>{(id) => <input id={id} inputMode="decimal" className="input tabular" value={form.originalAmount} onChange={(e) => setForm({ ...form, originalAmount: e.target.value })} />}</Field>
          <Field label="Balance (₱)" error={server.balance} hint="Blank = the loan amount.">{(id) => <input id={id} inputMode="decimal" className="input tabular" value={form.balance} onChange={(e) => setForm({ ...form, balance: e.target.value })} />}</Field>
          <Field label="Deduction per payroll (₱)" className="sm:col-span-2" error={server.monthlyDeduction}>{(id) => <input id={id} inputMode="decimal" className="input tabular" value={form.monthlyDeduction} onChange={(e) => setForm({ ...form, monthlyDeduction: e.target.value })} />}</Field>
          <Field label="Notes" className="sm:col-span-2" error={server.notes}>{(id) => <input id={id} className="input" maxLength={300} value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} />}</Field>
        </form>
      </Drawer>
      <Drawer open={!!editing} onClose={() => setEditing(null)} title={editing ? `${editing.employeeName} · ${editing.loanType}` : ""} subtitle={editing ? `Balance ${peso(editing.balance)} of ${peso(editing.originalAmount)}` : undefined} width="max-w-md"
        footer={<><Button variant="secondary" onClick={() => setEditing(null)}>Cancel</Button><Button icon="save-3-line" loading={busy} onClick={saveEdit}>Save</Button></>}>
        <form className="grid gap-4" onSubmit={(e) => { e.preventDefault(); void saveEdit(); }} noValidate>
          <Field label="Status" error={server.status}>{(id) => <select id={id} className="input" value={edit.status} onChange={(e) => setEdit({ ...edit, status: e.target.value as HrLoan["status"] })}><option value="ACTIVE">Active (deducted)</option><option value="HOLD">On hold (skipped)</option><option value="PAID">Paid</option></select>}</Field>
          <Field label="Deduction per payroll (₱)" error={server.monthlyDeduction}>{(id) => <input id={id} inputMode="decimal" className="input tabular" value={edit.monthlyDeduction} onChange={(e) => setEdit({ ...edit, monthlyDeduction: e.target.value })} />}</Field>
          <Field label="Notes" error={server.notes}>{(id) => <input id={id} className="input" maxLength={300} value={edit.notes} onChange={(e) => setEdit({ ...edit, notes: e.target.value })} />}</Field>
        </form>
      </Drawer>
    </Card>
  );
}

function YearPicker({ value, onChange }: { value: number; onChange: (y: number) => void }) {
  const now = new Date().getFullYear();
  return <select className="input w-32" aria-label="Year" value={value} onChange={(e) => onChange(Number(e.target.value))}>{Array.from({ length: 8 }, (_, i) => now - i).map((y) => <option key={y}>{y}</option>)}</select>;
}

function ThirteenthPanel() {
  const [params, setParams] = useSearchParams();
  const year = Number(params.get("year")) || new Date().getFullYear();
  const setYear = (y: number) => { const p = new URLSearchParams(params); p.set("year", String(y)); setParams(p, { replace: true }); };
  const { data, error, loading, reload } = useAsync(() => api.hr.thirteenthMonth(year), [year]);
  return (
    <Card>
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-ink-100 p-4">
        <p className="text-[13px] text-ink-500">13th month = basic salary of the year's payrolls ÷ 12. Up to {data ? peso(data.ceiling) : "the ceiling"} is tax-exempt (Payroll Rules).</p>
        <YearPicker value={year} onChange={setYear} />
      </div>
      {data && <div className="grid gap-4 border-b border-ink-100 p-4 sm:grid-cols-2"><StatCard tone="hero" icon="gift-line" label={`13th month ${year}`} value={peso(data.total)} /><StatCard icon="government-line" label="Taxable part" value={peso(data.taxable)} /></div>}
      <DataTable rows={data?.rows ?? null} loading={loading} error={error} onRetry={reload} rowKey={(r) => r.employeeId} empty={{ icon: "gift-line", title: "No employees" }} columns={[
        { key: "e", header: "Employee", cell: (r) => <><b className="text-ink-900">{r.employeeName}</b><span className="block text-[12px] text-ink-500">{r.employeeNo} · {r.payrolls} payroll(s)</span></> },
        { key: "b", header: "Basic paid", align: "right", cell: (r) => peso(r.basicTotal) },
        { key: "t", header: "13th month", align: "right", cell: (r) => <b>{peso(r.thirteenth)}</b> },
        { key: "x", header: "Exempt", align: "right", cell: (r) => peso(r.exempt) },
        { key: "y", header: "Taxable", align: "right", cell: (r) => (toCents(r.taxable) ? <Badge tone="warn" dot={false}>{peso(r.taxable)}</Badge> : peso(r.taxable)) },
      ]} />
    </Card>
  );
}

function PayrollReportPanel({ onOpen }: { onOpen: (id: number) => void }) {
  const [params, setParams] = useSearchParams();
  const year = Number(params.get("year")) || new Date().getFullYear();
  const setYear = (y: number) => { const p = new URLSearchParams(params); p.set("year", String(y)); setParams(p, { replace: true }); };
  const { data, error, loading, reload } = useAsync(() => api.hr.payrollReport(year), [year]);
  const t = data?.totals;
  return (
    <>
      <div className="mb-4 flex justify-end"><YearPicker value={year} onChange={setYear} /></div>
      {t && (
        <div className="mb-6 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <StatCard tone="hero" icon="bank-line" label={`Net pay ${year}`} value={pesoShort(t.net)} />
          <StatCard icon="money-dollar-box-line" label="Basic + overtime + allowances" value={pesoShort(String((toCents(t.basic) + toCents(t.overtime) + toCents(t.allowances)) / 100))} />
          <StatCard icon="government-line" label="SSS / PhilHealth / Pag-IBIG (employee)" value={pesoShort(String((toCents(t.sssEmployee) + toCents(t.philhealthEmployee) + toCents(t.pagibigEmployee)) / 100))} hint={`Employer: ${peso(String((toCents(t.sssEmployer) + toCents(t.philhealthEmployer) + toCents(t.pagibigEmployer)) / 100))}`} />
          <StatCard icon="file-list-3-line" label="Withholding tax" value={pesoShort(t.withholdingTax)} />
        </div>
      )}
      <Card>
        <CardHeader title={`Payrolls ${year}`} />
        <DataTable rows={data?.rows ?? null} loading={loading} error={error} onRetry={reload} rowKey={(p) => p.id} onRowClick={(p) => onOpen(p.id)} empty={{ icon: "file-list-3-line", title: "No payroll this year" }} columns={[
          { key: "e", header: "Employee", cell: (p) => <b className="text-ink-900">{p.employeeName}</b> },
          { key: "p", header: "Period", cell: (p) => <span className="whitespace-nowrap">{dateLabel(p.periodStart)} – {dateLabel(p.periodEnd)}</span> },
          { key: "g", header: "Gross", align: "right", cell: (p) => peso(p.gross) },
          { key: "n", header: "Net", align: "right", cell: (p) => <b>{peso(p.netPay)}</b> },
          { key: "s", header: "Status", cell: (p) => <Badge tone={PAY_TONE[p.status]} dot={false}>{p.status}</Badge> },
        ]} />
      </Card>
    </>
  );
}

function PayrollCalculator() {
  const [v, setV] = useState({ basic: "25000", overtime: "0", allowances: "0", deductions: "0", lateUndertime: "0" });
  const [result, setResult] = useState<Statutory | null>(null);
  const [problem, setProblem] = useState("");
  useEffect(() => {
    const t = setTimeout(() => { api.hr.calculate(v).then((s) => { setResult(s); setProblem(""); }).catch((err) => setProblem(Object.values(serverFields(err))[0] ?? (err as Error).message)); }, 300);
    return () => clearTimeout(t);
  }, [v]);
  const fields: [keyof typeof v, string][] = [["basic", "Monthly basic salary"], ["overtime", "Overtime pay"], ["allowances", "Allowances"], ["lateUndertime", "Late / undertime deduction"], ["deductions", "Loans & other deductions"]];
  const line = (label: string, value: string | undefined, minus = false, strong = false) => (
    <div className={`flex justify-between px-4 py-2.5 ${strong ? "font-semibold text-ink-900" : "text-ink-600"}`}><span>{label}</span><span className="tabular">{value ? `${minus ? "− " : ""}${peso(value)}` : "—"}</span></div>
  );
  return (
    <div className="grid gap-6 xl:grid-cols-[360px_1fr]">
      <Card className="self-start">
        <CardHeader title="Monthly amounts" />
        <div className="grid gap-4 p-5">
          {fields.map(([k, label]) => <Field key={k} label={`${label} (₱)`}>{(id) => <input id={id} inputMode="decimal" className="input tabular" value={v[k]} onChange={(e) => setV({ ...v, [k]: e.target.value })} />}</Field>)}
          {problem && <p className="text-[12px] text-red-600" role="alert">{problem}</p>}
        </div>
      </Card>
      <div className="space-y-6">
        <div className="grid gap-4 sm:grid-cols-3">
          <StatCard label="Gross pay" icon="money-dollar-box-line" value={result ? peso(result.grossPay) : "—"} />
          <StatCard tone="copper" label="Total deductions" icon="subtract-line" value={result ? peso(result.totalEmployeeDeductions) : "—"} />
          <StatCard tone="hero" label="Net pay" icon="bank-line" value={result ? peso(result.netPay) : "—"} />
        </div>
        <Card>
          <CardHeader title="Breakdown" subtitle="Computed by the server with the saved Payroll Rules: the same computation a generated payroll uses." />
          <div className="grid divide-y divide-ink-100 lg:grid-cols-2 lg:divide-x lg:divide-y-0">
            <div className="divide-y divide-ink-100">
              <p className="px-4 py-2 text-[11.5px] font-semibold tracking-wide text-ink-500 uppercase">Employee share (deducted)</p>
              {line("SSS", result?.sssEmployee, true)}{line("PhilHealth", result?.philhealthEmployee, true)}{line("Pag-IBIG", result?.pagibigEmployee, true)}{line("Withholding tax", result?.withholdingTax, true)}
            </div>
            <div className="divide-y divide-ink-100">
              <p className="px-4 py-2 text-[11.5px] font-semibold tracking-wide text-ink-500 uppercase">Employer share (not deducted)</p>
              {line("SSS", result?.sssEmployer)}{line("SSS EC", result?.sssEcEmployer)}{line("PhilHealth", result?.philhealthEmployer)}{line("Pag-IBIG", result?.pagibigEmployer)}{line("Total employer cost", result?.totalEmployerCost, false, true)}
            </div>
          </div>
        </Card>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ Philippine tax rules = the saved Payroll Rules
export function TaxRulesPage() {
  const { data, error, reload, setData } = useAsync(() => api.hr.rules(), []);
  const [values, setValues] = useState<Record<string, string>>({});
  const [server, setServer] = useState<Record<string, string>>({});
  const { busy, act } = useAction();
  const toast = useToast();
  const { can } = useAuth();
  if (data && !Object.keys(values).length) setValues(Object.fromEntries(data.map((r) => [r.key, r.value])));
  const changed = (data ?? []).filter((r) => values[r.key] !== undefined && values[r.key] !== r.value);
  async function save() {
    try {
      const rules = await act(() => api.hr.saveRules(Object.fromEntries(changed.map((r) => [r.key, values[r.key]]))));
      setData(rules); setValues(Object.fromEntries(rules.map((r) => [r.key, r.value]))); setServer({});
      toast({ tone: "success", title: "Payroll Rules saved", message: "New payrolls use them; payrolls already generated keep their amounts." });
    } catch (err) {
      setServer(serverFields(err));
      if (!Object.keys(serverFields(err)).length) toast({ tone: "error", title: "Not saved", message: (err as Error).message });
    }
  }
  if (error) return <ErrorState message={error.message} onRetry={reload} />;
  if (!data) return <Card><Skeleton rows={8} /></Card>;
  const groups = [...new Set(data.map((r) => r.group))];
  const val = (k: string) => Number(values[k] ?? data.find((r) => r.key === k)?.value ?? 0);
  const bir: [number, number, number, number][] = [[0, val("hr_bir_exempt_threshold"), 0, 0], [val("hr_bir_exempt_threshold"), val("hr_bir_bracket2"), 0, val("hr_bir_rate2")],
    [val("hr_bir_bracket2"), val("hr_bir_bracket3"), 1875, val("hr_bir_rate3")], [val("hr_bir_bracket3"), val("hr_bir_bracket4"), 8541.8, val("hr_bir_rate4")],
    [val("hr_bir_bracket4"), val("hr_bir_bracket5"), 33541.8, val("hr_bir_rate5")], [val("hr_bir_bracket5"), 0, 183541.8, val("hr_bir_rate6")]];
  const editable = can("employee_hr_settings");
  return (
    <>
      <PageHeader eyebrow="Payroll" title="Philippine Tax Rules" description="The contribution and withholding rules every payroll uses (SSS, PhilHealth, Pag-IBIG, BIR). Check them against the latest circulars."
        actions={editable && <Button icon="save-3-line" loading={busy} disabled={!changed.length} onClick={save}>Save {changed.length ? `${changed.length} change${changed.length === 1 ? "" : "s"}` : "rules"}</Button>} />
      <MovedFormNotice screen="Payroll Rules" />
      <div className="mb-6"><Notice tone="info">Changes apply to payrolls generated afterwards. Payrolls already generated keep their saved amounts (edit a draft's statutory amounts on its payslip if needed).</Notice></div>
      <div className="grid gap-6 xl:grid-cols-2">
        {groups.map((g) => (
          <Card key={g}>
            <CardHeader title={g} />
            <div className="grid gap-4 p-5 sm:grid-cols-2">
              {data.filter((r) => r.group === g).map((r: PayrollRule) => (
                <Field key={r.key} label={`${r.label} (${r.unit})`} error={server[r.key]} hint={values[r.key] !== r.default ? `Default ${r.default}` : undefined}>{(id) => (
                  <input id={id} inputMode="decimal" className="input tabular" disabled={!editable} value={values[r.key] ?? r.value} onChange={(e) => { setValues({ ...values, [r.key]: e.target.value }); setServer({}); }} />)}</Field>
              ))}
            </div>
          </Card>
        ))}
        <Card>
          <CardHeader title="BIR withholding table (monthly)" subtitle="Built from the brackets above" />
          <table className="w-full text-[13.5px]">
            <thead><tr className="border-b border-ink-100 text-left text-[12px] text-ink-500 uppercase"><th className="px-5 py-2">Taxable income</th><th className="px-5 py-2 text-right">Base tax</th><th className="px-5 py-2 text-right">Plus % of excess</th></tr></thead>
            <tbody className="divide-y divide-ink-100">
              {bir.map(([over, upTo, base, rate], i) => (
                <tr key={i}><td className="px-5 py-2">{i === 0 ? `Up to ${peso(String(upTo))}` : upTo ? `${peso(String(over))} – ${peso(String(upTo))}` : `Over ${peso(String(over))}`}</td>
                  <td className="px-5 py-2 text-right tabular">{peso(String(base))}</td><td className="px-5 py-2 text-right">{rate ? `${rate}%` : "Exempt"}</td></tr>
              ))}
            </tbody>
          </table>
        </Card>
      </div>
    </>
  );
}

// ------------------------------------------------------------------ Staff: daily attendance entry + dashboard
export function AttendanceEntryPage() {
  const [date, setDate] = useState(todayIso());
  return (
    <>
      <PageHeader eyebrow="Records" title="Daily Attendance Entry" description="Record each employee's time in and out. Leave and overtime requests are handled by HR." />
      <MovedFormNotice screen="Attendance" />
      <AttendanceSheet date={date} onDateChange={setDate} title={date === todayIso() ? "Today" : dateLabel(date)} />
      {date !== todayIso() && <p className="mt-3 text-[12.5px] text-ink-500">Entering a past day ({dateLabel(date)}). <button className="font-semibold text-brand-700 hover:underline" onClick={() => setDate(todayIso())}>Back to today</button> · <button className="font-semibold text-brand-700 hover:underline" onClick={() => setDate(addDays(date, -1))}>Day before</button></p>}
    </>
  );
}

export function StaffDashboard() {
  const user = useUser();
  const { data, error, reload } = useAsync(() => api.dashboards.staff(), []);
  const passes = useAsync(() => api.gatePasses.list(), []);
  if (error) return <ErrorState message={error.message} onRetry={reload} />;
  if (!data) return <Card><Skeleton rows={6} /></Card>;
  const portal = ROLES[user.role].portal;
  const todays = (passes.data ?? []).filter((p) => p.date === todayIso() && p.status === "Issued");
  const actions: [string, string, string, string][] = [["gate-passes", "passport-line", "Issue a gate pass", "Visitors, deliveries, movers"], ["certificates", "truck-line", "Move In / Out certificate", "Numbered certificate"], ["maintenance", "tools-line", "Maintenance tickets", `${data.openTickets} open`], ["attendance-entry", "fingerprint-line", "Attendance entry", `${data.attendanceEntered}/${data.attendanceExpected} today`]];
  return (
    <>
      <PageHeader eyebrow={`Good day, ${user.displayName?.split(" ")[0] ?? user.username}`} title="Front desk" description={dateLabel(todayIso())} />
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard tone={data.pendingRequests ? "copper" : "default"} icon="inbox-archive-line" label="Resident requests" value={data.pendingRequests} hint="Gate passes to approve" />
        <StatCard tone="hero" icon="passport-line" label="Passes valid today" value={data.gatePassesToday} />
        <StatCard icon="tools-line" label="Open tickets" value={data.openTickets} />
        <StatCard icon="wallet-3-line" label="Expenses this month" value={pesoShort(data.expensesThisMonth)} />
      </div>
      <div className="mt-6 grid gap-6 xl:grid-cols-[1fr_1.3fr]">
        <Card>
          <CardHeader title="Quick actions" />
          <div className="grid gap-2 p-3 sm:grid-cols-2">
            {actions.map(([to, icon, label, hint]) => (
              <Link key={to} to={`/${portal}/${to}`} className="group flex items-center gap-3 rounded-xl border border-ink-200 p-3 transition hover:border-brand-300 hover:bg-brand-50">
                <span className="grid size-10 place-items-center rounded-lg bg-brand-50 text-lg text-brand-600 group-hover:bg-white"><Icon name={icon} /></span>
                <span><span className="block font-semibold text-ink-900">{label}</span><span className="text-[12px] text-ink-500">{hint}</span></span>
              </Link>
            ))}
          </div>
        </Card>
        <Card>
          <CardHeader title="Expected at the gate today" actions={<Badge tone="info" dot={false}>{todays.length}</Badge>} />
          {todays.length === 0 ? <p className="px-5 py-8 text-center text-ink-500">No passes for today.</p> : (
            <ul className="divide-y divide-ink-100">{todays.map((p) => <li key={p.id} className="flex items-center justify-between gap-3 px-5 py-3"><div><p className="font-semibold text-ink-900">{p.name}</p><p className="text-[12.5px] text-ink-500">{p.type} · unit {p.unitNo} · {p.purpose}</p></div></li>)}</ul>
          )}
        </Card>
      </div>
    </>
  );
}
