// Who is signed in, and what they may do.
// Live mode: the server decides (GET /api/auth/me returns role, unit and permissions).
// Prototype mode: the Role Switcher picks a persona; permissions come from the same matrix
// the server uses (config/permissions.json). Hiding things here is only for convenience:
// the Flask API checks every rule again on every request.
import { createContext, type ReactNode, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { ROLES } from "../config/roles";
import { IS_MOCK, api, setMockRole } from "../services/api";
import { ApiError } from "../services/http";
import type { Role, SessionUser } from "../services/types";

interface AuthValue {
  /** undefined = still checking the session, null = signed out */
  user: SessionUser | null | undefined;
  checkError: string | null;
  refresh: () => Promise<void>;
  login: (username: string, password: string) => Promise<SessionUser>;
  logout: () => Promise<void>;
  /** Prototype only: switch the whole workspace to another role. */
  switchRole: (role: Role) => Promise<SessionUser>;
  can: (permission: string) => boolean;
  /** Residents with several units: which unit the portal shows (checked by the server on every request). */
  selectUnit: (unitId: number) => void;
}

const UNIT_KEY = "cl9.residentUnit";
/** Apply the unit the resident chose earlier in this browser session, when they still have it. */
function withChosenUnit(u: SessionUser): SessionUser {
  if (u.role !== "resident" || !u.units || u.units.length < 2) return u;
  try {
    const chosen = Number(sessionStorage.getItem(UNIT_KEY));
    if (chosen && u.units.some((x) => x.id === chosen)) return { ...u, unitId: chosen };
  } catch { /* storage unavailable: keep the default unit */ }
  return u;
}

const AuthContext = createContext<AuthValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<SessionUser | null | undefined>(undefined);
  const [checkError, setCheckError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setCheckError(null);
    try {
      setUser(withChosenUnit(await api.auth.me()));
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) setUser(null);
      else setCheckError(err instanceof Error ? err.message : "Can't reach the server.");
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  const login = useCallback(async (username: string, password: string) => {
    const { user: signedIn } = await api.auth.login(username, password);
    setUser(withChosenUnit(signedIn));
    return signedIn;
  }, []);

  const logout = useCallback(async () => {
    try {
      await api.auth.logout();
    } finally {
      setUser(null);
    }
  }, []);

  const switchRole = useCallback(async (role: Role) => {
    if (!IS_MOCK) throw new Error("The Role Switcher only exists in prototype mode.");
    setMockRole(role);
    const next = await api.auth.me();
    setUser(next);
    return next;
  }, []);

  const selectUnit = useCallback((unitId: number) => {
    setUser((u) => (u && u.units?.some((x) => x.id === unitId) ? { ...u, unitId } : u));
    try { sessionStorage.setItem(UNIT_KEY, String(unitId)); } catch { /* not remembered: fine */ }
  }, []);

  const value = useMemo<AuthValue>(() => {
    const allowed = new Set(user?.permissions ?? []);
    return {
      user, checkError, refresh, login, logout, switchRole, selectUnit,
      can: (permission) => permission === "index" || permission === "change_password" || allowed.has(permission),
    };
  }, [user, checkError, refresh, login, logout, switchRole, selectUnit]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}

/** The signed-in user (only use below a route that requires sign-in). */
export function useUser(): SessionUser {
  const { user } = useAuth();
  if (!user) throw new Error("useUser needs a signed-in user");
  return user;
}

export const roleConfig = (user: SessionUser) => ROLES[user.role];

/** Render children only when the user holds `permission` (hides elevated buttons). */
export function Can({ permission, children, fallback = null }: { permission: string; children: ReactNode; fallback?: ReactNode }) {
  const { can } = useAuth();
  return <>{can(permission) ? children : fallback}</>;
}
