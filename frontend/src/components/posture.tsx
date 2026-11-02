// Piezas visuales de la postura DevSecOps. Principio rector (POSTURE-RUBRIC §6):
// el % NUNCA aparece solo: siempre con su cobertura y su banda en texto (el
// color es refuerzo, no el único portador del estado).
import type { CSSProperties, ReactNode } from "react";

import type { PostureBand, SeverityCount, SourceStatus } from "../api/types";
import { useT } from "../i18n";
import { useCountUp } from "../motion";

// ─── Bandas ──────────────────────────────────────────────────────────────────
export interface BandMeta {
  /** Clave i18n del texto de ayuda de la banda (band.hint.<banda>). */
  hintKey: string;
  /** Orden de severidad: menor = peor (se muestra primero). */
  rank: number;
}

export const BAND_META: Record<PostureBand, BandMeta> = {
  critico: { hintKey: "band.hint.critico", rank: 0 },
  en_riesgo: { hintKey: "band.hint.en_riesgo", rank: 1 },
  aceptable: { hintKey: "band.hint.aceptable", rank: 2 },
  solido: { hintKey: "band.hint.solido", rank: 3 },
  evidencia_insuficiente: { hintKey: "band.hint.evidencia_insuficiente", rank: 4 },
  sin_datos: { hintKey: "band.hint.sin_datos", rank: 5 },
};

export const BAND_ORDER: PostureBand[] = [
  "critico",
  "en_riesgo",
  "aceptable",
  "solido",
  "evidencia_insuficiente",
  "sin_datos",
];

/** Tolera bandas desconocidas del backend (contrato evolutivo, ADR-008). */
export function bandOf(band: string | null | undefined): PostureBand {
  return band && band in BAND_META ? (band as PostureBand) : "sin_datos";
}

export function BandPill({ band }: { band: string }) {
  const b = bandOf(band);
  const t = useT();
  return (
    <span className={`band-pill band-${b}`} title={t(BAND_META[b].hintKey)}>
      <span className="band-dot" aria-hidden="true" />
      {t(`band.${b}`)}
    </span>
  );
}

// ─── Anillo de postura ───────────────────────────────────────────────────────
const R = 52;
const C = 2 * Math.PI * R;

export function PostureRing({
  score,
  band,
  coverage,
  size = 120,
  showCoverage = true,
}: {
  score: number | null;
  band: string;
  coverage: number;
  size?: number;
  showCoverage?: boolean;
}) {
  const b = bandOf(band);
  const t = useT();
  const shown = useCountUp(score);
  const pct = score === null ? 0 : Math.max(0, Math.min(100, score));
  const indicative = b === "evidencia_insuficiente";
  const style = { width: size, height: size, ["--ring-c" as string]: C } as CSSProperties;
  const label =
    score === null
      ? t("ring.noData")
      : t("ring.aria", { score, coverage, band: t(`band.${b}`) });
  return (
    <div className={`ring band-${b}`} style={style} role="img" aria-label={label}>
      <svg viewBox="0 0 120 120" aria-hidden="true">
        <circle className="ring-track" cx="60" cy="60" r={R} />
        {score !== null && (
          <circle
            className={`ring-arc${indicative ? " indicative" : ""}`}
            cx="60"
            cy="60"
            r={R}
            strokeDasharray={C}
            strokeDashoffset={C * (1 - pct / 100)}
          />
        )}
      </svg>
      <div className="ring-center">
        <span className="ring-num" style={{ fontSize: size * 0.28 }}>
          {shown === null ? "—" : shown}
          {shown !== null && <small>%</small>}
        </span>
        {showCoverage && size >= 96 && (
          <span className="ring-cov">{indicative && score !== null ? t("ring.indicative") : t("ring.cov", { n: coverage })}</span>
        )}
      </div>
    </div>
  );
}

// ─── Barra de severidad ──────────────────────────────────────────────────────
// Rótulos corto/largo por clave i18n: sevbar.<sev>.s / sevbar.<sev>.l
const SEG: { key: keyof SeverityCount }[] = [
  { key: "critical" },
  { key: "high" },
  { key: "medium" },
  { key: "low" },
];

export function SeverityBar({
  title,
  counts,
  empty,
  compact = false,
}: {
  title: string;
  counts: SeverityCount | null;
  /** Se muestra cuando no hay dato (fuente no conectada / sin datos). */
  empty: ReactNode;
  compact?: boolean;
}) {
  const t = useT();
  if (!counts) {
    return (
      <div className={`sevbar sevbar-empty${compact ? " compact" : ""}`}>
        <div className="sevbar-head">
          <span className="sevbar-title">{title}</span>
        </div>
        <div className="sevbar-track neutral" />
        <div className="sevbar-note">{empty}</div>
      </div>
    );
  }
  const total = SEG.reduce((n, s) => n + counts[s.key], 0);
  return (
    <div className={`sevbar${compact ? " compact" : ""}`}>
      <div className="sevbar-head">
        <span className="sevbar-title">{title}</span>
        <span className="sevbar-total mono">{total === 0 ? t("sevbar.clean") : t("sevbar.active", { n: total })}</span>
      </div>
      <div
        className="sevbar-track"
        role="img"
        aria-label={`${title}: ${SEG.map((s) => `${counts[s.key]} ${t(`sevbar.${s.key}.l`)}`).join(", ")}`}
      >
        {total === 0 ? (
          <span className="seg seg-clean" style={{ flexGrow: 1 }} />
        ) : (
          SEG.map((s) =>
            counts[s.key] > 0 ? (
              <span key={s.key} className={`seg seg-${s.key}`} style={{ flexGrow: counts[s.key] }} />
            ) : null,
          )
        )}
      </div>
      <div className="sevbar-legend mono">
        {SEG.map((s) => (
          <span key={s.key} className={`lg lg-${s.key}${counts[s.key] === 0 ? " zero" : ""}`} title={t(`sevbar.${s.key}.l`)}>
            {t(`sevbar.${s.key}.s`)} {counts[s.key]}
          </span>
        ))}
      </div>
    </div>
  );
}

// ─── Estado de fuentes: no conectada ≠ caída ─────────────────────────────────

export function SourceChip({
  name,
  status,
  connectHint,
}: {
  name: string;
  status: SourceStatus;
  /** Texto de acción para not_configured, p. ej. "conecta GitHub". */
  connectHint?: string;
}) {
  const t = useT();
  const label = t(`src.${status}`);
  const text = status === "not_configured" && connectHint ? connectHint : label;
  return (
    <span className={`src src-${status}`} title={`${name}: ${label}`}>
      <span className="src-dot" aria-hidden="true" />
      <span className="src-name">{name}</span>
      <span className="src-state">{text}</span>
    </span>
  );
}

// ─── Skeleton ────────────────────────────────────────────────────────────────
export function Skel({ w, h = 14, r = 6, style }: { w?: number | string; h?: number | string; r?: number; style?: CSSProperties }) {
  return <span className="skel" style={{ width: w ?? "100%", height: h, borderRadius: r, ...style }} aria-hidden="true" />;
}
