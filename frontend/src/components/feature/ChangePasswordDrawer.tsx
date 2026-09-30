import { useState } from "react";
import { useAction } from "../../hooks/useAsync";
import { api } from "../../services/api";
import { Drawer, useToast } from "../base/overlays";
import { Button, Field, Notice } from "../base/ui";

const EMPTY = { current: "", next: "", confirm: "" };

/** Same rules as the server: current password must match, at least 8 characters, must change. */
export function ChangePasswordDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [form, setForm] = useState(EMPTY);
  const [touched, setTouched] = useState(false);
  const [serverError, setServerError] = useState("");
  const { busy, act } = useAction();
  const toast = useToast();
  const errors = {
    current: !form.current && "Enter your current password.",
    next: form.next.length < 8 ? "Use at least 8 characters." : form.next === form.current ? "Choose a password different from the current one." : "",
    confirm: form.confirm !== form.next && "The passwords don't match.",
  };
  const close = () => { setForm(EMPTY); setTouched(false); setServerError(""); onClose(); };

  async function submit() {
    setTouched(true);
    setServerError("");
    if (errors.current || errors.next || errors.confirm) return;
    try {
      await act(() => api.auth.changePassword(form.current, form.next));
      toast({ tone: "success", title: "Password changed", message: "Use your new password the next time you sign in." });
      close();
    } catch (err) {
      setServerError(err instanceof Error ? err.message : "Couldn't change the password.");
    }
  }

  return (
    <Drawer open={open} onClose={close} title="Change password" subtitle="At least 8 characters, different from your current password." width="max-w-md"
      footer={<><Button variant="secondary" onClick={close}>Cancel</Button><Button onClick={submit} loading={busy} icon="lock-password-line">Change password</Button></>}>
      <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); void submit(); }}>
        {serverError && <Notice tone="warn">{serverError}</Notice>}
        {(["current", "next", "confirm"] as const).map((k) => (
          <Field key={k} label={{ current: "Current password", next: "New password", confirm: "Confirm new password" }[k]} error={touched && errors[k]}>
            {(id) => <input id={id} type="password" className="input" autoComplete={k === "current" ? "current-password" : "new-password"}
              aria-invalid={Boolean(touched && errors[k])} value={form[k]} onChange={(e) => setForm({ ...form, [k]: e.target.value })} />}
          </Field>
        ))}
        <button type="submit" hidden />
      </form>
    </Drawer>
  );
}
