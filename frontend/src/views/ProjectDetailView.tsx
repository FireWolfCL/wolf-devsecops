import { useState } from "react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";

import { useProjectSummary } from "../hooks";
import type { CheckResult, FindingsSummary, PostureScore, SourceResult, SourceStatus } from "../api/types";
import { FindingsDetail } from "../components/FindingsDetail";
import { ExtLink } from "../components/util";
import { BAND_META, BandPill, PostureRing, SeverityBar, Skel, SourceChip, bandOf } from "../components/posture";
import { useT, type TFn } from "../i18n";
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
}: {
  title: string;
  source: string;
  result: SourceResult<T>;
  connectVendor?: string; // nombre de la herramienta a conectar (no se traduce)
  index: number;
  render: (data: T) => ReactNode;
  link?: string; // deep-link de salida a la herramienta (URL pública)
}) {
  const t = useT();
  const connectHint = connectVendor ? `${t("detail.connect")} ${connectVendor}` : undefined;
  return (
    <div className={`card block src-card-${result.status}`} style={stagger(index)}>
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

/** Counts + toggle «Ver detalle» → lista real (QUÉ está afectado, CÓMO mejorarlo).
 * Reutilizable: Contenedores (source="trivy") y Hallazgos (source="defectdojo"). */
function FindingsBlock({
  projectKey, source, f, status, barTitle, noteKey, selectable = false,
}: {
  projectKey: string;
  source: string;
  f: FindingsSummary;
  status: SourceStatus;
  barTitle: string;
  noteKey?: string; // 2.3: nota que explica el pilar (exposición vs. gestión)
  selectable?: boolean;
}) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const canExpand = (status === "ok" || status === "stale") && f.total_active > 0;
  return (
    <>
      {noteKey && <p className="pillar-note muted">{t(noteKey)}</p>}
      <SeverityBar title={barTitle} counts={f.counts} empty={null} />
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
              <Link className="btn sm report-btn" to={`/projects/${encodeURIComponent(projectKey)}/report`}>
                {t("common.report")}
              </Link>
            </div>
          </div>
          {data.posture ? (
            <PostureHeader posture={data.posture} />
          ) : (
            <div className="alert info">{t("detail.noScore")}</div>
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
                render={(f) => (
                  <FindingsBlock
                    projectKey={projectKey}
                    source="defectdojo"
                    f={f}
                    status={data.findings.status}
                    barTitle={t("detail.severity")}
                    noteKey="detail.note.findings"
                    selectable={data.org === "Wolf"}
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
                source="GitHub"
                result={data.ci}
                link={data.links?.github}
                connectVendor="GitHub"
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
                source="GitHub"
                result={data.release}
                link={data.links?.github}
                connectVendor="GitHub"
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
