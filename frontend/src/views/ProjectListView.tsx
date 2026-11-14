// Dashboard: hero de postura global + tarjetas por proyecto.
// Orden operador-primero: lo peor arriba. Un promedio no puede esconder al peor
// proyecto (POSTURE-RUBRIC §5), por eso el hero destaca Crítico/En riesgo.
import { useMemo, useState } from "react";

import type { GlobalPosture, PostureOverview, ProjectListItem, SourceStatus } from "../api/types";
import {
  BAND_META,
  BAND_ORDER,
  BandPill,
  PostureRing,
  SeverityBar,
  Skel,
  SourceChip,
  bandOf,
} from "../components/posture";
import { RefreshButton } from "../components/RefreshButton";
import { refreshAll } from "../api/client";
import { usePosture, useProjects, useSpark } from "../hooks";
import { useT } from "../i18n";
import { stagger } from "../motion";

// ─── Sparkline de tendencia (mini-línea del % de postura) ────────────────────
// SVG inline, sin dependencias. Decorativo: con <2 puntos no dibuja nada. El
// color sigue la banda actual de la tarjeta. El delta es el último − el primero
// de la ventana (sube/baja en el periodo).
function Sparkline({ values, band, label }: { values: number[]; band: string; label: string }) {
  if (values.length < 2) return null;
  const W = 96;
  const H = 26;
  const P = 3;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const n = values.length;
  const x = (i: number) => P + (i / (n - 1)) * (W - 2 * P);
  const y = (v: number) => P + (1 - (v - min) / span) * (H - 2 * P);
  const d = values.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("");
  const lastI = n - 1;
  const color = `var(--b-${band === "sin_datos" || band === "evidencia_insuficiente" ? "neutral" : band})`;
  return (
    <svg className="spark" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={label} style={{ color }}>
      <path className="spark-line" d={d} fill="none" stroke="currentColor" />
      <circle cx={x(lastI)} cy={y(values[lastI])} r={2.4} fill="currentColor" />
    </svg>
  );
}

// ─── Hero ────────────────────────────────────────────────────────────────────
function HeroSkeleton() {
  const t = useT();
  return (
    <section className="hero" aria-busy="true" aria-label={t("list.heroLoadingAria")}>
      <div className="hero-ring">
        <span className="skel" style={{ width: 176, height: 176, borderRadius: "50%" }} />
      </div>
      <div className="hero-body">
        <Skel w={180} h={12} />
        <Skel w={140} h={26} r={13} style={{ marginTop: 14 }} />
        <Skel w="70%" h={10} style={{ marginTop: 18 }} />
        <Skel h={10} style={{ marginTop: 22 }} />
        <Skel h={10} w="80%" style={{ marginTop: 10 }} />
      </div>
      <div className="hero-attn">
        <Skel w={150} h={12} />
        <Skel h={34} r={8} style={{ marginTop: 14 }} />
        <Skel h={34} r={8} style={{ marginTop: 8 }} />
      </div>
    </section>
  );
}

