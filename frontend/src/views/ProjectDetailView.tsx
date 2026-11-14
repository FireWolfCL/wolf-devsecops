import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";

import { useProjectSummary } from "../hooks";
import type { CheckResult, FindingsSummary, PostureScore, SourceResult, SourceStatus } from "../api/types";
import { FindingsDetail } from "../components/FindingsDetail";
import { ExtLink } from "../components/util";
import { PipelineMap } from "../components/PipelineMap";
import { RefreshButton } from "../components/RefreshButton";
import { refreshProject } from "../api/client";
import { BAND_META, BandPill, PostureRing, SeverityBar, Skel, SourceChip, bandOf } from "../components/posture";
import { useT, useI18n, type TFn } from "../i18n";
import { stagger } from "../motion";
import { AIReviewPanel } from "./AIReviewPanel";

// Títulos legibles de los checks de la rúbrica v1 (POSTURE-RUBRIC §2): claves
// detail.check.<id>; si llega un id desconocido se muestra el id tal cual.
function checkTitle(id: string, t: TFn): string {
  const k = `detail.check.${id}`;
  const v = t(k);
  return v === k ? id : v;
}

const REASON_KEYS = [
  "threshold_not_met", "source_not_configured", "source_unavailable",
  "no_verdict", "missing_data", "covered_by_containers",
];

function reasonText(c: CheckResult, t: TFn): string {
  if (c.reason_code === "ok") {
    return c.result === "partial" ? t("detail.reason.okPartial") : c.result === "pass" ? t("detail.reason.okPass") : t("detail.reason.okEval");
  }
  return REASON_KEYS.includes(c.reason_code) ? t(`detail.reason.${c.reason_code}`) : c.reason_code;
}

function PostureHeader({ posture }: { posture: PostureScore }) {
  const t = useT();
  const band = bandOf(posture.band);
  return (
    <section className={`dhero band-${band}`} aria-label={t("detail.postureAria")}>
      <PostureRing score={posture.score} band={posture.band} coverage={posture.coverage} size={148} />
      <div className="dhero-body">
        <p className="eyebrow">{t("detail.eyebrow", { v: posture.rubric_version })}</p>
        <div className="hero-band">
          <BandPill band={posture.band} />
          <span className="hero-cov mono">{t("detail.coverage", { n: posture.coverage })}</span>
        </div>
        <p className="hero-hint">{t(BAND_META[band].hintKey)}</p>
        <div className="flags">
          {posture.capped_by === "coverage" && (
            <span className="flag">{t("detail.flag.coverage")}</span>
          )}
          {posture.capped_by === "critical" && (
            <span className="flag crit">{t("detail.flag.critical")}</span>
          )}
          {posture.stale && <span className="flag">{t("detail.flag.stale")}</span>}
          {posture.degraded && (
            <span className="flag">{t("detail.flag.degraded")}</span>
          )}
        </div>
      </div>
    </section>
  );
}

// 2.2 — "Las 3 acciones que más suben tu puntaje": de los checks en FAIL/PARTIAL,
// las 3 con mayor peso recuperable (weight − earned). Solo usa el ProjectSummary ya
// cargado: cero llamadas extra a las fuentes (ADR-012).
function topActions(checks: CheckResult[], n = 3): { id: string; pts: number }[] {
  return checks
    .filter((c) => c.result === "fail" || c.result === "partial")
    .map((c) => ({ id: c.id, pts: c.weight - c.earned }))
    .filter((a) => a.pts > 0)
    .sort((a, b) => b.pts - a.pts)
    .slice(0, n);
}

function actionText(id: string, t: TFn): string {
  const k = `detail.action.${id}`;
  const v = t(k);
  return v === k ? checkTitle(id, t) : v;  // si no hay texto accionable, cae al título del check
}

