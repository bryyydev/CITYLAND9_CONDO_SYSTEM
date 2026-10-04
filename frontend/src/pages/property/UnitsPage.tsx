// Units Directory (Superadmin, Admin). Live: /api/units. Units, owners, tenants, parking and storage.
// The server applies the billing engine's rules; this page shows what billing charges and flags
// units whose dues come out as ₱0 (no rate for their type).
import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useAuth } from "../../auth/AuthContext";
import { Drawer, useToast } from "../../components/base/overlays";
import { Badge, Button, Card, EmptyState, ErrorState, Field, Icon, IconButton, Notice, PageHeader, Skeleton, StatusBadge, Tabs, cx } from "../../components/base/ui";
import { focusFirstInvalid, useDebounced } from "../../components/feature/AccountFields";
import { legacyUrl } from "../../components/feature/ModuleRoute";
import { useAction, useAsync } from "../../hooks/useAsync";
import { dateLabel, monthLabel } from "../../lib/format";
import { peso } from "../../lib/money";
import { api } from "../../services/api";
import { ApiError } from "../../services/http";
import type { PersonInput, UnitDetail, UnitInput, UnitKind, UnitOptions, UnitPersonRecord, UnitRow } from "../../services/types";

const PER_PAGE = 20;
const KIND_LABEL: Record<UnitKind, string> = { residential: "Residential", PARKING: "Parking", STORAGE: "Storage" };
const isAsset = (type: string) => type === "PARKING" || type === "STORAGE";