function Hero({
  posture,
  isPrimary,
  names,
  bandFilter,
  onBand,
  onSelect,
}: {
  posture: GlobalPosture;
  isPrimary: boolean;
  names: Map<string, string>;
  bandFilter: string | null;
  onBand: (b: string | null) => void;
  onSelect: (key: string) => void;
}) {
  const t = useT();
  const band = bandOf(posture.band);
  const total = Math.max(posture.project_count, 1);
  // Críticos primero, luego en riesgo; sin duplicados.
  const attention = useMemo(() => {
    const seen = new Set<string>();
    const out: { key: string; band: "critico" | "en_riesgo" }[] = [];
    for (const k of posture.critical) if (!seen.has(k)) { seen.add(k); out.push({ key: k, band: "critico" }); }
    for (const k of posture.at_risk) if (!seen.has(k)) { seen.add(k); out.push({ key: k, band: "en_riesgo" }); }
    return out;
  }, [posture.critical, posture.at_risk]);
  const shownAttn = attention.slice(0, 6);

  return (
    <section className={`hero band-${band}`} aria-label={t("list.heroAria")}>
      <div className="hero-ring">
        <PostureRing score={posture.global_score} band={posture.band} coverage={posture.coverage} size={176} />
      </div>

      <div className="hero-body">
        <p className="eyebrow">{posture.org.toUpperCase()} · {posture.project_count} {t("posture.projects")}</p>
        <div className="hero-band">
          <BandPill band={posture.band} />
          <span className="hero-cov mono">{t("posture.coverage")} {posture.coverage}%</span>
        </div>
        <p className="hero-hint">{t(BAND_META[band].hintKey)}</p>

        <div className="dist" role="group" aria-label={t("list.distAria")}>
          <div className="dist-bar" aria-hidden="true">
            {BAND_ORDER.map((b) => {
              const n = posture.distribution[b] ?? 0;
              return n > 0 ? <span key={b} className={`band-${b}`} style={{ flexGrow: n }} /> : null;
            })}
          </div>
          <div className="dist-rows">
            {BAND_ORDER.map((b) => {
              const n = posture.distribution[b] ?? 0;
              const active = bandFilter === b;
              return (
                <button
                  key={b}
                  type="button"
                  className={`dist-row band-${b}${active ? " active" : ""}${n === 0 ? " zero" : ""}`}
                  aria-pressed={active}
                  disabled={n === 0}
                  onClick={() => onBand(active ? null : b)}
                  title={t(BAND_META[b].hintKey)}
                >
                  <span className="band-dot" aria-hidden="true" />
                  <span className="dist-label">{t(`band.${b}`)}</span>
                  <span className="dist-n mono">
                    {n}
                    <small>/{total}</small>
                  </span>
                </button>
              );
            })}
          </div>
        </div>

        {!isPrimary && (
          <p className="org-note">
            {t("list.externalNote", { org: posture.org })}
          </p>
        )}
        <p className="honesty">
          {t("list.honesty")}
        </p>
      </div>

      <aside className="hero-attn" aria-label={t("list.attnAria")}>
        <p className="eyebrow">{t("posture.needAttention").toUpperCase()}</p>
        {attention.length === 0 ? (
          <p className="attn-ok">{t("list.attnNone")}</p>
        ) : (
          <ul className="attn-list">
            {shownAttn.map((a, i) => (
              <li key={a.key} style={stagger(i)}>
                <button type="button" className={`attn-item band-${a.band}`} onClick={() => onSelect(a.key)}>
                  <span className="band-dot" aria-hidden="true" />
                  <span className="attn-name">{names.get(a.key) ?? a.key}</span>
                  <span className="attn-band">{t(`band.${bandOf(a.band)}`)}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
        {attention.length > shownAttn.length && (
          <p className="muted attn-more">+{attention.length - shownAttn.length} {t("posture.moreInList")}</p>
        )}
      </aside>
    </section>
  );
}

// ─── Tarjeta de proyecto ─────────────────────────────────────────────────────
const CI_KEY: Record<string, string> = {
  success: "ci.green",
  failure: "ci.failing",
  running: "ci.running",
  unknown: "ci.unknown",
};

function CiPill({ item }: { item: ProjectListItem }) {
  const t = useT();
  if (item.ci_status) {
    const cls = item.ci_status === "failure" ? "fail" : item.ci_status === "success" ? "pass" : "neutral";
    return <span className={`pill ${cls}`}>{CI_KEY[item.ci_status] ? t(CI_KEY[item.ci_status]) : item.ci_status}</span>;
  }
  // Sin dato de CI: no es una falla del proyecto. Se distingue por la peor fuente.
  if (item.worst_source_status === "not_configured")
    return <span className="pill neutral" title={t("ci.connectTip")}>{t("ci.connect")}</span>;
  if (item.worst_source_status === "unavailable")
    return <span className="pill soft-warn" title={t("ci.unavailableTip")}>{t("ci.unavailable")}</span>;
  return <span className="pill neutral">{t("ci.noData")}</span>;
}

const GATE_META: Record<string, { key: string; cls: string }> = {
  OK: { key: "gate.ok", cls: "pass" },
  WARN: { key: "gate.warn", cls: "soft-warn" },
  ERROR: { key: "gate.error", cls: "fail" },
  NONE: { key: "gate.none", cls: "neutral" },
};

function GatePill({ item }: { item: ProjectListItem }) {
  const t = useT();
  if (!item.quality_gate) {
    const unavailable = item.worst_source_status === "unavailable";
    return (
      <span className={`pill ${unavailable ? "soft-warn" : "neutral"}`}>
        {unavailable ? t("quality.unavailable") : t("quality.notConnected")}
      </span>
    );
  }
  const g = GATE_META[item.quality_gate];
  return (
    <span className={`pill ${g?.cls ?? "neutral"}`}>
      {g ? t(g.key) : t("gate.other", { v: item.quality_gate })}
    </span>
  );
}

const NOTE_KEY: Record<SourceStatus, string | null> = {
  ok: null,
  stale: "list.note.stale",
  unavailable: "list.note.unavailable",
  not_configured: "list.note.not_configured",
};

function ProjectCard({ item, index, spark, onSelect }: { item: ProjectListItem; index: number; spark?: number[]; onSelect: (key: string) => void }) {
  const t = useT();
  const band = bandOf(item.posture_band);
  const noteKey = NOTE_KEY[item.worst_source_status];
  const note = noteKey ? t(noteKey) : null;
  // Delta de la ventana: último − primero del sparkline (tendencia del periodo).
  const trend = spark && spark.length >= 2 ? spark[spark.length - 1] - spark[0] : null;
  const trendDir = trend == null ? "flat" : trend > 0.05 ? "up" : trend < -0.05 ? "down" : "flat";
  return (
    <article className={`pcard band-${band}`} style={stagger(index)}>
      <header className="pcard-head">
        <PostureRing
          score={item.posture_score}
          band={item.posture_band}
          coverage={item.posture_coverage}
          size={72}
          showCoverage={false}
        />
        <div className="pcard-id">
          <h3>
            <button type="button" className="pcard-link" onClick={() => onSelect(item.key)}>
              {item.name}
            </button>
          </h3>
          <div className="pcard-meta">
            <BandPill band={item.posture_band} />
            <span className="mono muted pcard-cov">{t("posture.coverage")} {item.posture_coverage}%</span>
          </div>
        </div>
      </header>

      <div className="pcard-bars">
        <SeverityBar
          title={t("list.sevFindings")}
          counts={item.findings}
          empty={<span>{t("list.noDojo")}</span>}
          compact
        />
        <SeverityBar
          title={t("list.sevContainers")}
          counts={item.container_findings}
          accepted={item.container_accepted}
          empty={<span>{t("list.noImages")}</span>}
          compact
        />
      </div>

      {spark && spark.length >= 2 && (
        <div className={`pcard-trend trend-${trendDir}`}>
          <Sparkline
            values={spark}
            band={band}
            label={t("list.sparkAria", { name: item.name, n: spark.length })}
          />
          {trend != null && (
            <span className="trend-d mono" title={t("list.trendTip")}>
              {trend > 0 ? "▲" : trend < 0 ? "▼" : "▬"} {trend > 0 ? "+" : ""}{trend.toFixed(1)} pp
            </span>
          )}
        </div>
      )}

      <footer className="pcard-foot">
        <CiPill item={item} />
        <GatePill item={item} />
      </footer>
      {note && (
        <p className={`pcard-note n-${item.worst_source_status}`}>
          <SourceChip name={t("list.sources")} status={item.worst_source_status} />
          <span>{note}</span>
        </p>
      )}
    </article>
  );
}

function CardSkeleton({ i }: { i: number }) {
  return (
    <div className="pcard skeleton" style={stagger(i)} aria-hidden="true">
      <div className="pcard-head">
        <span className="skel" style={{ width: 72, height: 72, borderRadius: "50%" }} />
        <div className="pcard-id">
          <Skel w="60%" h={16} />
          <Skel w="45%" h={20} r={10} style={{ marginTop: 10 }} />
        </div>
      </div>
      <div className="pcard-bars">
        <Skel h={34} r={6} />
        <Skel h={34} r={6} />
      </div>
      <div className="pcard-foot">
        <Skel w={96} h={22} r={11} />
        <Skel w={110} h={22} r={11} />
      </div>
    </div>
  );
}

// ─── Vista ───────────────────────────────────────────────────────────────────
export function ProjectListView({ onSelect }: { onSelect: (key: string) => void }) {
  const { data, loading, error, reload } = useProjects();
  const posture = usePosture();
  const spark = useSpark();  // decorativo: una sola llamada, alimenta todas las cards
  const sparkSeries = spark.data?.series;
  const t = useT();
  const [query, setQuery] = useState("");
  const [bandFilter, setBandFilter] = useState<string | null>(null);
  // null = pestaña «Todas» (lista con todos los grupos; el hero sigue en la titular).
  const [orgSel, setOrgSel] = useState<string | null>(null);

  const names = useMemo(() => new Map((data ?? []).map((p): [string, string] => [p.key, p.name])), [data]);

  const overview: PostureOverview | null = posture.data;
  const primary = overview?.primary ?? "Wolf";
  // Orgs: titular primero, luego el resto alfabético (incluye orgs sin agregado).
  const orgNames = useMemo(() => {
    const set = new Set<string>([primary]);
    (overview?.orgs ?? []).forEach((o) => set.add(o.org));
    (data ?? []).forEach((p) => set.add(p.org));
    return [...set].sort((a, b) => (a === primary ? -1 : b === primary ? 1 : a.localeCompare(b)));
  }, [overview, data, primary]);
  const scope = orgSel && orgNames.includes(orgSel) ? orgSel : null;
  const selected = scope ?? primary;
  const heroPosture = overview?.orgs.find((o) => o.org === selected) ?? null;

  function pickOrg(o: string | null) {
    setOrgSel(o);
    setBandFilter(null);
  }

  // Grupos por organización. Dentro de cada grupo: peor primero; a igualdad de
  // banda, menor puntaje primero, luego nombre. El filtro por banda aplica a la
  // org seleccionada (la que describe el hero).
  const groups = useMemo(() => {
    const q = query.trim().toLowerCase();
    return orgNames
      .map((org) => {
        const items = (data ?? [])
          .filter((p) => p.org === org)
          .filter(() => scope === null || org === scope)
          .filter((p) => (bandFilter ? org === selected && bandOf(p.posture_band) === bandFilter : true))
          .filter((p) => (q ? p.name.toLowerCase().includes(q) || p.key.toLowerCase().includes(q) : true))
          .sort((a, b) => {
            const r = BAND_META[bandOf(a.posture_band)].rank - BAND_META[bandOf(b.posture_band)].rank;
            if (r !== 0) return r;
            const s2 = (a.posture_score ?? 101) - (b.posture_score ?? 101);
            return s2 !== 0 ? s2 : a.name.localeCompare(b.name);
          });
        return { org, items, total: (data ?? []).filter((p) => p.org === org).length };
      })
      .filter((g) => g.items.length > 0 && !(bandFilter && g.org !== selected));
  }, [data, orgNames, query, bandFilter, selected, scope]);
  const shownCount = groups.reduce((n, g) => n + g.items.length, 0);

  return (
    <div className="dash">
      {posture.loading && !posture.data && <HeroSkeleton />}
      {orgNames.length > 1 && (
        <div className="org-seg" role="group" aria-label={t("list.orgAria")}>
          <button
            type="button"
            className={`org-opt${scope === null ? " active" : ""}`}
            aria-pressed={scope === null}
            onClick={() => pickOrg(null)}
          >
            {t("list.allOrgs")}
          </button>
          {orgNames.map((o) => (
            <button
              key={o}
              type="button"
              className={`org-opt${o === scope ? " active" : ""}`}
              aria-pressed={o === scope}
              onClick={() => pickOrg(o)}
            >
              {o}
              {o !== primary && <small>{t("list.external")}</small>}
            </button>
          ))}
        </div>
      )}
      {heroPosture && (
        <Hero
          key={heroPosture.org}
          posture={heroPosture}
          isPrimary={heroPosture.org === primary}
          names={names}
          bandFilter={bandFilter}
          onBand={setBandFilter}
          onSelect={onSelect}
        />
      )}
      {posture.data && !heroPosture && (
        <div className="alert info">{t("list.noPosture", { org: selected })}</div>
      )}
      {posture.error && !posture.data && (
        <div className="alert warn" role="alert">
          {t("list.postureError", { error: posture.error })}{" "}
          <button type="button" className="btn sm" onClick={posture.reload}>{t("common.retry")}</button>
        </div>
      )}

      <div className="list-bar">
        <h2 className="list-title">
          {t("nav.projects")}{data ? <span className="mono muted"> · {shownCount}{shownCount !== data.length ? `/${data.length}` : ""}</span> : null}
        </h2>
        <div className="list-tools">
          <RefreshButton onRefresh={refreshAll} />
          {bandFilter && (
            <button type="button" className="chip-clear" onClick={() => setBandFilter(null)}>
              {selected} · {t(`band.${bandOf(bandFilter)}`)} ✕
            </button>
          )}
          <input
            type="search"
            className="search"
            placeholder={t("common.search")}
            aria-label={t("common.search")}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
      </div>

      {loading && !data && (
        <div className="pgrid" aria-busy="true" aria-label={t("list.loadingAria")}>
          {Array.from({ length: 6 }, (_, i) => <CardSkeleton key={i} i={i} />)}
        </div>
      )}
      {error && (
        <div className="alert err" role="alert">
          {t("list.loadError", { error })}{" "}
          <button type="button" className="btn sm" onClick={reload}>{t("common.retry")}</button>
        </div>
      )}
      {data && data.length === 0 && (
        <p className="empty">{t("list.empty")}</p>
      )}
      {data && data.length > 0 && groups.length === 0 && (
        <p className="empty">{t("list.noMatch")}</p>
      )}
      {groups.map((g) => (
        <section key={g.org} className="org-group" aria-label={t("list.orgGroupAria", { org: g.org })}>
          <header className="org-head">
            <h3>{g.org}</h3>
            {g.org !== primary && <span className="org-tag">{t("list.external")}</span>}
            <span className="mono muted">
              {g.items.length}
              {g.items.length !== g.total ? `/${g.total}` : ""} {t("posture.projects")}
            </span>
          </header>
          <div className="pgrid">
            {g.items.map((p, i) => <ProjectCard key={p.key} item={p} index={i} spark={sparkSeries?.[p.key]} onSelect={onSelect} />)}
          </div>
        </section>
      ))}
    </div>
  );
}
