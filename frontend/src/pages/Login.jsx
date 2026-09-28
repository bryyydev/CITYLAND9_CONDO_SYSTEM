import { useState } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext.jsx";
import { homePath } from "../navigation.js";

export default function Login() {
  const { user, login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [touched, setTouched] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  if (user) return <Navigate to={homePath(user)} replace />;

  async function submit(e) {
    e.preventDefault();
    setTouched(true);
    setError("");
    if (!username.trim() || !password) return;
    setBusy(true);
    try {
      const signedIn = await login(username.trim(), password);
      navigate(location.state?.from || homePath(signedIn), { replace: true });
    } catch (err) {
      setError(err.message);
      setPassword("");
      setTouched(false); // show only the server's message, not "Enter your password"
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login">
      <section className="login-art" aria-hidden="true">
        <div className="brand plain">
          <div className="brand-mark inverse">C9</div>
          <div className="brand-text"><b>CITYLAND 9</b><span>CONDO SYSTEM 2026</span></div>
        </div>
        <div>
          <h2>Condo management for Cityland 9, on your own network.</h2>
          <p>Billing, SOA, water readings, payroll and resident services. Your data stays on the office server.</p>
        </div>
        <svg className="building" viewBox="0 0 300 120" width="300" fill="none" stroke="#fff" strokeWidth="2">
          <rect x="30" y="20" width="70" height="100" /><rect x="110" y="0" width="80" height="120" /><rect x="200" y="35" width="70" height="85" />
          {Array.from({ length: 12 }, (_, i) => (
            <rect key={i} x={122 + (i % 3) * 22} y={12 + Math.floor(i / 3) * 22} width="12" height="12" />
          ))}
        </svg>
      </section>

      <section className="login-form">
        <form className="login-card" onSubmit={submit} noValidate>
          <div className="eyebrow">WELCOME BACK</div>
          <h1>Sign in</h1>
          <p className="muted intro">Use the account given to you by the administrator.</p>

          {error && <div className="alert danger" role="alert">{error}</div>}

          <div className={`field${touched && !username.trim() ? " invalid" : ""}`}>
            <label htmlFor="username">Username</label>
            <input id="username" autoComplete="username" autoFocus value={username} onChange={(e) => setUsername(e.target.value)} />
            <div className="err">Enter your username.</div>
          </div>
          <div className={`field${touched && !password ? " invalid" : ""}`}>
            <label htmlFor="password">Password</label>
            <input id="password" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
            <div className="err">Enter your password.</div>
          </div>

          <button className="btn primary block" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
        </form>
      </section>
    </div>
  );
}