function TopActionsCard({ checks }: { checks: CheckResult[] }) {
  const t = useT();
  const actions = topActions(checks);
  if (actions.length === 0) return null;
  return (
    <section className="card top-actions" aria-label={t("detail.actions.title")}>
      <h3>{t("detail.actions.title")}</h3>
      <ol className="ta-list">
        {actions.map((a, i) => (
          <li key={a.id} className="ta-item" style={stagger(i)}>
            <span className="ta-rank" aria-hidden="true">{i + 1}</span>
            <span className="ta-text">{actionText(a.id, t)}</span>
            <span className="ta-pts mono" title={t("detail.actions.foot")}>
              {t("detail.actions.pts", { n: Number.isInteger(a.pts) ? a.pts : a.pts.toFixed(1) })}
            </span>
          </li>
        ))}
      </ol>
      <p className="muted ta-foot">{t("detail.actions.foot")}</p>
    </section>
  );
}

function ChecksCard({ checks }: { checks: CheckResult[] }) {
  const t = useT();
  return (
    <section className="card checks" aria-label={t("detail.checksAria")}>
      <h3>{t("detail.checksTitle")}</h3>
      <ul className="check-list">
        {checks.map((c, i) => (
          <li key={c.id} className={`check ck-${c.result}`} style={stagger(i)}>
            <span className="ck-badge">{t(`detail.outcome.${c.result}`)}</span>
            <div className="ck-main">
              <span className="ck-title">{checkTitle(c.id, t)}</span>
              <span className="ck-reason muted">{reasonText(c, t)}</span>
            </div>
            <span className="ck-score mono" title={t("detail.scoreTip")}>
              {c.result === "na" ? `— / ${c.weight}` : `${Number.isInteger(c.earned) ? c.earned : c.earned.toFixed(1)} / ${c.weight}`}
            </span>
          </li>
        ))}
      </ul>
      <p className="muted ck-foot">
        {t("detail.checksFoot")}
      </p>
    </section>
  );
}

function Block<T>({
  title,
  source,
  result,
  connectVendor,
  index,
  render,
  link,
  anchorId,
}: {
  title: string;
  source: string;
  result: SourceResult<T>;
  connectVendor?: string; // nombre de la herramienta a conectar (no se traduce)
  index: number;
  render: (data: T) => ReactNode;
  link?: string; // deep-link de salida a la herramienta (URL pública)
  anchorId?: string; // ancla para saltos internos (p. ej. «aceptar en Hallazgos»)
}) {
  const t = useT();
  const connectHint = connectVendor ? `${t("detail.connect")} ${connectVendor}` : undefined;
  return (
    <div id={anchorId} className={`card block src-card-${result.status}`} style={stagger(index)}>
      <h3>
        {title} <SourceChip name={source} status={result.status} connectHint={connectHint} />
        {link && (result.status === "ok" || result.status === "stale") && (
          <ExtLink href={link} className="ext-link sm card-ext">{t("detail.inSource", { source })}</ExtLink>
        )}
      </h3>
      {result.data ? (
        render(result.data)
      ) : (
        <p className="muted">
          {result.status === "not_configured"
            ? connectVendor
              ? t("detail.notConnectedHint", { vendor: connectVendor })
              : t("detail.notConnected")
            : (result.error ?? t("detail.noSourceData"))}
        </p>
      )}
      {/* Nunca se oculta el error técnico (saneado por el backend). */}
      {result.data && result.error && <p className="muted tech-err">{result.error}</p>}
    </div>
  );
}

function Row({ k, v }: { k: string; v: ReactNode }) {
  return (
    <div className="row">
      <span className="muted">{k}</span>
      <span className="mono">{v}</span>
    </div>
  );
}

/** Sello de frescura: "Datos al día de {hora relativa}" + aviso si algún dato es stale.
 * Usa el fetched_at más viejo (as_of) que calcula el backend, no generated_at. */
