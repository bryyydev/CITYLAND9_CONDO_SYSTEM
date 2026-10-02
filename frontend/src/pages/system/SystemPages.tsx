// Superadmin workspace + shared read-only Audit Logs.
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useAuth, useUser } from "../../auth/AuthContext";
import { DataTable } from "../../components/base/DataTable";
import { useToast } from "../../components/base/overlays";
import { Badge, Button, Card, CardHeader, ErrorState, Field, Icon, Notice, PageHeader, SearchInput, Skeleton, StatCard } from "../../components/base/ui";
import { legacyUrl } from "../../components/feature/ModuleRoute";
import { MODULES, MODULE_CATALOG } from "../../config/modules";
import { ROLES, ROLE_ORDER } from "../../config/roles";
import { useAction, useAsync } from "../../hooks/useAsync";
import { dateTimeLabel, monthLabel } from "../../lib/format";
import { isAmount } from "../../lib/money";
import { IS_MOCK, api } from "../../services/api";
import type { RatesAndRules, RatesAndRulesUpdate } from "../../services/types";

// ------------------------------------------------------------------ Superadmin dashboard
export function SystemDashboard() {
  const user = useUser();
  const { data, error, reload } = useAsync(() => api.dashboards.system(), []);
  const audit = useAsync(() => api.admin.auditLogs(""), []);
  if (error) return <ErrorState message={error.message} onRetry={reload} />;
  if (!data) return <Card><Skeleton rows={6} /></Card>;
  const staff = ROLE_ORDER.filter((r) => r !== "resident").reduce((s, r) => s + data.users[r], 0);
  return (
    <>
      <PageHeader eyebrow={`Good day, ${user.displayName?.split(" ")[0] ?? user.username}`} title="System administration" description="Accounts, access, rates and the health of the local server." />
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard tone="hero" icon="shield-user-line" label="Staff accounts" value={staff} hint={ROLE_ORDER.filter((r) => r !== "resident").map((r) => `${data.users[r]} ${ROLES[r].label}`).join(" · ")} />
        <StatCard tone={data.unlinkedResidentAccounts ? "copper" : "default"} icon="user-heart-line" label="Resident accounts" value={data.residentAccounts} hint={`${data.unlinkedResidentAccounts} not linked to an owner/tenant`} />
        <StatCard icon="file-shield-2-line" label="Audit entries today" value={data.auditToday} />
        <StatCard icon="database-2-line" label="Last database backup" value={data.lastBackup ? dateTimeLabel(data.lastBackup).split(",").slice(0, 2).join(",") : "—"} hint={`Schema ${data.schemaRevision}`} />
      </div>
      <div className="mt-6 grid gap-6 xl:grid-cols-[1fr_1.3fr]">
        <Card>
          <CardHeader title="Administration" />
          <div className="grid gap-2 p-3 sm:grid-cols-2 xl:grid-cols-1 2xl:grid-cols-2">
            {(["users", "residentAccounts", "ratesRules", "auditLogs", "systemSettings", "modules"] as const).map((id) => {
              const m = MODULES[id];
              return (
                <Link key={id} to={`/superadmin${m.path}`} className="group flex items-center gap-3 rounded-xl border border-ink-200 p-3 transition hover:border-brand-300 hover:bg-brand-50">
                  <span className="grid size-10 place-items-center rounded-lg bg-brand-50 text-lg text-brand-600 group-hover:bg-white"><Icon name={m.icon} /></span>
                  <span className="min-w-0"><span className="block font-semibold text-ink-900">{m.label}</span><span className="line-clamp-1 text-[12px] text-ink-500">{m.description}</span></span>
                </Link>
              );
            })}
          </div>
        </Card>
        <Card>
          <CardHeader title="Recent activity" actions={<Link className="text-[13px] font-semibold text-brand-600 hover:underline" to="/superadmin/audit-logs">Audit logs</Link>} />
          {!audit.data ? <Skeleton rows={5} /> : (
            <ul className="divide-y divide-ink-100">{audit.data.slice(0, 7).map((a) => (
              <li key={a.id} className="flex gap-3 px-5 py-2.5"><Icon name="history-line" className="mt-0.5 text-ink-400" /><div className="min-w-0"><p className="truncate">{a.action}</p><p className="text-[12px] text-ink-500">{a.username} · {dateTimeLabel(a.at)}</p></div></li>
            ))}</ul>
          )}
        </Card>
      </div>
    </>
  );
}

