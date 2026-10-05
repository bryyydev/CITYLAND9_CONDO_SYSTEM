// Resident Accounts (Superadmin): resident portal logins, each linked to a unit and, ideally, to the
// owner or tenant record so access ends by itself at move-out. Live: /api/admin/resident-accounts.
// Every rule is enforced by the server; this page explains the state and prevents obvious mistakes.
import { type ReactNode, useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ConfirmDialog, Drawer, useToast } from "../../components/base/overlays";
import { Badge, Button, Card, EmptyState, ErrorState, Field, Icon, IconButton, Notice, PageHeader, Skeleton, Tabs, type Tone, cx } from "../../components/base/ui";
import { PasswordInput, TemporaryBadge, USERNAME_RULE, focusFirstInvalid, passwordProblem, useDebounced } from "../../components/feature/AccountFields";
import { useAction, useAsync } from "../../hooks/useAsync";
import { dateTimeLabel } from "../../lib/format";
import { api } from "../../services/api";
import { ApiError } from "../../services/http";
import type { PortalPerson, PortalState, ResidentAccount, ResidentAccountOptions, ResidentAccountStatus } from "../../services/types";
import { ProvisionDrawer, type ProvisionTarget, ReissueDrawer } from "../../components/feature/ResidentProvisioning";

const PER_PAGE = 20;
const STATUS_META: Record<ResidentAccountStatus, { label: string; tone: Tone }> = {
  active: { label: "Active", tone: "ok" },
  pending: { label: "Pending activation", tone: "info" },
  ended: { label: "Access ended", tone: "warn" },
  inactive: { label: "Deactivated", tone: "neutral" },
  unlinked: { label: "No unit", tone: "bad" },
};
type StatusFilter = ResidentAccountStatus | "all";

const PORTAL_META: Record<PortalState, { label: string; tone: Tone }> = {
  none: { label: "No portal account", tone: "neutral" },
  pending: { label: "Pending activation", tone: "info" },
  active: { label: "Active", tone: "ok" },
  disabled: { label: "Disabled", tone: "bad" },
  ended: { label: "Access ended", tone: "warn" },
};

export default function ResidentAccountsPage() {
  const [params, setParams] = useSearchParams();
  const view = params.get("view") === "people" ? "people" : "accounts";
  const setView = (v: "people" | "accounts", q?: string) => {
    const next = new URLSearchParams();
    if (v === "people") next.set("view", "people");
    if (q) next.set("q", q);
    setParams(next, { replace: true });
  };
  return (
    <>
      <PageHeader eyebrow="Administration" title="Resident Accounts"
        description="Resident portal logins. The system generates the username and a one-time activation code; residents never sign up themselves. Every change is recorded in the audit log."
        actions={view === "accounts" && <Button icon="user-add-line" onClick={() => setView("people")}>Generate resident account</Button>} />
      <div className="mb-4"><Tabs value={view} onChange={(v) => setView(v)} tabs={[{ key: "accounts", label: "Portal accounts" }, { key: "people", label: "Owners & tenants" }]} /></div>
      {view === "people" ? <PeoplePanel onOpenAccount={(username) => setView("accounts", username)} /> : <AccountsPanel initialQuery={params.get("q") ?? ""} />}
    </>
  );
}

