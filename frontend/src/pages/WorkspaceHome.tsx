// Live-mode home for staff workspaces whose dashboard API isn't built yet: the role's modules,
// each opening its React page or, until rebuilt, its classic screen.
import { Link } from "react-router-dom";
import { useAuth, useUser } from "../auth/AuthContext";
import { Icon, PageHeader } from "../components/base/ui";
import { legacyUrl } from "../components/feature/ModuleRoute";
import { MODULES } from "../config/modules";
import { ROLES } from "../config/roles";

export default function WorkspaceHome() {
  const user = useUser();
  const { can } = useAuth();
  const cfg = ROLES[user.role];
  const groups = cfg.nav
    .map((g) => ({ group: g.group, items: g.items.map((id) => MODULES[id]).filter((m) => m.path !== "" && can(m.permission)) }))
    .filter((g) => g.items.length);
  return (
    <>
      <PageHeader eyebrow={cfg.tagline} title={cfg.workspace} description="Your modules. Those marked Classic screen still open the current system while they're rebuilt in the new design." />
      <div className="space-y-8">
        {groups.map(({ group, items }) => (
          <section key={group}>
            <h2 className="mb-3 text-[12px] font-semibold tracking-[0.12em] text-ink-500 uppercase">{group}</h2>
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
              {items.map((m) => {
                const body = (
                  <>
                    <span className="grid size-11 shrink-0 place-items-center rounded-xl bg-brand-50 text-xl text-brand-600 group-hover:bg-brand-600 group-hover:text-white"><Icon name={m.icon} /></span>
                    <span className="min-w-0"><span className="block font-semibold text-ink-900">{m.label}</span><span className="text-[12.5px] text-ink-500">{m.description}</span>
                      {!m.live && <span className="mt-1 flex items-center gap-1 text-[11px] font-semibold tracking-wide text-copper-600">CLASSIC SCREEN <Icon name="external-link-line" /></span>}</span>
                  </>
                );
                const cls = "group flex items-start gap-3 rounded-xl border border-ink-200 bg-white p-4 shadow-card transition hover:-translate-y-0.5 hover:border-brand-300";
                return m.live || !m.legacyPath
                  ? <Link key={m.id} to={`/${cfg.portal}${m.path}`} className={cls}>{body}</Link>
                  : <a key={m.id} href={legacyUrl(m.legacyPath)} className={cls}>{body}</a>;
              })}
            </div>
          </section>
        ))}
      </div>
    </>
  );
}