// ------------------------------------------------------------------ Resident accounts
export function ResidentAccountsPage() {
  const { data, error, loading, reload } = useAsync(() => api.admin.residentAccounts(), []);
  return (
    <>
      <PageHeader eyebrow="Administration" title="Resident Accounts" description="Portal logins for owners and tenants. An account linked to an owner/tenant record loses access automatically when that person moves out." />
      {data?.some((a) => !a.linked) && <div className="mb-4"><Notice tone="warn" title="Some accounts are not linked">Unlinked accounts keep access to their unit even after a move-out, and can't edit their own contact details. Link them to the owner or tenant record.</Notice></div>}
      <Card>
        <DataTable rows={data} loading={loading} error={error} onRetry={reload} rowKey={(a) => a.id} columns={[
          { key: "u", header: "Username", cell: (a) => <b className="text-ink-900">{a.username}</b> },
          { key: "n", header: "Resident", cell: (a) => <>{a.displayName}<span className="block text-[12px] text-ink-500">{a.personType}</span></> },
          { key: "x", header: "Unit", cell: (a) => a.unitNo },
          { key: "l", header: "Linked record", cell: (a) => <Badge tone={a.linked ? "ok" : "warn"}>{a.linked ? "Linked" : "Not linked"}</Badge> },
          { key: "s", header: "Access", cell: (a) => <Badge tone={a.active ? "ok" : "neutral"}>{a.active ? "Active" : "Ended"}</Badge> },
        ]} />
      </Card>
    </>
  );
}

// ------------------------------------------------------------------ Rates & rules
export function RatesRulesPage() {
  const { data, error, reload } = useAsync(() => api.admin.rates(), []);
  if (error) return <ErrorState message={error.message} onRetry={reload} />;
  if (!data) return <Card><Skeleton rows={10} /></Card>;
  return <RatesForm key={JSON.stringify(data)} saved={data} onSaved={reload} />;
}

function RatesForm({ saved, onSaved }: { saved: RatesAndRules; onSaved: () => void }) {
  const { smtpPasswordSet, ...initial } = saved;
  const [form, setForm] = useState<RatesAndRulesUpdate>({ ...initial, smtpPassword: "" });
  const { busy, act } = useAction();
  const toast = useToast();
  const set = <K extends keyof RatesAndRulesUpdate>(k: K, v: RatesAndRulesUpdate[K]) => setForm({ ...form, [k]: v });
  const money = (label: string, value: string, onChange: (v: string) => void) => (
    <Field label={label} error={!isAmount(value) && value !== "0.00" && "Enter an amount."}>{(id) => <input id={id} inputMode="decimal" className="input tabular" value={value} onChange={(e) => onChange(e.target.value)} />}</Field>
  );
  async function save() {
    try {
      await act(() => api.admin.saveRates(form));
      toast({ tone: "success", title: "Rates & Rules saved", message: "New rates apply to bills generated from now on. Issued bills don't change." });
      onSaved();
    } catch (err) {
      toast({ tone: "error", title: "Not saved", message: (err as Error).message });
    }
  }
  return (
    <>
      <PageHeader eyebrow="Administration" title="Rates & Rules" description="Dues rates, water, penalties and payment and email settings. Changes apply to bills generated from now on; issued bills stay frozen."
        actions={<Button icon="save-3-line" loading={busy} onClick={save}>Save changes</Button>} />
      <div className="grid gap-6 xl:grid-cols-2">
        <Card><CardHeader title="Condo dues (₱ per m²)" />
          <div className="grid gap-4 p-5 sm:grid-cols-2">
            {money("Studio", form.ratesPerSqm.studio, (v) => set("ratesPerSqm", { ...form.ratesPerSqm, studio: v }))}
            {money("1 bedroom", form.ratesPerSqm.oneBed, (v) => set("ratesPerSqm", { ...form.ratesPerSqm, oneBed: v }))}
            {money("2 bedroom", form.ratesPerSqm.twoBed, (v) => set("ratesPerSqm", { ...form.ratesPerSqm, twoBed: v }))}
            {money("3 bedroom", form.ratesPerSqm.threeBed, (v) => set("ratesPerSqm", { ...form.ratesPerSqm, threeBed: v }))}
            {money("Parking (₱/m²)", form.parkingRatePerSqm, (v) => set("parkingRatePerSqm", v))}
            {money("Storage (₱/m²)", form.storageRatePerSqm, (v) => set("storageRatePerSqm", v))}
            <Field label="Storage counts in the total from" className="sm:col-span-2" hint={`Currently ${monthLabel(form.storageInTotalFrom)}`}>{(id) => <input id={id} type="month" className="input" value={form.storageInTotalFrom} onChange={(e) => set("storageInTotalFrom", e.target.value)} />}</Field>
          </div></Card>
        <Card><CardHeader title="Water & penalties" />
          <div className="grid gap-4 p-5 sm:grid-cols-2">
            {money("Water rate (₱/m³)", form.waterRate, (v) => set("waterRate", v))}
            <label className="flex items-center gap-3 self-end rounded-lg border border-ink-200 px-3 py-2.5"><input type="checkbox" className="size-4 accent-brand-600" checked={form.waterAutoCompute} onChange={(e) => set("waterAutoCompute", e.target.checked)} />Compute water on bills automatically</label>
            <Field label="Penalty rate (%)">{(id) => <input id={id} inputMode="decimal" className="input tabular" value={form.penaltyRate} onChange={(e) => set("penaltyRate", e.target.value)} />}</Field>
            <fieldset className="sm:col-span-2"><legend className="mb-2 text-[12.5px] font-semibold text-ink-700">Penalty applies to unpaid</legend>
              <div className="flex flex-wrap gap-2">{(["condo", "parking", "storage", "water"] as const).map((k) => (
                <label key={k} className="flex items-center gap-2 rounded-lg border border-ink-200 px-3 py-2 capitalize"><input type="checkbox" className="accent-brand-600" checked={form.penaltyIncludes[k]} onChange={(e) => set("penaltyIncludes", { ...form.penaltyIncludes, [k]: e.target.checked })} />{k === "condo" ? "Condo dues" : k}</label>
              ))}</div></fieldset>
            <div className="sm:col-span-2"><Notice tone="warn" title="Not in effect yet (known issue D8)">Bills are always due on the <b>8th</b> of the month and penalties follow that due date. The "due day" and "penalty day" settings of the classic screen are ignored by the server, so they aren't offered here.</Notice></div>
          </div></Card>
        <Card><CardHeader title="Corporation & payment instructions" />
          <div className="grid gap-4 p-5">
            <Field label="Corporation name">{(id) => <input id={id} className="input" value={form.corporationName} onChange={(e) => set("corporationName", e.target.value)} />}</Field>
            <Field label="Address">{(id) => <input id={id} className="input" value={form.address} onChange={(e) => set("address", e.target.value)} />}</Field>
            <Field label="Online payment link (optional)" hint="Shown as a QR code on printed SOAs.">{(id) => <input id={id} className="input" placeholder="https://" value={form.onlinePaymentUrl} onChange={(e) => set("onlinePaymentUrl", e.target.value)} />}</Field>
            <Field label="Payment instructions">{(id) => <textarea id={id} rows={3} className="input" value={form.onlinePaymentInstructions} onChange={(e) => set("onlinePaymentInstructions", e.target.value)} />}</Field>
          </div></Card>
        <Card><CardHeader title="SOA email (SMTP)" subtitle="Optional. Used only to email statements." />
          <div className="grid gap-4 p-5 sm:grid-cols-2">
            <Field label="SMTP host">{(id) => <input id={id} className="input" value={form.smtpHost} onChange={(e) => set("smtpHost", e.target.value)} />}</Field>
            <Field label="Port">{(id) => <input id={id} inputMode="numeric" className="input" value={form.smtpPort} onChange={(e) => set("smtpPort", e.target.value)} />}</Field>
            <Field label="Sender email">{(id) => <input id={id} type="email" className="input" value={form.smtpSender} onChange={(e) => set("smtpSender", e.target.value)} />}</Field>
            <Field label="Username">{(id) => <input id={id} className="input" autoComplete="off" value={form.smtpUsername} onChange={(e) => set("smtpUsername", e.target.value)} />}</Field>
            <Field label="Password" className="sm:col-span-2" hint={smtpPasswordSet ? "A password is saved. It is never shown; type a new one only to replace it." : "No password saved."}>
              {(id) => <input id={id} type="password" className="input" autoComplete="new-password" placeholder={smtpPasswordSet ? "•••••••• (saved)" : ""} value={form.smtpPassword} onChange={(e) => set("smtpPassword", e.target.value)} />}
            </Field>
          </div></Card>
      </div>
    </>
  );
}

// ------------------------------------------------------------------ Audit logs (Superadmin, Accounting)
export function AuditLogsPage() {
  const [q, setQ] = useState("");
  const [applied, setApplied] = useState("");
  const { data, error, loading, reload } = useAsync(() => api.admin.auditLogs(applied), [applied]);
  const toast = useToast();
  const csv = useMemo(() => (data ?? []).map((a) => [dateTimeLabel(a.at), a.username, a.action].map((v) => `"${v.replace(/"/g, '""')}"`).join(",")).join("\n"), [data]);
  function exportCsv() {
    const blob = new Blob([`"When (Manila time)","User","Action"\n${csv}\n`], { type: "text/csv" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `cityland9-audit-log${applied ? `-${applied}` : ""}.csv`;
    a.click();
    URL.revokeObjectURL(a.href);
    toast({ tone: "success", title: "Audit log exported", message: `${data?.length ?? 0} entries` });
  }
  return (
    <>
      <PageHeader eyebrow="Controls" title="Audit Logs" description="A permanent, read-only record of important actions: sign-ins, bills, payments and receipts, rate changes, approvals. Entries can't be edited or deleted."
        actions={<Button variant="secondary" icon="download-2-line" onClick={exportCsv} disabled={!data?.length}>Export CSV</Button>} />
      <Card>
        <form className="flex flex-wrap items-center gap-3 border-b border-ink-100 p-4" onSubmit={(e) => { e.preventDefault(); setApplied(q); }}>
          <Icon name="lock-line" className="text-ink-400" /><span className="text-[12.5px] text-ink-500">Read-only · times shown in Philippine time</span>
          <div className="flex-1" /><SearchInput value={q} onChange={setQ} placeholder="Search user or action" /><Button type="submit" variant="secondary">Search</Button>
        </form>
        <DataTable rows={data} loading={loading} error={error} onRetry={reload} rowKey={(a) => a.id} empty={{ icon: "file-shield-2-line", title: "No matching entries" }} columns={[
          { key: "t", header: "When", cell: (a) => <span className="whitespace-nowrap tabular">{dateTimeLabel(a.at)}</span> },
          { key: "u", header: "User", cell: (a) => <b className="text-ink-900">{a.username}</b> },
          { key: "a", header: "Action", cell: (a) => a.action },
        ]} />
      </Card>
    </>
  );
}

// ------------------------------------------------------------------ System settings (backup / export / import)
export function SystemSettingsPage() {
  const toast = useToast();
  const info = (title: string) => toast({ tone: "info", title, message: "In the live system this runs on the server." });
  return (
    <>
      <PageHeader eyebrow="Administration" title="Settings" description="Database backup, export and import for the local server. All data stays on the office PC." />
      <div className="grid gap-6 lg:grid-cols-3">
        <Card><CardHeader title="Backup" />
          <div className="space-y-3 p-5 text-[13.5px] text-ink-600">
            <p>Full MySQL backups are written to <code className="rounded bg-ink-100 px-1">database/backups/</code> on the server PC, automatically before every import and every schema migration.</p>
            <p>To make one now, run <code className="rounded bg-ink-100 px-1">python database/local_mysql.py backup</code> on the server.</p>
          </div></Card>
        <Card><CardHeader title="Export to Excel" />
          <div className="space-y-4 p-5 text-[13.5px] text-ink-600">
            <p>Download every table as an Excel workbook, for safekeeping or reporting.</p>
            {IS_MOCK ? <Button variant="secondary" icon="file-excel-2-line" onClick={() => info("Excel export")}>Export database</Button>
              : <a href={legacyUrl("/database/export.xlsx")} className="inline-flex h-10 items-center gap-2 rounded-lg border border-ink-200 bg-white px-4 font-semibold shadow-sm hover:bg-ink-50"><Icon name="file-excel-2-line" />Export database</a>}
          </div></Card>
        <Card><CardHeader title="Import from Excel" />
          <div className="space-y-4 p-5 text-[13.5px] text-ink-600">
            <Notice tone="warn">Importing replaces data. A backup is taken first; if it can't be taken, the import is refused.</Notice>
            {IS_MOCK ? <Button variant="secondary" icon="upload-2-line" onClick={() => info("Excel import")}>Open import (classic screen)</Button>
              : <a href={legacyUrl("/settings")} className="inline-flex h-10 items-center gap-2 rounded-lg border border-ink-200 bg-white px-4 font-semibold shadow-sm hover:bg-ink-50"><Icon name="upload-2-line" />Open import (classic screen)</a>}
          </div></Card>
      </div>
    </>
  );
}

// ------------------------------------------------------------------ All modules launcher (Superadmin)
export function ModulesLauncher() {
  const { can } = useAuth();
  return (
    <>
      <PageHeader eyebrow="Operations" title="All Property Modules" description="Every module in the system. As Superadmin you can open all of them." />
      <div className="space-y-8">
        {MODULE_CATALOG.map(({ group, ids }) => (
          <section key={group}>
            <h2 className="mb-3 text-[12px] font-semibold tracking-[0.12em] text-ink-500 uppercase">{group}</h2>
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
              {ids.map((id) => MODULES[id]).filter((m) => can(m.permission)).map((m) => (
                <Link key={m.id} to={`/superadmin${m.path}`} className="group flex items-start gap-3 rounded-xl border border-ink-200 bg-white p-4 shadow-card transition hover:-translate-y-0.5 hover:border-brand-300">
                  <span className="grid size-11 shrink-0 place-items-center rounded-xl bg-brand-50 text-xl text-brand-600 group-hover:bg-brand-600 group-hover:text-white"><Icon name={m.icon} /></span>
                  <span><span className="block font-semibold text-ink-900">{m.label}</span><span className="text-[12.5px] text-ink-500">{m.description}</span>
                    {!IS_MOCK && !m.live && <span className="mt-1 block text-[11px] font-semibold tracking-wide text-copper-600">CLASSIC SCREEN</span>}</span>
                </Link>
              ))}
            </div>
          </section>
        ))}
      </div>
    </>
  );
}
