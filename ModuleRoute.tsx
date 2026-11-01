// Route guard + live/classic switch for one module.
//  1. no permission            -> 403 page (the server would refuse too)
//  2. live build, no API yet   -> link to the module's classic screen (never mock data)
//  3. otherwise                -> the React page
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../../auth/AuthContext";
import { MODULES, type ModuleId } from "../../config/modules";
import { ROLES } from "../../config/roles";
import { IS_MOCK } from "../../services/api";
import { Card, EmptyState, Icon, PageHeader } from "../base/ui";

// Classic screens are served by Flask; under `npm run dev` that is port 5000, not Vite's 5173.
export const legacyUrl = (path: string) => `${import.meta.env.DEV ? "http://127.0.0.1:5000" : ""}${path}`;

export function Forbidden() {
  return (
    <Card>
      <EmptyState icon="lock-2-line" title="You don't have access to this page">
        Your role doesn't include this module. If you need it, ask the system administrator.
      </EmptyState>
    </Card>
  );
}

/** In a live build, modules whose API isn't built yet open their classic screen. */
export function LegacyBridge({ id }: { id: ModuleId }) {
  const m = MODULES[id];
  return (
    <>
      <PageHeader eyebrow="Classic screen" title={m.label} description={m.description} />
      <Card>
        <EmptyState icon={m.icon} title={`${m.label} still runs on the classic screen`}
          action={m.legacyPath && (
            <a href={legacyUrl(m.legacyPath)} className="inline-flex h-10 items-center gap-2 rounded-lg bg-brand-600 px-4 font-semibold text-white shadow-sm hover:bg-brand-700">
              <Icon name="external-link-line" /> Open {m.label}
            </a>
          )}>
          This module is being rebuilt in the new design. Until then it works exactly as before: same data, same forms, same permissions, and you stay signed in.
        </EmptyState>
      </Card>
    </>
  );
}

/** Superadmin opens property modules from "All Property Modules" (they aren't in its sidebar): a way back. */
function BackToModules({ id }: { id: ModuleId }) {
  const { user } = useAuth();
  if (user?.role !== "super_admin" || ROLES.super_admin.nav.some((g) => g.items.includes(id))) return null;
  return (
    <Link to={`/${ROLES.super_admin.portal}${MODULES.modules.path}`}
      className="no-print mb-4 inline-flex h-9 items-center gap-1.5 rounded-lg px-2 -ml-2 text-[13.5px] font-semibold text-ink-600 hover:bg-ink-100 hover:text-ink-900">
      <Icon name="arrow-left-line" /> All Property Modules
    </Link>
  );
}

export function ModuleRoute({ id, children }: { id: ModuleId; children: ReactNode }) {
  const { can } = useAuth();
  const m = MODULES[id];
  if (!can(m.permission)) return <Forbidden />;
  if (!IS_MOCK && !m.live) return <><BackToModules id={id} /><LegacyBridge id={id} /></>;
  return <><BackToModules id={id} />{children}</>;
}