export default function UnitsPage() {
  const [params, setParams] = useSearchParams();
  const initialKind = (["PARKING", "STORAGE"].includes(params.get("kind") ?? "") ? params.get("kind") : "residential") as UnitKind;
  const [kind, setKind] = useState<UnitKind>(initialKind);
  const [filters, setFilters] = useState({ q: params.get("q") ?? "", floor: "", status: "" as "" | "Occupied" | "Vacant", past: params.get("past") === "1" });
  const [page, setPage] = useState(1);
  const q = useDebounced(filters.q);
  const list = useAsync(() => api.units.page({ q, kind, floor: filters.floor, status: filters.status, past: filters.past, page, perPage: PER_PAGE }), [q, kind, filters.floor, filters.status, filters.past, page]);
  const options = useAsync(() => api.units.options(), []);
  const [creating, setCreating] = useState(false);
  const openId = Number(params.get("unit")) || null;
  const movedForm = params.get("moved") === "form";
  const toast = useToast();
  const { can } = useAuth();
  const data = list.data;
  const pages = data ? Math.max(Math.ceil(data.total / data.perPage), 1) : 1;
  const filtered = Boolean(filters.q || filters.floor || filters.status);

  useEffect(() => setPage(1), [q, kind, filters.floor, filters.status, filters.past]);
  const setParam = (key: string, value: string | null) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value); else next.delete(key);
    setParams(next, { replace: true });
  };
  const openUnit = (id: number | null) => setParam("unit", id ? String(id) : null);

  const duesCell = (u: UnitRow) => (
    <span className="inline-flex items-center gap-1.5 tabular">
      {u.zeroDues && <Icon name="error-warning-fill" className="text-amber-600" />}
      {peso(u.monthlyTotal)}
    </span>
  );
  const linkCell = (u: UnitRow) => isAsset(u.type)
    ? (u.assignedTo ? <>Unit <b>{u.assignedTo.unitNo}</b></> : <span className="text-ink-400">Not assigned</span>)
    : ([u.parkingUnit?.unitNo, u.storageUnit?.unitNo].filter(Boolean).join(" · ") || <span className="text-ink-400">—</span>);

  return (
    <>
      <PageHeader eyebrow="Property & Billing" title="Units Directory"
        description="Units with their current owner and tenant. Parking and storage are units too, assigned to a residential unit. Monthly amounts are what billing charges."
        actions={can("units") && <Button icon="add-line" onClick={() => setCreating(true)}>Add unit</Button>} />

      {movedForm && (
        <div className="mb-4" role="status">
          <Notice tone="warn">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span>Units moved to this screen. The form you sent from the old page was <b>not saved</b>; please make the change here.</span>
              <Button size="sm" variant="ghost" onClick={() => setParam("moved", null)}>Dismiss</Button>
            </div>
          </Notice>
        </div>
      )}

      <Card>
        <div className="flex flex-wrap items-end gap-3 border-b border-ink-100 p-4">
          <div className="max-w-full"><Tabs value={kind} onChange={(k) => { setKind(k); setParam("kind", k === "residential" ? null : k); }}
            tabs={(["residential", "PARKING", "STORAGE"] as UnitKind[]).map((k) => ({ key: k, label: KIND_LABEL[k], count: data?.counts[k] }))} /></div>
          <Field label="Search" className="min-w-[180px] flex-1 sm:max-w-xs">{(id) => (
            <div className="relative" role="search">
              <Icon name="search-line" className="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-ink-500" />
              <input id={id} type="search" className="input pl-9" placeholder="Unit, owner or tenant" value={filters.q} onChange={(e) => setFilters({ ...filters, q: e.target.value })} />
            </div>)}</Field>
          <Field label="Floor" className="w-[calc(50%-0.375rem)] sm:w-32">{(id) => (
            <select id={id} className="input" value={filters.floor} onChange={(e) => setFilters({ ...filters, floor: e.target.value })}>
              <option value="">All</option>{(options.data?.floors ?? []).map((f) => <option key={f} value={f}>{f}</option>)}
            </select>)}</Field>
          <Field label="Status" className="w-[calc(50%-0.375rem)] sm:w-36">{(id) => (
            <select id={id} className="input" value={filters.status} onChange={(e) => setFilters({ ...filters, status: e.target.value as typeof filters.status })}>
              <option value="">All</option><option>Occupied</option><option>Vacant</option>
            </select>)}</Field>
          {kind === "residential" && (
            <label className="flex h-10 items-center gap-2 self-end text-[13px] text-ink-700" title="Also find units by a past owner/tenant, or by a contact number or email">
              <input type="checkbox" className="size-4 accent-brand-600" checked={filters.past} onChange={(e) => setFilters({ ...filters, past: e.target.checked })} />
              Include past owners &amp; tenants
            </label>
          )}
          {filtered && <Button variant="ghost" icon="close-circle-line" onClick={() => setFilters({ q: "", floor: "", status: "", past: false })}>Reset</Button>}
          <p className="ml-auto self-center text-[12.5px] text-ink-500" aria-live="polite">{data ? `${data.total} unit${data.total === 1 ? "" : "s"}` : " "}</p>
        </div>

        {list.error ? <ErrorState title="Couldn't load the units" message={list.error.message} onRetry={list.reload} />
          : !data ? <Skeleton rows={8} />
          : data.units.length === 0 ? (filtered
            ? <EmptyState icon="search-line" title="No units match" action={<Button variant="secondary" onClick={() => setFilters({ q: "", floor: "", status: "", past: false })}>Reset filters</Button>}>Try another unit number, name, floor or status.</EmptyState>
            : <EmptyState icon="building-2-line" title={`No ${KIND_LABEL[kind].toLowerCase()} units yet`} action={can("units") && <Button icon="add-line" onClick={() => setCreating(true)}>Add unit</Button>} />)
          : (
            <div className={cx(list.loading && "opacity-60 transition-opacity")} aria-busy={list.loading || undefined}>
              <div className="hidden overflow-x-auto md:block">
                <table className="w-full border-collapse text-[13.5px]">
                  <caption className="sr-only">{KIND_LABEL[kind]} units</caption>
                  <thead><tr>
                    <th scope="col" className="th">Unit</th>
                    <th scope="col" className="th text-right">Area</th>
                    {kind === "residential" && <th scope="col" className="th">Owner / tenant</th>}
                    <th scope="col" className="th">{kind === "residential" ? "Parking · storage" : "Assigned to"}</th>
                    <th scope="col" className="th">Status</th>
                    <th scope="col" className="th text-right">Monthly</th>
                    <th scope="col" className="th hidden xl:table-cell">Latest bill</th>
                  </tr></thead>
                  <tbody>
                    {data.units.map((u) => (
                      <tr key={u.id} className="cursor-pointer hover:bg-ink-50/60" onClick={() => openUnit(u.id)}>
                        <td className="border-b border-ink-100 px-4 py-2.5">
                          <button type="button" className="font-semibold text-ink-900 hover:text-brand-700 hover:underline" onClick={(e) => { e.stopPropagation(); openUnit(u.id); }}>{u.unitNo}</button>
                          <span className="block text-[12px] text-ink-500">{u.type || "—"}{u.floor ? ` · Floor ${u.floor}` : ""}</span>
                        </td>
                        <td className="border-b border-ink-100 px-4 py-2.5 text-right tabular">{u.areaSqm} m²</td>
                        {kind === "residential" && <td className="border-b border-ink-100 px-4 py-2.5">{u.ownerName ?? <span className="text-ink-400">No owner</span>}{u.tenantName && <span className="block text-[12px] text-ink-500">Tenant: {u.tenantName}</span>}</td>}
                        <td className="border-b border-ink-100 px-4 py-2.5">{linkCell(u)}</td>
                        <td className="border-b border-ink-100 px-4 py-2.5"><StatusBadge status={u.status} /></td>
                        <td className="border-b border-ink-100 px-4 py-2.5 text-right" title={u.zeroDues ? "Condo dues come out as ₱0: this unit type has no rate. Open the unit for details." : undefined}>{duesCell(u)}</td>
                        <td className="hidden border-b border-ink-100 px-4 py-2.5 xl:table-cell">{u.latestBill ? <><StatusBadge status={u.latestBill.status} /><span className="ml-2 text-[12px] text-ink-500">{monthLabel(u.latestBill.month)}</span></> : <span className="text-ink-400">—</span>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <ul className="divide-y divide-ink-100 md:hidden">
                {data.units.map((u) => (
                  <li key={u.id}>
                    <button type="button" className="w-full space-y-1 px-4 py-3 text-left hover:bg-ink-50" onClick={() => openUnit(u.id)}>
                      <div className="flex items-start justify-between gap-3">
                        <span><b className="text-ink-900">{u.unitNo}</b><span className="block text-[12px] text-ink-500">{u.type}{u.floor ? ` · Floor ${u.floor}` : ""} · {u.areaSqm} m²</span></span>
                        <StatusBadge status={u.status} />
                      </div>
                      <p className="text-[13px] text-ink-700">{isAsset(u.type) ? linkCell(u) : (u.ownerName ?? "No owner")}{!isAsset(u.type) && u.tenantName ? ` · Tenant: ${u.tenantName}` : ""}</p>
                      <p className="text-[13px]">Monthly: {duesCell(u)}</p>
                    </button>
                  </li>
                ))}
              </ul>
              <nav className="flex flex-wrap items-center justify-between gap-3 border-t border-ink-100 px-4 py-3 text-[13px] text-ink-600" aria-label="Pagination">
                <span>Showing {(data.page - 1) * data.perPage + 1}–{(data.page - 1) * data.perPage + data.units.length} of {data.total}</span>
                <div className="flex items-center gap-1">
                  <IconButton icon="arrow-left-s-line" label="Previous page" disabled={page <= 1} className="disabled:opacity-30" onClick={() => setPage(page - 1)} />
                  <span className="px-2">Page {data.page} of {pages}</span>
                  <IconButton icon="arrow-right-s-line" label="Next page" disabled={page >= pages} className="disabled:opacity-30" onClick={() => setPage(page + 1)} />
                </div>
              </nav>
            </div>
          )}
      </Card>

      <UnitDetailDrawer unitId={openId} options={options.data} onClose={() => openUnit(null)} onChanged={() => { list.reload(); options.reload(); }} />
      <UnitFormDrawer open={creating} options={options.data} onClose={() => setCreating(false)}
        onSaved={(u) => {
          setCreating(false); list.reload(); options.reload(); openUnit(u.id);
          toast({ tone: "success", title: `Unit ${u.unitNo} created`, message: u.zeroDues ? "Note: its condo dues come out as ₱0. Set a rate per m²." : undefined });
        }} />
    </>
  );
}

// ------------------------------------------------------------------ unit detail
function UnitDetailDrawer({ unitId, options, onClose, onChanged }:
  { unitId: number | null; options: UnitOptions | null; onClose: () => void; onChanged: () => void }) {
  const detail = useAsync(() => (unitId ? api.units.detail(unitId) : Promise.resolve(null)), [unitId]);
  const [editing, setEditing] = useState(false);
  const [person, setPerson] = useState<{ kind: "Owner" | "Tenant"; record: UnitPersonRecord | null } | null>(null);
  const { can } = useAuth();
  const toast = useToast();
  const u = detail.data;
  if (!unitId) return null;

  const updated = (next: UnitDetail, message: string) => { detail.setData(next); onChanged(); toast({ tone: "success", title: message }); };
  const peopleList = (kind: "Owner" | "Tenant", rows: UnitPersonRecord[]) => (
    <section>
      <div className="mb-2 flex items-center justify-between gap-2">
        <h3 className="font-semibold text-ink-900">{kind === "Owner" ? "Owners" : "Tenants"}</h3>
        {can(kind === "Owner" ? "add_owner" : "add_tenant") && <Button size="sm" variant="secondary" icon="user-add-line" onClick={() => setPerson({ kind, record: null })}>Add {kind.toLowerCase()}</Button>}
      </div>
      {rows.length === 0 ? <p className="rounded-xl border border-dashed border-ink-200 px-4 py-3 text-[13px] text-ink-500">No {kind.toLowerCase()} recorded.</p> : (
        <ul className="divide-y divide-ink-100 rounded-xl border border-ink-200">
          {rows.map((p) => (
            <li key={p.id} className={cx("flex items-start justify-between gap-3 px-4 py-2.5", p.status === "Past" && "bg-ink-50/60")}>
              <div className="min-w-0">
                <p className="font-semibold text-ink-900">{p.name}{p.representative && <span className="ml-2 rounded bg-brand-50 px-1.5 py-0.5 text-[11px] font-semibold text-brand-700">Representative</span>}</p>
                <p className="text-[12.5px] break-words text-ink-500">{[p.contactNo, p.email].filter(Boolean).join(" · ") || "No contact details"}</p>
                <p className="text-[12px] text-ink-500">{p.moveIn ? `In ${dateLabel(p.moveIn)}` : "Move-in not recorded"}{p.moveOut ? ` · out ${dateLabel(p.moveOut)}` : ""}{p.receiveSoaEmail ? " · SOA by email" : ""}{!p.includeInSoa ? " · not on SOA" : ""}</p>
              </div>
              <div className="flex shrink-0 items-center gap-2">
                <Badge tone={p.status === "Current" ? "ok" : "neutral"}>{p.status}</Badge>
                {can(kind === "Owner" ? "edit_owner" : "edit_tenant") && <IconButton icon="edit-line" label={`Edit ${p.name}`} onClick={() => setPerson({ kind, record: p })} />}
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );

  return (
    <>
      <Drawer open onClose={onClose} width="max-w-2xl" title={u ? `Unit ${u.unitNo}` : "Unit"}
        subtitle={u ? `${u.type || "No type"}${u.floor ? ` · Floor ${u.floor}` : ""} · ${u.areaSqm} m²` : ""}
        footer={u && can("edit_unit") ? <><Button variant="secondary" onClick={onClose}>Close</Button><Button icon="edit-line" onClick={() => setEditing(true)}>Edit unit</Button></> : undefined}>
        {detail.error ? <ErrorState title="Couldn't load the unit" message={detail.error.message} onRetry={detail.reload} />
          : !u ? <Skeleton rows={8} />
          : (
            <div className="space-y-6">
              {u.zeroDues && <Notice tone="warn" title="Condo dues come out as ₱0">This unit has an area but no rate: Rates &amp; Rules has no rate for the type “{u.type}” and the unit has no rate of its own. Bills will charge ₱0 condo dues until a rate per m² is set (Edit unit).</Notice>}
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
                {([
                  ["Condo dues / month", peso(u.dues.condo)],
                  ...(isAsset(u.type) ? [] : [["Parking / month", peso(u.dues.parking)], ["Storage / month", peso(u.dues.storage)]]),
                  ["Rate used", `₱${u.effectiveRatePerSqm}/m²${u.autoRate ? " (auto)" : ""}`],
                  ["Occupancy", u.occupancy], ["Status", u.status],
                  ...(isAsset(u.type) ? [["Assigned to", u.assignedTo ? `Unit ${u.assignedTo.unitNo}` : "Not assigned"]]
                    : [["Parking unit", u.parkingUnit?.unitNo ?? "—"], ["Storage unit", u.storageUnit?.unitNo ?? "—"]]),
                ] as [string, string][]).map(([k, v]) => (
                  <div key={k} className="rounded-lg bg-ink-50 px-3 py-2"><p className="text-[11.5px] text-ink-500">{k}</p><p className="truncate font-semibold text-ink-900 tabular">{v}</p></div>
                ))}
              </div>
              {!isAsset(u.type) && <>{peopleList("Owner", u.owners)}{peopleList("Tenant", u.tenants)}
                <p className="-mt-3 text-[12.5px] text-ink-500">Marking an owner or tenant “Past” ends their resident portal access automatically.</p></>}
              <section>
                <h3 className="mb-2 font-semibold text-ink-900">Recent bills</h3>
                {u.bills.length === 0 ? <p className="text-[13px] text-ink-500">No bills yet.</p> : (
                  <div className="overflow-x-auto rounded-xl border border-ink-200">
                    <table className="w-full border-collapse text-[13px]">
                      <thead><tr><th className="th">Month</th><th className="th text-right">Total</th><th className="th text-right">Balance</th><th className="th">Status</th><th className="th"><span className="sr-only">Open</span></th></tr></thead>
                      <tbody>{u.bills.map((b) => (
                        <tr key={b.id} className="border-t border-ink-100">
                          <td className="px-4 py-2 whitespace-nowrap">{monthLabel(b.month)}</td>
                          <td className="px-4 py-2 text-right tabular">{peso(b.total)}</td>
                          <td className="px-4 py-2 text-right tabular">{peso(b.balance)}</td>
                          <td className="px-4 py-2"><StatusBadge status={b.status} /></td>
                          <td className="px-4 py-2 text-right"><a className="inline-flex items-center gap-1 text-[12.5px] font-semibold text-brand-600 hover:underline" href={legacyUrl(`/billing/${b.id}`)} title="Opens the bill (Billing still runs on the classic screen)">Open <Icon name="external-link-line" /></a></td>
                        </tr>
                      ))}</tbody>
                    </table>
                  </div>
                )}
              </section>
            </div>
          )}
      </Drawer>
      {u && <UnitFormDrawer open={editing} unit={u} options={options} onClose={() => setEditing(false)} onSaved={(next) => { setEditing(false); updated(next, `Unit ${next.unitNo} updated`); }} />}
      {u && person && <PersonDrawer unit={u} kind={person.kind} record={person.record} onClose={() => setPerson(null)}
        onSaved={(next, msg) => { setPerson(null); updated(next, msg); }} />}
    </>
  );
}

// ------------------------------------------------------------------ create / edit unit
const blankPerson = (): PersonInput => ({ name: "", contactNo: "", email: "", moveIn: "", moveOut: "", status: "Current", notes: "", receiveSoaEmail: false, includeInSoa: true, representative: true });

function UnitFormDrawer({ open, unit, options, onClose, onSaved }:
  { open: boolean; unit?: UnitDetail; options: UnitOptions | null; onClose: () => void; onSaved: (u: UnitDetail) => void }) {
  const isNew = !unit;
  const initial = (): UnitInput & { unitNo: string } => unit
    ? { unitNo: unit.unitNo, floor: unit.floor, type: unit.type, areaSqm: String(unit.areaSqm), ratePerSqm: unit.autoRate ? "" : unit.ratePerSqm, autoRate: unit.autoRate,
        duesMode: unit.duesMode, manualMonthlyDues: unit.manualMonthlyDues, occupancy: unit.occupancy, status: unit.status,
        parkingUnitId: unit.parkingUnit?.id ?? null, storageUnitId: unit.storageUnit?.id ?? null }
    : { unitNo: "", floor: "", type: "", areaSqm: "", ratePerSqm: "", autoRate: true, duesMode: "per_sqm", manualMonthlyDues: "0", occupancy: "Owner", status: "Vacant", parkingUnitId: null, storageUnitId: null };
  const [form, setForm] = useState(initial);
  const [owner, setOwner] = useState<PersonInput>(blankPerson);
  const [tenant, setTenant] = useState<PersonInput>(blankPerson);
  const [touched, setTouched] = useState(false);
  const [serverFields, setServerFields] = useState<Record<string, string>>({});
  const [serverError, setServerError] = useState("");
  const { busy, act } = useAction();
  const ref = useRef<HTMLFormElement>(null);

  useEffect(() => { if (open) { setForm(initial()); setOwner(blankPerson()); setTenant(blankPerson()); setTouched(false); setServerFields({}); setServerError(""); } },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [open]);
  if (!open) return null;

  const typeRate = options?.typeRates[form.type];
  const rateMissing = !isAsset(form.type) && form.type !== "" && (form.autoRate || !form.ratePerSqm) && Number(typeRate ?? 0) === 0;
  const errors: Record<string, string> = {
    unitNo: isNew && !form.unitNo.trim() ? "Enter the unit number." : "",
    type: !form.type ? "Choose a unit type." : "",
    areaSqm: form.areaSqm !== "" && !/^\d{1,6}(\.\d{1,2})?$/.test(form.areaSqm) ? "Enter the area in m², up to 2 decimals." : "",
    ratePerSqm: !form.autoRate && form.ratePerSqm !== "" && !/^\d{1,6}(\.\d{1,4})?$/.test(form.ratePerSqm) ? "Enter a rate, up to 4 decimals." : "",
    manualMonthlyDues: form.duesMode === "manual" && !/^\d{1,8}(\.\d{1,2})?$/.test(form.manualMonthlyDues) ? "Enter an amount." : "",
  };
  const err = (k: string) => (touched ? errors[k] : "") || serverFields[k] || "";
  const set = (patch: Partial<typeof form>, keys: string[] = []) => {
    setForm({ ...form, ...patch });
    if (keys.length) setServerFields(Object.fromEntries(Object.entries(serverFields).filter(([k]) => !keys.includes(k))));
  };
  const assetSelect = (key: "parkingUnitId" | "storageUnitId", label: string, rows: UnitOptions["parking"]) => (
    <Field label={label} error={err(key)}>{(id) => (
      <select id={id} className="input" value={form[key] ?? ""} aria-invalid={Boolean(err(key)) || undefined}
        onChange={(e) => set({ [key]: e.target.value ? Number(e.target.value) : null } as Partial<typeof form>, [key])}>
        <option value="">None</option>
        {rows.map((a) => {
          const taken = a.assignedTo && a.assignedTo.id !== unit?.id;
          return <option key={a.id} value={a.id} disabled={Boolean(taken)}>{a.unitNo}{a.floor ? ` · ${a.floor}` : ""}{taken ? ` (assigned to ${a.assignedTo!.unitNo})` : ""}</option>;
        })}
      </select>)}</Field>
  );

  async function submit() {
    setTouched(true); setServerError("");
    if (busy) return;
    if (Object.values(errors).some(Boolean)) { focusFirstInvalid(ref); return; }
    const body: UnitInput = { floor: form.floor.trim(), type: form.type, areaSqm: form.areaSqm || "0", ratePerSqm: form.autoRate ? "" : form.ratePerSqm, autoRate: form.autoRate,
      duesMode: form.duesMode, manualMonthlyDues: form.manualMonthlyDues || "0", occupancy: form.occupancy, status: form.status,
      parkingUnitId: isAsset(form.type) ? null : form.parkingUnitId, storageUnitId: isAsset(form.type) ? null : form.storageUnitId };
    try {
      const saved = isNew
        ? await act(() => api.units.create({ ...body, unitNo: form.unitNo.trim(), owner: owner.name.trim() ? owner : null, tenant: tenant.name.trim() ? tenant : null }))
        : await act(() => api.units.update(unit!.id, body));
      onSaved(saved);
    } catch (e) {
      if (e instanceof ApiError && Object.keys(e.fields).length) { setServerFields(e.fields); setServerError("Some values weren't accepted. Check the highlighted fields."); focusFirstInvalid(ref); }
      else setServerError((e as Error).message);
    }
  }

  const personMini = (label: string, p: PersonInput, setP: (p: PersonInput) => void, prefix: string, withMoveIn: boolean) => (
    <fieldset className="space-y-3 rounded-xl border border-ink-200 p-4">
      <legend className="px-1 text-[12.5px] font-semibold text-ink-700">{label} (optional)</legend>
      <Field label="Name" error={serverFields[`${prefix}.name`]}>{(id) => <input id={id} className="input" value={p.name} onChange={(e) => setP({ ...p, name: e.target.value })} />}</Field>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Contact number" error={serverFields[`${prefix}.contactNo`]}>{(id) => <input id={id} className="input" value={p.contactNo} onChange={(e) => setP({ ...p, contactNo: e.target.value })} />}</Field>
        <Field label="Email" error={serverFields[`${prefix}.email`]}>{(id) => <input id={id} type="email" className="input" value={p.email} onChange={(e) => setP({ ...p, email: e.target.value })} />}</Field>
        {withMoveIn && <Field label="Move-in date" error={serverFields[`${prefix}.moveIn`]}>{(id) => <input id={id} type="date" className="input" value={p.moveIn} onChange={(e) => setP({ ...p, moveIn: e.target.value })} />}</Field>}
      </div>
      <label className="flex items-center gap-2 text-[13px]"><input type="checkbox" className="size-4 accent-brand-600" checked={p.receiveSoaEmail} onChange={(e) => setP({ ...p, receiveSoaEmail: e.target.checked })} />Send SOAs by email</label>
    </fieldset>
  );

  return (
    <Drawer open onClose={() => { if (!busy) onClose(); }} width="max-w-xl" title={isNew ? "Add unit" : `Edit unit ${unit!.unitNo}`}
      subtitle={isNew ? "Parking and storage are added as units of type PARKING / STORAGE." : "The unit number can't be changed."}
      footer={<><Button variant="secondary" onClick={onClose} disabled={busy}>Cancel</Button><Button icon="save-3-line" loading={busy} onClick={submit}>{isNew ? "Create unit" : "Save changes"}</Button></>}>
      <form ref={ref} className="space-y-4" noValidate onSubmit={(e) => { e.preventDefault(); void submit(); }}>
        {serverError && <Notice tone="warn">{serverError}</Notice>}
        <div className="grid gap-3 sm:grid-cols-2">
          {isNew && <Field label="Unit number" error={err("unitNo")} hint="e.g. 12-01, P-15">{(id) => <input id={id} className="input" data-autofocus value={form.unitNo} aria-invalid={Boolean(err("unitNo")) || undefined} onChange={(e) => set({ unitNo: e.target.value }, ["unitNo"])} />}</Field>}
          <Field label="Type" error={err("type")}>{(id) => (
            <select id={id} className="input" value={form.type} aria-invalid={Boolean(err("type")) || undefined} onChange={(e) => set({ type: e.target.value }, ["type"])}>
              <option value="">Choose…</option>
              {[...new Set([...(options?.unitTypes ?? []), ...(unit?.type ? [unit.type] : [])])].map((t) => <option key={t} value={t}>{t}</option>)}
            </select>)}</Field>
          <Field label="Floor" error={err("floor")}>{(id) => <input id={id} className="input" maxLength={20} value={form.floor} onChange={(e) => set({ floor: e.target.value }, ["floor"])} />}</Field>
          <Field label="Area (m²)" error={err("areaSqm")}>{(id) => <input id={id} inputMode="decimal" className="input tabular" value={form.areaSqm} aria-invalid={Boolean(err("areaSqm")) || undefined} onChange={(e) => set({ areaSqm: e.target.value }, ["areaSqm"])} />}</Field>
          <Field label="Status" error={err("status")}>{(id) => (
            <select id={id} className="input" value={form.status} onChange={(e) => set({ status: e.target.value as UnitInput["status"] })}><option>Vacant</option><option>Occupied</option></select>)}</Field>
          {!isAsset(form.type) && <Field label="Occupied by" error={err("occupancy")}>{(id) => (
            <select id={id} className="input" value={form.occupancy} onChange={(e) => set({ occupancy: e.target.value as UnitInput["occupancy"] })}><option>Owner</option><option>Tenant</option></select>)}</Field>}
        </div>

        <fieldset className="space-y-3 rounded-xl border border-ink-200 p-4">
          <legend className="px-1 text-[12.5px] font-semibold text-ink-700">Dues rate</legend>
          <label className="flex items-center gap-2 text-[13px]"><input type="checkbox" className="size-4 accent-brand-600" checked={form.autoRate} onChange={(e) => set({ autoRate: e.target.checked }, ["ratePerSqm"])} />
            Use the Rates &amp; Rules rate for this type{typeRate !== undefined && ` (₱${typeRate}/m²)`}</label>
          {!form.autoRate && <Field label="Rate per m²" error={err("ratePerSqm")} hint="Empty = the Rates & Rules rate for this type.">{(id) => <input id={id} inputMode="decimal" className="input tabular" value={form.ratePerSqm} aria-invalid={Boolean(err("ratePerSqm")) || undefined} onChange={(e) => set({ ratePerSqm: e.target.value }, ["ratePerSqm"])} />}</Field>}
          {rateMissing && <Notice tone="warn">Rates &amp; Rules has no rate for “{form.type}”, so condo dues would be ₱0. Untick the box above and enter a rate per m².</Notice>}
          <Field label="Pricing" hint={form.duesMode === "manual" ? "Bills charge this fixed amount every month instead of area × rate." : undefined}>{(id) => (
            <select id={id} className="input" value={form.duesMode} onChange={(e) => set({ duesMode: e.target.value as UnitInput["duesMode"] })}><option value="per_sqm">Per m² (area × rate)</option><option value="manual">Manual monthly amount</option></select>)}</Field>
          {form.duesMode === "manual" && <Field label="Manual monthly amount (₱)" error={err("manualMonthlyDues")}>{(id) => <input id={id} inputMode="decimal" className="input tabular" value={form.manualMonthlyDues} aria-invalid={Boolean(err("manualMonthlyDues")) || undefined} onChange={(e) => set({ manualMonthlyDues: e.target.value }, ["manualMonthlyDues"])} />}</Field>}
        </fieldset>

        {!isAsset(form.type) && form.type !== "" && (
          <div className="grid gap-3 sm:grid-cols-2">
            {assetSelect("parkingUnitId", "Parking unit", options?.parking ?? [])}
            {assetSelect("storageUnitId", "Storage unit", options?.storage ?? [])}
          </div>
        )}

        {isNew && !isAsset(form.type) && (
          <>
            {personMini("First owner", owner, setOwner, "owner", false)}
            {personMini("First tenant", tenant, setTenant, "tenant", true)}
          </>
        )}
        <button type="submit" hidden />
      </form>
    </Drawer>
  );
}

// ------------------------------------------------------------------ add / edit owner or tenant
function PersonDrawer({ unit, kind, record, onClose, onSaved }:
  { unit: UnitDetail; kind: "Owner" | "Tenant"; record: UnitPersonRecord | null; onClose: () => void; onSaved: (u: UnitDetail, message: string) => void }) {
  const [form, setForm] = useState<PersonInput>(() => record
    ? { name: record.name, contactNo: record.contactNo, email: record.email, moveIn: record.moveIn ?? "", moveOut: record.moveOut ?? "", status: record.status,
        notes: record.notes, receiveSoaEmail: record.receiveSoaEmail, includeInSoa: record.includeInSoa, representative: record.representative }
    : { ...blankPerson(), representative: kind === "Tenant" && !unit.tenants.some((t) => t.status === "Current") });
  const [touched, setTouched] = useState(false);
  const [serverFields, setServerFields] = useState<Record<string, string>>({});
  const [serverError, setServerError] = useState("");
  const { busy, act } = useAction();
  const ref = useRef<HTMLFormElement>(null);
  const errors: Record<string, string> = {
    name: !form.name.trim() ? `Enter the ${kind.toLowerCase()}'s name.` : "",
    email: form.email && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(form.email) ? "Enter a valid email address." : form.receiveSoaEmail && !form.email ? "Enter an email address to send SOAs to." : "",
    moveOut: form.moveIn && form.moveOut && form.moveOut < form.moveIn ? "Move-out can't be before move-in." : "",
  };
  const err = (k: string) => (touched ? errors[k] : "") || serverFields[k] || "";
  const set = (p: Partial<PersonInput>, keys: string[] = []) => { setForm({ ...form, ...p }); if (keys.length) setServerFields(Object.fromEntries(Object.entries(serverFields).filter(([k]) => !keys.includes(k)))); };
  const endsAccess = record?.status === "Current" && form.status === "Past";

  async function submit() {
    setTouched(true); setServerError("");
    if (busy) return;
    if (Object.values(errors).some(Boolean)) { focusFirstInvalid(ref); return; }
    try {
      const next = record ? await act(() => api.units.updatePerson(unit.id, kind, record.id, form)) : await act(() => api.units.addPerson(unit.id, kind, form));
      onSaved(next, record ? `${form.name} updated` : `${kind} ${form.name} added`);
    } catch (e) {
      if (e instanceof ApiError && Object.keys(e.fields).length) { setServerFields(e.fields); focusFirstInvalid(ref); }
      else setServerError((e as Error).message);
    }
  }

  return (
    <Drawer open onClose={() => { if (!busy) onClose(); }} width="max-w-lg" title={record ? `Edit ${kind.toLowerCase()}` : `Add ${kind.toLowerCase()}`} subtitle={`Unit ${unit.unitNo}`}
      footer={<><Button variant="secondary" onClick={onClose} disabled={busy}>Cancel</Button><Button icon="save-3-line" loading={busy} onClick={submit}>{record ? "Save changes" : `Add ${kind.toLowerCase()}`}</Button></>}>
      <form ref={ref} className="space-y-4" noValidate onSubmit={(e) => { e.preventDefault(); void submit(); }}>
        {serverError && <Notice tone="warn">{serverError}</Notice>}
        <Field label="Name" error={err("name")}>{(id) => <input id={id} className="input" data-autofocus value={form.name} aria-invalid={Boolean(err("name")) || undefined} onChange={(e) => set({ name: e.target.value }, ["name"])} />}</Field>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Contact number" error={err("contactNo")}>{(id) => <input id={id} className="input" value={form.contactNo} onChange={(e) => set({ contactNo: e.target.value }, ["contactNo"])} />}</Field>
          <Field label="Email" error={err("email")}>{(id) => <input id={id} type="email" className="input" value={form.email} aria-invalid={Boolean(err("email")) || undefined} onChange={(e) => set({ email: e.target.value }, ["email"])} />}</Field>
          <Field label="Move-in date" error={err("moveIn")}>{(id) => <input id={id} type="date" className="input" value={form.moveIn} onChange={(e) => set({ moveIn: e.target.value }, ["moveIn"])} />}</Field>
          {(record || kind === "Tenant") && <Field label="Move-out date" error={err("moveOut")} hint={record ? "Filled in with today when marked Past." : "Only for a past tenant."}>{(id) => <input id={id} type="date" className="input" value={form.moveOut} aria-invalid={Boolean(err("moveOut")) || undefined} onChange={(e) => set({ moveOut: e.target.value }, ["moveOut"])} />}</Field>}
        </div>
        {record && (
          <fieldset>
            <legend className="mb-1.5 text-[12.5px] font-semibold text-ink-700">Status</legend>
            <div className="grid gap-2 sm:grid-cols-2">
              {(["Current", "Past"] as const).map((s) => (
                <label key={s} className={cx("flex min-h-11 cursor-pointer items-center gap-3 rounded-lg border px-3 py-2", form.status === s ? (s === "Current" ? "border-brand-500 bg-brand-50" : "border-amber-300 bg-amber-50") : "border-ink-200 hover:bg-ink-50")}>
                  <input type="radio" name="person-status" className="size-4 accent-brand-600" checked={form.status === s} onChange={() => set({ status: s, ...(s === "Current" ? { moveOut: "" } : {}) })} />
                  <span className="font-medium text-ink-900">{s === "Current" ? `Current ${kind.toLowerCase()}` : "Past (moved out / sold)"}</span>
                </label>
              ))}
            </div>
          </fieldset>
        )}
        {endsAccess && <Notice tone="warn">Marking {form.name || "them"} Past ends their resident portal access right away.</Notice>}
        <div className="space-y-2">
          <label className="flex items-center gap-2 text-[13px]"><input type="checkbox" className="size-4 accent-brand-600" checked={form.receiveSoaEmail} onChange={(e) => set({ receiveSoaEmail: e.target.checked }, ["email"])} />Send SOAs by email</label>
          <label className="flex items-center gap-2 text-[13px]"><input type="checkbox" className="size-4 accent-brand-600" checked={form.includeInSoa} onChange={(e) => set({ includeInSoa: e.target.checked })} />Show on the SOA</label>
          {kind === "Tenant" && form.status === "Current" && <label className="flex items-center gap-2 text-[13px]"><input type="checkbox" className="size-4 accent-brand-600" checked={form.representative} onChange={(e) => set({ representative: e.target.checked })} />Representative tenant (the unit's main tenant)</label>}
        </div>
        <Field label="Notes" error={err("notes")}>{(id) => <textarea id={id} rows={2} maxLength={500} className="input h-auto py-2" value={form.notes} onChange={(e) => set({ notes: e.target.value }, ["notes"])} />}</Field>
        <button type="submit" hidden />
      </form>
    </Drawer>
  );
}
