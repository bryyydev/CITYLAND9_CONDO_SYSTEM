import { Link } from "react-router-dom";
import { useApi } from "../../api/useApi.js";
import { useAuth } from "../../auth/AuthContext.jsx";
import Icon from "../../components/Icon.jsx";
import { ErrorState, Loading } from "../../components/States.jsx";
import { badgeClass, dateLabel, monthLabel, peso } from "../../format.js";

export default function ResidentHome() {
  const { unitId } = useAuth();
  const summary = useApi(`/resident/units/${unitId}/summary`);
  const notices = useApi("/resident/notices");

  if (summary.loading) return <div className="card"><Loading label="Loading your unit…" /></div>;
  if (summary.error) return <ErrorState title="Couldn't load your unit" message={summary.error.message} onRetry={summary.reload} />;

  const { unit, residentName, outstandingBalance, latestSoa, openTickets } = summary.data;
  const owes = Number(outstandingBalance) > 0;

  return (
    <>
      <div className="page-head">
        <div>
          <div className="eyebrow">RESIDENT PORTAL</div>
          <h1>Welcome, {residentName}</h1>
          <p className="muted">Unit {unit.unitNo}{unit.type ? ` · ${unit.type}` : ""}{unit.floor ? ` · Floor ${unit.floor}` : ""}</p>
        </div>
        <div className="actions">
          <Link className="btn" to="/resident/maintenance"><Icon name="wrench" />Request maintenance</Link>
          {latestSoa && <Link className="btn primary" to={`/resident/soa/${latestSoa.id}`}><Icon name="bill" />View latest SOA</Link>}
        </div>
      </div>

      <section className="grid kpis-3">
        <div className={`card kpi ${owes ? "hero" : ""}`}>
          <div className="label"><Icon name="bill" size={16} />Outstanding balance</div>
          <div className="value">{peso(outstandingBalance)}</div>
          <div className="hint">{owes ? (latestSoa?.dueDate ? `Due ${dateLabel(latestSoa.dueDate)}` : "Please settle at the Admin Office") : "Nothing to pay. Thank you!"}</div>
        </div>
        <div className="card kpi">
          <div className="label"><Icon name="receipt" size={16} />Latest statement</div>
          {latestSoa ? (
            <>
              <div className="value sm">{monthLabel(latestSoa.billingMonth)}</div>
              <div className="hint"><span className={badgeClass(latestSoa.status)}>{latestSoa.status}</span> · Total {peso(latestSoa.total)}</div>
            </>
          ) : <div className="hint">No statements yet.</div>}
        </div>
        <div className="card kpi">
          <div className="label"><Icon name="wrench" size={16} />Open maintenance requests</div>
          <div className="value">{openTickets}</div>
          <div className="hint"><Link to="/resident/maintenance">See requests →</Link></div>
        </div>
      </section>

      <div className="card" style={{ marginTop: 16 }}>
        <div className="card-head"><h2>Latest notices</h2><Link className="btn sm" to="/resident/notices">All notices</Link></div>
        {notices.loading ? <Loading rows={3} /> : notices.error ? (
          <div className="card-body"><ErrorState title="Couldn't load notices" message={notices.error.message} onRetry={notices.reload} /></div>
        ) : notices.data.notices.length === 0 ? (
          <p className="card-body muted">No notices have been posted.</p>
        ) : (
          <ul className="list card-body">
            {notices.data.notices.slice(0, 3).map((n) => (
              <li key={n.id}><div><b>{n.title}</b><p className="muted clamp">{n.message}</p></div><span className="muted nowrap">{dateLabel(n.publishDate)}</span></li>
            ))}
          </ul>
        )}
      </div>
    </>
  );
}
