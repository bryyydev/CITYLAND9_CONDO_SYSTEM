// Users & Access Management (Superadmin). Live: /api/admin/users. Every rule is enforced by the
// server; this page only explains protected accounts and prevents obviously invalid input.
import { useEffect, useId, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ConfirmDialog, Drawer, useToast } from "../../components/base/overlays";
import { Badge, Button, Card, EmptyState, ErrorState, Field, Icon, IconButton, Notice, PageHeader, Skeleton, StatusBadge, type Tone, cx } from "../../components/base/ui";
import { PasswordInput, TemporaryBadge, USERNAME_RULE, focusFirstInvalid, useDebounced } from "../../components/feature/AccountFields";
import { ChangePasswordDrawer } from "../../components/feature/ChangePasswordDrawer";
import { useAction, useAsync } from "../../hooks/useAsync";
import { TIME_ZONE, dateTimeLabel } from "../../lib/format";
import { api } from "../../services/api";
import { ApiError } from "../../services/http";
import type { Role, UserAccount, UserQuery } from "../../services/types";

const PER_PAGE = 20;
const ROLE_TONE: Record<Role, Tone> = { super_admin: "neutral", admin: "info", manager: "copper", staff: "ok", accounting: "warn", resident: "info" };
const EMPTY_FILTERS = { q: "", role: "" as UserQuery["role"], status: "" as UserQuery["status"] };

