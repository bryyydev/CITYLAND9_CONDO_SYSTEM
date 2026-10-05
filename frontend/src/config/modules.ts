// Every module of the system, defined once. Sidebars, routes, breadcrumbs, the module launcher
// and route guards all come from here.
//
//   permission - the backend permission key (backend/app/core/permissions.py). Deny by default:
//                a module is shown and routed only when the user holds this permission.
//   live       - true when the module runs on the Flask API today. In live mode, modules with
//                live: false show a link to their classic screen (legacyPath) instead of data.
//   path       - relative to the role's workspace: /app/<portal><path>

export type ModuleId =
  | "dashboard" | "modules"
  | "users" | "residentAccounts" | "ratesRules" | "auditLogs" | "systemSettings"
  | "units" | "billing" | "payments" | "advances" | "water" | "reports"
  | "billingAdvances" | "collections" | "financialReports"
  | "certificates" | "gatePasses" | "expenses" | "maintenance" | "vendors" | "documents" | "announcements"
  | "employees" | "attendance" | "attendanceEntry" | "leave" | "payroll" | "taxRules"
  | "rHome" | "rSoa" | "rPayments" | "rWater" | "rMaintenance" | "rGatePass" | "rNotices" | "rProfile";

export interface ModuleDef {
  id: ModuleId;
  label: string;
  icon: string; // Remix Icon name without the "ri-" prefix
  path: string;
  permission: string;
  live: boolean;
  legacyPath?: string;
  description: string;
}

const m = (d: ModuleDef) => d;

