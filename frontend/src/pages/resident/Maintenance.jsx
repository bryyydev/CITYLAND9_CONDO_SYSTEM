import { useState } from "react";
import Swal from "sweetalert2";
import { api } from "../../api/client.js";
import { useApi } from "../../api/useApi.js";
import { useAuth } from "../../auth/AuthContext.jsx";
import Icon from "../../components/Icon.jsx";
import { EmptyState, ErrorState, Loading } from "../../components/States.jsx";
import { badgeClass } from "../../format.js";

const EMPTY = { title: "", description: "", category: "General", priority: "Normal" };

export default function Maintenance() {
  const { unitId } = useAuth();
  const { data, error, loading, reload } = useApi(`/resident/units/${unitId}/maintenance`);
  const [form, setForm] = useState(EMPTY);
  const [touched, setTouched] = useState(false);
  const [sending, setSending] = useState(false);
  const set = (field) => (e) => setForm({ ...form, [field]: e.target.value });

  async function submit(e) {
    e.preventDefault();
    setTouched(true);
    if (!form.title.trim() || !form.description.trim()) return;
    setSending(true);
    try {
      const { ticket } = await api(`/resident/units/${unitId}/maintenance`, { method: "POST", body: form });
      setForm(EMPTY);
      setTouched(false);
      reload();
      Swal.fire({ icon: "success", title: "Request sent", text: `Your ticket number is ${ticket.ticketNo}.`, confirmButtonColor: "#2f7888" });
    } catch (err) {
      Swal.fire({ icon: "error", title: "Couldn't send the request", text: err.message, confirmButtonColor: "#2f7888" });
    } finally {
      setSending(false);
    }
  }

  const invalid = (field) => touched && !form[field].trim();

  return (
    <>
      <div className="page-head">
        <div>
          <div className="eyebrow">MY UNIT</div>
          <h1>Maintenance Requests</h1>
          <p className="muted">Report a problem in your unit. The building staff will update the status here.</p>
        </div>
      </div>

      <div className="grid two-col-even">
        <form className="card" onSubmit={submit} noValidate>
          <div className="card-head"><h2>New request</h2></div>
          <div className="card-body form-grid">
            <div className="field">
              <label htmlFor="m-cat">Category</label>
              <select id="m-cat" value={form.category} onChange={set("category")}>
                {(data?.categories || ["General"]).map((c) => <option key={c}>{c}</option>)}
              </select>
            </div>
            <div className="field">
              <label htmlFor="m-pri">Priority</label>
              <select id="m-pri" value={form.priority} onChange={set("priority")}>
                {(data?.priorities || ["Normal"]).map((p) => <option key={p}>{p}</option>)}
              </select>
            </div>
            <div className={`field full${invalid("title") ? " invalid" : ""}`}>
              <label htmlFor="m-title">Title</label>
              <input id="m-title" maxLength={200} value={form.title} onChange={set("title")} placeholder="e.g. Leaking kitchen faucet" />
              <div className="err">Enter a short title.</div>
            </div>
            <div className={`field full${invalid("description") ? " invalid" : ""}`}>
              <label htmlFor="m-desc">Description</label>
              <textarea id="m-desc" rows={4} value={form.description} onChange={set("description")} placeholder="What happened, where, and since when?" />
              <div className="err">Describe the problem.</div>
            </div>
          </div>
          <div className="card-foot"><button className="btn primary" disabled={sending}><Icon name="wrench" />{sending ? "Sending…" : "Send request"}</button></div>
        </form>

        <div className="card">
          <div className="card-head"><h2>My requests</h2></div>
          {loading ? <Loading rows={4} /> : error ? (
            <div className="card-body"><ErrorState title="Couldn't load your requests" message={error.message} onRetry={reload} /></div>
          ) : data.tickets.length === 0 ? (
            <EmptyState icon="wrench" title="No requests yet">Requests you send will be listed here.</EmptyState>
          ) : (
            <ul className="list card-body">
              {data.tickets.map((t) => (
                <li key={t.ticketNo}>
                  <div>
                    <b>{t.title}</b>
                    <span className="muted small"> · {t.category} · {t.ticketNo}</span>
                    {t.resolution && <p className="muted">Update: {t.resolution}</p>}
                  </div>
                  <span className={badgeClass(t.status)}>{t.status}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </>
  );
}