export default function UsersPage() {
  const [filters, setFilters] = useState(EMPTY_FILTERS);
  const [page, setPage] = useState(1);
  const q = useDebounced(filters.q);
  const query: UserQuery = { q, role: filters.role, status: filters.status, page, perPage: PER_PAGE };
  const list = useAsync(() => api.admin.users(query), [q, filters.role, filters.status, page]);
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<{ user: UserAccount; deactivate?: boolean } | null>(null);
  const [resetting, setResetting] = useState<UserAccount | null>(null);
  const [deleting, setDeleting] = useState<UserAccount | null>(null);
  const [ownPassword, setOwnPassword] = useState(false);
  // The classic /users screen redirects here; ?moved=form means a form from an old tab was not saved.
  const [params, setParams] = useSearchParams();
  const movedForm = params.get("moved") === "form";
  const { busy, act } = useAction();
  const toast = useToast();
  const filtered = Boolean(filters.q || filters.role || filters.status);
  const data = list.data;
  const pages = data ? Math.max(Math.ceil(data.total / data.perPage), 1) : 1;

  // Back to page 1 whenever the filters change.
  useEffect(() => setPage(1), [q, filters.role, filters.status]);
  // If a deletion empties the last page, step back.
  useEffect(() => { if (data && page > pages) setPage(pages); }, [data, page, pages]);

  const explain = (reason: string | null) => toast({ tone: "info", title: "Not available for this account", message: reason ?? undefined });

  async function confirmDelete() {
    if (!deleting || busy) return;
    try {
      await act(() => api.admin.deleteUser(deleting.id));
      toast({ tone: "success", title: `Account ${deleting.username} deleted`, message: "Their past actions stay in the audit log." });
      list.reload();
    } catch (err) {
      toast({ tone: "error", title: "Account not deleted", message: (err as Error).message });
    } finally {
      setDeleting(null);
    }
  }

  const actions = (u: UserAccount, stacked = false) => (
    <div className={cx("flex gap-2", stacked ? "w-full flex-wrap" : "justify-end whitespace-nowrap")}>
      <Button size="sm" variant="secondary" icon="shield-keyhole-line" className={cx(stacked && "flex-1", !u.canEdit && "opacity-50")}
        aria-disabled={!u.canEdit} title={u.editBlockedReason ?? `Change the role or status of ${u.username}`}
        aria-label={`Edit access for ${u.username}`} onClick={() => (u.canEdit ? setEditing({ user: u }) : explain(u.editBlockedReason))}>Edit access</Button>
      {u.isSelf ? (
        <Button size="sm" variant="secondary" icon="lock-password-line" className={cx(stacked && "flex-1")} onClick={() => setOwnPassword(true)}>Change my password</Button>
      ) : (
        <Button size="sm" variant="secondary" icon="key-2-line" className={cx(stacked && "flex-1", !u.canResetPassword && "opacity-50")}
          aria-disabled={!u.canResetPassword} title={u.resetBlockedReason ?? `Set a temporary password for ${u.username}`}
          aria-label={`Reset password for ${u.username}`} onClick={() => (u.canResetPassword ? setResetting(u) : explain(u.resetBlockedReason))}>Reset password</Button>
      )}
      <Button size="sm" variant="ghost" icon="delete-bin-6-line"
        className={cx("text-red-700 hover:bg-red-50 hover:text-red-800", stacked && "flex-1", !u.canDelete && "opacity-50")}
        aria-disabled={!u.canDelete} title={u.deleteBlockedReason ?? `Delete ${u.username}`}
        aria-label={`Delete ${u.username}`} onClick={() => (u.canDelete ? setDeleting(u) : explain(u.deleteBlockedReason))}>Delete</Button>
    </div>
  );

  const nameCell = (u: UserAccount) => (
    <>
      <span className={cx("font-semibold", u.active ? "text-ink-900" : "text-ink-500")}>{u.username}</span>
      {u.isSelf && <span className="ml-2 rounded bg-brand-50 px-1.5 py-0.5 text-[11px] font-semibold text-brand-700">You</span>}
      {u.mustChangePassword && u.active && <TemporaryBadge />}
    </>
  );

  return (
    <>
      <PageHeader eyebrow="Administration" title="Users & Access" description="Staff accounts, their roles and sign-in access. Every change is recorded in the audit log."
        actions={<Button icon="user-add-line" onClick={() => setAdding(true)}>Add User</Button>} />

      {movedForm && (
        <div className="mb-4" role="status">
          <Notice tone="warn">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span>Users &amp; Access moved to this screen. The form you sent from the old page was <b>not saved</b>; please make the change here.</span>
              <Button size="sm" variant="ghost" onClick={() => setParams({}, { replace: true })}>Dismiss</Button>
            </div>
          </Notice>
        </div>
      )}

      <Card>
        {/* Search and filters */}
        <form className="flex flex-wrap items-end gap-3 border-b border-ink-100 p-4" role="search" onSubmit={(e) => e.preventDefault()}>
          <Field label="Search" className="min-w-[200px] flex-1 sm:max-w-xs">{(id) => (
            <div className="relative">
              <Icon name="search-line" className="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-ink-500" />
              <input id={id} type="search" className="input pl-9" placeholder="Username" value={filters.q} onChange={(e) => setFilters({ ...filters, q: e.target.value })} />
            </div>)}</Field>
          <Field label="Role" className="w-[calc(50%-0.375rem)] sm:w-44">{(id) => (
            <select id={id} className="input" value={filters.role} onChange={(e) => setFilters({ ...filters, role: e.target.value as UserQuery["role"] })}>
              <option value="">All roles</option>
              {(data?.roles ?? []).map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
            </select>)}</Field>
          <Field label="Status" className="w-[calc(50%-0.375rem)] sm:w-36">{(id) => (
            <select id={id} className="input" value={filters.status} onChange={(e) => setFilters({ ...filters, status: e.target.value as UserQuery["status"] })}>
              <option value="">All</option><option value="active">Active</option><option value="inactive">Inactive</option>
            </select>)}</Field>
          {filtered && <Button type="button" variant="ghost" icon="close-circle-line" onClick={() => setFilters(EMPTY_FILTERS)}>Reset filters</Button>}
          <p className="ml-auto self-center text-[12.5px] text-ink-500" aria-live="polite">
            {data ? `${data.total} account${data.total === 1 ? "" : "s"}${filtered ? " match" : ""}` : " "}
          </p>
        </form>

        {/* States */}
        {list.error ? <ErrorState title="Couldn't load the accounts" message={list.error.message} onRetry={list.reload} />
          : !data ? <Skeleton rows={6} />
          : data.users.length === 0 ? (filtered
            ? <EmptyState icon="search-line" title="No accounts match these filters" action={<Button variant="secondary" onClick={() => setFilters(EMPTY_FILTERS)}>Reset filters</Button>}>Try another username, role or status.</EmptyState>
            : <EmptyState icon="shield-user-line" title="No accounts yet" action={<Button icon="user-add-line" onClick={() => setAdding(true)}>Add User</Button>} />)
          : (
            <div className={cx(list.loading && "opacity-60 transition-opacity")} aria-busy={list.loading || undefined}>
              {/* Table: tablet and up */}
              <div className="hidden overflow-x-auto md:block">
                <table className="w-full border-collapse text-[13.5px]">
                  <caption className="sr-only">User accounts</caption>
                  <thead>
                    <tr>
                      <th scope="col" className="th">Username</th>
                      <th scope="col" className="th">Role</th>
                      <th scope="col" className="th">Status</th>
                      <th scope="col" className="th hidden 2xl:table-cell" title={`Shown in Philippine time (${TIME_ZONE}); stored in UTC`}>Created <span className="font-normal normal-case">(PHT)</span></th>
                      <th scope="col" className="th text-right">Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.users.map((u) => (
                      <tr key={u.id} className={cx("hover:bg-ink-50/60", !u.active && "bg-ink-50/40")}>
                        <td className="border-b border-ink-100 px-4 py-2">{nameCell(u)}<div className="text-[12px] text-ink-500 tabular 2xl:hidden">Created {dateTimeLabel(u.createdAt)} PHT</div></td>
                        <td className="border-b border-ink-100 px-4 py-2"><Badge tone={ROLE_TONE[u.role]} dot={false}>{u.roleLabel}</Badge></td>
                        <td className="border-b border-ink-100 px-4 py-2"><StatusBadge status={u.active ? "Active" : "Inactive"} /></td>
                        <td className="hidden border-b border-ink-100 px-4 py-2 whitespace-nowrap text-ink-600 tabular 2xl:table-cell">{dateTimeLabel(u.createdAt)}</td>
                        <td className="border-b border-ink-100 px-4 py-1.5">{actions(u)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {/* Cards: phones */}
              <ul className="divide-y divide-ink-100 md:hidden">
                {data.users.map((u) => (
                  <li key={u.id} className={cx("space-y-2.5 px-4 py-3", !u.active && "bg-ink-50/40")}>
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="break-words">{nameCell(u)}</p>
                        <p className="text-[12.5px] text-ink-500">Created {dateTimeLabel(u.createdAt)} PHT</p>
                      </div>
                      <StatusBadge status={u.active ? "Active" : "Inactive"} />
                    </div>
                    <Badge tone={ROLE_TONE[u.role]} dot={false}>{u.roleLabel}</Badge>
                    {actions(u, true)}
                  </li>
                ))}
              </ul>
              {/* Pagination */}
              <nav className="flex flex-wrap items-center justify-between gap-3 border-t border-ink-100 px-4 py-3 text-[13px] text-ink-600" aria-label="Pagination">
                <span>Showing {(data.page - 1) * data.perPage + 1}–{(data.page - 1) * data.perPage + data.users.length} of {data.total}</span>
                <div className="flex items-center gap-1">
                  <IconButton icon="arrow-left-s-line" label="Previous page" disabled={page <= 1} className="disabled:opacity-30" onClick={() => setPage(page - 1)} />
                  <span className="px-2">Page {data.page} of {pages}</span>
                  <IconButton icon="arrow-right-s-line" label="Next page" disabled={page >= pages} className="disabled:opacity-30" onClick={() => setPage(page + 1)} />
                </div>
              </nav>
            </div>
          )}
      </Card>

      <p className="mt-3 text-[12.5px] text-ink-500">
        Resident portal logins are created and linked to the owner or tenant under <Link to="/superadmin/resident-accounts" className="font-semibold text-brand-600 hover:underline">Resident Accounts</Link>.
      </p>

      <AddUserDrawer open={adding} roles={data?.creatableRoles ?? []} minLength={data?.minPasswordLength ?? 10}
        onClose={() => setAdding(false)}
        onCreated={(u) => {
          setAdding(false); setFilters(EMPTY_FILTERS); setPage(1); list.reload();
          toast({ tone: "success", title: `Account ${u.username} created`, message: `${u.roleLabel}. Give them the temporary password securely; they choose their own at first sign-in.` });
        }} />
      <EditAccessDrawer target={editing} roles={data?.creatableRoles ?? []} onClose={() => setEditing(null)}
        onSaved={(u, summary) => { setEditing(null); list.reload(); toast({ tone: "success", title: `${u.username} updated`, message: summary }); }} />
      <ResetPasswordDrawer user={resetting} minLength={data?.minPasswordLength ?? 10} onClose={() => setResetting(null)} />
      <ChangePasswordDrawer open={ownPassword} onClose={() => setOwnPassword(false)} />
      <ConfirmDialog open={!!deleting} tone="danger" busy={busy} title={`Delete ${deleting?.username ?? ""}?`} confirmLabel="Delete account"
        onClose={() => setDeleting(null)} onConfirm={confirmDelete}
        message={<>
          <p>The account <b>{deleting?.username}</b> ({deleting?.roleLabel}) will be removed and can no longer sign in. This can't be undone. Their past actions stay in the audit log.</p>
          {deleting?.active && deleting.canEdit && (
            <p className="mt-3 text-[13px]">
              To stop access but keep the account, <button type="button" className="font-semibold text-brand-600 underline hover:text-brand-700"
                onClick={() => { const u = deleting; setDeleting(null); setEditing({ user: u, deactivate: true }); }}>deactivate it instead</button>.
            </p>
          )}
        </>} />
    </>
  );
}

function RoleChoices({ name, roles, value, onChange, error }:
  { name: string; roles: { value: Role; label: string }[]; value: Role | ""; onChange: (r: Role) => void; error: string }) {
  const errId = useId();
  return (
    <fieldset aria-describedby={errId}>
      <legend className="mb-1.5 text-[12.5px] font-semibold text-ink-700">Role</legend>
      <div className="grid gap-2 sm:grid-cols-2">
        {roles.map((r, i) => (
          <label key={r.value} className={cx("flex min-h-11 cursor-pointer items-center gap-3 rounded-lg border px-3 py-2 transition focus-within:ring-2 focus-within:ring-brand-300",
            value === r.value ? "border-brand-500 bg-brand-50" : "border-ink-200 hover:bg-ink-50")}>
            <input type="radio" name={name} value={r.value} className="size-4 accent-brand-600" checked={value === r.value}
              aria-invalid={(Boolean(error) && i === 0) || undefined} onChange={() => onChange(r.value)} />
            <span className="font-medium text-ink-900">{r.label}</span>
          </label>
        ))}
      </div>
      <p id={errId} className="mt-1.5 text-[12px] text-red-600" role={error ? "alert" : undefined}>{error}</p>
    </fieldset>
  );
}

// ------------------------------------------------------------------ Add user
function AddUserDrawer({ open, roles, minLength, onClose, onCreated }:
  { open: boolean; roles: { value: Role; label: string }[]; minLength: number; onClose: () => void; onCreated: (u: UserAccount) => void }) {
  const [form, setForm] = useState({ username: "", password: "", confirm: "", role: "" as Role | "" });
  const [touched, setTouched] = useState(false);
  const [serverFields, setServerFields] = useState<Record<string, string>>({});
  const [serverError, setServerError] = useState("");
  const { busy, act } = useAction();
  const formRef = useRef<HTMLFormElement>(null);
  const errors = {
    username: !USERNAME_RULE.test(form.username) ? "Use 3–80 letters, numbers, dots, dashes or underscores." : serverFields.username ?? "",
    password: form.password.length < minLength ? `Use at least ${minLength} characters.`
      : form.username.length >= 3 && form.password.toLowerCase().includes(form.username.toLowerCase()) ? "The password must not contain the username."
      : serverFields.password ?? "",
    confirm: form.confirm !== form.password ? "The passwords don't match." : "",
    role: !form.role ? "Choose a role." : serverFields.role ?? "",
  };
  const close = () => { if (busy) return; setForm({ username: "", password: "", confirm: "", role: "" }); setTouched(false); setServerFields({}); setServerError(""); onClose(); };

  async function submit() {
    setTouched(true);
    if (busy) return;
    if (errors.username || errors.password || errors.confirm || errors.role) { focusFirstInvalid(formRef); return; }
    setServerError("");
    try {
      const user = await act(() => api.admin.createUser({ username: form.username, password: form.password, role: form.role as Role }));
      setForm({ username: "", password: "", confirm: "", role: "" }); setTouched(false); setServerFields({});
      onCreated(user);
    } catch (err) {
      // Recoverable: keep everything the user typed and show the server's reasons.
      if (err instanceof ApiError && Object.keys(err.fields).length) { setServerFields(err.fields); focusFirstInvalid(formRef); }
      else setServerError((err as Error).message);
    }
  }

  const show = (k: keyof typeof errors) => (touched || serverFields[k] ? errors[k] : "");
  return (
    <Drawer open={open} onClose={close} title="Add user" subtitle="Creates a staff account with a temporary password." width="max-w-lg"
      footer={<><Button variant="secondary" onClick={close} disabled={busy}>Cancel</Button><Button icon="user-add-line" loading={busy} onClick={submit}>Create account</Button></>}>
      <form ref={formRef} className="space-y-4" noValidate onSubmit={(e) => { e.preventDefault(); void submit(); }}>
        {serverError && <Notice tone="warn">{serverError}</Notice>}
        <Field label="Username" error={show("username")} hint="Letters, numbers, dots, dashes or underscores, e.g. maria.santos">{(id) => (
          <input id={id} className="input" autoComplete="off" autoCapitalize="none" spellCheck={false} data-autofocus value={form.username} aria-invalid={Boolean(show("username")) || undefined}
            onChange={(e) => { setForm({ ...form, username: e.target.value.trim() }); setServerFields({ ...serverFields, username: "" }); }} />)}</Field>
        <Field label="Temporary password" error={show("password")} hint={`At least ${minLength} characters, not containing the username. They must choose their own at first sign-in.`}>{(id) => (
          <PasswordInput id={id} value={form.password} invalid={Boolean(show("password"))} onChange={(v) => { setForm({ ...form, password: v }); setServerFields({ ...serverFields, password: "" }); }} />)}</Field>
        <Field label="Confirm password" error={show("confirm")}>{(id) => (
          <PasswordInput id={id} value={form.confirm} invalid={Boolean(show("confirm"))} onChange={(v) => setForm({ ...form, confirm: v })} />)}</Field>
        <RoleChoices name="add-role" roles={roles} value={form.role} error={show("role")}
          onChange={(r) => { setForm({ ...form, role: r }); setServerFields({ ...serverFields, role: "" }); }} />
        {form.role === "super_admin" && <Notice tone="warn">Superadmin accounts have full access, including user management and system settings.</Notice>}
        <p className="text-[12.5px] text-ink-500">Resident portal logins aren't created here: they're linked to the owner or tenant under <b>Resident Accounts</b>.</p>
        <button type="submit" hidden />
      </form>
    </Drawer>
  );
}

// ------------------------------------------------------------------ Change role / status
function EditAccessDrawer({ target, roles, onClose, onSaved }:
  { target: { user: UserAccount; deactivate?: boolean } | null; roles: { value: Role; label: string }[]; onClose: () => void; onSaved: (u: UserAccount, summary: string) => void }) {
  const user = target?.user ?? null;
  const [form, setForm] = useState({ role: "" as Role | "", active: true, reason: "" });
  const [serverFields, setServerFields] = useState<Record<string, string>>({});
  const [serverError, setServerError] = useState("");
  const { busy, act } = useAction();
  const formRef = useRef<HTMLFormElement>(null);
  const statusName = useId();

  useEffect(() => {
    if (!target) return;
    setForm({ role: target.user.role, active: target.deactivate ? false : target.user.active, reason: "" });
    setServerFields({}); setServerError("");
  }, [target]);

  if (!user) return <Drawer open={false} onClose={onClose} title="">{null}</Drawer>;
  const roleChanged = form.role !== user.role;
  const statusChanged = form.active !== user.active;
  const changed = roleChanged || statusChanged;
  const endsSessions = roleChanged || (user.active && !form.active);
  const reasonError = form.reason.length > 500 ? "Keep the note under 500 characters." : serverFields.reason ?? "";
  const close = () => { if (!busy) onClose(); };

  async function submit() {
    if (busy || !user || !changed) return;
    if (reasonError) { focusFirstInvalid(formRef); return; }
    setServerError("");
    try {
      const updated = await act(() => api.admin.updateUser(user.id, {
        ...(roleChanged ? { role: form.role as Role } : {}),
        ...(statusChanged ? { active: form.active } : {}),
        ...(form.reason.trim() ? { reason: form.reason.trim() } : {}),
      }));
      const parts = [roleChanged && `now ${updated.roleLabel}`, statusChanged && (updated.active ? "can sign in again" : "can no longer sign in")].filter(Boolean);
      onSaved(updated, `${parts.join(", ")}.${endsSessions ? " Their open sessions were signed out." : ""}`);
    } catch (err) {
      if (err instanceof ApiError && Object.keys(err.fields).length) { setServerFields(err.fields); focusFirstInvalid(formRef); }
      else setServerError((err as Error).message);
    }
  }

  return (
    <Drawer open={!!target} onClose={close} title="Edit access" subtitle={`${user.username} · currently ${user.roleLabel}, ${user.active ? "active" : "inactive"}`} width="max-w-lg"
      footer={<>
        <Button variant="secondary" onClick={close} disabled={busy}>Cancel</Button>
        <Button icon={form.active ? "save-3-line" : "user-forbid-line"} variant={user.active && !form.active ? "danger" : "primary"} loading={busy}
          disabled={!changed} title={changed ? undefined : "Change the role or status first"} onClick={submit}>
          {user.active && !form.active && !roleChanged ? "Deactivate account" : "Save changes"}
        </Button>
      </>}>
      <form ref={formRef} className="space-y-5" noValidate onSubmit={(e) => { e.preventDefault(); void submit(); }}>
        {serverError && <Notice tone="warn">{serverError}</Notice>}
        <RoleChoices name="edit-role" roles={roles} value={form.role} error={serverFields.role ?? ""}
          onChange={(r) => { setForm({ ...form, role: r }); setServerFields({ ...serverFields, role: "" }); }} />
        {roleChanged && form.role === "super_admin" && <Notice tone="warn">Superadmin accounts have full access, including user management and system settings.</Notice>}

        <fieldset>
          <legend className="mb-1.5 text-[12.5px] font-semibold text-ink-700">Sign-in access</legend>
          <div className="grid gap-2 sm:grid-cols-2">
            {[{ v: true, label: "Active", hint: "Can sign in" }, { v: false, label: "Inactive", hint: "Can't sign in; account and history kept" }].map((o) => (
              <label key={o.label} className={cx("flex min-h-11 cursor-pointer items-start gap-3 rounded-lg border px-3 py-2 transition focus-within:ring-2 focus-within:ring-brand-300",
                form.active === o.v ? (o.v ? "border-brand-500 bg-brand-50" : "border-red-300 bg-red-50") : "border-ink-200 hover:bg-ink-50")}>
                <input type="radio" name={statusName} className="mt-1 size-4 accent-brand-600" checked={form.active === o.v} onChange={() => setForm({ ...form, active: o.v })} />
                <span><span className="block font-medium text-ink-900">{o.label}</span><span className="block text-[12px] text-ink-500">{o.hint}</span></span>
              </label>
            ))}
          </div>
        </fieldset>

        <Field label="Note for the audit log (optional)" error={reasonError} hint="Why the change was made, e.g. “Moved to Accounting, 1 Oct”.">{(id) => (
          <textarea id={id} className="input h-auto min-h-20 py-2" maxLength={600} value={form.reason} aria-invalid={Boolean(reasonError) || undefined}
            onChange={(e) => { setForm({ ...form, reason: e.target.value }); setServerFields({ ...serverFields, reason: "" }); }} />)}</Field>

        {endsSessions && changed && (
          <Notice>
            {user.active && !form.active
              ? <><b>{user.username}</b> is signed out everywhere right away and can't sign in until reactivated.</>
              : <><b>{user.username}</b> is signed out everywhere right away; the next sign-in opens the {roles.find((r) => r.value === form.role)?.label} workspace.</>}
          </Notice>
        )}
        <button type="submit" hidden />
      </form>
    </Drawer>
  );
}

// ------------------------------------------------------------------ Reset another account's password
function ResetPasswordDrawer({ user, minLength, onClose }: { user: UserAccount | null; minLength: number; onClose: () => void }) {
  const [pw, setPw] = useState({ next: "", confirm: "" });
  const [touched, setTouched] = useState(false);
  const [serverError, setServerError] = useState("");
  const { busy, act } = useAction();
  const toast = useToast();
  const formRef = useRef<HTMLFormElement>(null);
  const errors = {
    next: pw.next.length < minLength ? `Use at least ${minLength} characters.`
      : user && user.username.length >= 3 && pw.next.toLowerCase().includes(user.username.toLowerCase()) ? "The password must not contain the username." : "",
    confirm: pw.confirm !== pw.next ? "The passwords don't match." : "",
  };
  const close = () => { if (busy) return; setPw({ next: "", confirm: "" }); setTouched(false); setServerError(""); onClose(); };
  async function submit() {
    setTouched(true);
    if (!user || busy) return;
    if (errors.next || errors.confirm) { focusFirstInvalid(formRef); return; }
    try {
      await act(() => api.admin.resetUserPassword(user.id, pw.next));
      toast({ tone: "success", title: `Temporary password set for ${user.username}`, message: "They were signed out everywhere and must choose a new password at their next sign-in. Give them the temporary password securely." });
      setPw({ next: "", confirm: "" }); setTouched(false); setServerError(""); onClose();
    } catch (err) {
      setServerError((err as Error).message);
    }
  }
  return (
    <Drawer open={!!user} onClose={close} title="Reset password" subtitle={user ? `For ${user.username} (${user.roleLabel})` : ""} width="max-w-md"
      footer={<><Button variant="secondary" onClick={close} disabled={busy}>Cancel</Button><Button icon="key-2-line" loading={busy} onClick={submit}>Set temporary password</Button></>}>
      <form ref={formRef} className="space-y-4" noValidate onSubmit={(e) => { e.preventDefault(); void submit(); }}>
        <Notice>This replaces <b>{user?.username}</b>'s password and signs them out everywhere. They must choose their own password at the next sign-in. To change <b>your own</b> password, use <b>Change password</b> in your account menu.</Notice>
        {serverError && <Notice tone="warn">{serverError}</Notice>}
        <Field label="Temporary password" error={touched && errors.next} hint={`At least ${minLength} characters, not containing the username.`}>{(id) => <PasswordInput id={id} value={pw.next} invalid={touched && !!errors.next} onChange={(v) => setPw({ ...pw, next: v })} />}</Field>
        <Field label="Confirm temporary password" error={touched && errors.confirm}>{(id) => <PasswordInput id={id} value={pw.confirm} invalid={touched && !!errors.confirm} onChange={(v) => setPw({ ...pw, confirm: v })} />}</Field>
        <button type="submit" hidden />
      </form>
    </Drawer>
  );
}
