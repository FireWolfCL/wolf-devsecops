// About / reconocimiento (admin). Branding de Wolf Industries, el developer y la
// versión en ejecución. Todo es estático salvo la versión (del backend). El enlace
// externo pasa por ExtLink (https, rel noopener).
import { Link } from "react-router-dom";

import wolfHead from "../assets/wolf-head.svg";
import { updatesApi } from "../api/admin";
import { ExtLink } from "../components/util";
import { useAsync, useMeta } from "../hooks";
import { useT } from "../i18n";

const SITE = "https://wolf-industries.tech";

export function About() {
  const t = useT();
  const { data: ver } = useAsync(() => updatesApi.version(), []);
  const { data: meta } = useMeta();
  const edition = meta?.edition ?? "community";
  const editionLabel = edition.charAt(0).toUpperCase() + edition.slice(1);

  return (
    <div className="about">
      <header className="about-hero">
        <img src={wolfHead} alt="Wolf Industries" className="about-logo" />
        <div>
          <h2 className="about-title">
            Wolf DevSecOps <span className="about-edition">{editionLabel}</span>
          </h2>
          <p className="about-tagline">{t("about.tagline")}</p>
          <p className="about-ver mono">
            {t("about.version")} {ver?.current_version ?? "—"}
          </p>
          <Link to="/plans" className="ext-link sm about-link">{t("pro.seePlans")}</Link>
        </div>
      </header>

      <p className="about-lead">{t("about.what")}</p>

      <div className="about-cards">
        <section className="about-card">
          <h3>{t("about.byTitle")}</h3>
          <p className="about-brand">Wolf Industries</p>
          <p className="muted">{t("about.byDesc")}</p>
          <ExtLink href={SITE} className="ext-link about-link">wolf-industries.tech</ExtLink>
        </section>

        <section className="about-card">
          <h3>{t("about.devTitle")}</h3>
          {/* Firma del desarrollador — consistente con el easter egg Fire_Wolf. */}
          <p className="about-dev">🐺 Fire_Wolf</p>
          <p className="muted">{t("about.devDesc")}</p>
        </section>

        <section className="about-card">
          <h3>{t("about.stackTitle")}</h3>
          <p className="muted">{t("about.stack")}</p>
          {meta && (
            <p className="muted" style={{ fontSize: 12 }}>
              {t("about.projectsConnected", { n: meta.project_count })}
            </p>
          )}
        </section>
      </div>

      <footer className="about-foot muted">{t("about.footer")}</footer>
    </div>
  );
}
