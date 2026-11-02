// Sesión del front (patrón WIF AuthContext, login de dos pasos). Aquí NO hay
// primitivas de auth: el token lo emite y verifica el backend; el front solo lo
// guarda y lo adjunta. El mfa_token vive SOLO en memoria (nunca en storage).
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";

import { authApi } from "../api/admin";
import { setUnauthorizedHandler } from "../api/http";
import { tNow } from "../i18n";
import type { Me } from "../api/types";
import { tokenStorage } from "./tokenStorage";

export type LoginOutcome = { kind: "mfa" } | { kind: "ok" };

interface AuthValue {
  user: Me | null;
  /** true mientras se resuelve la sesión inicial (token guardado -> /auth/me). */
  loading: boolean;
  /** true entre el paso 1 y el paso 2 del login. */
  awaitingMfa: boolean;
  login: (username: string, password: string) => Promise<LoginOutcome>;
  completeMfaLogin: (code: string) => Promise<void>;
  cancelMfa: () => void;
  logout: () => void;
  refreshUser: () => Promise<void>;
}

const AuthCtx = createContext<AuthValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<Me | null>(null);
  const [loading, setLoading] = useState<boolean>(() => tokenStorage.get() !== null);
  const [awaitingMfa, setAwaitingMfa] = useState(false);
  const mfaToken = useRef<string | null>(null);

  // 401 en cualquier llamada: el token ya fue limpiado; RequireAuth redirige a /login.
  useEffect(() => {
    setUnauthorizedHandler(() => {
      setUser(null);
      setLoading(false);
    });
    return () => setUnauthorizedHandler(null);
  }, []);

  // Sesión inicial.
  useEffect(() => {
    if (tokenStorage.get() === null) return;
    let active = true;
    authApi
      .me()
      .then((me) => active && setUser(me))
      .catch(() => {
        // 401 ya limpió el token; ante error de red no hay sesión verificable.
        if (active) setUser(null);
      })
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, []);

  const refreshUser = useCallback(async () => {
    setUser(await authApi.me());
  }, []);

  const establishSession = useCallback(async (accessToken: string) => {
    tokenStorage.set(accessToken);
    try {
      setUser(await authApi.me());
    } catch (e) {
      tokenStorage.clear();
      throw e;
    }
  }, []);

  const login = useCallback(
    async (username: string, password: string): Promise<LoginOutcome> => {
      const r = await authApi.login(username, password);
      if (r.mfa_required) {
        // Sin access_token: el segundo factor completa la sesión.
        mfaToken.current = r.mfa_token;
        setAwaitingMfa(true);
        return { kind: "mfa" };
      }
      if (!r.access_token) throw new Error(tNow("auth.noAccessToken"));
      await establishSession(r.access_token);
      return { kind: "ok" };
    },
    [establishSession],
  );

  const completeMfaLogin = useCallback(
    async (code: string) => {
      if (!mfaToken.current) throw new Error(tNow("auth.mfaExpired"));
      const r = await authApi.verifyMfa(mfaToken.current, code);
      await establishSession(r.access_token);
      mfaToken.current = null;
      setAwaitingMfa(false);
    },
    [establishSession],
  );

  const cancelMfa = useCallback(() => {
    mfaToken.current = null;
    setAwaitingMfa(false);
  }, []);

  const logout = useCallback(() => {
    tokenStorage.clear();
    mfaToken.current = null;
    setAwaitingMfa(false);
    setUser(null);
  }, []);

  const value = useMemo<AuthValue>(
    () => ({ user, loading, awaitingMfa, login, completeMfaLogin, cancelMfa, logout, refreshUser }),
    [user, loading, awaitingMfa, login, completeMfaLogin, cancelMfa, logout, refreshUser],
  );
  return <AuthCtx.Provider value={value}>{children}</AuthCtx.Provider>;
}

export function useAuth(): AuthValue {
  const ctx = useContext(AuthCtx);
  if (!ctx) throw new Error("useAuth debe usarse dentro de <AuthProvider>.");
  return ctx;
}
