import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { api, ApiError, setCsrfToken } from "../api/client.js";

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
    return { user, checkError, refresh, login, logout, can: (key) => allowed.has(key) };
  }, [user, checkError, refresh, login, logout]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export const useAuth = () => useContext(AuthContext);
