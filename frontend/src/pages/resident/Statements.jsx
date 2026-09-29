import { Link } from "react-router-dom";
import { useApi } from "../../api/useApi.js";
import { useAuth } from "../../auth/AuthContext.jsx";
import { EmptyState, ErrorState, Loading } from "../../components/States.jsx";
import { badgeClass, dateLabel, monthLabel, peso } from "../../format.js";

export default function Statements() {
  const { unitId } = useAuth();
  const { data, error, loading, reload } = useApi(`/resident/units/${unitId}/soa`);

  return (
    <>
      <div className="page-head">
        <div>
          <div className="eyebrow">MY UNIT</div>
          <h1>Statement of Account</h1>
          <p className="muted">Every monthly statement for your unit. Open one to see the charges and payments, or to print it.</p>
        </div>
      </div>
      <div className="card">
        {loading ? <Loading label="Loading statements…" /> : error ? (
          <div className="card-body"><ErrorState title="Couldn't load your statements" message={error.message} onRetry={reload} /></div>
        ) : data.statements.length === 0 ? (
          <EmptyState icon="bill" title="No statements yet">Your first statement will appear here after the monthly billing is generated.</EmptyState>
        ) : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Billing period</th><th>Due date</th><th className="num">Total</th><th className="num">Paid</th><th className="num">Balance</th><th>Status</th><th /></tr></thead>
              <tbody>
                {data.statements.map((s) => (
                  <tr key={s.id}>
                    <td><b>{monthLabel(s.billingMonth)}</b></td>
                    <td>{dateLabel(s.dueDate)}</td>
                    <td className="num">{peso(s.total)}</td>
                    <td className="num">{peso(s.amountPaid)}</td>
                    <td className="num"><b>{peso(s.balance)}</b></td>
                    <td><span className={badgeClass(s.status)}>{s.status}</span></td>
                    <td><Link className="btn sm" to={`/resident/soa/${s.id}`}>View</Link></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  );
}
