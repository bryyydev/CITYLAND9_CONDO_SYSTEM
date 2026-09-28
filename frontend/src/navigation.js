// The ONE navigation definition for the React app: sidebar, routes, breadcrumbs
// and page titles all come from here.
//
// `key` is the legacy Flask endpoint name. The server decides which keys a user
// may open (GET /api/auth/me -> permissions), using the same role table as the
// legacy sidebar, so menu visibility can never drift from backend authorization.
//
// `path` mirrors the legacy URL, so /app/billing/advance <-> /billing/advance.
// `migrated: false` means the React page links to the legacy page for now.

export const NAV = [
  {
    group: "WORKSPACE",
    items: [{ key: "dashboard", label: "Dashboard", icon: "home", path: "/dashboard" }],
  },
  {
    group: "CONDO MANAGEMENT",
    items: [
      {
        label: "Units", icon: "building",
        children: [{ key: "units", label: "Unit Directory", path: "/units" }],
      },
      {
        label: "Billing & SOA", icon: "bill",
        children: [
          { key: "billing", label: "Billing Management", path: "/billing" },
          { key: "advance_payments", label: "Advance Payments", path: "/billing/advance" },
          { key: "billing_email", label: "SOA Email", path: "/billing/email" },
        ],
      },
      { key: "water", label: "Water Readings", icon: "drop", path: "/water" },
    ],
  },
  {
    group: "OPERATIONS",
    items: [
      { key: "move_certificate", label: "Move In / Out", icon: "swap", path: "/move-certificate" },
      { key: "gate_pass", label: "Gate Pass", icon: "pass", path: "/gate-pass" },
      { key: "expenses", label: "Expenses", icon: "receipt", path: "/expenses" },
    ],
  },
  {
    group: "EMPLOYEE MANAGEMENT",
    items: [
      {
        key: "employees", label: "Employees", icon: "users", path: "/employees",
        children: [
          { key: "employee_attendance", label: "Attendance", path: "/employees/attendance" },
          { key: "employee_leave", label: "Leave", path: "/employees/leave" },
          { key: "employee_overtime", label: "Overtime", path: "/employees/overtime" },
          { key: "employee_payroll", label: "Payroll", path: "/employees/payroll" },
          { key: "employee_hr_loans", label: "Loans & Deductions", path: "/employees/hr/loans" },
          { key: "employee_13th_month_full", label: "13th Month", path: "/employees/13th-month" },
        ],
      },
      { key: "employee_hr_settings", label: "Payroll Rules", icon: "gear", path: "/employees/hr-settings" },
      { key: "employee_payroll_reports", label: "Payroll Reports", icon: "chart", path: "/employees/payroll-reports" },
    ],
  },
  {
    group: "COMMUNITY SERVICES",
    items: [
      { key: "resident_portal", label: "Resident Portal", icon: "door", path: "/portal" },
      { key: "resident_users", label: "Resident Accounts", icon: "users", path: "/resident-users" },
      { key: "announcements", label: "Announcements", icon: "megaphone", path: "/announcements" },
      { key: "maintenance", label: "Maintenance", icon: "wrench", path: "/maintenance" },
      { key: "vendors", label: "Vendors", icon: "truck", path: "/vendors" },
      { key: "documents", label: "Documents", icon: "file", path: "/documents" },
    ],
  },
  {
    group: "ADMINISTRATION",
    items: [
      { key: "reports", label: "Condo Reports", icon: "chart", path: "/reports" },
      { key: "users", label: "Users & Access", icon: "shield", path: "/users" },
      { key: "audit_logs", label: "Audit Logs", icon: "list", path: "/audit" },
      { key: "settings", label: "Rates & Rules", icon: "gear", path: "/settings" },
    ],
  },
];

// Flat list of every page: {key, label, path, group, parent, icon}
export const PAGES = NAV.flatMap(({ group, items }) =>
  items.flatMap((item) => [
    ...(item.key ? [{ ...item, group, parent: null }] : []),
    ...(item.children || []).map((child) => ({ ...child, group, parent: item.label, icon: item.icon })),
  ]),
);

export const pageByKey = (key) => PAGES.find((p) => p.key === key);

// Where each role lands after sign-in (the server sends the endpoint name).
export const homePath = (user) => pageByKey(user?.home)?.path ?? "/dashboard";

// Legacy pages are on the same server. In `npm run dev` they are on :5000.
export const LEGACY_ORIGIN = import.meta.env.DEV ? "http://127.0.0.1:5000" : "";
export const legacyUrl = (path) => `${LEGACY_ORIGIN}${path}`;
