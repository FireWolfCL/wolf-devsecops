// Guards de ruta. SOLO ergonomía: el servidor (require_role) es la autoridad;
// si el rol cambió o el token expiró, el 401/403 de la API manda.
import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";

import type { Role } from "../api/types";
import { useT } from "../i18n";
import { useAuth } from "./AuthContext";

export function RequireAuth({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();
  const location = useLocation();
  const t = useT();

  if (loading) return <p className="state">{t("auth.verifyingSession")}</p>;
  if (!user) return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  // Cambio de contraseña pendiente: forzar /profile hasta resolverlo.
  if (user.must_change_password && location.pathname !== "/profile") {
    return <Navigate to="/profile" replace />;
  }
  return <>{children}</>;
}

export function RequireRole({ role, children }: { role: Role; children: ReactNode }) {
  const { user } = useAuth();
  if (user && user.role !== role) return <Navigate to="/" replace />;
  return <>{children}</>;
}