function Freshness({ stamp }: { stamp: import("../api/types").FreshnessStamp | null }) {
  const t = useT();
  const { lang } = useI18n();
  if (!stamp || !stamp.as_of) return null;
  const ms = new Date(stamp.as_of).getTime();
  let rel = new Date(stamp.as_of).toLocaleString();
  if (!Number.isNaN(ms)) {
    const secs = Math.round((ms - Date.now()) / 1000);
    const units: [Intl.RelativeTimeFormatUnit, number][] = [["day", 86400], ["hour", 3600], ["minute", 60]];
    try {
      const rtf = new Intl.RelativeTimeFormat(lang, { numeric: "auto" });
      const hit = units.find(([, n]) => Math.abs(secs) >= n);
      rel = hit ? rtf.format(Math.round(secs / hit[1]), hit[0]) : rtf.format(0, "second");
    } catch { /* deja el toLocaleString */ }
  }
  return (
    <p className="muted mono deps-meta">
      {t("detail.freshness", { when: rel })}
      {stamp.stale && <span className="flag" style={{ marginLeft: 8 }}>{t("detail.stale")}</span>}
    </p>
  );
}

/** Procedencia del estado que se ve: último commit de `main` + autor, SIEMPRE visible
 * e INDEPENDIENTE de CI (responde "qué versión se chequea y quién la pusheó"). Solo
 * se oculta si no hay dato de procedencia (fuente caída → fail-closed, no inventa). */
function Provenance({ prov }: { prov: import("../api/types").ProvenanceInfo | null }) {
  const t = useT();
  if (!prov || !prov.sha_short) return null;
  const sha = prov.html_url
    ? <ExtLink href={prov.html_url}><code>{prov.sha_short}</code></ExtLink>
    : <code>{prov.sha_short}</code>;
  return (
    <p className="muted mono deps-meta">
      {t("detail.provLine")}{" "}
      {prov.default_branch && <><strong>{prov.default_branch}</strong>{" · "}</>}
      {sha}{" · "}{prov.author ?? t("pipeline.unknownActor")}
      {prov.message && <> — {prov.message}</>}
    </p>
  );
}

/** Counts + toggle «Ver detalle» → lista real (QUÉ está afectado, CÓMO mejorarlo).
 * Reutilizable: Contenedores (source="trivy") y Hallazgos (source="defectdojo"). */
function FindingsBlock({
  projectKey, source, f, status, barTitle, noteKey, selectable = false, openOnHash,
}: {
  projectKey: string;
  source: string;
  f: FindingsSummary;
  status: SourceStatus;
  barTitle: string;
  noteKey?: string; // 2.3: nota que explica el pilar (exposición vs. gestión)
  selectable?: boolean;
  openOnHash?: string; // abre el detalle cuando la URL llega con este hash (salto «aceptar en Hallazgos»)
}) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const canExpand = (status === "ok" || status === "stale") && f.total_active > 0;
  // Un enlace desde Contenedores («aceptar en Hallazgos») fija el hash; abrimos el
  // detalle al llegar y en cada cambio de hash, sin acoplar estado entre tarjetas.
  useEffect(() => {
    if (!openOnHash || !canExpand) return;
    const check = () => { if (window.location.hash === `#${openOnHash}`) setOpen(true); };
    check();
    window.addEventListener("hashchange", check);
    return () => window.removeEventListener("hashchange", check);
  }, [openOnHash, canExpand]);
  return (
    <>
      {noteKey && <p className="pillar-note muted">{t(noteKey)}</p>}
      <SeverityBar title={barTitle} counts={f.counts} accepted={f.accepted} empty={null} />
      <Row k={t("detail.active")} v={f.total_active} />
      {canExpand && (
        <>
          <button
            type="button"
            className="btn sm toggle-detail"
            aria-expanded={open}
            onClick={() => setOpen((o) => !o)}
          >
            {open ? t("detail.hideDetail") : t("detail.showDetail")}
          </button>
          {open && (
            <div className="findings-wrap">
              <FindingsDetail projectKey={projectKey} source={source} selectable={selectable} />
            </div>
          )}
        </>
      )}
    </>
  );
}