// ------------------------------------------------------------------ owners & tenants -> generate accounts
function PeoplePanel({ onOpenAccount }: { onOpenAccount: (username: string) => void }) {
  const [search, setSearch] = useState("");
  const [state, setState] = useState<PortalState | "all">("none");
  const q = useDebounced(search);
  const list = useAsync(() => api.admin.residentPeople({ q, state: state === "all" ? "" : state }), [q, state]);
  const [target, setTarget] = useState<ProvisionTarget | null>(null);
  const counts = list.data?.counts;
  const tabs: { key: PortalState | "all"; label: string; count?: number }[] = [
    { key: "none", label: "No portal account", count: counts?.none },
    { key: "pending", label: "Pending activation", count: counts?.pending },
    { key: "active", label: "Active", count: counts?.active },
    { key: "disabled", label: "Disabled", count: counts?.disabled },
    { key: "all", label: "All" },
  ];
  const action = (p: PortalPerson) => p.state === "none"
    ? <Button size="sm" icon="user-add-line" onClick={() => setTarget({ personType: p.personType, personId: p.personId, name: p.name, unitNo: p.unitNo })}>Generate account</Button>
    : p.account ? <Button size="sm" variant="secondary" icon="arrow-right-line" onClick={() => onOpenAccount(p.account!.username)}>Open {p.account.username}</Button> : null;
  return (
    <>
      <Notice tone="info">Only current owners and tenants of active residential units are listed. An account is created only when you choose it here (or when adding an owner/tenant in the Units Directory); nothing is created in bulk. Activation codes expire after {list.data?.activationDays ?? 7} days.</Notice>
      <Card className="mt-4">
        <div className="flex flex-wrap items-end gap-3 border-b border-ink-100 p-4">
          <Field label="Search" className="min-w-[200px] flex-1 sm:max-w-xs">{(id) => <input id={id} type="search" className="input" placeholder="Name or unit" value={search} onChange={(e) => setSearch(e.target.value)} />}</Field>
          <div className="min-w-0 max-w-full"><Tabs tabs={tabs} value={state} onChange={setState} /></div>
        </div>
        {list.error ? <ErrorState title="Couldn't load the owners and tenants" message={list.error.message} onRetry={list.reload} />
          : !list.data ? <Skeleton rows={6} />
          : list.data.people.length === 0 ? <EmptyState icon="user-search-line" title="Nobody here">{state === "none" ? "Every current owner and tenant already has a portal account." : "No owners or tenants match."}</EmptyState>
          : (
            <div className={cx("overflow-x-auto", list.loading && "opacity-60")}>
              <table className="w-full min-w-[640px] border-collapse text-[13.5px]">
                <thead><tr><th className="th">Person</th><th className="th">Unit</th><th className="th">Portal account</th><th className="th text-right">Action</th></tr></thead>
                <tbody>
                  {list.data.people.map((p) => (
                    <tr key={`${p.personType}:${p.personId}`} className="align-top">
                      <td className="border-b border-ink-100 px-4 py-2.5"><b className="text-ink-900">{p.name}</b><span className="block text-[12.5px] text-ink-500">{p.personType}{p.email ? ` · ${p.email}` : ""}</span></td>
                      <td className="border-b border-ink-100 px-4 py-2.5 font-semibold tabular">{p.unitNo}</td>
                      <td className="border-b border-ink-100 px-4 py-2.5"><Badge tone={PORTAL_META[p.state].tone}>{PORTAL_META[p.state].label}</Badge>{p.account && <span className="mt-0.5 block text-[12.5px] text-ink-500">{p.account.username}</span>}</td>
                      <td className="border-b border-ink-100 px-4 py-2 text-right">{action(p)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
      </Card>
      <ProvisionDrawer target={target} onClose={() => setTarget(null)} onDone={() => list.reload()} />
    </>
  );
}

// ------------------------------------------------------------------ portal accounts
function AccountsPanel({ initialQuery }: { initialQuery: string }) {
  const [search, setSearch] = useState(initialQuery);
  const [status, setStatus] = useState<StatusFilter>("all");
  const [page, setPage] = useState(1);
  const q = useDebounced(search);
  const list = useAsync(() => api.admin.residentAccounts({ q, status: status === "all" ? "" : status, page, perPage: PER_PAGE }), [q, status, page]);
  const [editing, setEditing] = useState<ResidentAccount | "new" | null>(null);
  const [reissuing, setReissuing] = useState<ResidentAccount | null>(null);
  const [toggling, setToggling] = useState<ResidentAccount | null>(null);
  const [linksOf, setLinksOf] = useState<ResidentAccount | null>(null);
  const [params, setParams] = useSearchParams();
  const movedForm = params.get("moved") === "form";
  const toast = useToast();
  const data = list.data;
  const pages = data ? Math.max(Math.ceil(data.total / data.perPage), 1) : 1;
  const counts = data?.counts;

  useEffect(() => setPage(1), [q, status]);
  useEffect(() => { if (data && page > pages) setPage(pages); }, [data, page, pages]);

  const tabs: { key: StatusFilter; label: string; count?: number }[] = [
    { key: "all", label: "All", count: counts ? counts.active + counts.pending + counts.ended + counts.inactive + counts.unlinked : undefined },
    ...(["active", "pending", "ended", "inactive", "unlinked"] as const)
      .filter((s) => s !== "unlinked" || (counts?.unlinked ?? 0) > 0 || status === "unlinked")
      .map((s) => ({ key: s, label: STATUS_META[s].label, count: counts?.[s] })),
  ];

  const actions = (a: ResidentAccount, stacked = false) => (
    <div className={cx("flex gap-2", stacked ? "w-full flex-wrap" : "justify-end whitespace-nowrap")}>
      <Button size="sm" variant="secondary" icon="links-line" className={cx(stacked && "flex-1")} aria-label={`Edit unit and link for ${a.username}`}
        title={`Change the unit, owner/tenant link or name of ${a.username}`} onClick={() => setEditing(a)}>{a.unit ? "Edit link" : "Link to unit"}</Button>
      <Button size="sm" variant="secondary" icon="key-2-line" className={cx(stacked && "flex-1")} aria-label={`New activation code for ${a.username}`}
        title={`Issue a new one-time activation code for ${a.username}`} disabled={!a.active} onClick={() => setReissuing(a)}>New activation code</Button>
      {a.links.length > 0 && <Button size="sm" variant="ghost" icon="building-2-line" className={cx(stacked && "flex-1")} aria-label={`Units of ${a.username}`} onClick={() => setLinksOf(a)}>Units ({a.links.filter((l) => l.active).length})</Button>}
      {a.unit && (
        <Button size="sm" variant="ghost" icon={a.active ? "user-forbid-line" : "user-follow-line"}
          className={cx(a.active && "text-red-700 hover:bg-red-50 hover:text-red-800", stacked && "flex-1")}
          aria-label={`${a.active ? "Deactivate" : "Reactivate"} ${a.username}`} onClick={() => setToggling(a)}>{a.active ? "Deactivate" : "Reactivate"}</Button>
      )}
    </div>
  );

  const nameCell = (a: ResidentAccount) => (
    <>
      <span className={cx("font-semibold", a.status === "active" ? "text-ink-900" : "text-ink-500")}>{a.displayName}</span>
      {a.mustChangePassword && a.active && a.status !== "pending" && <TemporaryBadge />}
      <span className="block text-[12.5px] text-ink-500">{a.username}</span>
    </>
  );
  const linkCell = (a: ResidentAccount) => a.linked
    ? <><Badge tone="ok">{a.personType}</Badge><span className="mt-0.5 block text-[12.5px] text-ink-600">{a.linkedName}{a.linkedStatus && a.linkedStatus !== "Current" ? ` · ${a.linkedStatus}` : ""}</span></>
    : a.unit ? <span title="Not linked to an owner or tenant record: access won't end automatically at move-out."><Badge tone="warn">Not linked</Badge><span className="mt-0.5 block text-[12.5px] text-ink-500">{a.personType}</span></span>
    : <span className="text-ink-400">—</span>;
  const statusCell = (a: ResidentAccount) => (
    <>
      <Badge tone={STATUS_META[a.status].tone}>{STATUS_META[a.status].label}</Badge>
      {a.statusReason && a.status !== "inactive" && <span className="mt-1 block max-w-xs text-[12px] leading-snug text-ink-500">{a.statusReason}</span>}
      {a.status === "pending" && a.activationExpiresAt && !a.activationExpired && <span className="mt-0.5 block text-[12px] text-ink-500">Code valid until {dateTimeLabel(a.activationExpiresAt)}</span>}
    </>
  );

  return (
    <>
      {movedForm && (
        <div className="mb-4" role="status">
          <Notice tone="warn">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span>Resident Accounts moved to this screen. The form you sent from the old page was <b>not saved</b>; please make the change here.</span>
              <Button size="sm" variant="ghost" onClick={() => setParams({}, { replace: true })}>Dismiss</Button>
            </div>
          </Notice>
        </div>
      )}
      {(counts?.notLinkedToPerson ?? 0) > 0 && (
        <div className="mb-4">
          <Notice tone="warn" title={`${counts!.notLinkedToPerson} account${counts!.notLinkedToPerson === 1 ? " is" : "s are"} not linked to an owner or tenant`}>
            Those residents keep access to their unit even after moving out, and can't update their own contact details. Use <b>Edit link</b> to connect them to the owner or tenant record.
          </Notice>
        </div>
      )}

      <Card>
        <div className="flex flex-wrap items-end gap-3 border-b border-ink-100 p-4">
          <Field label="Search" className="min-w-[200px] flex-1 sm:max-w-xs">{(id) => (
            <div className="relative" role="search">
              <Icon name="search-line" className="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-ink-500" />
              <input id={id} type="search" className="input pl-9" placeholder="Name, username or unit" value={search} onChange={(e) => setSearch(e.target.value)} />
            </div>)}</Field>
          <div className="min-w-0 max-w-full"><Tabs tabs={tabs} value={status} onChange={setStatus} /></div>
          <p className="ml-auto self-center text-[12.5px] text-ink-500" aria-live="polite">
            {data ? `${data.total} account${data.total === 1 ? "" : "s"}${search || status !== "all" ? " match" : ""}` : " "}
          </p>
        </div>

        {list.error ? <ErrorState title="Couldn't load the resident accounts" message={list.error.message} onRetry={list.reload} />
          : !data ? <Skeleton rows={6} />
          : data.accounts.length === 0 ? (search || status !== "all"
            ? <EmptyState icon="search-line" title="No accounts match" action={<Button variant="secondary" onClick={() => { setSearch(""); setStatus("all"); }}>Clear filters</Button>}>Try another name, username or unit.</EmptyState>
            : <EmptyState icon="user-heart-line" title="No resident accounts yet">
                Open <b>Owners &amp; tenants</b> and choose <b>Generate account</b> for a resident, or tick “Create resident portal account” when adding an owner or tenant in the Units Directory.
              </EmptyState>)
          : (
            <div className={cx(list.loading && "opacity-60 transition-opacity")} aria-busy={list.loading || undefined}>
              <div className="hidden overflow-x-auto md:block">
                <table className="w-full border-collapse text-[13.5px]">
                  <caption className="sr-only">Resident portal accounts</caption>
                  <thead>
                    <tr>
                      <th scope="col" className="th">Resident</th>
                      <th scope="col" className="th">Unit</th>
                      <th scope="col" className="th">Owner / tenant record</th>
                      <th scope="col" className="th">Portal access</th>
                      <th scope="col" className="th text-right">Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.accounts.map((a) => (
                      <tr key={a.id} className={cx("align-top hover:bg-ink-50/60", a.status !== "active" && "bg-ink-50/40")}>
                        <td className="border-b border-ink-100 px-4 py-2.5">{nameCell(a)}</td>
                        <td className="border-b border-ink-100 px-4 py-2.5 font-semibold text-ink-800 tabular">{a.links.filter((l) => l.active).map((l) => l.unitNo).join(", ") || a.unit?.unitNo || <span className="font-normal text-ink-400">—</span>}</td>
                        <td className="border-b border-ink-100 px-4 py-2.5">{linkCell(a)}</td>
                        <td className="border-b border-ink-100 px-4 py-2.5">{statusCell(a)}</td>
                        <td className="border-b border-ink-100 px-4 py-2">{actions(a)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <ul className="divide-y divide-ink-100 md:hidden">
                {data.accounts.map((a) => (
                  <li key={a.id} className={cx("space-y-2.5 px-4 py-3", a.status !== "active" && "bg-ink-50/40")}>
                    <div className="flex items-start justify-between gap-3">
                      <p className="min-w-0 break-words">{nameCell(a)}</p>
                      <span className="shrink-0 font-semibold text-ink-800 tabular">{a.unit?.unitNo ?? "No unit"}</span>
                    </div>
                    <div className="flex flex-wrap items-start gap-x-6 gap-y-2">
                      <div>{linkCell(a)}</div>
                      <div>{statusCell(a)}</div>
                    </div>
                    <p className="text-[12px] text-ink-500">Created {dateTimeLabel(a.createdAt)} PHT</p>
                    {actions(a, true)}
                  </li>
                ))}
              </ul>
              <nav className="flex flex-wrap items-center justify-between gap-3 border-t border-ink-100 px-4 py-3 text-[13px] text-ink-600" aria-label="Pagination">
                <span>Showing {(data.page - 1) * data.perPage + 1}–{(data.page - 1) * data.perPage + data.accounts.length} of {data.total}</span>
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
        Staff logins are managed under <Link to="/superadmin/users" className="font-semibold text-brand-600 hover:underline">Users &amp; Access</Link>.
        Owners and tenants themselves are added in the Units Directory.
      </p>

      <AccountDrawer target={editing} minLength={data?.minPasswordLength ?? 10} onClose={() => setEditing(null)}
        onSaved={(a, created) => {
          setEditing(null);
          if (created) { setSearch(""); setStatus("all"); setPage(1); }
          list.reload();
          toast(created
            ? { tone: "success", title: `Account ${a.username} created`, message: `For ${a.displayName}, unit ${a.unit?.unitNo}. Give them the temporary password securely; they choose their own at first sign-in.` }
            : { tone: "success", title: `${a.username} updated`, message: a.statusReason ?? "Saved." });
        }} />
      <ReissueDrawer account={reissuing} onClose={() => setReissuing(null)} onDone={() => list.reload()} />
      <LinksDrawer account={linksOf} onClose={() => setLinksOf(null)} onChanged={(a) => { setLinksOf(a); list.reload(); }} />
      <ToggleAccessDialog account={toggling} onClose={() => setToggling(null)}
        onDone={(a) => {
          setToggling(null); list.reload();
          toast({ tone: "success", title: a.active ? `${a.username} reactivated` : `${a.username} deactivated`,
            message: a.active ? (a.statusReason ?? "They can sign in to the portal again.") : "They were signed out everywhere and can't sign in until reactivated." });
        }} />
    </>
  );
}

// ------------------------------------------------------------------ create / edit (unit, owner-tenant link, name)
const NOT_LINKED = "none";
const personKey = (type: string, id: number) => `${type}:${id}`;

function AccountDrawer({ target, minLength, onClose, onSaved }:
  { target: ResidentAccount | "new" | null; minLength: number; onClose: () => void; onSaved: (a: ResidentAccount, created: boolean) => void }) {
  const isNew = target === "new";
  const account = target && target !== "new" ? target : null;
  const options = useAsync<ResidentAccountOptions | null>(() => (target ? api.admin.residentAccountOptions() : Promise.resolve(null)), [target]);
  const blank = { username: "", password: "", confirm: "", unitId: "" as number | "", person: NOT_LINKED, personType: "Owner" as "Owner" | "Tenant", displayName: "", reason: "" };
  const [form, setForm] = useState(blank);
  const [nameTouched, setNameTouched] = useState(false);
  const [touched, setTouched] = useState(false);
  const [serverFields, setServerFields] = useState<Record<string, string>>({});
  const [serverError, setServerError] = useState("");
  const { busy, act } = useAction();
  const formRef = useRef<HTMLFormElement>(null);

  useEffect(() => {
    if (!target) return;
    setForm(account ? {
      ...blank, unitId: account.unit?.id ?? "", personType: account.personType, displayName: account.unit ? account.displayName : "",
      person: account.personId ? personKey(account.personType, account.personId) : NOT_LINKED,
    } : blank);
    setNameTouched(Boolean(account)); setTouched(false); setServerFields({}); setServerError("");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target]);

  if (!target) return <Drawer open={false} onClose={onClose} title="">{null}</Drawer>;

  const unit = options.data?.units.find((u) => u.id === form.unitId) ?? null;
  const people = unit?.people ?? [];
  // Keep showing an existing link whose owner/tenant is no longer current (moved out), so it can be kept or replaced.
  const keptLink = account?.personId && account.unit?.id === form.unitId && !people.some((p) => personKey(p.personType, p.id) === personKey(account.personType, account.personId!))
    ? { key: personKey(account.personType, account.personId), name: account.linkedName ?? `${account.personType} record #${account.personId}`, personType: account.personType } : null;
  const selected = people.find((p) => personKey(p.personType, p.id) === form.person);
  const linkedType = selected?.personType ?? (keptLink && form.person === keptLink.key ? keptLink.personType : null);
  const personId = form.person === NOT_LINKED ? null : Number(form.person.split(":")[1]);
  const personType = linkedType ?? form.personType;
  const set = (patch: Partial<typeof form>, clear: string[] = []) => {
    setForm({ ...form, ...patch });
    if (clear.length) setServerFields(Object.fromEntries(Object.entries(serverFields).filter(([k]) => !clear.includes(k))));
  };
  const choosePerson = (key: string) => {
    const p = people.find((x) => personKey(x.personType, x.id) === key);
    set({ person: key, ...(p && !nameTouched ? { displayName: p.name } : {}) }, ["personId", "displayName"]);
  };

  const errors = {
    username: !isNew ? "" : !USERNAME_RULE.test(form.username) ? "Use 3–80 letters, numbers, dots, dashes or underscores." : serverFields.username ?? "",
    password: !isNew ? "" : passwordProblem(form.password, form.username, minLength) || (serverFields.password ?? ""),
    confirm: isNew && form.confirm !== form.password ? "The passwords don't match." : "",
    unitId: !form.unitId ? "Choose the unit." : serverFields.unitId ?? "",
    personId: serverFields.personId ?? serverFields.personType ?? "",
    displayName: !form.displayName.trim() && form.person === NOT_LINKED ? "Enter the resident's name." : form.displayName.length > 200 ? "Keep the name under 200 characters." : serverFields.displayName ?? "",
    reason: form.reason.length > 500 ? "Keep the note under 500 characters." : serverFields.reason ?? "",
  };
  const show = (k: keyof typeof errors) => (touched || serverFields[k] || (k === "personId" && serverFields.personType) ? errors[k] : "");
  const link = { unitId: Number(form.unitId), personType, personId, displayName: form.displayName.trim() || (selected?.name ?? "") };
  const changed = isNew || !account || account.unit?.id !== link.unitId || account.personId !== link.personId
    || account.personType !== link.personType || account.displayName !== link.displayName;
  const close = () => { if (!busy) onClose(); };

  async function submit() {
    setTouched(true);
    if (busy || !changed) return;
    if (Object.values(errors).some(Boolean)) { focusFirstInvalid(formRef); return; }
    setServerError("");
    try {
      const saved = isNew
        ? await act(() => api.admin.createResidentAccount({ username: form.username, password: form.password, ...link }))
        : await act(() => api.admin.updateResidentAccount(account!.id, { ...link, ...(form.reason.trim() ? { reason: form.reason.trim() } : {}) }));
      onSaved(saved, isNew);
    } catch (err) {
      if (err instanceof ApiError && Object.keys(err.fields).length) { setServerFields(err.fields); focusFirstInvalid(formRef); }
      else setServerError((err as Error).message);
    }
  }

  const choice = (key: string, title: ReactNode, hint: ReactNode, disabled = false) => (
    <label key={key} className={cx("flex min-h-11 items-start gap-3 rounded-lg border px-3 py-2 transition focus-within:ring-2 focus-within:ring-brand-300",
      disabled ? "cursor-not-allowed border-ink-100 bg-ink-50 opacity-70" : "cursor-pointer",
      !disabled && (form.person === key ? "border-brand-500 bg-brand-50" : "border-ink-200 hover:bg-ink-50"))}>
      <input type="radio" name="ra-person" className="mt-1 size-4 accent-brand-600" checked={form.person === key} disabled={disabled}
        aria-invalid={(Boolean(show("personId")) && form.person === key) || undefined} onChange={() => choosePerson(key)} />
      <span className="min-w-0"><span className="block font-medium text-ink-900">{title}</span><span className="block text-[12px] text-ink-500">{hint}</span></span>
    </label>
  );

  return (
    <Drawer open onClose={close} width="max-w-lg"
      title={isNew ? "Add resident account" : account?.unit ? "Edit unit and link" : "Link to a unit"}
      subtitle={isNew ? "Creates a resident portal login with a temporary password." : `${account?.username} · ${account?.unit ? `unit ${account.unit.unitNo}` : "not linked to a unit yet"}`}
      footer={<>
        <Button variant="secondary" onClick={close} disabled={busy}>Cancel</Button>
        <Button icon={isNew ? "user-add-line" : "save-3-line"} loading={busy} disabled={!changed} title={changed ? undefined : "Change something first"} onClick={submit}>
          {isNew ? "Create account" : "Save changes"}
        </Button>
      </>}>
      <form ref={formRef} className="space-y-5" noValidate onSubmit={(e) => { e.preventDefault(); void submit(); }}>
        {serverError && <Notice tone="warn">{serverError}</Notice>}
        {options.error ? <ErrorState title="Couldn't load the units" message={options.error.message} onRetry={options.reload} />
          : !options.data ? <Skeleton rows={4} />
          : (
            <>
              <Field label="Unit" error={show("unitId")}>{(id) => (
                <select id={id} className="input" data-autofocus={!isNew || undefined} value={form.unitId} aria-invalid={Boolean(show("unitId")) || undefined}
                  onChange={(e) => set({ unitId: e.target.value ? Number(e.target.value) : "", person: NOT_LINKED, ...(nameTouched ? {} : { displayName: "" }) }, ["unitId", "personId"])}>
                  <option value="">Choose a unit…</option>
                  {options.data!.units.map((u) => <option key={u.id} value={u.id}>{u.unitNo}{u.people.length ? ` · ${u.people.map((p) => p.name).join(", ")}` : ""}</option>)}
                </select>)}</Field>

              {form.unitId !== "" && (
                <fieldset>
                  <legend className="mb-1.5 text-[12.5px] font-semibold text-ink-700">Who is this login for?</legend>
                  <div className="space-y-2">
                    {people.map((p) => {
                      const takenBy = p.account && p.account !== account?.username ? p.account : null;
                      return choice(personKey(p.personType, p.id), <>{p.name} <span className="font-normal text-ink-500">· {p.personType}</span></>,
                        takenBy ? `Already has a portal account (${takenBy})` : `Current ${p.personType.toLowerCase()}: access ends automatically at move-out`, Boolean(takenBy));
                    })}
                    {keptLink && choice(keptLink.key, <>{keptLink.name} <span className="font-normal text-ink-500">· {keptLink.personType}</span></>,
                      "Current link, but no longer a current owner/tenant of this unit: portal access has ended")}
                    {people.length === 0 && !keptLink && <p className="text-[12.5px] text-ink-500">This unit has no current owner or tenant on record. Add them in the Units Directory to link the login, or continue without a link.</p>}
                    {choice(NOT_LINKED, "Not linked to an owner/tenant record", "E.g. a family member. Access does NOT end automatically at move-out.")}
                  </div>
                  {show("personId") && <p className="mt-1.5 text-[12px] text-red-600" role="alert">{show("personId")}</p>}
                  {form.person === NOT_LINKED && (
                    <div className="mt-3 grid gap-2 sm:grid-cols-2">
                      {(["Owner", "Tenant"] as const).map((t) => (
                        <label key={t} className={cx("flex min-h-11 cursor-pointer items-center gap-3 rounded-lg border px-3 py-2 transition focus-within:ring-2 focus-within:ring-brand-300",
                          form.personType === t ? "border-brand-500 bg-brand-50" : "border-ink-200 hover:bg-ink-50")}>
                          <input type="radio" name="ra-type" className="size-4 accent-brand-600" checked={form.personType === t} onChange={() => set({ personType: t })} />
                          <span className="font-medium text-ink-900">Lives as {t === "Owner" ? "an owner" : "a tenant"}</span>
                        </label>
                      ))}
                    </div>
                  )}
                </fieldset>
              )}

              <Field label="Name shown in the portal" error={show("displayName")}
                hint={form.person === NOT_LINKED ? "The resident's full name." : "Filled in from the owner/tenant record; change it if needed."}>{(id) => (
                <input id={id} className="input" maxLength={220} value={form.displayName} aria-invalid={Boolean(show("displayName")) || undefined}
                  placeholder={selected?.name ?? ""} onChange={(e) => { setNameTouched(true); set({ displayName: e.target.value }, ["displayName"]); }} />)}</Field>
            </>
          )}

        {isNew && (
          <div className="space-y-4 border-t border-ink-100 pt-5">
            <Field label="Username" error={show("username")} hint="Letters, numbers, dots, dashes or underscores, e.g. owner.1201">{(id) => (
              <input id={id} className="input" autoComplete="off" autoCapitalize="none" spellCheck={false} value={form.username} aria-invalid={Boolean(show("username")) || undefined}
                onChange={(e) => set({ username: e.target.value.trim() }, ["username"])} />)}</Field>
            <Field label="Temporary password" error={show("password")} hint={`At least ${minLength} characters, not containing the username. They choose their own at first sign-in.`}>{(id) => (
              <PasswordInput id={id} value={form.password} invalid={Boolean(show("password"))} onChange={(v) => set({ password: v }, ["password"])} />)}</Field>
            <Field label="Confirm password" error={show("confirm")}>{(id) => (
              <PasswordInput id={id} value={form.confirm} invalid={Boolean(show("confirm"))} onChange={(v) => set({ confirm: v })} />)}</Field>
          </div>
        )}

        {!isNew && (
          <>
            <Field label="Note for the audit log (optional)" error={show("reason")} hint="Why the change was made, e.g. “Unit sold to new owner, 1 Oct”.">{(id) => (
              <textarea id={id} className="input h-auto min-h-20 py-2" maxLength={600} value={form.reason} aria-invalid={Boolean(show("reason")) || undefined}
                onChange={(e) => set({ reason: e.target.value }, ["reason"])} />)}</Field>
            {account?.unit && (account.unit.id !== link.unitId || account.personId !== link.personId) && (
              <Notice><b>{account.username}</b> is signed out everywhere right away; the next sign-in opens the portal for the new unit/link.</Notice>
            )}
          </>
        )}
        <button type="submit" hidden />
      </form>
    </Drawer>
  );
}

// ------------------------------------------------------------------ deactivate / reactivate
function ToggleAccessDialog({ account, onClose, onDone }: { account: ResidentAccount | null; onClose: () => void; onDone: (a: ResidentAccount) => void }) {
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");
  const { busy, act } = useAction();
  useEffect(() => { setReason(""); setError(""); }, [account]);
  if (!account) return null;
  const deactivate = account.active;
  async function confirm() {
    if (busy || !account) return;
    try {
      onDone(await act(() => api.admin.updateResidentAccount(account.id, { active: !deactivate, ...(reason.trim() ? { reason: reason.trim() } : {}) })));
    } catch (err) {
      setError((err as Error).message);
    }
  }
  return (
    <ConfirmDialog open tone={deactivate ? "danger" : "primary"} busy={busy} onClose={() => { if (!busy) onClose(); }} onConfirm={confirm}
      title={`${deactivate ? "Deactivate" : "Reactivate"} ${account.username}?`} confirmLabel={deactivate ? "Deactivate account" : "Reactivate account"}
      message={<div className="space-y-3">
        <p>{deactivate
          ? <><b>{account.displayName}</b> (unit {account.unit?.unitNo}) is signed out everywhere and can't use the resident portal until reactivated. The account and its history are kept.</>
          : <><b>{account.displayName}</b> (unit {account.unit?.unitNo}) can sign in to the resident portal again.</>}</p>
        {!deactivate && account.linked && account.linkedStatus !== "Current" && (
          <Notice tone="warn">The linked {account.personType.toLowerCase()} is no longer current, so access stays ended. Use <b>Edit link</b> to link the current owner/tenant.</Notice>
        )}
        <label className="block text-[12.5px] font-semibold text-ink-700">Note for the audit log (optional)
          <textarea className="input mt-1.5 h-auto min-h-16 py-2 font-normal" maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)} />
        </label>
        {error && <p className="text-[12.5px] text-red-600" role="alert">{error}</p>}
      </div>} />
  );
}

// ------------------------------------------------------------------ units of an account (one account per person)
function LinksDrawer({ account, onClose, onChanged }: { account: ResidentAccount | null; onClose: () => void; onChanged: (a: ResidentAccount) => void }) {
  const [ending, setEnding] = useState<number | null>(null);
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");
  const { busy, act } = useAction();
  const toast = useToast();
  async function end(linkId: number) {
    if (!account || busy) return;
    try {
      const a = await act(() => api.admin.endResidentLink(account.id, linkId, reason.trim()));
      toast({ tone: "success", title: "Access to the unit ended", message: "The account's other units are not affected." });
      setEnding(null); setReason(""); setError("");
      onChanged(a);
    } catch (err) {
      setError((err as Error).message);
    }
  }
  return (
    <Drawer open={!!account} onClose={() => { if (!busy) { setEnding(null); setError(""); onClose(); } }} width="max-w-lg" title={account ? `Units of ${account.username}` : ""}
      subtitle="One account per person. To add a unit, use Generate account for that owner/tenant and confirm it is the same person.">
      {account && (
        <ul className="space-y-3">
          {account.links.map((l, i) => (
            <li key={l.id ?? `v${i}`} className={cx("rounded-xl border p-3 text-[13.5px]", l.active ? "border-ink-200" : "border-ink-100 bg-ink-50 text-ink-500")}>
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div>
                  <b className="text-ink-900">Unit {l.unitNo}</b> <span className="text-ink-500">· {l.personType}{l.personName ? ` ${l.personName}` : ""}</span>
                  {!l.active && <span className="block text-[12.5px]">Ended{l.endedAt ? ` ${dateTimeLabel(l.endedAt)}` : ""}{l.endReason ? ` · ${l.endReason}` : ""}</span>}
                  {l.active && l.problem && <span className="block text-[12.5px] text-amber-800">{l.problem}</span>}
                </div>
                {l.active && l.id !== null && ending !== l.id && <Button size="sm" variant="ghost" className="text-red-700 hover:bg-red-50" onClick={() => { setEnding(l.id); setReason(""); setError(""); }}>End access</Button>}
              </div>
              {ending === l.id && l.id !== null && (
                <div className="mt-3 space-y-2">
                  <label className="block text-[12.5px] font-semibold text-ink-700">Reason (kept in the audit log)
                    <input className="input mt-1 font-normal" maxLength={300} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. Unit sold" />
                  </label>
                  {error && <p className="text-[12.5px] text-red-600" role="alert">{error}</p>}
                  <div className="flex justify-end gap-2">
                    <Button size="sm" variant="secondary" disabled={busy} onClick={() => setEnding(null)}>Cancel</Button>
                    <Button size="sm" variant="danger" loading={busy} disabled={busy} onClick={() => end(l.id!)}>End access to unit {l.unitNo}</Button>
                  </div>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </Drawer>
  );
}
