import { NavLink, Outlet, useLocation } from "react-router-dom";

import wolfHead from "../assets/wolf-head.svg";
import { updatesApi } from "../api/admin";
import { useAuth } from "../auth/AuthContext";
import { useAsync } from "../hooks";
import { useI18n } from "../i18n";
import { LANGS, LANG_NAMES, type Lang } from "../i18n/translations";
import { WolfGuide } from "./WolfGuide";
import { NotificationBell } from "./NotificationBell";
import { FireWolfEasterEgg } from "../wolf-ux/FireWolfEasterEgg";

function LangSwitcher() {
  const { lang, setLang, t } = useI18n();
  return (
    <select
      className="lang-switcher"
      aria-label={t("layout.langAria")}
      value={lang}
      onChange={(e) => setLang(e.target.value as Lang)}
    >
      {LANGS.map((l) => (
        <option key={l} value={l}>{LANG_NAMES[l]}</option>
      ))}
    </select>
  );
}

function VersionBadge() {
  const { t } = useI18n();
  const { data, error } = useAsync(updatesApi.version, []);
  if (error || !data) return null;
  return <span className="badge off" title={t("layout.versionTip")}>v{data.current_version}</span>;
}

export function Layout() {
  const { user, logout } = useAuth();
  const { t, lang } = useI18n();
  const isAdmin = user?.role === "admin";
  const { pathname } = useLocation();
  // Ocultar lo que el rol no puede ver es ergonomía; el servidor decide.
  return (
    <>
      <header className="app-header">
        {/* Logo oficial de Wolf Industries (vectorizado, hereda la paleta vía mask). */}
        <span
          className="brand-mark"
          role="img"
          aria-label="Wolf Industries"
          style={{ maskImage: `url(${wolfHead})`, WebkitMaskImage: `url(${wolfHead})` }}
        />
        <h1>Wolf DevSecOps</h1>
        <span className="tag">COMMUNITY</span>
        <nav className="app-nav" aria-label={t("layout.mainNav")}>
          <NavLink to="/" end>{t("nav.projects")}</NavLink>
          <NavLink to="/tools">{t("nav.tools")}</NavLink>
          <NavLink to="/activity">{lang === "en" ? "What's new" : "Novedades"}</NavLink>
          {isAdmin && <NavLink to="/admin">{t("nav.admin")}</NavLink>}
        </nav>
        {user && (
          <div className="header-user">
            <NotificationBell />
            <LangSwitcher />
            <VersionBadge />
            <NavLink to="/profile" className="mono">{user.username}</NavLink>
            <span className="badge ok">{t(`role.${user.role}`)}</span>
            <button type="button" className="btn sm" onClick={logout}>{t("common.logout")}</button>
          </div>
        )}
      </header>
      <main>
        {/* Solo el contenido anima al cambiar de ruta; el header persiste. */}
        <div className="route" key={pathname}>
          <Outlet />
        </div>
      </main>
      {/* Guía interactiva (lobo cibernético) + firma Fire_Wolf (Konami). */}
      <WolfGuide />
      <FireWolfEasterEgg
        developer="Fire_Wolf.cl"
        frames={["/wolf/Lobo-1.png", "/wolf/Lobo-2.png", "/wolf/Lobo-3.png", "/wolf/Lobo-4.png"]}
        howlSrc="/wolf/lobo.mp3"
      />
    </>
  );
}
