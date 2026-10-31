import { useState } from "react";
import { useAuth } from "../../auth/AuthContext";
import { useAction } from "../../hooks/useAsync";
import { api } from "../../services/api";
import { Drawer, useToast } from "../base/overlays";
import { Button, Field, Notice } from "../base/ui";

const EMPTY = { current: "", next: "", confirm: "" };

/** The signed-in user's OWN password. Same rules as the server (minimum length from the server's
 * policy; it also rejects common passwords and ones containing the username). Changing it ends the
 * account's sessions on other PCs; this one stays signed in. */
export function ChangePasswordForm({ onDone, submitLabel = "Change password", formId }: { onDone: () => void; submitLabel?: string; formId?: string }) {
  const { user, refresh } = useAuth();
  const minLength = user?.passwordPolicy?.minLength ?? 10;
  const [form, setForm] = useState(EMPTY);
  const [touched, setTouched] = useState(false);
  const [serverError, setServerError] = useState("");
  const { busy, act } = useAction();
  const toast = useToast();
  const errors = {
    current: !form.current && "Enter your current password.",
    next: form.next.length < minLength ? `Use at least ${minLength} characters.` : form.next === form.current ? "Choose a password different from the current one." : "",
    confirm: form.confirm !== form.next && "The passwords don't match.",
  };

  async function submit() {
    setTouched(true);
    setServerError("");
    if (busy || errors.current || errors.next || errors.confirm) return;
    try {
      await act(() => api.auth.changePassword(form.current, form.next));
      await refresh();
      toast({ tone: "success", title: "Password changed", message: "You stay signed in here; other devices were signed out." });
      setForm(EMPTY); setTouched(false);
      onDone();
    } catch (err) {
      setServerError(err instanceof Error ? err.message : "Couldn't change the password.");
    }
  }

  return (
    <form id={formId} className="space-y-4" noValidate onSubmit={(e) => { e.preventDefault(); void submit(); }}>
      {serverError && <Notice tone="warn">{serverError}</Notice>}
      {(["current", "next", "confirm"] as const).map((k) => (
        <Field key={k} label={{ current: user?.activationPending ? "Activation code" : "Current password", next: "New password", confirm: "Confirm new password" }[k]} error={touched && errors[k]}
          hint={k === "next" ? `At least ${minLength} characters. Avoid common passwords and your username.` : undefined}>
          {(id) => <input id={id} type="password" className="input" autoComplete={k === "current" ? "current-password" : "new-password"}
            aria-invalid={Boolean(touched && errors[k])} value={form[k]} onChange={(e) => setForm({ ...form, [k]: e.target.value })} />}
        </Field>
      ))}
      {formId ? <button type="submit" hidden /> : <Button type="submit" className="w-full" icon="lock-password-line" loading={busy}>{submitLabel}</Button>}
    </form>
  );
}

export function ChangePasswordDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { user } = useAuth();
  const minLength = user?.passwordPolicy?.minLength ?? 10;
  return (
    <Drawer open={open} onClose={onClose} title="Change password" subtitle={`At least ${minLength} characters, different from your current password.`} width="max-w-md"
      footer={<><Button variant="secondary" onClick={onClose}>Cancel</Button><Button type="submit" form="change-own-password" icon="lock-password-line">Change password</Button></>}>
      {open && <ChangePasswordForm formId="change-own-password" onDone={onClose} />}
    </Drawer>
  );
}
