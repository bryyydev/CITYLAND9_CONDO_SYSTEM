import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import Swal from "sweetalert2";
import { useAuth } from "../auth/AuthContext.jsx";
import { legacyUrl } from "../navigation.js";
import { usePreference } from "../preferences.js";
import Icon from "./Icon.jsx";

/** Sidebar entries the user may see: items and sub-items filtered by permission. */
export function visibleNav(nav, can) {
  return nav
    .map(({ group, items }) => ({
      group,
      items: items
        .map((item) => ({ ...item, kids: (item.children || []).filter((c) => can(c.key)) }))
        .filter((item) => (item.key && can(item.key)) || item.kids.length),
    }))
    .filter((g) => g.items.length);
}

function Sidebar({ nav, base, onNavigate }) {
  const { can } = useAuth();
  const [closedGroups, setClosedGroups] = usePreference("cl9.closedGroups", []);
  const toggle = (group) =>
    setClosedGroups(closedGroups.includes(group) ? closedGroups.filter((g) => g !== group) : [...closedGroups, group]);

  return (
    <nav className="nav" aria-label="Main navigation">
      {visibleNav(nav, can).map(({ group, items }) => {
        const open = !closedGroups.includes(group);
        return (
          <div className="nav-group" key={group} data-open={open}>
            <button className="nav-group-btn" aria-expanded={open} onClick={() => toggle(group)}>
              <span>{group}</span><Icon name="chev" size={14} />
            </button>
            {open && (
              <div className="nav-items">
                {items.map((item) => {
                  // A parent is a page of its own (Employees) or only a heading for its sub-pages (Units, Billing & SOA).
                  const ownPage = Boolean(item.key && can(item.key));
                  const target = ownPage ? item.path : item.kids[0].path;
                  return (
                    <div key={item.label}>
                      <NavLink
                        to={`${base}${target}`}
                        end
                        aria-current={ownPage ? "page" : "false"}
                        className={({ isActive }) => `nav-item${ownPage && isActive ? " active" : ""}`}
                        title={item.label}
                        onClick={onNavigate}
                      >
                        <Icon name={item.icon} /><span>{item.label}</span>
                      </NavLink>
                      {item.kids.map((kid) => (
                        <NavLink key={kid.key} to={`${base}${kid.path}`} end className="nav-item sub" onClick={onNavigate}>
                          <span>{kid.label}</span>
                        </NavLink>
                      ))}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        );
      })}
    </nav>
  );
}

function Breadcrumbs({ nav, base, title }) {
  const { pathname } = useLocation();
  for (const { group, items } of nav) {
    for (const item of items) {
      for (const page of [item, ...(item.children || [])]) {
        if (page.path !== undefined && `${base}${page.path}` === pathname) {
          const g = group.charAt(0) + group.slice(1).toLowerCase();
          const parent = page !== item ? `${item.label} / ` : "";
          return <div className="crumbs">{g} / {parent}<b>{page.label}</b></div>;
        }
      }
    }
  }
  return <div className="crumbs"><b>{title}</b></div>;
}

/**
 * Portal shell: sidebar + top bar + page.
 * `nav`  - navigation groups for this portal (filtered by permission here)
 * `base` - URL prefix of the portal, e.g. "/accounting"
 * `title`- portal name shown in the breadcrumb on its home page
 */
export default function Layout({ nav, base, title }) {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const [collapsed, setCollapsed] = usePreference("cl9.sidebarCollapsed", false);
  const [theme, setTheme] = usePreference("cl9.theme", "light");
  const [drawerOpen, setDrawerOpen] = useState(false);

  useEffect(() => { document.documentElement.dataset.theme = theme; }, [theme]);
  useEffect(() => { setDrawerOpen(false); }, [pathname]);
  useEffect(() => {
    const onKey = (e) => e.key === "Escape" && setDrawerOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  async function signOut() {
    const answer = await Swal.fire({
      title: "Sign out?", text: "You will need to sign in again to continue.", icon: "question",
      showCancelButton: true, confirmButtonText: "Sign out", cancelButtonText: "Stay signed in",
      confirmButtonColor: "#2f7888",
    });
    if (!answer.isConfirmed) return;
    await logout();
    navigate("/login", { replace: true });
  }

  return (
    <div className={`app${collapsed ? " collapsed" : ""}${drawerOpen ? " drawer-open" : ""}`}>
      <a className="skip-link" href="#content">Skip to content</a>
      <aside className="sidebar">
        <NavLink to={base} end className="brand" title={`${title} home`}>
          <div className="brand-mark">C9</div>
          <div className="brand-text"><b>CITYLAND 9</b><span>{title.toUpperCase()}</span></div>
        </NavLink>
        <Sidebar nav={nav} base={base} onNavigate={() => setDrawerOpen(false)} />
        <div className="sidebar-foot"><i className="dot" /><span>LAN SYSTEM · Online</span></div>
      </aside>
      {drawerOpen && <div className="overlay" onClick={() => setDrawerOpen(false)} />}

      <div className="main">
        <header className="topbar">
          <button className="icon-btn mobile-only" onClick={() => setDrawerOpen(true)} aria-label="Open menu"><Icon name="menu" /></button>
          <button className="icon-btn desktop-only" onClick={() => setCollapsed(!collapsed)} aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}><Icon name="panel" /></button>
          <Breadcrumbs nav={nav} base={base} title={title} />
          <div className="spacer" />
          <div className="org"><b>CITYLAND 9 CONDOMINIUM CORPORATION</b>9 Dela Rosa St., Brgy. Pio del Pilar, Makati City</div>
          <button className="icon-btn" onClick={() => setTheme(theme === "dark" ? "light" : "dark")} aria-label="Toggle light/dark theme"><Icon name="sun" /></button>
          <div className="user-chip">
            <div className="avatar">{user.username.slice(0, 2).toUpperCase()}</div>
            <div><b>{user.username}</b><small>{user.roleLabel}</small></div>
          </div>
          <a className="icon-btn" href={legacyUrl("/change-password")} title="Change password" aria-label="Change password"><Icon name="key" /></a>
          <button className="icon-btn" onClick={signOut} aria-label="Sign out" title="Sign out"><Icon name="logout" /></button>
        </header>
        <main className="content" id="content">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
