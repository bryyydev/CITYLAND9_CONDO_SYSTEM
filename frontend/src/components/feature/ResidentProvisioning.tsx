// Resident portal account provisioning (Superadmin): confirm -> (identity review) -> one-time
// handoff of the username and activation code. The server generates both; the code is shown ONLY
// here, right after it was issued, and is gone when this panel closes.
import { useState } from "react";
import { dateTimeLabel } from "../../lib/format";
import { api } from "../../services/api";
import { ApiError } from "../../services/http";
import type { ActivationCredentials, ProvisionCandidate, ResidentAccount } from "../../services/types";
import { newFormToken } from "../../pages/property/Billing";
import { Drawer, useToast } from "../base/overlays";
import { Badge, Button, Icon, Notice } from "../base/ui";

export interface ProvisionTarget { personType: "Owner" | "Tenant"; personId: number; name: string; unitNo: string }
type Handoff = { credentials: ActivationCredentials; name: string; units: string[] };

/** Generate a resident portal account for one owner/tenant. */
export function ProvisionDrawer({ target, onClose, onDone }: { target: ProvisionTarget | null; onClose: () => void; onDone: (account: ResidentAccount) => void }) {
  const [busy, setBusy] = useState(false);
  const [candidates, setCandidates] = useState<ProvisionCandidate[] | null>(null);
  const [choice, setChoice] = useState<string>("");
  const [error, setError] = useState("");
  const [handoff, setHandoff] = useState<Handoff | null>(null);
  const toast = useToast();
  const reset = () => { setCandidates(null); setChoice(""); setError(""); setHandoff(null); setBusy(false); };
  const close = () => { if (busy) return; reset(); onClose(); };

  async function run(mode: "auto" | "new" | "link", linkUserId?: number) {
    if (!target || busy) return;
    setBusy(true);
    setError("");
    try {
      const r = await api.admin.provisionResident({ personType: target.personType, personId: target.personId, mode, linkUserId, formToken: newFormToken() });
      if (r.credentials) {
        setHandoff({ credentials: r.credentials, name: target.name, units: r.account.links.filter((l) => l.active).map((l) => l.unitNo) });
      } else {
        toast({ tone: "success", title: `Unit ${target.unitNo} added to ${r.account.username}`, message: "No new code: the resident uses their existing sign-in." });
        reset();
        onClose();
      }
      onDone(r.account);
    } catch (err) {
      if (err instanceof ApiError && err.details.needsReview) {
        setCandidates(err.details.candidates as ProvisionCandidate[]);
      } else {
        setError((err as Error).message);
      }
    } finally {
      setBusy(false);
    }
  }

  if (handoff) return <HandoffDrawer handoff={handoff} onClose={() => { reset(); onClose(); }} />;
  return (
    <Drawer open={!!target} onClose={close} width="max-w-lg" title="Generate resident portal account"
      subtitle={target ? `${target.name} · ${target.personType} of unit ${target.unitNo}` : undefined}
      footer={!candidates ? <>
        <Button variant="secondary" disabled={busy} onClick={close}>Cancel</Button>
        <Button icon="user-add-line" loading={busy} disabled={busy} onClick={() => run("auto")}>Generate account</Button>
      </> : <>
        <Button variant="secondary" disabled={busy} onClick={close}>Cancel</Button>
        <Button icon="check-line" loading={busy} disabled={busy || !choice}
          onClick={() => (choice === "new" ? run("new") : run("link", Number(choice)))}>{choice === "new" ? "Create separate account" : choice ? "Add unit to that account" : "Choose an option"}</Button>
      </>}>
      {target && !candidates && (
        <div className="space-y-4 text-[13.5px]">
          <p>The system will create a <b>Resident</b> portal account for <b>{target.name}</b> with access to unit <b>{target.unitNo}</b>:</p>
          <ul className="list-disc space-y-1 pl-5 text-ink-700">
            <li>a unique username, generated from the name</li>
            <li>a one-time <b>activation code</b>, shown to you once, to hand over in person</li>
            <li>the resident signs in with it and must choose their own password; the code then stops working</li>
          </ul>
          <Notice tone="info">The resident can't see anything until they activate the account. Billing and statements work the same with or without a portal account.</Notice>
          {error && <div role="alert"><Notice tone="warn">{error}</Notice></div>}
        </div>
      )}
      {target && candidates && (
        <div className="space-y-4 text-[13.5px]">
          <Notice tone="warn" title="This may be the same person as an existing account">
            Owner/tenant records belong to one unit each, so the system can't tell for sure. People are never merged automatically:
            check their identity (e.g. ID shown at the office) before adding this unit to an existing account.
          </Notice>
          <fieldset className="space-y-2">
            <legend className="sr-only">Choose how to continue</legend>
            {candidates.map((c) => (
              <label key={c.userId} className={`flex cursor-pointer gap-3 rounded-xl border p-3 ${choice === String(c.userId) ? "border-brand-500 bg-brand-50" : "border-ink-200"}`}>
                <input type="radio" name="identity" className="mt-1" checked={choice === String(c.userId)} onChange={() => setChoice(String(c.userId))} />
                <span>
                  <b className="text-ink-900">Same verified person: add unit {target.unitNo} to {c.username}</b>
                  <span className="block text-ink-600">{c.displayName} · units {c.units.join(", ") || "—"}</span>
                  <span className="mt-1 flex flex-wrap gap-1">{c.reasons.map((r) => <Badge key={r} tone="warn" dot={false}>{r}</Badge>)}</span>
                </span>
              </label>
            ))}
            <label className={`flex cursor-pointer gap-3 rounded-xl border p-3 ${choice === "new" ? "border-brand-500 bg-brand-50" : "border-ink-200"}`}>
              <input type="radio" name="identity" className="mt-1" checked={choice === "new"} onChange={() => setChoice("new")} />
              <span><b className="text-ink-900">A different person: create a separate account</b>
                <span className="block text-ink-600">A new username and activation code are generated for {target.name}.</span></span>
            </label>
          </fieldset>
          {error && <div role="alert"><Notice tone="warn">{error}</Notice></div>}
        </div>
      )}
    </Drawer>
  );
}

