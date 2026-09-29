import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { api, ApiError, setCsrfToken } from "../api/client.js";

// Who is signed in, as reported by the server (GET /api/auth/me):
//   { username, role, roleLabel, portal, home, unitId, permissions[] }
// The server decides role, unit and permissions; React only reads them.
// Hiding things in React is for convenience: the Flask API enforces every rule again.

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  // undefined = still checking the session, null = signed out
  const [user, setUser] = useState(undefined);
  const [checkError, setCheckError] = useState(null);

  const refresh = useCallback(async () => {
    setCheckError(null);
    try {
      const data = await api("/auth/me");
      setUser(data.user);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) setUser(null);
      else setCheckError(err.message);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const login = useCallback(async (username, password) => {
    const data = await api("/auth/login", { method: "POST", body: { username, password } });
    setCsrfToken(data.csrfToken); // the session was renewed, so is the token
    setUser(data.user);
    return data.user;
  }, []);

  const logout = useCallback(async () => {
    try {
      await api("/auth/logout", { method: "POST" });
    } finally {
      setCsrfToken(null);
      setUser(null);
    }
  }, []);

  const value = useMemo(() => {
    const allowed = new Set(user?.permissions || []);
    return {
      user,
      role: user?.role ?? null,
      unitId: user?.unitId ?? null,
      portal: user?.portal ?? null,
      checkError,
      refresh,
      login,
      logout,
      /** can("mark_bill_paid") -> may this user use that function? */
      can: (permission) => allowed.has(permission),
      /** hasRole(["admin", "accounting"]) */
      hasRole: (roles) => !!user && roles.includes(user.role),
    };
  }, [user, checkError, refresh, login, logout]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export const useAuth = () => useContext(AuthContext);

/** Render children only when the user has `permission` (hides elevated buttons/modals). */
export function Can({ permission, children, fallback = null }) {
  const { can } = useAuth();
  return can(permission) ? children : fallback;
}
