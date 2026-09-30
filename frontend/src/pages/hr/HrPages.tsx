// HR & Payroll workspace (Manager) + Staff attendance entry and Staff dashboard.
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useUser } from "../../auth/AuthContext";
import { DataTable } from "../../components/base/DataTable";
import { ConfirmDialog, useToast } from "../../components/base/overlays";
import { Badge, Button, Card, CardHeader, ErrorState, Field, Icon, Notice, PageHeader, SearchInput, Skeleton, StatCard, StatusBadge, Tabs } from "../../components/base/ui";
import { ROLES } from "../../config/roles";
import { useAction, useAsync } from "../../hooks/useAsync";
import { addMonths, currentMonth, dateLabel, monthLabel, todayIso } from "../../lib/format";
import { fromCents, peso, pesoShort, toCents } from "../../lib/money";
import { RULES, previewPayroll, sssMsc } from "../../lib/payroll";
import { api } from "../../services/api";
import type { AttendanceStatus, LeaveRequest, OvertimeRequest } from "../../services/types";
import { MonthPicker } from "../property/Billing";

// ------------------------------------------------------------------ HR dashboard
export function HrDashboard() {
  const user = useUser();
  const { data, error, reload } = useAsync(() => api.dashboards.hr(), []);
  const leave = useAsync(() => api.hr.leave(), []);
  const ot = useAsync(() => api.hr.overtime(), []);
  if (error) return <ErrorState message={error.message} onRetry={reload} />;
  if (!data) return <Card><Skeleton rows={6} /></Card>;
  const portal = ROLES[user.role].portal;
  const pending = [...(leave.data ?? []).filter((l) => l.status === "Pending").map((l) => ({ key: `l${l.id}`, who: l.employeeName, what: `${l.leaveType} · ${l.days} day(s) from ${dateLabel(l.from)}`, to: "leave" })),
    ...(ot.data ?? []).filter((o) => o.status === "Pending").map((o) => ({ key: `o${o.id}`, who: o.employeeName, what: `Overtime · ${o.hours}h on ${dateLabel(o.date)}`, to: "attendance" }))];
  return (
    <>
      <PageHeader eyebrow={`Good day, ${user.displayName?.split(" ")[0] ?? user.username}`} title="People & payroll" description="Attendance today, requests waiting for you, and the monthly payroll." />
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard tone="hero" icon="team-line" label="Headcount" value={data.headcount} hint={`${data.onLeave} on leave`} />
        <StatCard icon="time-line" label="Present today" value={data.presentToday} hint={`${data.lateToday} late`} />
        <StatCard tone={pending.length ? "copper" : "default"} icon="inbox-archive-line" label="Waiting for approval" value={pending.length} hint={`${data.pendingLeave} leave · ${data.pendingOvertime} overtime`} />
        <StatCard icon="money-dollar-box-line" label="Monthly basic payroll" value={pesoShort(data.monthlyPayroll)} />
      </div>
      <div className="mt-6 grid gap-6 xl:grid-cols-[1.4fr_1fr]">
        <Card>
          <CardHeader title="Requests waiting for you" />
          {pending.length === 0 ? <p className="px-5 py-8 text-center text-ink-500">Nothing to approve. </p> : (
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
        <Card>
          <CardHeader title="Payroll calendar" />
          <ul className="space-y-3 p-5 text-[13.5px]">
            <li className="flex gap-3"><Icon name="calendar-event-line" className="mt-0.5 text-copper-600" /><div><b>13th</b> — cut-off for overtime and leave forms</div></li>
            <li className="flex gap-3"><Icon name="money-dollar-circle-line" className="mt-0.5 text-brand-600" /><div><b>15th & 30th</b> — payroll release</div></li>
            <li className="flex gap-3"><Icon name="government-line" className="mt-0.5 text-ink-500" /><div>SSS, PhilHealth and Pag-IBIG remittances follow each agency's schedule.</div></li>
          </ul>
        </Card>
      </div>
    </>
  );
}

// ------------------------------------------------------------------ Employees
export function EmployeesPage() {
  const { data, error, loading, reload } = useAsync(() => api.hr.employees(), []);
  const [q, setQ] = useState("");
  const rows = useMemo(() => {
    const n = q.trim().toLowerCase();
    return (data ?? []).filter((e) => !n || [e.fullName, e.employeeNo, e.position, e.department].some((v) => v.toLowerCase().includes(n)));
  }, [data, q]);
  return (
    <>
      <PageHeader eyebrow="People" title="Employee Roster" description="Building employees, positions and monthly basic salary." />
      <Card>
        <div className="flex justify-end border-b border-ink-100 p-4"><SearchInput value={q} onChange={setQ} placeholder="Name, number, position" /></div>
        <DataTable rows={data ? rows : null} loading={loading} error={error} onRetry={reload} rowKey={(e) => e.id} columns={[
          { key: "n", header: "Employee", cell: (e) => <><b className="text-ink-900">{e.fullName}</b><span className="block text-[12px] text-ink-500">{e.employeeNo}</span></> },
          { key: "p", header: "Position", cell: (e) => <>{e.position}<span className="block text-[12px] text-ink-500">{e.department}</span></> },
          { key: "h", header: "Hired", cell: (e) => dateLabel(e.dateHired) },
          { key: "c", header: "Contact", cell: (e) => e.contactNo },
          { key: "s", header: "Monthly basic", align: "right", cell: (e) => peso(e.monthlySalary) },
          { key: "st", header: "Status", cell: (e) => <StatusBadge status={e.status} /> },
        ]} />
      </Card>
    </>
  );
}

// ------------------------------------------------------------------ Attendance (manager) & approvals
function Approvals<R extends LeaveRequest | OvertimeRequest>({ rows, describe, onDecide }: { rows: R[]; describe: (r: R) => string; onDecide: (r: R, s: "Approved" | "Rejected") => Promise<void> }) {
  const [confirm, setConfirm] = useState<{ row: R; status: "Approved" | "Rejected" } | null>(null);
  const { busy, act } = useAction();
  return (
    <>
      <DataTable rows={rows} rowKey={(r) => r.id} empty={{ icon: "checkbox-circle-line", title: "No requests" }} columns={[
        { key: "e", header: "Employee", cell: (r) => <b className="text-ink-900">{r.employeeName}</b> },
        { key: "d", header: "Request", cell: (r) => <>{describe(r)}<span className="block text-[12px] text-ink-500">{r.reason}</span></> },
        { key: "s", header: "Status", cell: (r) => <StatusBadge status={r.status} /> },
        { key: "a", header: "", cell: (r) => r.status === "Pending" && (
          <div className="flex justify-end gap-2">
            <Button size="sm" variant="secondary" onClick={() => setConfirm({ row: r, status: "Rejected" })}>Reject</Button>
            <Button size="sm" onClick={() => setConfirm({ row: r, status: "Approved" })}>Approve</Button>
          </div>) },
      ]} />
      <ConfirmDialog open={!!confirm} title={confirm ? `${confirm.status === "Approved" ? "Approve" : "Reject"} request?` : ""} busy={busy} tone={confirm?.status === "Rejected" ? "danger" : "primary"}
        confirmLabel={confirm?.status === "Approved" ? "Approve" : "Reject"} message={confirm && `${confirm.row.employeeName} · ${describe(confirm.row)}`}
        onClose={() => setConfirm(null)} onConfirm={async () => { if (confirm) await act(() => onDecide(confirm.row, confirm.status)); setConfirm(null); }} />
    </>
  );
}

export function AttendancePage() {
  const [date, setDate] = useState(todayIso());
  const [tab, setTab] = useState<"attendance" | "overtime">("attendance");
  const att = useAsync(() => api.hr.attendance(date), [date]);
  const ot = useAsync(() => api.hr.overtime(), []);
  const toast = useToast();
  return (
    <>
      <PageHeader eyebrow="People" title="Attendance & Overtime" description="Daily time records and overtime approvals. Staff enter attendance; overtime needs your approval before payroll."
        actions={<input type="date" className="input w-44" aria-label="Date" max={todayIso()} value={date} onChange={(e) => setDate(e.target.value)} />} />
      <div className="mb-4"><Tabs value={tab} onChange={setTab} tabs={[{ key: "attendance", label: `Attendance · ${dateLabel(date)}`, count: att.data?.length }, { key: "overtime", label: "Overtime requests", count: ot.data?.filter((o) => o.status === "Pending").length }]} /></div>
      <Card>
        {tab === "attendance" ? (
          <DataTable rows={att.data} loading={att.loading} error={att.error} onRetry={att.reload} rowKey={(a) => a.id} empty={{ icon: "time-line", title: "No attendance recorded for this day" }} columns={[
            { key: "e", header: "Employee", cell: (a) => <b className="text-ink-900">{a.employeeName}</b> },
            { key: "i", header: "Time in", cell: (a) => a.timeIn ?? "—" },
            { key: "o", header: "Time out", cell: (a) => a.timeOut ?? "—" },
            { key: "l", header: "Late", align: "right", cell: (a) => (a.lateMinutes ? `${a.lateMinutes} min` : "—") },
            { key: "s", header: "Status", cell: (a) => <StatusBadge status={a.status} /> },
          ]} />
        ) : (
          <Approvals rows={ot.data ?? []} describe={(o) => `${o.hours} hour(s) on ${dateLabel(o.date)}`}
            onDecide={async (o, s) => { try { await api.hr.decideOvertime(o.id, s); toast({ tone: "success", title: `Overtime ${s.toLowerCase()}` }); ot.reload(); } catch (err) { toast({ tone: "error", title: "Not saved", message: (err as Error).message }); } }} />
        )}
      </Card>
    </>
  );
}

export function LeavePage() {
  const { data, error, reload } = useAsync(() => api.hr.leave(), []);
  const toast = useToast();
  return (
    <>
      <PageHeader eyebrow="People" title="Leave Management" description="Leave requests are always filed as Pending; only an approver can approve or reject them." />
      {error ? <ErrorState message={error.message} onRetry={reload} /> : !data ? <Card><Skeleton rows={5} /></Card> : (
        <Card>
          <Approvals rows={data} describe={(l) => `${l.leaveType} · ${dateLabel(l.from)}${l.to !== l.from ? ` – ${dateLabel(l.to)}` : ""} · ${l.days} day(s)`}
            onDecide={async (l, s) => { try { await api.hr.decideLeave(l.id, s); toast({ tone: "success", title: `Leave ${s.toLowerCase()}` }); reload(); } catch (err) { toast({ tone: "error", title: "Not saved", message: (err as Error).message }); } }} />
        </Card>
      )}
    </>
  );
}

// ------------------------------------------------------------------ Payroll engine + PH calculator
export function PayrollPage() {
  const [period, setPeriod] = useState(addMonths(currentMonth(), -1));
  const [tab, setTab] = useState<"runs" | "calculator">("runs");
  const { data, error, loading, reload } = useAsync(() => api.hr.payroll(period), [period]);
  const total = (data ?? []).reduce((s, p) => ({ gross: s.gross + toCents(p.basic) + toCents(p.overtime) + toCents(p.allowances), net: s.net + toCents(p.netPay) }), { gross: 0, net: 0 });
  return (
    <>
      <PageHeader eyebrow="Payroll" title="Payroll Engine" description="Payroll per period with Philippine statutory deductions. Official figures are computed by the server from the saved Payroll Rules." />
      <div className="mb-4 flex flex-wrap items-center gap-3"><Tabs value={tab} onChange={setTab} tabs={[{ key: "runs", label: "Payroll runs" }, { key: "calculator", label: "PH payroll calculator" }]} />{tab === "runs" && <MonthPicker value={period} onChange={setPeriod} max={currentMonth()} />}</div>
      {tab === "calculator" ? <PayrollCalculator /> : (
        <>
          <div className="mb-6 grid gap-4 sm:grid-cols-3">
            <StatCard icon="team-line" label="Employees in run" value={data?.length ?? "—"} />
            <StatCard icon="money-dollar-box-line" label="Gross pay" value={peso(fromCents(total.gross))} />
            <StatCard tone="hero" icon="bank-line" label="Net pay to release" value={peso(fromCents(total.net))} />
          </div>
          <Card>
            <CardHeader title={`Payroll · ${monthLabel(period)}`} />
            <DataTable rows={data} loading={loading} error={error} onRetry={reload} rowKey={(p) => p.id} columns={[
              { key: "e", header: "Employee", cell: (p) => <b className="text-ink-900">{p.employeeName}</b> },
              { key: "b", header: "Basic", align: "right", cell: (p) => peso(p.basic) },
              { key: "o", header: "Overtime", align: "right", cell: (p) => peso(p.overtime) },
              { key: "a", header: "Allowances", align: "right", cell: (p) => peso(p.allowances) },
              { key: "d", header: "Deductions", align: "right", cell: (p) => peso(p.deductions) },
              { key: "n", header: "Net pay", align: "right", cell: (p) => <b>{peso(p.netPay)}</b> },
              { key: "s", header: "Status", cell: (p) => <StatusBadge status={p.status} /> },
            ]} />
          </Card>
        </>
      )}
    </>
  );
}

function PayrollCalculator() {
  const [v, setV] = useState({ basicMonthly: "25000", overtimePay: "0", taxableAllowances: "0", nonTaxableAllowances: "1500", lateUndertimeDeduction: "0", loanDeductions: "0" });
  const num = (s: string) => Math.max(Number(s.replace(/,/g, "")) || 0, 0);
  const p = previewPayroll({ basicMonthly: num(v.basicMonthly), overtimePay: num(v.overtimePay), taxableAllowances: num(v.taxableAllowances), nonTaxableAllowances: num(v.nonTaxableAllowances), lateUndertimeDeduction: num(v.lateUndertimeDeduction), loanDeductions: num(v.loanDeductions) });
  const fields: [keyof typeof v, string][] = [["basicMonthly", "Monthly basic salary"], ["overtimePay", "Overtime pay"], ["taxableAllowances", "Taxable allowances"], ["nonTaxableAllowances", "Non-taxable allowances (de minimis)"], ["lateUndertimeDeduction", "Late / undertime deduction"], ["loanDeductions", "Loan deductions"]];
  const row = (label: string, value: string, strong = false, minus = false) => (
    <div className={`flex justify-between px-4 py-2.5 ${strong ? "font-semibold text-ink-900" : "text-ink-600"}`}><span>{label}</span><span className="tabular">{minus ? "− " : ""}{peso(value)}</span></div>
  );
  return (
    <div className="grid gap-6 xl:grid-cols-[380px_1fr]">
      <Card className="self-start">
        <CardHeader title="Employee pay (monthly)" />
        <div className="grid gap-4 p-5">
          {fields.map(([k, label]) => <Field key={k} label={`${label} (₱)`}>{(id) => <input id={id} inputMode="decimal" className="input tabular" value={v[k]} onChange={(e) => setV({ ...v, [k]: e.target.value })} />}</Field>)}
        </div>
      </Card>
      <div className="space-y-6">
        <div className="grid gap-4 sm:grid-cols-3">
          <StatCard label="Gross pay" icon="money-dollar-box-line" value={peso(p.gross)} />
          <StatCard tone="copper" label="Total deductions" icon="subtract-line" value={peso(p.totalDeductions)} />
          <StatCard tone="hero" label="Net pay" icon="bank-line" value={peso(p.netPay)} />
        </div>
        <Card>
          <CardHeader title="Breakdown" subtitle={`SSS monthly salary credit: ${peso(p.sss.msc)}`} />
          <div className="grid divide-y divide-ink-100 lg:grid-cols-2 lg:divide-x lg:divide-y-0">
            <div className="divide-y divide-ink-100">
              <p className="px-4 py-2 text-[11.5px] font-semibold tracking-wide text-ink-500 uppercase">Employee share (deducted)</p>
              {row("SSS", p.sss.employee, false, true)}{row("PhilHealth", p.philhealth.employee, false, true)}{row("Pag-IBIG", p.pagibig.employee, false, true)}
              {row("Taxable income", p.taxableIncome, true)}{row("BIR withholding tax", p.withholdingTax, false, true)}
            </div>
            <div className="divide-y divide-ink-100">
              <p className="px-4 py-2 text-[11.5px] font-semibold tracking-wide text-ink-500 uppercase">Employer share (not deducted)</p>
              {row("SSS (incl. EC)", p.sss.employer)}{row("PhilHealth", p.philhealth.employer)}{row("Pag-IBIG", p.pagibig.employer)}
            </div>
          </div>
        </Card>
        <Notice tone="warn" title="Preview only">This calculator helps plan and check payroll. The official payroll is computed by the server from the saved Payroll Rules. Rates follow the 2025 SSS, PhilHealth, Pag-IBIG and BIR (TRAIN) schedules; confirm against the latest circulars.</Notice>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ Philippine tax rules (reference)
export function TaxRulesPage() {
  const s = RULES.sss, ph = RULES.philhealth, hd = RULES.pagibig;
  const bir = [...RULES.bir].reverse();
  return (
    <>
      <PageHeader eyebrow="Payroll" title="Philippine Tax Rules" description="The contribution and withholding tables the payroll preview uses. The official rules are saved under Payroll Rules on the server." />
      <div className="grid gap-6 xl:grid-cols-2">
        <Card><CardHeader title="SSS" subtitle="Social Security System" />
          <div className="space-y-2 p-5 text-[13.5px]">
            <p>Contribution rate <b>{s.rate * 100}%</b> of the Monthly Salary Credit: employee <b>{s.employeeRate * 100}%</b>, employer <b>{s.employerRate * 100}%</b> (+ EC ₱{s.ecLow}/₱{s.ecHigh}).</p>
            <p>MSC from <b>{peso(s.minMsc)}</b> to <b>{peso(s.maxMsc)}</b> in ₱{s.step} steps. Example: ₱25,000 salary → MSC {peso(sssMsc(25000))}.</p>
          </div></Card>
        <Card><CardHeader title="PhilHealth" subtitle="National Health Insurance" />
          <div className="space-y-2 p-5 text-[13.5px]"><p>Premium <b>{ph.rate * 100}%</b> of monthly basic salary, shared equally by employee and employer.</p><p>Salary floor <b>{peso(ph.floor)}</b>, ceiling <b>{peso(ph.ceiling)}</b>.</p></div></Card>
        <Card><CardHeader title="Pag-IBIG (HDMF)" subtitle="Home Development Mutual Fund" />
          <div className="space-y-2 p-5 text-[13.5px]"><p>Employee <b>{hd.employeeRate * 100}%</b> ({hd.employeeRateLow * 100}% if salary ≤ {peso(hd.lowThreshold)}), employer <b>{hd.employerRate * 100}%</b>.</p><p>Maximum fund salary <b>{peso(hd.maxBase)}</b> (₱200 each per month).</p></div></Card>
        <Card><CardHeader title="BIR withholding tax (monthly)" subtitle="TRAIN law table, 2023 onward" />
          <DataTable rows={[{ over: 0, base: 0, rate: 0 }, ...bir.map(([over, base, rate]) => ({ over, base, rate }))]} rowKey={(r) => r.over} columns={[
            { key: "o", header: "Taxable income over", cell: (r) => peso(r.over) },
            { key: "b", header: "Base tax", align: "right", cell: (r) => peso(r.base) },
            { key: "r", header: "Plus % of excess", align: "right", cell: (r) => (r.rate ? `${r.rate * 100}%` : "Exempt") },
          ]} /></Card>
      </div>
    </>
  );
}

// ------------------------------------------------------------------ Staff: daily attendance entry + dashboard
export function AttendanceEntryPage() {
  const date = todayIso();
  const employees = useAsync(() => api.hr.employees(), []);
  const att = useAsync(() => api.hr.attendance(date), [date]);
  const [form, setForm] = useState({ employeeId: 0, timeIn: "08:00", timeOut: "", status: "Present" as AttendanceStatus });
  const { busy, act } = useAction();
  const toast = useToast();
  const recorded = new Set((att.data ?? []).map((a) => a.employeeId));
  const active = (employees.data ?? []).filter((e) => e.status !== "Resigned");
  async function save() {
    if (!form.employeeId) return toast({ tone: "error", title: "Choose an employee" });
    try {
      const a = await act(() => api.hr.saveAttendance({ employeeId: form.employeeId, date, timeIn: form.status === "Absent" || form.status === "On Leave" ? null : form.timeIn, timeOut: form.timeOut || null, status: form.status }));
      toast({ tone: "success", title: `Saved · ${a.employeeName}`, message: a.lateMinutes ? `${a.lateMinutes} minutes late` : a.status });
      att.reload();
      setForm({ ...form, employeeId: active.find((e) => !recorded.has(e.id) && e.id !== a.employeeId)?.id ?? 0 });
    } catch (err) {
      toast({ tone: "error", title: "Not saved", message: (err as Error).message });
    }
  }
  return (
    <>
      <PageHeader eyebrow="Records" title="Daily Attendance Entry" description={`Record time in and out for ${dateLabel(date)}. Leave and overtime requests are handled by HR.`} />
      <div className="grid gap-6 xl:grid-cols-[380px_1fr]">
        <Card className="self-start">
          <CardHeader title="Time record" subtitle={`${recorded.size} of ${active.length} recorded today`} />
          <form className="grid gap-4 p-5" onSubmit={(e) => { e.preventDefault(); void save(); }}>
            <Field label="Employee">{(id) => <select id={id} className="input" value={form.employeeId} onChange={(e) => setForm({ ...form, employeeId: Number(e.target.value) })}><option value={0}>Choose…</option>{active.map((e) => <option key={e.id} value={e.id}>{e.fullName}{recorded.has(e.id) ? " ✓" : ""}</option>)}</select>}</Field>
            <Field label="Status">{(id) => <select id={id} className="input" value={form.status} onChange={(e) => setForm({ ...form, status: e.target.value as AttendanceStatus })}>{["Present", "Late", "Half Day", "Absent", "On Leave"].map((s) => <option key={s}>{s}</option>)}</select>}</Field>
            <div className="grid grid-cols-2 gap-4">
              <Field label="Time in">{(id) => <input id={id} type="time" className="input" disabled={form.status === "Absent" || form.status === "On Leave"} value={form.timeIn} onChange={(e) => setForm({ ...form, timeIn: e.target.value })} />}</Field>
              <Field label="Time out">{(id) => <input id={id} type="time" className="input" value={form.timeOut} onChange={(e) => setForm({ ...form, timeOut: e.target.value })} />}</Field>
            </div>
            <Button type="submit" icon="fingerprint-line" loading={busy}>Save time record</Button>
          </form>
        </Card>
        <Card>
          <CardHeader title={`Today · ${dateLabel(date)}`} />
          <DataTable rows={att.data} loading={att.loading} error={att.error} onRetry={att.reload} rowKey={(a) => a.id} empty={{ icon: "time-line", title: "Nothing recorded yet today" }} columns={[
            { key: "e", header: "Employee", cell: (a) => <b className="text-ink-900">{a.employeeName}</b> },
            { key: "i", header: "In", cell: (a) => a.timeIn ?? "—" },
            { key: "o", header: "Out", cell: (a) => a.timeOut ?? "—" },
            { key: "s", header: "Status", cell: (a) => <StatusBadge status={a.status} /> },
          ]} />
        </Card>
      </div>
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
