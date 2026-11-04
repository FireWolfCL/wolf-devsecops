// Agrupa las vistas de administración (Usuarios, Integraciones, Configuración,
// IA, Actualizaciones) bajo una sola sección con sub-pestañas, para no saturar el
// nav principal. El servidor sigue siendo la autoridad (cada página exige rol admin).
import { NavLink, Outlet } from "react-router-dom";

import { useT } from "../i18n";

const ADMIN_TABS = [
  { to: "/admin/users", label: "admin.tab.users" },
  { to: "/admin/integraciones", label: "admin.tab.integrations" },
  { to: "/admin/settings", label: "admin.tab.settings" },
  { to: "/admin/license", label: "admin.tab.license" },
  { to: "/admin/ai", label: "admin.tab.ai" },
  { to: "/admin/updates", label: "admin.tab.updates" },
  { to: "/admin/audit", label: "admin.tab.audit" },
  { to: "/admin/about", label: "admin.tab.about" },
];

export function AdminLayout() {
  const t = useT();
  return (
    <section className="admin-section">
      <div className="tools-head">
        <h2>{t("nav.admin")}</h2>
        <p className="muted">{t("admin.subtitle")}</p>
      </div>
      <nav className="tool-tabs" aria-label={t("nav.admin")}>
        {ADMIN_TABS.map((tab) => (
          <NavLink
            key={tab.to}
            to={tab.to}
            className={({ isActive }) => `tool-tab ${isActive ? "active" : ""}`}
          >
            {t(tab.label)}
          </NavLink>
        ))}
      </nav>
      <div className="admin-body">
        <Outlet />
      </div>
    </section>
  );
}
