// Accounting workspace: collections, receivables, bills & advances, official receipts.
import { useState } from "react";
import { Link } from "react-router-dom";
import { useUser } from "../../auth/AuthContext";
import { Card, CardHeader, ErrorState, Icon, PageHeader, Skeleton, StatCard, Tabs } from "../../components/base/ui";
import { useAsync } from "../../hooks/useAsync";
import { dateTimeLabel, monthLabel } from "../../lib/format";
import { pesoShort, toCents } from "../../lib/money";
import { IS_MOCK, api } from "../../services/api";
import { BillingWorkspace } from "../property/Billing";
import { AdvancesWorkspace, ReceiptQuickList, ReceiptsLedger } from "../property/Ledger";
import { LegacyBridge } from "../../components/feature/ModuleRoute";
import { MODULES } from "../../config/modules";
import { CollectionsChart, ReportsPage } from "../property/PropertyPages";

const pct = (n: number) => `${Math.round(n * 100)}%`;

export function AccountingDashboard() {
  const user = useUser();
  const collections = useAsync(() => api.admin.collections(6), []);
  const audit = useAsync(() => api.admin.auditLogs({ q: "", user: "", from: "", to: "", page: 1, perPage: 6 }).then((p) => p.entries), []);
  if (collections.error) return <ErrorState message={collections.error.message} onRetry={collections.reload} />;
  const data = collections.data;
  const last = data?.at(-2);
  const now = data?.at(-1);
  return (
    <>
      <PageHeader eyebrow={`Good day, ${user.displayName?.split(" ")[0] ?? user.username}`} title="Finance overview" description="Collections, receivables and official receipts." />
      {!data || !last || !now ? <Card><Skeleton rows={6} /></Card> : (
        <>
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <StatCard tone="hero" icon="hand-coin-line" label={`Collected · ${monthLabel(last.month, "short")}`} value={pesoShort(last.collected)} hint={`${pct(last.collectionRate)} of ${pesoShort(last.billed)} billed`} />
            <StatCard icon="calendar-line" label={`Collected · ${monthLabel(now.month, "short")} so far`} value={pesoShort(now.collected)} />
            <StatCard tone="copper" icon="error-warning-line" label="Receivables" value={pesoShort(now.outstanding)} hint="Balances on latest SOAs" />
            <StatCard icon="line-chart-line" label="6-month collection rate" value={pct(data.reduce((s, c) => s + toCents(c.collected), 0) / Math.max(data.reduce((s, c) => s + toCents(c.billed), 0), 1))} />
          </div>
          <div className="mt-6 grid gap-6 xl:grid-cols-[1.6fr_1fr]">
            <Card><CardHeader title="Billed vs collected" actions={<Link className="text-[13px] font-semibold text-brand-600 hover:underline" to="/accounting/financial-reports">Full report</Link>} /><div className="p-5"><CollectionsChart data={data} /></div></Card>
            <Card><CardHeader title="Latest official receipts" actions={<Link className="text-[13px] font-semibold text-brand-600 hover:underline" to="/accounting/collections">All</Link>} /><ReceiptQuickList /></Card>
          </div>
          <Card className="mt-6">
            <CardHeader title="Recent activity" subtitle="From the audit log (read-only)" actions={<Link className="text-[13px] font-semibold text-brand-600 hover:underline" to="/accounting/audit-logs">Audit logs</Link>} />
            {!audit.data ? <Skeleton rows={4} /> : (
              <ul className="divide-y divide-ink-100">
                {audit.data.map((a) => (
                  <li key={a.id} className="flex items-start gap-3 px-5 py-2.5">
                    <Icon name="history-line" className="mt-0.5 text-ink-400" />
                    <div className="min-w-0 flex-1"><p className="truncate text-ink-800">{a.action}</p><p className="text-[12px] text-ink-500">{a.username} · {dateTimeLabel(a.at)}</p></div>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </>
      )}
    </>
  );
}

export function BillingAdvancesPage() {
  const [tab, setTab] = useState<"bills" | "advances">("bills");
  return (
    <>
      <PageHeader eyebrow="Finance" title="Billing & Advance Payments" description="Monthly bills and SOAs, payments, and prepaid condo dues. Issued bills are frozen; recalculation is explicit and audited." />
      <div className="mb-4"><Tabs value={tab} onChange={setTab} tabs={[{ key: "bills", label: "Bills & SOA" }, { key: "advances", label: "Advance payments" }]} /></div>
      {/* Advance Payments still runs on its classic screen until it is rebuilt (never mock data). */}
      {tab === "bills" ? <BillingWorkspace embedded /> : IS_MOCK || MODULES.advances.live ? <AdvancesWorkspace embedded /> : <LegacyBridge id="advances" />}
    </>
  );
}

export const CollectionsPage = () => <ReceiptsLedger title="Collections & Official Receipts" eyebrow="Finance" recordAction />;
export const FinancialReportsPage = () => <ReportsPage variant="financial" />;
