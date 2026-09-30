// The six workspaces. Each role sees only its own navigation; every item is also filtered by
// the backend permission matrix (config/permissions.ts), so a menu can never offer something
// the server would refuse.
import type { Role } from "../services/types";
import type { ModuleId } from "./modules";

export interface RoleConfig {
  role: Role;
  label: string;
  workspace: string;
  portal: string; // URL segment: /app/<portal>
  tagline: string;
  badge: string; // Tailwind classes for the header badge
  nav: { group: string; items: ModuleId[] }[];
}

export const ROLES: Record<Role, RoleConfig> = {
  super_admin: {
    role: "super_admin",
    label: "Superadmin",
    workspace: "System Administration",
    portal: "superadmin",
    tagline: "System Owner · IT Administrator",
    badge: "bg-ink-900 text-white",
    nav: [
      { group: "Workspace", items: ["dashboard"] },
      { group: "Administration", items: ["users", "residentAccounts", "ratesRules", "auditLogs", "systemSettings"] },
      { group: "Operations", items: ["modules"] },
    ],
  },
  admin: {
    role: "admin",
    label: "Admin",
    workspace: "Property Management",
    portal: "admin",
    tagline: "Property Manager · Office Head",
    badge: "bg-brand-600 text-white",
    nav: [
      { group: "Workspace", items: ["dashboard"] },
      { group: "Property & Billing", items: ["units", "billing", "payments", "advances", "water"] },
      { group: "Operations", items: ["certificates", "gatePasses", "expenses"] },
      { group: "Community", items: ["maintenance", "announcements", "vendors", "documents"] },
      { group: "Reports", items: ["reports"] },
    ],
  },
  manager: {
    role: "manager",
    label: "HR & Payroll",
    workspace: "HR & Payroll",
    portal: "hr",
    tagline: "Manager · HR & Payroll Officer",
    badge: "bg-copper-600 text-white",
    nav: [
      { group: "Workspace", items: ["dashboard"] },
      { group: "People", items: ["employees", "attendance", "leave"] },
      { group: "Payroll", items: ["payroll", "taxRules"] },
      { group: "Communication", items: ["announcements"] },
    ],
  },
  staff: {
    role: "staff",
    label: "Staff",
    workspace: "Front Desk",
    portal: "staff",
    tagline: "Front Desk · Admin Assistant · Security",
    badge: "bg-emerald-700 text-white",
    nav: [
      { group: "Workspace", items: ["dashboard"] },
      { group: "Front Desk", items: ["gatePasses", "certificates", "maintenance"] },
      { group: "Records", items: ["expenses", "attendanceEntry"] },
    ],
  },
  accounting: {
    role: "accounting",
    label: "Accounting",
    workspace: "Finance & Accounting",
    portal: "accounting",
    tagline: "Accountant · Bookkeeper",
    badge: "bg-amber-600 text-ink-900",
    nav: [
      { group: "Workspace", items: ["dashboard"] },
      { group: "Finance", items: ["financialReports", "billingAdvances", "collections"] },
      { group: "Controls", items: ["auditLogs"] },
    ],
  },
  resident: {
    role: "resident",
    label: "Resident",
    workspace: "Resident Portal",
    portal: "resident",
    tagline: "Unit Owner · Tenant",
    badge: "bg-brand-100 text-brand-800",
    nav: [
      { group: "Home", items: ["rHome"] },
      { group: "My Unit", items: ["rSoa", "rPayments", "rWater"] },
      { group: "Requests", items: ["rMaintenance", "rGatePass"] },
      { group: "Community", items: ["rNotices"] },
      { group: "Account", items: ["rProfile"] },
    ],
  },
};

export const ROLE_ORDER: Role[] = ["super_admin", "admin", "manager", "staff", "accounting", "resident"];

export const roleByPortal = (portal: string) => ROLE_ORDER.map((r) => ROLES[r]).find((c) => c.portal === portal);
