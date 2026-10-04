// Superadmin workspace: dashboard and the module launcher.
import { Link } from "react-router-dom";
import { useAuth, useUser } from "../../auth/AuthContext";
import { Card, CardHeader, ErrorState, Icon, PageHeader, Skeleton, StatCard } from "../../components/base/ui";
import { MODULES, MODULE_CATALOG } from "../../config/modules";
import { ROLES, ROLE_ORDER } from "../../config/roles";
import { useAsync } from "../../hooks/useAsync";
import { dateTimeLabel } from "../../lib/format";
import { IS_MOCK, api } from "../../services/api";

// ------------------------------------------------------------------ Superadmin dashboard
export function SystemDashboard() {
  const user = useUser();
  const { data, error, reload } = useAsync(() => api.dashboards.system(), []);
  const audit = useAsync(() => api.admin.auditLogs({ q: "", user: "", from: "", to: "", page: 1, perPage: 7 }).then((p) => p.entries), []);
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
            <ul className="divide-y divide-ink-100">{audit.data.map((a) => (
              <li key={a.id} className="flex gap-3 px-5 py-2.5"><Icon name="history-line" className="mt-0.5 text-ink-400" /><div className="min-w-0"><p className="truncate">{a.action}</p><p className="text-[12px] text-ink-500">{a.username} · {dateTimeLabel(a.at)}</p></div></li>
            ))}</ul>
          )}
        </Card>
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
