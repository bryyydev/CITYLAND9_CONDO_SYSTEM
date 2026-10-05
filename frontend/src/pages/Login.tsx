import { useState } from "react";
import { Navigate, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { Button, Field, Icon, Notice, cx } from "../components/base/ui";
import { ROLES, ROLE_ORDER } from "../config/roles";
import { IS_MOCK, PERSONAS } from "../services/api";
import type { Role } from "../services/types";

export default function Login() {
  const { user, login, switchRole } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [params] = useSearchParams();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [touched, setTouched] = useState(false);
  // A reason passed by the server when it signed someone out (e.g. "no longer a current tenant").
  const [error, setError] = useState(params.get("notice") ?? "");
  const [busy, setBusy] = useState(false);

  if (user) return <Navigate to={`/${ROLES[user.role].portal}`} replace />;
  const from = (location.state as { from?: string } | null)?.from;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setTouched(true);
    setError("");
    if (!username.trim() || !password) return;
    setBusy(true);
    try {
      const signedIn = await login(username.trim(), password);
      const home = `/${ROLES[signedIn.role].portal}`;
      navigate(from?.startsWith(home) ? from : home, { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign-in failed.");
      setPassword("");
      setTouched(false);
    } finally {
      setBusy(false);
    }
  }

  async function prototypeAs(role: Role) {
    const signedIn = await switchRole(role);
    navigate(`/${ROLES[signedIn.role].portal}`, { replace: true });
  }

  return (
    <div className="grid min-h-dvh lg:grid-cols-[1.05fr_1fr]">
      <section className="relative hidden overflow-hidden bg-ink-900 p-12 text-white lg:flex lg:flex-col lg:justify-between" aria-hidden="true">
        <div className="absolute -top-40 -right-40 size-[520px] rounded-full bg-brand-600/30 blur-3xl" />
        <div className="absolute -bottom-48 -left-24 size-[460px] rounded-full bg-copper-500/20 blur-3xl" />
        <div className="relative flex items-center gap-3">
          <div className="grid size-12 place-items-center rounded-2xl bg-gradient-to-br from-brand-500 to-brand-700 font-display text-lg font-bold">C9</div>
          <div><div className="font-display text-lg font-semibold tracking-wide">CITYLAND 9</div><div className="text-[12px] tracking-[0.16em] text-copper-200">CONDOMINIUM CORPORATION</div></div>
        </div>
        <div className="relative max-w-lg">
          <h2 className="font-display text-[34px] leading-tight font-semibold">Condo management for Cityland 9, on your own network.</h2>
          <p className="mt-4 text-ink-300">Billing and SOAs, official receipts, water readings, payroll and resident services. Your data stays on the office server.</p>
          <div className="mt-8 grid grid-cols-3 gap-3 text-[12.5px]">
            {[["shield-check-line", "Role-based access"], ["receipt-line", "Gap-free ORs"], ["wifi-off-line", "Works offline on LAN"]].map(([i, t]) => (
              <div key={t} className="rounded-xl border border-white/10 bg-white/5 p-3"><i className={`ri-${i} text-xl text-copper-200`} /><p className="mt-1 text-ink-200">{t}</p></div>
            ))}
          </div>
        </div>
        <p className="relative text-[12px] text-ink-400">9 Dela Rosa Street, Barangay Pio del Pilar, Makati City</p>
      </section>

      <section className="flex items-center justify-center bg-ink-50 px-4 py-10">
        <div className="w-full max-w-[420px] animate-rise-in">
          <div className="mb-8 flex items-center gap-3 lg:hidden">
            <div className="grid size-11 place-items-center rounded-xl bg-brand-600 font-display font-bold text-white">C9</div>
            <div className="font-display text-lg font-semibold">CITYLAND 9</div>
          </div>
          <p className="text-[11.5px] font-semibold tracking-[0.14em] text-copper-600 uppercase">Welcome back</p>
          <h1 className="mt-1 text-[28px] font-semibold text-ink-900">Sign in</h1>
          <p className="mt-1 text-ink-500">Use the account given to you by the administrator.</p>

          {error && <div className="mt-5"><Notice tone="warn">{error}</Notice></div>}

          <form className="mt-6 space-y-4" onSubmit={submit} noValidate>
            <Field label="Username" error={touched && !username.trim() && "Enter your username."}>
              {(id) => <input id={id} className="input h-11" autoComplete="username" autoFocus value={username} aria-invalid={touched && !username.trim()} onChange={(e) => setUsername(e.target.value)} />}
            </Field>
            <Field label="Password" error={touched && !password && "Enter your password."}>
              {(id) => (
                <div className="relative">
                  <input id={id} type={showPassword ? "text" : "password"} className="input h-11 pr-11" autoComplete="current-password" value={password} aria-invalid={touched && !password} onChange={(e) => setPassword(e.target.value)} />
                  <button type="button" onClick={() => setShowPassword((v) => !v)} aria-label={showPassword ? "Hide password" : "Show password"} aria-pressed={showPassword} title={showPassword ? "Hide password" : "Show password"}
                    className="absolute inset-y-0 right-0 grid w-11 place-items-center rounded-r-lg text-lg text-ink-500 hover:text-ink-800 focus-visible:outline-2 focus-visible:outline-brand-600">
                    <Icon name={showPassword ? "eye-off-line" : "eye-line"} />
                  </button>
                </div>
              )}
            </Field>
            <Button type="submit" className="h-11 w-full" loading={busy} icon="login-box-line">Sign in</Button>
          </form>

          {IS_MOCK && (
            <div className="mt-8 rounded-xl border border-copper-200 bg-copper-50 p-4">
              <p className="flex items-center gap-2 font-semibold text-copper-700"><Icon name="flask-line" /> Prototype mode — open a workspace</p>
              <p className="mt-1 text-[12.5px] text-copper-700">Mock data only. You can switch roles any time from the top bar.</p>
              <div className="mt-3 grid grid-cols-2 gap-2">
                {ROLE_ORDER.map((r) => (
                  <button key={r} onClick={() => void prototypeAs(r)} className="rounded-lg border border-copper-200 bg-white px-3 py-2 text-left transition hover:border-copper-400">
                    <span className={cx("inline-block rounded px-1.5 text-[10.5px] font-semibold", ROLES[r].badge)}>{ROLES[r].label}</span>
                    <span className="mt-1 block truncate text-[12.5px] font-semibold text-ink-800">{PERSONAS?.[r].displayName}</span>
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      </section>
    </div>
  );
}