export const MODULES: Record<ModuleId, ModuleDef> = {
  dashboard: m({ id: "dashboard", label: "Dashboard", icon: "dashboard-3-line", path: "", permission: "index", live: true, description: "Overview of your workspace" }),
  modules: m({ id: "modules", label: "All Property Modules", icon: "apps-2-line", path: "/modules", permission: "index", live: true, description: "Every module in the system" }),

  users: m({ id: "users", label: "Users & Access Management", icon: "shield-user-line", path: "/users", permission: "users", live: true, description: "Staff accounts, roles and password resets" }),
  residentAccounts: m({ id: "residentAccounts", label: "Resident Accounts", icon: "user-heart-line", path: "/resident-accounts", permission: "resident_users", live: true, description: "Portal logins linked to owners and tenants" }),
  ratesRules: m({ id: "ratesRules", label: "Rates & Rules", icon: "scales-3-line", path: "/rates-rules", permission: "settings", live: true, description: "Dues rates, water, penalties, payment and email settings" }),
  auditLogs: m({ id: "auditLogs", label: "Audit Logs", icon: "file-shield-2-line", path: "/audit-logs", permission: "audit_logs", live: true, description: "Read-only record of every important action" }),
  systemSettings: m({ id: "systemSettings", label: "Settings", icon: "settings-4-line", path: "/settings", permission: "database_export", live: true, description: "Database backup, export and import" }),

  units: m({ id: "units", label: "Units Directory", icon: "building-2-line", path: "/units", permission: "units", live: true, description: "Units, owners, tenants, parking and storage" }),
  billing: m({ id: "billing", label: "Billing & SOA", icon: "file-list-3-line", path: "/billing", permission: "billing", live: true, description: "Monthly bills, statements and payments" }),
  payments: m({ id: "payments", label: "Payments & ORs", icon: "receipt-line", path: "/payments", permission: "receipts", live: true, description: "Official receipts ledger" }),
  advances: m({ id: "advances", label: "Advance Payments", icon: "calendar-check-line", path: "/advances", permission: "advance_payments", live: true, description: "Prepaid condo dues" }),
  water: m({ id: "water", label: "Water Readings", icon: "drop-line", path: "/water-readings", permission: "water", live: true, description: "Monthly meter readings" }),
  reports: m({ id: "reports", label: "Property Reports", icon: "bar-chart-box-line", path: "/reports", permission: "reports", live: true, description: "Collections and occupancy reports" }),

  billingAdvances: m({ id: "billingAdvances", label: "Billing & Advance Payments", icon: "file-list-3-line", path: "/billing-advances", permission: "billing", live: true, description: "Bills, SOAs and prepaid dues" }),
  collections: m({ id: "collections", label: "Collections & ORs", icon: "hand-coin-line", path: "/collections", permission: "receipts", live: true, description: "Payments received and official receipts" }),
  financialReports: m({ id: "financialReports", label: "Financial Reports", icon: "line-chart-line", path: "/financial-reports", permission: "reports", live: true, description: "Billing vs collections, receivables" }),

  certificates: m({ id: "certificates", label: "Move In/Out Certificates", icon: "truck-line", path: "/certificates", permission: "move_certificate", live: true, description: "Numbered move-in and move-out certificates" }),
  gatePasses: m({ id: "gatePasses", label: "Gate Passes", icon: "passport-line", path: "/gate-passes", permission: "gate_pass", live: true, description: "Issue passes and approve resident requests" }),
  expenses: m({ id: "expenses", label: "Expense Logs", icon: "wallet-3-line", path: "/expenses", permission: "expenses", live: true, description: "Building expenses" }),
  maintenance: m({ id: "maintenance", label: "Maintenance Tickets", icon: "tools-line", path: "/maintenance", permission: "maintenance_update", live: true, description: "Resident and office repair requests" }),
  vendors: m({ id: "vendors", label: "Vendor Directory", icon: "store-2-line", path: "/vendors", permission: "vendors", live: true, description: "Contractors and suppliers" }),
  documents: m({ id: "documents", label: "Documents", icon: "folder-3-line", path: "/documents", permission: "documents", live: true, description: "Document records" }),
  announcements: m({ id: "announcements", label: "Announcements", icon: "megaphone-line", path: "/announcements", permission: "announcements", live: true, description: "Notices for residents" }),

  employees: m({ id: "employees", label: "Employee Roster", icon: "team-line", path: "/employees", permission: "employees", live: true, description: "Employees and positions" }),
  attendance: m({ id: "attendance", label: "Attendance & Overtime", icon: "time-line", path: "/attendance", permission: "employee_overtime", live: true, description: "Daily attendance and overtime approvals" }),
  attendanceEntry: m({ id: "attendanceEntry", label: "Daily Attendance Entry", icon: "fingerprint-line", path: "/attendance-entry", permission: "employee_attendance", live: true, description: "Record today's time in and out" }),
  leave: m({ id: "leave", label: "Leave Management", icon: "calendar-event-line", path: "/leave", permission: "employee_leave", live: true, description: "Leave requests and approvals" }),
  payroll: m({ id: "payroll", label: "Payroll Engine", icon: "money-dollar-box-line", path: "/payroll", permission: "employee_payroll", live: true, description: "Payroll runs and payslips" }),
  taxRules: m({ id: "taxRules", label: "Philippine Tax Rules", icon: "government-line", path: "/tax-rules", permission: "employee_hr_settings", live: true, description: "SSS, PhilHealth, Pag-IBIG and BIR tables" }),

  rHome: m({ id: "rHome", label: "Home Dashboard", icon: "home-5-line", path: "", permission: "api_resident", live: true, description: "Your balance, statement and requests" }),
  rSoa: m({ id: "rSoa", label: "View & Download SOA", icon: "file-text-line", path: "/my-soa", permission: "api_resident", live: true, description: "Monthly statements of account" }),
  rPayments: m({ id: "rPayments", label: "Payment History", icon: "receipt-line", path: "/payment-history", permission: "api_resident", live: true, description: "Payments and official receipts" }),
  rWater: m({ id: "rWater", label: "Water Usage", icon: "drop-line", path: "/water", permission: "api_resident", live: true, description: "Monthly meter readings" }),
  rMaintenance: m({ id: "rMaintenance", label: "My Unit Maintenance", icon: "tools-line", path: "/my-maintenance", permission: "api_resident", live: true, description: "Report a problem in your unit" }),
  rGatePass: m({ id: "rGatePass", label: "Gate Pass & Moving", icon: "passport-line", path: "/gate-pass", permission: "api_resident", live: true, description: "Visitor, delivery and moving passes" }),
  rNotices: m({ id: "rNotices", label: "Announcements & Notices", icon: "megaphone-line", path: "/announcements", permission: "api_resident", live: true, description: "News from the administration" }),
  rProfile: m({ id: "rProfile", label: "Profile & Password", icon: "user-settings-line", path: "/profile", permission: "api_resident", live: true, description: "Contact details and password" }),
};

/** Groups shown by the Superadmin "All Property Modules" launcher. */
export const MODULE_CATALOG: { group: string; ids: ModuleId[] }[] = [
  { group: "Property & Billing", ids: ["units", "billing", "payments", "advances", "water", "reports"] },
  { group: "Operations", ids: ["certificates", "gatePasses", "expenses"] },
  { group: "Community", ids: ["maintenance", "announcements", "vendors", "documents"] },
  { group: "HR & Payroll", ids: ["employees", "attendance", "leave", "payroll", "taxRules"] },
];
