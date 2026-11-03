// Comparativa de planes (Community / Pro / Enterprise). Resalta el plan actual
// (desde /api/meta). Texto de la matriz bilingüe inline (es/en) para no inflar el
// i18n; el resto usa t(). Es informativa: el backend es la autoridad del gate.
import { Fragment } from "react";
import { Link } from "react-router-dom";

import wolfHead from "../assets/wolf-head.svg";
import { ExtLink } from "../components/util";
import { useMeta } from "../hooks";
import { useI18n } from "../i18n";

type Cell = boolean | { es: string; en: string };
type Row = { label: { es: string; en: string }; c: Cell; p: Cell; e: Cell };
type Group = { cat: { es: string; en: string }; rows: Row[] };

const YES = true;
const NO = false;
const U = { es: "Ilimitado", en: "Unlimited" };

const GROUPS: Group[] = [
  { cat: { es: "Escala", en: "Scale" }, rows: [
    { label: { es: "Proyectos", en: "Projects" }, c: U, p: U, e: U },
    { label: { es: "Organizaciones", en: "Organizations" }, c: { es: "1", en: "1" }, p: { es: "Varias", en: "Multiple" }, e: { es: "Multi-tenant", en: "Multi-tenant" } },
  ]},
  { cat: { es: "Fuentes", en: "Sources" }, rows: [
    { label: { es: "Trivy·Harbor·DefectDojo·Sonar·GitHub", en: "Trivy·Harbor·DefectDojo·Sonar·GitHub" }, c: YES, p: YES, e: YES },
    { label: { es: "Code map · dependencias · token cifrado", en: "Code map · dependencies · encrypted token" }, c: YES, p: YES, e: YES },
  ]},
  { cat: { es: "Postura", en: "Posture" }, rows: [
    { label: { es: "Postura (%/banda/cobertura)", en: "Posture (%/band/coverage)" }, c: YES, p: YES, e: YES },
    { label: { es: "Historial de tendencia", en: "Trend history" }, c: { es: "30 días", en: "30 days" }, p: U, e: { es: "+ retención", en: "+ retention" } },
    { label: { es: "Postura global por organización", en: "Global posture per org" }, c: NO, p: YES, e: YES },
  ]},
  { cat: { es: "Reportes", en: "Reports" }, rows: [
    { label: { es: "Reporte + MD/PDF + evidencia Sonar", en: "Report + MD/PDF + Sonar evidence" }, c: YES, p: YES, e: YES },
    { label: { es: "Reportes programados", en: "Scheduled reports" }, c: NO, p: YES, e: YES },
    { label: { es: "Reporte de cumplimiento (ISO/CIS)", en: "Compliance report (ISO/CIS)" }, c: NO, p: YES, e: YES },
    { label: { es: "White-label / marca propia", en: "White-label" }, c: NO, p: NO, e: YES },
  ]},
  { cat: { es: "Notificaciones", en: "Notifications" }, rows: [
    { label: { es: "Campana in-app + Novedades", en: "In-app bell + What's new" }, c: YES, p: YES, e: YES },
    { label: { es: "Telegram · email · bot", en: "Telegram · email · bot" }, c: NO, p: YES, e: YES },
    { label: { es: "SLA / escalamiento", en: "SLA / escalation" }, c: NO, p: NO, e: YES },
  ]},
  { cat: { es: "IA", en: "AI" }, rows: [
    { label: { es: "Reviewer IA local (Ollama)", en: "Local AI reviewer (Ollama)" }, c: YES, p: YES, e: YES },
    { label: { es: "Code-fix con línea + hosted", en: "AI code-fix with line + hosted" }, c: NO, p: YES, e: YES },
    { label: { es: "Remediación → PR-draft", en: "Remediation → PR-draft" }, c: NO, p: YES, e: YES },
  ]},
  { cat: { es: "Identidad & acceso", en: "Identity & access" }, rows: [
    { label: { es: "Usuarios locales + MFA + RBAC", en: "Local users + MFA + RBAC" }, c: YES, p: YES, e: YES },
    { label: { es: "SSO / LDAP / Active Directory", en: "SSO / LDAP / Active Directory" }, c: NO, p: YES, e: YES },
    { label: { es: "SCIM · roles custom · row-level", en: "SCIM · custom roles · row-level" }, c: NO, p: NO, e: YES },
  ]},
  { cat: { es: "Operación & soporte", en: "Operations & support" }, rows: [
    { label: { es: "Instalador asistido + Compose", en: "Assisted installer + Compose" }, c: YES, p: YES, e: YES },
    { label: { es: "Export + retención de auditoría", en: "Audit export + retention" }, c: NO, p: YES, e: YES },
    { label: { es: "Canal de updates firmado", en: "Signed update channel" }, c: NO, p: YES, e: YES },
    { label: { es: "Soporte", en: "Support" }, c: { es: "Comunidad", en: "Community" }, p: { es: "Email", en: "Email" }, e: { es: "SLA", en: "SLA" } },
  ]},
];

function CellView({ v, es }: { v: Cell; es: boolean }) {
  if (v === true) return <span className="plan-yes" aria-label="sí">✓</span>;
  if (v === false) return <span className="plan-no" aria-label="no">—</span>;
  return <span className="plan-val">{es ? v.es : v.en}</span>;
}

const PLAN_KEYS = ["community", "pro", "enterprise"] as const;
const PLAN_LABEL: Record<string, string> = { community: "Community", pro: "Pro", enterprise: "Enterprise" };

export function Plans() {
  const { lang } = useI18n();
  const es = lang === "es";
  const { data: meta } = useMeta();
  const current = meta?.edition ?? "community";

  return (
    <div className="plans">
      <header className="plans-head">
        <span className="plans-mark" role="img" aria-label="Wolf Industries"
          style={{ maskImage: `url(${wolfHead})`, WebkitMaskImage: `url(${wolfHead})` }} />
        <h1>{es ? "Planes" : "Plans"}</h1>
        <p className="muted">{es
          ? "Elige según tu escala. El valor de seguridad base va completo en Community."
          : "Pick by your scale. Core security value is complete in Community."}</p>
      </header>

      <div className="plans-table-wrap">
        <table className="plans-table">
          <thead>
            <tr>
              <th>{es ? "Función" : "Feature"}</th>
              {PLAN_KEYS.map((k) => (
                <th key={k} className={k === current ? "plan-current" : undefined}>
                  {PLAN_LABEL[k]}
                  {k === current && <span className="plan-you">{es ? "tu plan" : "your plan"}</span>}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {GROUPS.map((g) => (
              <Fragment key={g.cat.en}>
                <tr className="plan-cat">
                  <td colSpan={4}>{es ? g.cat.es : g.cat.en}</td>
                </tr>
                {g.rows.map((r) => (
                  <tr key={r.label.en}>
                    <td>{es ? r.label.es : r.label.en}</td>
                    <td className={current === "community" ? "plan-current" : undefined}><CellView v={r.c} es={es} /></td>
                    <td className={current === "pro" ? "plan-current" : undefined}><CellView v={r.p} es={es} /></td>
                    <td className={current === "enterprise" ? "plan-current" : undefined}><CellView v={r.e} es={es} /></td>
                  </tr>
                ))}
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>

      <footer className="plans-foot muted">
        <ExtLink href="https://wolf-industries.tech" className="ext-link sm">wolf-industries.tech</ExtLink>
        <span>·</span>
        <Link to="/" className="ext-link sm">{es ? "Volver" : "Back"}</Link>
      </footer>
    </div>
  );
}
