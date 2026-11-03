// Indicadores de feature Pro, consistentes en toda la UI. El backend es la
// autoridad del gate (402); esto solo informa y enlaza a la comparativa de planes.
import { Link } from "react-router-dom";

import { useT } from "../i18n";

/** Chip "PRO" para marcar una feature de pago. */
export function ProBadge({ tier = "PRO" }: { tier?: string }) {
  return <span className="pro-badge" title={tier}>{tier}</span>;
}

/** Aviso inline: la feature es Pro, con enlace a la comparativa de planes. */
export function ProNotice({ msg }: { msg?: string }) {
  const t = useT();
  return (
    <div className="alert info pro-notice" role="status">
      <ProBadge />
      <span>{msg ?? t("pro.feature")}</span>
      <Link to="/plans" className="ext-link sm">{t("pro.seePlans")}</Link>
    </div>
  );
}