/** Issue a replacement activation code (the previous code / password stops working). */
export function ReissueDrawer({ account, onClose, onDone }: { account: ResidentAccount | null; onClose: () => void; onDone: (a: ResidentAccount) => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [handoff, setHandoff] = useState<Handoff | null>(null);
  async function issue() {
    if (!account || busy) return;
    setBusy(true);
    setError("");
    try {
      const r = await api.admin.issueActivationCode(account.id, newFormToken());
      setHandoff({ credentials: r.credentials, name: account.displayName, units: r.account.links.filter((l) => l.active).map((l) => l.unitNo) });
      onDone(r.account);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const close = () => { if (busy) return; setError(""); setHandoff(null); onClose(); };
  if (handoff) return <HandoffDrawer handoff={handoff} onClose={close} />;
  return (
    <Drawer open={!!account} onClose={close} width="max-w-md" title="Issue a new activation code" subtitle={account ? `${account.displayName} · ${account.username}` : undefined}
      footer={<><Button variant="secondary" disabled={busy} onClick={close}>Cancel</Button><Button icon="key-2-line" loading={busy} disabled={busy} onClick={issue}>Issue new code</Button></>}>
      {account && (
        <div className="space-y-3 text-[13.5px]">
          <p>Use this when the code was lost or expired, or the resident forgot their password.</p>
          <Notice tone="warn">{account.mustChangePassword ? "The previous activation code stops working at once." : "The resident's current password stops working at once and they are signed out everywhere."} They must sign in with the new code and choose a password.</Notice>
          {error && <div role="alert"><Notice tone="warn">{error}</Notice></div>}
        </div>
      )}
    </Drawer>
  );
}

/** One-time handoff: username + activation code + expiry + instructions. Nothing is kept after closing. */
function HandoffDrawer({ handoff, onClose }: { handoff: Handoff; onClose: () => void }) {
  const [copied, setCopied] = useState(false);
  const { credentials: c } = handoff;
  const loginUrl = `${window.location.origin}/app/login`;
  function printSlip() {
    // A separate window with ONLY the slip (no app data, nothing in its URL).
    const w = window.open("", "_blank", "width=520,height=640");
    if (!w) return;
    const esc = (s: string) => s.replace(/[&<>"]/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[ch]!);
    w.document.write(`<!doctype html><html><head><meta charset="utf-8"><title>Resident portal activation</title>
<style>body{font-family:Arial,sans-serif;color:#1f2933;margin:28px}h1{font-size:18px;color:#0f2a3d;margin:0 0 4px}p{font-size:13px;line-height:1.5}
.box{border:1px solid #c9d2d8;border-radius:8px;padding:14px 16px;margin:14px 0}.k{font-size:11px;color:#5b6773;letter-spacing:1px}.v{font-size:17px;font-weight:bold;margin:2px 0 10px}
.code{font-family:Consolas,monospace;font-size:24px;letter-spacing:3px}ol{font-size:13px;line-height:1.6;padding-left:18px}.note{font-size:11.5px;color:#5b6773;border-top:1px solid #e5e9ec;padding-top:10px}</style></head><body>
<h1>Resident portal activation</h1><p>For <b>${esc(handoff.name)}</b> · unit ${esc(handoff.units.join(", "))}</p>
<div class="box"><div class="k">USERNAME</div><div class="v">${esc(c.username)}</div><div class="k">ACTIVATION CODE</div><div class="v code">${esc(c.activationCode)}</div>
<div class="k">VALID UNTIL</div><div class="v" style="font-size:14px">${esc(dateTimeLabel(c.expiresAt))}</div></div>
<ol><li>Open <b>${esc(loginUrl)}</b> on the building network.</li><li>Sign in with the username and the activation code as the password.</li>
<li>Choose your own password (at least 10 characters). The activation code then stops working.</li></ol>
<p class="note">Keep this slip private and destroy it after activating. Printed or downloaded copies are outside the system's control. If the code is lost or expires, ask the Admin Office for a new one.</p>
<script>window.onload=function(){window.print()}</script></body></html>`);
    w.document.close();
  }
  async function copy() {
    try { await navigator.clipboard.writeText(c.activationCode); setCopied(true); } catch { setCopied(false); }
  }
  return (
    <Drawer open onClose={onClose} width="max-w-lg" title="Hand over the activation details" subtitle={`${handoff.name} · unit ${handoff.units.join(", ")}`}
      footer={<><Button variant="secondary" icon="printer-line" onClick={printSlip}>Print slip</Button><Button icon="check-line" onClick={onClose}>Done, I handed it over</Button></>}>
      <div className="space-y-4 text-[13.5px]">
        <Notice tone="warn" title="Shown only now">This code is not stored in readable form and can't be shown again. If it's lost, issue a new one.</Notice>
        <dl className="overflow-hidden rounded-xl border border-ink-200">
          <div className="px-4 py-3"><dt className="text-[11px] font-semibold tracking-[0.08em] text-ink-500 uppercase">Username</dt><dd className="text-[17px] font-semibold text-ink-900">{c.username}</dd></div>
          <div className="border-t border-ink-100 bg-brand-50/60 px-4 py-3">
            <dt className="text-[11px] font-semibold tracking-[0.08em] text-ink-500 uppercase">Activation code</dt>
            <dd className="flex flex-wrap items-center justify-between gap-2">
              <span className="font-mono text-[24px] font-bold tracking-[0.18em] text-[#0f2a3d]" aria-label={`Activation code ${c.activationCode.split("").join(" ")}`}>{c.activationCode}</span>
              <Button size="sm" variant="ghost" icon={copied ? "check-line" : "file-copy-line"} onClick={copy}>{copied ? "Copied" : "Copy"}</Button>
            </dd>
          </div>
          <div className="border-t border-ink-100 px-4 py-3"><dt className="text-[11px] font-semibold tracking-[0.08em] text-ink-500 uppercase">Valid until</dt><dd className="font-semibold text-ink-900">{dateTimeLabel(c.expiresAt)}</dd></div>
        </dl>
        <div>
          <p className="font-semibold text-ink-900"><Icon name="information-line" /> Tell the resident</p>
          <ol className="mt-1 list-decimal space-y-1 pl-5 text-ink-700">
            <li>Open <b>{loginUrl}</b> on the building network.</li>
            <li>Sign in with the username and the activation code as the password.</li>
            <li>Choose their own password. The activation code then stops working.</li>
          </ol>
        </div>
        <p className="text-[12.5px] text-ink-500">Hand it over in person. A printed slip is outside the system's control: ask the resident to destroy it after activating.</p>
      </div>
    </Drawer>
  );
}
