import Icon from "./Icon.jsx";

// Shared loading / empty / error states, used by every module page.

export function Loading({ label = "Loading…", rows = 5 }) {
  return (
    <div className="card-body" aria-busy="true">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="skeleton" style={{ width: `${[92, 78, 85, 64, 88][i % 5]}%` }} />
      ))}
      <span className="sr-only">{label}</span>
    </div>
  );
}

export function EmptyState({ icon = "search", title, children, action }) {
  return (
    <div className="empty">
      <div className="empty-icon"><Icon name={icon} size={22} /></div>
      <h3>{title}</h3>
      {children && <p>{children}</p>}
      {action}
    </div>
  );
}

export function ErrorState({ title = "Something went wrong", message, onRetry }) {
  return (
    <div className="error-box" role="alert">
      <Icon name="alert" size={20} />
      <div>
        <b>{title}</b>
        {message && <p className="muted">{message}</p>}
        {onRetry && <button className="btn sm" onClick={onRetry}>Try again</button>}
      </div>
    </div>
  );
}

export function FullPageLoading() {
  return (
    <div className="full-center" aria-busy="true">
      <div className="brand-mark lg">C9</div>
      <p className="muted">Loading CityLand 9…</p>
    </div>
  );
}
