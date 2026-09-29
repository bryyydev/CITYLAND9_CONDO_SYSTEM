import { useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import Swal from "sweetalert2";
import { useApi } from "../../api/useApi.js";
import { useAuth } from "../../auth/AuthContext.jsx";
import Icon from "../../components/Icon.jsx";
import { ErrorState, Loading } from "../../components/States.jsx";
import { badgeClass, dateLabel, monthLabel, peso } from "../../format.js";

function Line({ label, detail, amount, negative }) {
  return (
    <tr>
      <td>{label}{detail && <span className="sub">{detail}</span>}</td>
      <td className="num">{negative ? "− " : ""}{peso(amount)}</td>
    </tr>
  );
}

export default function Statement() {
  const { unitId } = useAuth();
  const { billId } = useParams();
  const { data, error, loading, reload } = useApi(`/resident/units/${unitId}/soa/${billId}`);
  const docRef = useRef(null);
  const [saving, setSaving] = useState(false);

  if (loading) return <div className="card"><Loading label="Loading statement…" /></div>;
  if (error) {
    return (
      <>
        <Link className="btn" to="/resident/soa">← All statements</Link>
        <div style={{ marginTop: 16 }}>
          <ErrorState title={error.status === 404 ? "Statement not found" : "Couldn't load the statement"} message={error.message} onRetry={error.status === 404 ? undefined : reload} />
        </div>
      </>
    );
  }

  const { unit, corporation, statement: s } = data;
  const c = s.charges;
  const month = monthLabel(s.billingMonth).toUpperCase();

  async function downloadPdf() {
    setSaving(true);
    try {
      // Loaded only when needed; bundled locally, no internet required.
      const html2pdf = (await import("html2pdf.js")).default;
      await html2pdf().set({
        margin: 10, filename: `SOA-${unit.unitNo}-${s.billingMonth}.pdf`,
        html2canvas: { scale: 2, backgroundColor: "#ffffff" }, jsPDF: { unit: "mm", format: "a4" },
      }).from(docRef.current).save();
    } catch {
      Swal.fire({ icon: "error", title: "Couldn't create the PDF", text: "Please use Print instead.", confirmButtonColor: "#2f7888" });
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      <div className="page-head no-print">
        <div>
          <div className="eyebrow">STATEMENT OF ACCOUNT</div>
          <h1>{monthLabel(s.billingMonth)}</h1>
          <p className="muted">Unit {unit.unitNo}</p>
        </div>
        <div className="actions">
          <Link className="btn" to="/resident/soa">← All statements</Link>
          <button className="btn" onClick={downloadPdf} disabled={saving}><Icon name="file" />{saving ? "Preparing PDF…" : "Download PDF"}</button>
          <button className="btn primary" onClick={() => window.print()}><Icon name="receipt" />Print</button>
        </div>
      </div>

      <article className="soa" ref={docRef} aria-label="Statement of account">
        <div className="soa-head">
          <div><h2>{corporation}</h2><p>Statement of Account</p></div>
          <div className="soa-title"><span className={badgeClass(s.status)}>{s.status}</span></div>
        </div>
        <div className="soa-meta">
          <div><small>UNIT</small><b>{unit.unitNo}</b></div>
          <div><small>BILLING PERIOD</small><b>{monthLabel(s.billingMonth)}</b></div>
          <div><small>DUE DATE</small><b>{dateLabel(s.dueDate)}</b></div>
          <div><small>BALANCE</small><b>{peso(s.balance)}</b></div>
        </div>

        <h3>CHARGES</h3>
        <table>
          <thead><tr><th>Description</th><th className="num">Amount</th></tr></thead>
          <tbody>
            <Line label={`${month} DUES`} amount={c.condoDues} />
            {Number(c.parking) > 0 && <Line label="Parking" amount={c.parking} />}
            {Number(c.storage) > 0 && <Line label="Storage" amount={c.storage} />}
            <Line
              label={`${month} WATER`}
              detail={c.waterPaidSeparately ? "Paid separately, not included in the total"
                : c.waterUsage !== null ? `${c.waterUsage} m³ × ${peso(c.waterRate)}/m³` : "No reading for this period"}
              amount={c.water}
            />
            {Number(c.previousBalance) > 0 && <Line label="Previous balance" detail="Unpaid from earlier statements" amount={c.previousBalance} />}
            {Number(c.penalty) > 0 && <Line label="Penalty" detail="Late payment" amount={c.penalty} />}
            {Number(c.advanceApplied) > 0 && <Line label="Advance payment applied" amount={c.advanceApplied} negative />}
            <tr className="total-row"><td>TOTAL AMOUNT DUE</td><td className="num">{peso(s.total)}</td></tr>
            <tr><td>Amount paid</td><td className="num">− {peso(s.amountPaid)}</td></tr>
            <tr className="balance-row"><td><b>BALANCE</b></td><td className="num"><b>{peso(s.balance)}</b></td></tr>
          </tbody>
        </table>
        {s.note && <p className="muted soa-note">Note: {s.note}</p>}

        <h3>PAYMENTS</h3>
        {s.payments.length === 0 ? <p className="muted">No payments recorded for this statement.</p> : (
          <table>
            <thead><tr><th>Date</th><th>Reference</th><th>Type</th><th className="num">Amount</th></tr></thead>
            <tbody>
              {s.payments.map((p, i) => (
                <tr key={i}><td>{dateLabel(p.date)}</td><td>{p.reference || "—"}</td><td>{p.type} · {p.method}</td><td className="num">{peso(p.amount)}</td></tr>
              ))}
            </tbody>
          </table>
        )}
        <p className="muted soa-foot">Pay at the Admin Office by cash, check or online transfer. For questions about this statement, please contact the Admin Office.</p>
      </article>
    </>
  );
}
