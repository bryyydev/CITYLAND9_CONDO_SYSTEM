// Workspace shell: sidebar + top bar + page. Everything in it follows the active role.
import { useEffect, useRef, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { useAuth, useUser } from "../../auth/AuthContext";
import { MODULES } from "../../config/modules";
import { ROLES, ROLE_ORDER } from "../../config/roles";
import { initials } from "../../lib/format";
import { IS_MOCK, PERSONAS } from "../../services/api";
import type { Role } from "../../services/types";
import { ConfirmDialog, useToast } from "../base/overlays";
import { Icon, IconButton, cx } from "../base/ui";
import { ChangePasswordDrawer } from "./ChangePasswordDrawer";

function useNav() {
  const user = useUser();
  const { can } = useAuth();
  const cfg = ROLES[user.role];
  const groups = cfg.nav
    .map((g) => ({ group: g.group, items: g.items.map((id) => MODULES[id]).filter((m) => can(m.permission)) }))
    .filter((g) => g.items.length);
  return { cfg, groups };
}

function Sidebar({ onNavigate }: { onNavigate?: () => void }) {
  const { cfg, groups } = useNav();
  return (
    <div className="flex h-full flex-col bg-ink-900 text-ink-200">
      <NavLink to={`/${cfg.portal}`} end onClick={onNavigate} className="flex items-center gap-3 px-5 py-5">
        <div className="grid size-10 place-items-center rounded-xl bg-gradient-to-br from-brand-500 to-brand-700 font-display text-[15px] font-bold text-white shadow-lg shadow-brand-900/40">C9</div>
        <div className="leading-tight">
          <div className="font-display text-[15px] font-semibold tracking-wide text-white">CITYLAND 9</div>
          <div className="text-[11px] tracking-[0.14em] text-copper-200 uppercase">{cfg.workspace}</div>
        </div>
      </NavLink>
      <nav className="flex-1 space-y-5 overflow-y-auto px-3 pb-6" aria-label="Main navigation">
        {groups.map(({ group, items }) => (
          <div key={group}>
            <div className="px-3 pb-1.5 text-[10.5px] font-semibold tracking-[0.14em] text-ink-400 uppercase">{group}</div>
            {items.map((m) => (
              <NavLink key={m.id} to={`/${cfg.portal}${m.path}`} end={m.path === ""} onClick={onNavigate}
                className={({ isActive }) => cx("group flex items-center gap-3 rounded-lg px-3 py-2 text-[13.5px] font-medium transition",
                  isActive ? "bg-brand-600 text-white shadow-sm shadow-brand-900/40" : "text-ink-300 hover:bg-white/5 hover:text-white")}>
                <Icon name={m.icon} className="text-[17px] opacity-90" />
                <span className="truncate">{m.label}</span>
                {IS_MOCK || m.live ? null : <span className="ml-auto text-[13px] text-ink-400" title="Opens the classic screen" aria-label="classic screen"><Icon name="external-link-line" /></span>}
              </NavLink>
            ))}
          </div>
        ))}
      </nav>
      <div className="flex items-center gap-2 border-t border-white/10 px-5 py-3 text-[11.5px] text-ink-400">
        <span className="size-2 rounded-full bg-emerald-400" /> {IS_MOCK ? "Prototype · mock data" : "LAN system · online"}
      </div>
    </div>
  );
}

function Breadcrumb() {
  const { cfg, groups } = useNav();
  const { pathname } = useLocation();
  const rest = pathname.replace(`/${cfg.portal}`, "");
  const hit = groups.flatMap((g) => g.items.map((m) => ({ g: g.group, m })))
    .filter(({ m }) => (m.path === "" ? rest === "" || rest === "/" : rest === m.path || rest.startsWith(`${m.path}/`)))
    .sort((a, b) => b.m.path.length - a.m.path.length)[0];
  return (
    <div className="min-w-0 truncate text-[13px] text-ink-500">
      {hit && hit.m.path !== "" ? <>{hit.g} <span className="text-ink-300">/</span> <b className="font-semibold text-ink-900">{hit.m.label}</b></> : <b className="font-semibold text-ink-900">{cfg.workspace}</b>}
    </div>
  );
}

/** Prototype only: switch the whole workspace between the six roles. */
function RoleSwitcher() {
  const user = useUser();
  const { switchRole } = useAuth();
  const navigate = useNavigate();
  const toast = useToast();
  return (
    <label className="relative flex items-center gap-2 rounded-lg border border-copper-200 bg-copper-50 py-1 pr-2 pl-3 text-[12px] font-semibold text-copper-700">
      <Icon name="swap-2-line" className="text-base" />
      <span className="hidden lg:inline">Role</span>
      <select value={user.role} aria-label="Switch role (prototype)"
        className="cursor-pointer appearance-none rounded-md bg-white py-1 pr-7 pl-2 text-[13px] font-semibold text-ink-900 shadow-sm outline-none ring-1 ring-copper-200 focus:ring-2 focus:ring-copper-400"
        onChange={async (e) => {
          const next = await switchRole(e.target.value as Role);
          navigate(`/${ROLES[next.role].portal}`);
          toast({ tone: "info", title: `Now viewing as ${ROLES[next.role].label}`, message: `${next.displayName} · ${ROLES[next.role].workspace}` });
        }}>
        {ROLE_ORDER.map((r) => <option key={r} value={r}>{ROLES[r].label} — {PERSONAS?.[r].displayName}</option>)}
      </select>
      <Icon name="arrow-down-s-line" className="pointer-events-none absolute right-3 text-ink-500" />
    </label>
  );
}

function UserMenu({ onChangePassword, onSignOut }: { onChangePassword: () => void; onSignOut: () => void }) {
  const user = useUser();
  const cfg = ROLES[user.role];
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => !ref.current?.contains(e.target as Node) && setOpen(false);
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", close); document.removeEventListener("keydown", esc); };
  }, [open]);
  const name = user.displayName ?? user.username;
  return (
    <div className="relative" ref={ref}>
      <button onClick={() => setOpen(!open)} aria-expanded={open} aria-haspopup="menu"
        className="flex items-center gap-2.5 rounded-xl py-1 pr-2 pl-1 transition hover:bg-ink-100">
        <span className="grid size-9 place-items-center rounded-full bg-brand-600 font-display text-[13px] font-semibold text-white">{initials(name)}</span>
        <span className="hidden text-left leading-tight md:block">
          <span className="block text-[13px] font-semibold text-ink-900">{name}</span>
          <span className="block text-[11.5px] text-ink-500">{user.subtitle ?? user.roleLabel}</span>
        </span>
        <Icon name="arrow-down-s-line" className="text-ink-400" />
      </button>
      {open && (
        <div role="menu" className="absolute right-0 z-40 mt-2 w-64 animate-pop-in overflow-hidden rounded-xl border border-ink-200 bg-white shadow-pop">
          <div className="border-b border-ink-100 px-4 py-3">
            <p className="font-semibold text-ink-900">{name}</p>
            <p className="truncate text-[12.5px] text-ink-500">{user.email ?? user.username}</p>
            <span className={cx("mt-2 inline-block rounded-md px-2 py-0.5 text-[11px] font-semibold", cfg.badge)}>{cfg.label}</span>
          </div>
          <button role="menuitem" className="flex w-full items-center gap-2.5 px-4 py-2.5 text-left hover:bg-ink-50" onClick={() => { setOpen(false); onChangePassword(); }}>
            <Icon name="lock-password-line" className="text-ink-500" /> Change password
          </button>
          <button role="menuitem" className="flex w-full items-center gap-2.5 px-4 py-2.5 text-left text-red-700 hover:bg-red-50" onClick={() => { setOpen(false); onSignOut(); }}>
            <Icon name="logout-box-r-line" /> Sign out
          </button>
        </div>
      )}
    </div>
  );
}

