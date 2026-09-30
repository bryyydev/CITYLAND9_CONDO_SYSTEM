// Routes. Each role has its own workspace at /app/<portal>; a user can only enter their own.
// Inside it, every module route checks the backend permission (ModuleRoute).
import type { ReactNode } from "react";
import { Link, Navigate, Route, Routes, useLocation, useParams } from "react-router-dom";
import { useAuth } from "./auth/AuthContext";
import { Card, EmptyState, ErrorState } from "./components/base/ui";
import AppShell from "./components/feature/AppShell";
import { ModuleRoute } from "./components/feature/ModuleRoute";
import { MODULES, MODULE_CATALOG, type ModuleId } from "./config/modules";
import { ROLES, ROLE_ORDER } from "./config/roles";
import { AccountingDashboard, BillingAdvancesPage, CollectionsPage, FinancialReportsPage } from "./pages/accounting/AccountingPages";
import { AttendanceEntryPage, AttendancePage, EmployeesPage, HrDashboard, LeavePage, PayrollPage, StaffDashboard, TaxRulesPage } from "./pages/hr/HrPages";
import Login from "./pages/Login";
import { AnnouncementsPage, CertificatesPage, DocumentsPage, ExpensesPage, GatePassesPage, MaintenancePage, VendorsPage } from "./pages/operations/OperationsPages";
import BillingPage from "./pages/property/Billing";
import { AdvancesWorkspace, ReceiptsLedger } from "./pages/property/Ledger";
import { AdminDashboard, ReportsPage, UnitsPage, WaterReadingsPage } from "./pages/property/PropertyPages";
import {
  ResidentGatePass, ResidentHome, ResidentMaintenance, ResidentNotices, ResidentPayments, ResidentProfilePage,
  ResidentReceipt, ResidentSoaDetail, ResidentSoaList, ResidentWater,
} from "./pages/resident/ResidentPages";
import { AuditLogsPage, ModulesLauncher, ResidentAccountsPage, RatesRulesPage, SystemDashboard, SystemSettingsPage, UsersPage } from "./pages/system/SystemPages";
import WorkspaceHome from "./pages/WorkspaceHome";
import { IS_MOCK } from "./services/api";
import type { Role } from "./services/types";

const PAGES: Partial<Record<ModuleId, ReactNode>> = {
  modules: <ModulesLauncher />,
  users: <UsersPage />, residentAccounts: <ResidentAccountsPage />, ratesRules: <RatesRulesPage />, auditLogs: <AuditLogsPage />, systemSettings: <SystemSettingsPage />,
  units: <UnitsPage />, billing: <BillingPage />, payments: <ReceiptsLedger />, advances: <AdvancesWorkspace />, water: <WaterReadingsPage />, reports: <ReportsPage variant="property" />,
  billingAdvances: <BillingAdvancesPage />, collections: <CollectionsPage />, financialReports: <FinancialReportsPage />,
  certificates: <CertificatesPage />, gatePasses: <GatePassesPage />, expenses: <ExpensesPage />, maintenance: <MaintenancePage />, vendors: <VendorsPage />, documents: <DocumentsPage />, announcements: <AnnouncementsPage />,
  employees: <EmployeesPage />, attendance: <AttendancePage />, attendanceEntry: <AttendanceEntryPage />, leave: <LeavePage />, payroll: <PayrollPage />, taxRules: <TaxRulesPage />,
  rSoa: <ResidentSoaList />, rPayments: <ResidentPayments />, rWater: <ResidentWater />, rMaintenance: <ResidentMaintenance />, rGatePass: <ResidentGatePass />, rNotices: <ResidentNotices />, rProfile: <ResidentProfilePage />,
};

const DASHBOARDS: Record<Role, ReactNode> = {
  super_admin: <SystemDashboard />, admin: <AdminDashboard />, manager: <HrDashboard />, staff: <StaffDashboard />, accounting: <AccountingDashboard />, resident: <ResidentHome />,
};

