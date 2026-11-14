// Botón "Actualizar ahora": invalida la caché del backend y recarga la página para
// traer datos frescos de las fuentes sin esperar el TTL. Solo operator/admin (dispara
// llamadas salientes a las APIs). Al terminar recarga; en error vuelve a habilitarse.
import { useState } from "react";

import { useAuth } from "../auth/AuthContext";
import { useT } from "../i18n";

export function RefreshButton({ onRefresh }: { onRefresh: () => Promise<unknown> }) {
  const t = useT();
  const { user } = useAuth();
  const [busy, setBusy] = useState(false);
  if (user?.role !== "operator" && user?.role !== "admin") return null;

  const go = async () => {
    setBusy(true);
    try {
      await onRefresh();
      window.location.reload(); // la caché ya está limpia: la recarga pide datos frescos
    } catch {
      setBusy(false); // deja reintentar
    }
  };

  return (
    <button type="button" className="btn sm" onClick={go} disabled={busy} title={t("refresh.title")}>
      {busy ? t("refresh.busy") : t("refresh.label")}
    </button>
  );
}