export default function AppShell() {
  const user = useUser();
  const { logout } = useAuth();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const cfg = ROLES[user.role];
  const [drawer, setDrawer] = useState(false);
  const [pwOpen, setPwOpen] = useState(false);
  const [confirmOut, setConfirmOut] = useState(false);

  useEffect(() => setDrawer(false), [pathname]);
  useEffect(() => {
    if (!drawer) return;
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setDrawer(false);
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [drawer]);

  return (
    <div className="min-h-dvh lg:pl-[264px]">
      <a href="#content" className="sr-only z-50 rounded bg-white px-3 py-2 focus:not-sr-only focus:fixed focus:top-2 focus:left-2">Skip to content</a>
      <aside className="no-print fixed inset-y-0 left-0 z-30 hidden w-[264px] lg:block"><Sidebar /></aside>
      {drawer && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <div className="absolute inset-0 animate-fade-in bg-ink-900/40 backdrop-blur-sm" onClick={() => setDrawer(false)} />
          <aside className="relative h-full w-[280px] max-w-[85vw] animate-slide-in-left shadow-pop"><Sidebar onNavigate={() => setDrawer(false)} /></aside>
        </div>
      )}

      <header className="no-print sticky top-0 z-20 flex h-16 items-center gap-2 border-b border-ink-200/70 bg-white/80 px-3 backdrop-blur-md sm:gap-3 sm:px-6">
        <IconButton icon="menu-line" label="Open menu" className="lg:hidden" onClick={() => setDrawer(true)} />
        <Breadcrumb />
        <div className="flex-1" />
        <span className={cx("hidden rounded-md px-2.5 py-1 text-[11.5px] font-semibold tracking-wide whitespace-nowrap sm:inline-block", cfg.badge)}>{cfg.label.toUpperCase()}</span>
        {IS_MOCK && <RoleSwitcher />}
        <UserMenu onChangePassword={() => setPwOpen(true)} onSignOut={() => setConfirmOut(true)} />
      </header>

      {IS_MOCK && (
        <div className="no-print border-b border-copper-200 bg-copper-50 px-4 py-1.5 text-center text-[12px] text-copper-700 sm:px-6">
          <Icon name="flask-line" /> Prototype mode — mock data only. Nothing here reaches the CityLand 9 database.
        </div>
      )}

      <main id="content" className="mx-auto w-full max-w-[1400px] px-4 py-6 sm:px-6 lg:px-8 lg:py-8">
        <div key={pathname} className="animate-rise-in"><Outlet /></div>
      </main>

      <ChangePasswordDrawer open={pwOpen} onClose={() => setPwOpen(false)} />
      <ConfirmDialog open={confirmOut} title="Sign out?" message="You'll need to sign in again to continue." confirmLabel="Sign out" cancelLabel="Stay signed in"
        onClose={() => setConfirmOut(false)}
        onConfirm={async () => { setConfirmOut(false); await logout(); navigate("/login", { replace: true }); }} />
    </div>
  );
}