function DetailSkeleton() {
  const t = useT();
  return (
    <div aria-busy="true" aria-label={t("detail.loadingAria")}>
      <section className="dhero">
        <span className="skel" style={{ width: 148, height: 148, borderRadius: "50%" }} />
        <div className="dhero-body">
          <Skel w={200} h={12} />
          <Skel w={150} h={26} r={13} style={{ marginTop: 14 }} />
          <Skel w="60%" h={10} style={{ marginTop: 16 }} />
        </div>
      </section>
      <div className="dgrid">
        <div className="card"><Skel w="40%" h={16} /><Skel h={12} style={{ marginTop: 18 }} /><Skel h={12} style={{ marginTop: 10 }} /><Skel h={12} style={{ marginTop: 10 }} /></div>
        <div className="card"><Skel w="40%" h={16} /><Skel h={12} style={{ marginTop: 18 }} /><Skel h={12} style={{ marginTop: 10 }} /><Skel h={12} style={{ marginTop: 10 }} /></div>
      </div>
    </div>
  );
}

// name: clave i18n del rótulo (null = literal "ci"); vendor: herramienta a conectar.
const SRC_ORDER: { name: string | null; vendor: string }[] = [
  { name: null, vendor: "GitHub" },
  { name: "detail.s.findings", vendor: "DefectDojo" },
  { name: "detail.s.containers", vendor: "Harbor/Trivy" },
  { name: "detail.s.quality", vendor: "SonarQube" },
  { name: "detail.s.release", vendor: "GitHub" },
];

