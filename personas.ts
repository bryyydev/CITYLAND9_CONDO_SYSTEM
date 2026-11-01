// Prototype identities for the Role Switcher. Imported only by the mock data source, so these
// names never ship in a production build (in live mode the user comes from /api/auth/me).
import type { Role } from "./types";

export interface Persona {
  username: string;
  displayName: string;
  email: string;
  subtitle: string;
  unitNo?: string;
}

export const PERSONAS: Record<Role, Persona> = {
  super_admin: { username: "rafael.cruz", displayName: "Rafael Cruz", email: "rafael.cruz@cityland9.ph", subtitle: "System Owner" },
  admin: { username: "melissa.bautista", displayName: "Melissa Bautista", email: "melissa.bautista@cityland9.ph", subtitle: "Property Manager" },
  manager: { username: "jason.fernandez", displayName: "Jason Fernandez", email: "jason.fernandez@cityland9.ph", subtitle: "HR & Payroll Officer" },
  staff: { username: "rico.dizon", displayName: "Rico Dizon", email: "rico.dizon@cityland9.ph", subtitle: "Front Desk" },
  accounting: { username: "karen.uy", displayName: "Karen Uy", email: "karen.uy@cityland9.ph", subtitle: "Accountant" },
  resident: { username: "marcus.v", displayName: "Marcus Villanueva", email: "marcus.v@email.com", subtitle: "Unit Owner · 12-01", unitNo: "12-01" },
};
