// The ONE navigation definition for the staff-side portals: sidebar, routes,
// breadcrumbs and page titles all come from here.
//
// `key` is the permission (= legacy Flask endpoint name). The server sends the user's
// permissions (GET /api/auth/me), computed from backend/app/core/permissions.py, so the
// menu can never show something the API would refuse.
//
// `path` mirrors the legacy URL and is mounted under the user's portal:
//   /app/accounting/billing  ->  legacy page /billing
// Residents have their own navigation (RESIDENT_NAV) and pages.

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
          { key: "receipts", label: "Official Receipts", path: "/receipts" },
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

// Resident portal (real React pages, no legacy screens).
export const RESIDENT_NAV = [
  {
    group: "MY UNIT",
    items: [
      { key: "api_resident", label: "Home", icon: "home", path: "" },
      { key: "api_resident", label: "Statement of Account", icon: "bill", path: "/soa" },
      { key: "api_resident", label: "Maintenance Requests", icon: "wrench", path: "/maintenance" },
      { key: "api_resident", label: "Notices", icon: "megaphone", path: "/notices" },
    ],
  },
];

// Where a user lands after sign-in: /<portal> (each portal has its own home page).
export const homePath = (user) => (user?.portal ? `/${user.portal}` : "/login");

// Legacy pages are on the same server. In `npm run dev` they are on :5000.
export const LEGACY_ORIGIN = import.meta.env.DEV ? "http://127.0.0.1:5000" : "";
export const legacyUrl = (path) => `${LEGACY_ORIGIN}${path}`;
