// Form pieces shared by the account screens (Users & Access, Resident Accounts).
import { type RefObject, useEffect, useState } from "react";
import { Icon } from "../base/ui";

export const USERNAME_RULE = /^[A-Za-z0-9._-]{3,80}$/;

export function useDebounced<T>(value: T, ms = 300) {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

/** After a failed submit, move keyboard focus to the first field the form marks invalid. */
export function focusFirstInvalid(form: RefObject<HTMLFormElement | null>) {
  requestAnimationFrame(() => form.current?.querySelector<HTMLElement>("[aria-invalid='true']")?.focus());
}

/** Client-side check matching app.core.security.password_problem's basic rules ("" when fine). */
export function passwordProblem(password: string, username: string, minLength: number) {
  if (password.length < minLength) return `Use at least ${minLength} characters.`;
  if (username.length >= 3 && password.toLowerCase().includes(username.toLowerCase())) return "The password must not contain the username.";
  return "";
}

export function TemporaryBadge() {
  return (
    <span className="ml-2 inline-flex items-center gap-1 rounded bg-copper-50 px-1.5 py-0.5 text-[11px] font-semibold text-copper-700"
      title="A temporary password was set. A new one must be chosen at the next sign-in.">
      <Icon name="key-2-line" /> Temporary password
    </span>
  );
}

/** Password field with a visibility toggle. */
export function PasswordInput({ id, value, onChange, invalid, describedBy, autoComplete = "new-password" }:
  { id: string; value: string; onChange: (v: string) => void; invalid?: boolean; describedBy?: string; autoComplete?: string }) {
  const [shown, setShown] = useState(false);
  return (
    <div className="relative">
      <input id={id} type={shown ? "text" : "password"} className="input pr-11" autoComplete={autoComplete} value={value}
        aria-invalid={invalid || undefined} aria-describedby={describedBy} onChange={(e) => onChange(e.target.value)} />
      <button type="button" onClick={() => setShown(!shown)} aria-label={shown ? "Hide password" : "Show password"} aria-pressed={shown}
        className="absolute top-1/2 right-1 grid size-9 -translate-y-1/2 place-items-center rounded-md text-ink-500 hover:bg-ink-100 hover:text-ink-800">
        <Icon name={shown ? "eye-off-line" : "eye-line"} className="text-lg" />
      </button>
    </div>
  );
}
