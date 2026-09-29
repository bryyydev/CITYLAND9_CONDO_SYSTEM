import { useApi } from "../../api/useApi.js";
import { EmptyState, ErrorState, Loading } from "../../components/States.jsx";
import { dateLabel } from "../../format.js";

export default function Notices() {
  const { data, error, loading, reload } = useApi("/resident/notices");
  return (
    <>
      <div className="page-head">
        <div>
          <div className="eyebrow">MY UNIT</div>
          <h1>Notices</h1>
          <p className="muted">Announcements from the Cityland 9 administration.</p>
        </div>
      </div>
      <div className="card">
        {loading ? <Loading rows={4} /> : error ? (
          <div className="card-body"><ErrorState title="Couldn't load notices" message={error.message} onRetry={reload} /></div>
        ) : data.notices.length === 0 ? (
          <EmptyState icon="megaphone" title="No notices yet">Announcements from the administration will appear here.</EmptyState>
        ) : (
          <ul className="list card-body">
            {data.notices.map((n) => (
              <li key={n.id} className="notice">
                <div><b>{n.title}</b><p className="pre">{n.message}</p></div>
                <span className="muted nowrap">{dateLabel(n.publishDate)}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </>
  );
}