/** Old bookmarked paths inside a workspace -> their new names (resident portal before 2026-10, classic URLs). */
const RESIDENT_MOVED: Record<string, string> = { soa: "my-soa", payments: "payment-history", maintenance: "my-maintenance", notices: "announcements" };
const STAFF_MOVED: Record<string, string> = {
  receipts: "payments", water: "water-readings", "move-certificate": "certificates", "gate-pass": "gate-passes", audit: "audit-logs", settings: "rates-rules",
  "resident-users": "resident-accounts", "billing/advance": "advances", "employees/attendance": "attendance", "employees/leave": "leave", "employees/payroll": "payroll", "employees/hr-settings": "tax-rules",
};

function modulesFor(role: Role): ModuleId[] {
  const ids = new Set(ROLES[role].nav.flatMap((g) => g.items));
  if (role === "super_admin") MODULE_CATALOG.forEach((g) => g.ids.forEach((id) => ids.add(id)));
  return [...ids].filter((id) => MODULES[id].path !== "");
}

function Workspace({ role }: { role: Role }) {
  const { user, checkError, refresh } = useAuth();
  const location = useLocation();
  if (checkError) return <div className="grid min-h-dvh place-items-center p-4"><ErrorState title="Can't reach the CityLand 9 server" message={checkError} onRetry={refresh} /></div>;
  if (user === undefined) return <FullPageLoading />;
  if (!user) return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  if (user.role !== role) return <Navigate to={`/${ROLES[user.role].portal}`} replace />;
  return <AppShell />;
}

function Dashboard({ role }: { role: Role }) {
  // Live staff dashboards need APIs that aren't built yet: show the module launcher instead.
  if (!IS_MOCK && role !== "resident") return <WorkspaceHome />;
  return <>{DASHBOARDS[role]}</>;
}

function MovedOrMissing({ role }: { role: Role }) {
  const rest = useParams()["*"] ?? "";
  const cfg = ROLES[role];
  const hit = Object.entries(role === "resident" ? RESIDENT_MOVED : STAFF_MOVED).sort((a, b) => b[0].length - a[0].length).find(([old]) => rest === old || rest.startsWith(`${old}/`));
  if (hit) return <Navigate to={`/${cfg.portal}/${hit[1]}${rest.slice(hit[0].length)}`} replace />;
  return <NotFound home={`/${cfg.portal}`} />;
}

function NotFound({ home }: { home: string }) {
  return (
    <Card><EmptyState icon="map-pin-line" title="Page not found" action={<Link to={home} className="inline-flex h-10 items-center gap-2 rounded-lg bg-brand-600 px-4 font-semibold text-white hover:bg-brand-700">Go to my workspace</Link>}>
      The link may be old, or the page isn't part of your workspace.
    </EmptyState></Card>
  );
}

function FullPageLoading() {
  return (
    <div className="grid min-h-dvh place-items-center">
      <div className="flex flex-col items-center gap-3 text-ink-500" aria-busy="true">
        <div className="grid size-14 animate-pulse place-items-center rounded-2xl bg-brand-600 font-display text-lg font-bold text-white">C9</div>
        Loading CityLand 9…
      </div>
    </div>
  );
}

function HomeRedirect() {
  const { user } = useAuth();
  if (user === undefined) return <FullPageLoading />;
  return <Navigate to={user ? `/${ROLES[user.role].portal}` : "/login"} replace />;
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      {ROLE_ORDER.map((role) => {
        const portal = ROLES[role].portal;
        return (
          <Route key={role} path={`/${portal}`} element={<Workspace role={role} />}>
            <Route index element={<Dashboard role={role} />} />
            {modulesFor(role).map((id) => (
              <Route key={id} path={MODULES[id].path.slice(1)} element={<ModuleRoute id={id}>{PAGES[id]}</ModuleRoute>} />
            ))}
            {role === "resident" && <>
              <Route path="my-soa/:billId" element={<ModuleRoute id="rSoa"><ResidentSoaDetail /></ModuleRoute>} />
              <Route path="payment-history/:receiptId" element={<ModuleRoute id="rPayments"><ResidentReceipt /></ModuleRoute>} />
            </>}
            <Route path="*" element={<MovedOrMissing role={role} />} />
          </Route>
        );
      })}
      <Route path="*" element={<HomeRedirect />} />
    </Routes>
  );
}
