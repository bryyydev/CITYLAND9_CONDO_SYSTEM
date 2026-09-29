import { Link } from "react-router-dom";
import { useAuth } from "../../auth/AuthContext.jsx";
import Icon from "../../components/Icon.jsx";
import { visibleNav } from "../../components/Layout.jsx";
import { NAV } from "../../navigation.js";

/** Landing page of a staff-side portal: a card for every module this user may open. */
export default function PortalHome({ eyebrow, title, intro }) {
  const { user, can, portal } = useAuth();
  const groups = visibleNav(NAV, can);
  return (
    <>
      <div className="page-head">
        <div>
          <div className="eyebrow">{eyebrow}</div>
          <h1>{title}</h1>
          <p className="muted">Welcome, {user.username}. {intro}</p>
        </div>
      </div>
      {groups.map(({ group, items }) => (
        <section key={group} className="module-group">
          <h2 className="section-title">{group}</h2>
          <div className="module-grid">
            {items.flatMap((item) => [
              ...(item.key && can(item.key) ? [item] : []),
              ...item.kids.map((k) => ({ ...k, icon: item.icon })),
            ]).map((page) => (
              <Link key={page.key} to={`/${portal}${page.path}`} className="card module-card">
                <span className="module-icon"><Icon name={page.icon} /></span>
                <b>{page.label}</b>
              </Link>
            ))}
          </div>
        </section>
      ))}
    </>
  );
}
