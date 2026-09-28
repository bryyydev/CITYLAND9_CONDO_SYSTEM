import Icon from "../components/Icon.jsx";
import { legacyUrl } from "../navigation.js";

// Bridge page for modules that are not migrated yet: the module keeps working on
// the current system, and the user's session carries over (same server, same cookie).
export default function LegacyModule({ page }) {
  return (
    <>
      <div className="page-head">
        <div>
          <div className="eyebrow">{page.group}</div>
          <h1>{page.label}</h1>
          <p className="muted">This module is still running on the current system while it's being moved to the new interface.</p>
        </div>
      </div>
      <div className="card">
        <div className="empty">
          <div className="empty-icon"><Icon name={page.icon || "info"} size={22} /></div>
          <h3>{page.label} opens in the current system</h3>
          <p>Everything works exactly as before: same data, same forms, same permissions. You stay signed in.</p>
          <a className="btn primary" href={legacyUrl(page.path)}>
            <Icon name="external" />Open {page.label}
          </a>
        </div>
      </div>
    </>
  );
}