export function ProjectDetailView({
  projectKey,
  onBack,
}: {
  projectKey: string;
  onBack: () => void;
}) {
  const t = useT();
  const { data, loading, error, reload } = useProjectSummary(projectKey);

  // Forge del proyecto (CI + release): GitHub, GitLab (1.3) o Jenkins (1.4).
  // Etiqueta y deep-link según cuál esté configurado (github gana si hay varios).
  const forgeName = data?.links?.github ? "GitHub" : data?.links?.gitlab ? "GitLab" : data?.links?.jenkins ? "Jenkins" : "GitHub";
  const forgeLink = data?.links?.github ?? data?.links?.gitlab ?? data?.links?.jenkins;

  const statuses: SourceStatus[] = data
    ? [data.ci.status, data.findings.status, data.containers?.status ?? "not_configured", data.quality.status, data.release.status]
    : [];

  return (
    <div className="detail">
      <button className="btn-back" onClick={onBack}>
        {t("common.back")}
      </button>
      {loading && !data && <DetailSkeleton />}
      {error && (
        <div className="alert err" role="alert">
          {t("detail.loadError", { key: projectKey, error })}{" "}
          <button type="button" className="btn sm" onClick={reload}>{t("common.retry")}</button>
        </div>
      )}
      {data && (
        <>
          <div className="detail-title-row">
            <h2 className="detail-title">{data.name}</h2>
            <div className="detail-actions">
              <RefreshButton onRefresh={() => refreshProject(projectKey)} />
              <Link className="btn sm" to={`/projects/${encodeURIComponent(projectKey)}/deps`}>
                {t("detail.deps")}
              </Link>
              <Link className="btn sm" to={`/projects/${encodeURIComponent(projectKey)}/codemap`}>
                {t("detail.codemap")}
              </Link>
              <Link className="btn sm" to={`/projects/${encodeURIComponent(projectKey)}/code-review`}>
                {t("detail.codeReview")}
              </Link>
              <Link className="btn sm" to={`/projects/${encodeURIComponent(projectKey)}/history`}>
                {t("detail.history")}
              </Link>
              <Link className="btn sm" to={`/projects/${encodeURIComponent(projectKey)}/compare`}>
                {t("detail.compare")}
              </Link>
              <Link className="btn sm report-btn" to={`/projects/${encodeURIComponent(projectKey)}/report`}>
                {t("common.report")}
              </Link>
            </div>
          </div>
          <Freshness stamp={data.freshness} />
          <Provenance prov={data.provenance?.data ?? null} />
          {data.posture ? (
            <PostureHeader posture={data.posture} />
          ) : (
            <div className="alert info">{t("detail.noScore")}</div>
          )}

          <PipelineMap projectKey={projectKey} />

          {data.posture && data.posture.checks.length > 0 && (
            <TopActionsCard checks={data.posture.checks} />
          )}

          <div className="src-strip" aria-label={t("detail.srcStripAria")}>
            {SRC_ORDER.map((s, i) => (
              <SourceChip key={s.name ?? "ci"} name={s.name ? t(s.name) : "ci"} status={statuses[i]} connectHint={`${t("detail.connect")} ${s.vendor}`} />
            ))}
          </div>

          <div className="dgrid">
            {data.posture && data.posture.checks.length > 0 && <ChecksCard checks={data.posture.checks} />}
            <div className="dcol">
              <Block
                index={0}
                title={t("detail.t.findings")}
                source="DefectDojo"
                result={data.findings}
                link={data.links?.defectdojo}
                connectVendor="DefectDojo"
                anchorId="findings-card"
                render={(f) => (
                  <FindingsBlock
                    projectKey={projectKey}
                    source="defectdojo"
                    f={f}
                    status={data.findings.status}
                    barTitle={t("detail.severity")}
                    noteKey="detail.note.findings"
                    selectable={data.org === "Wolf"}
                    openOnHash="findings"
                  />
                )}
              />
              <Block
                index={1}
                title={t("detail.t.containers")}
                source="Trivy/Harbor"
                result={data.containers}
                link={data.links?.harbor}
                connectVendor="Harbor/Trivy"
                render={(f) => (
                  <FindingsBlock
                    projectKey={projectKey}
                    source="trivy"
                    f={f}
                    status={data.containers.status}
                    barTitle={t("detail.imageFs")}
                    noteKey="detail.note.containers"
                    selectable={data.org === "Wolf"}
                  />
                )}
              />
              <Block
                index={2}
                title={t("detail.t.ci")}
                source={forgeName}
                result={data.ci}
                link={forgeLink}
                connectVendor={forgeName}
                render={(ci) => (
                  <>
                    <Row k={t("detail.row.status")} v={ci.status} />
                    <Row k={t("detail.row.workflow")} v={ci.workflow} />
                    <Row k={t("detail.row.branch")} v={ci.branch ?? "—"} />
                    <Row k={t("detail.row.run")} v={ci.run_number ?? "—"} />
                  </>
                )}
              />
              <Block
                index={3}
                title={t("detail.t.quality")}
                source="SonarQube"
                result={data.quality}
                link={data.links?.sonarqube}
                connectVendor="SonarQube"
                render={(q) => (
                  <>
                    <Row k={t("detail.row.qualityGate")} v={q.quality_gate} />
                    <Row k={t("detail.row.bugs")} v={q.bugs} />
                    <Row k={t("detail.row.vulns")} v={q.vulnerabilities} />
                    <Row k={t("detail.row.coverage")} v={q.coverage != null ? `${q.coverage}%` : "—"} />
                  </>
                )}
              />
              <Block
                index={4}
                title={t("detail.t.release")}
                source={forgeName}
                result={data.release}
                link={forgeLink}
                connectVendor={forgeName}
                render={(r) => (
                  <>
                    <Row k={t("detail.row.tag")} v={r.tag ?? "—"} />
                    <Row k={t("detail.row.published")} v={r.published_at ?? "—"} />
                    <Row k="SBOM" v={r.sbom_url ? t("detail.row.sbomYes") : t("detail.row.sbomNo")} />
                  </>
                )}
              />
            </div>
          </div>
          <AIReviewPanel projectKey={projectKey} />
        </>
      )}
    </div>
  );
}
