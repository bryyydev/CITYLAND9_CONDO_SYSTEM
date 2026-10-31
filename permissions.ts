// Read access to the backend permission matrix.
// permissions.json is GENERATED from backend/app/core/permissions.py
// (python database/tools/export_permissions.py; a test fails if it is stale).
// Live mode uses the list the server sends for the signed-in user; the prototype uses this copy.
import matrix from "./permissions.json";
import type { Role } from "../services/types";

const table = matrix.permissions as Record<string, string[]>;
const anySignedIn = new Set(matrix.anySignedIn);

/** Same rule as the server: unknown permissions are denied. */
export const roleCan = (role: Role, permission: string): boolean =>
  anySignedIn.has(permission) || (table[permission] ?? []).includes(role);

export const permissionsForRole = (role: Role): string[] =>
  Object.keys(table).filter((key) => table[key].includes(role)).sort();
