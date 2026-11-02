// Inventario de dependencias de un proyecto (Trivy). Responde: ¿qué paquetes
// usa el proyecto y cuáles tienen CVE con o sin fix? Todo es DATO del backend,
// renderizado como texto plano. `degraded` = inventario parcial (solo con CVE):
// se avisa de forma explícita para no confundirlo con "el proyecto está limpio".
import { useMemo, useState } from "react";

import { fetchDependencies } from "../api/client";
import type { DependencyItem } from "../api/types";
import { fmtDate } from "../components/util";
import { useAsync } from "../hooks";
import { useT } from "../i18n";

const MAX_ROWS = 500;

export function DependencyInventoryView({ projectKey, onBack }: { projectKey: string; onBack: () => void }) {
  const t = useT();
  const { data, loading, error, reload } = useAsync(() => fetchDependencies(projectKey), [projectKey]);
  const [query, setQuery] = useState("");
  const [onlyVuln, setOnlyVuln] = useState(false);

  const packages = data?.packages ?? [];
  const vulnCount = useMemo(() => packages.filter((p) => p.vulnerable).length, [packages]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    const match = (p: DependencyItem) =>
      !q ||
      [p.name, p.version, p.ecosystem, p.target, p.fixed_version ?? "", ...p.cve_ids].some((v) =>
        v.toLowerCase().includes(q),
      );
    return packages
      .filter((p) => (onlyVuln ? p.vulnerable : true))
      .filter(match)
      // Vulnerables primero (lo accionable), luego por nombre.
      .sort((a, b) => Number(b.vulnerable) - Number(a.vulnerable) || a.name.localeCompare(b.name));
  }, [packages, query, onlyVuln]);

  const shown = filtered.slice(0, MAX_ROWS);

  return (
    <div className="detail deps-view">
      <button type="button" className="btn-back" onClick={onBack}>{t("common.backToProject")}</button>
      <div className="detail-title-row">
        <h2 className="detail-title">{t("deps.title")}</h2>
      </div>
      <p className="muted">{t("deps.subtitle", { name: projectKey })}</p>

      {loading && !data && <p className="state" aria-busy="true">{t("deps.loadingAria")}…</p>}
      {error && (
        <div className="alert err" role="alert">
          {t("deps.loadError", { key: projectKey, error })}{" "}
          <button type="button" className="btn sm" onClick={reload}>{t("common.retry")}</button>
        </div>
      )}

      {data && (
        <>
          {data.degraded && <div className="alert warn" role="status">{t("deps.degraded")}</div>}
          <p className="muted mono deps-meta">{t("deps.generated", { date: fmtDate(data.generated_at) })}</p>

          <div className="list-bar">
            <p className="mono muted">
              {t("deps.count", { shown: filtered.length, total: packages.length, vuln: vulnCount })}
            </p>
            <div className="list-tools">
              <label className="check-inline">
                <input type="checkbox" checked={onlyVuln} onChange={(e) => setOnlyVuln(e.target.checked)} />
                {t("deps.onlyVuln")}
              </label>
              <input
                type="search"
                className="search"
                placeholder={t("deps.filter")}
                aria-label={t("deps.filter")}
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
            </div>
          </div>

          {packages.length === 0 ? (
            <p className="empty">{t("deps.empty")}</p>
          ) : filtered.length === 0 ? (
            <p className="empty">{t("deps.noMatch")}</p>
          ) : (
            <div className="table-scroll">
              <table className="findings-table deps-table">
                <thead>
                  <tr>
                    <th>{t("deps.col.package")}</th>
                    <th>{t("deps.col.version")}</th>
                    <th>{t("deps.col.ecosystem")}</th>
                    <th>{t("deps.col.target")}</th>
                    <th>{t("deps.col.status")}</th>
                    <th>{t("deps.col.fix")}</th>
                  </tr>
                </thead>
                <tbody>
                  {shown.map((p, i) => (
                    <tr key={`${p.target}|${p.name}|${p.version}|${i}`} className={p.vulnerable ? "dep-vuln" : undefined}>
                      <td className="mono">{p.name}</td>
                      <td className="mono muted">{p.version}</td>
                      <td>{p.ecosystem}</td>
                      <td className="mono muted dep-target">{p.target}</td>
                      <td>
                        {p.vulnerable ? (
                          <>
                            <span className="sev-pill high">{t("deps.vulnerable")}</span>
                            {p.cve_ids.length > 0 && <div className="mono dep-cves">{p.cve_ids.join(", ")}</div>}
                          </>
                        ) : (
                          <span className="muted">{t("deps.clean")}</span>
                        )}
                      </td>
                      <td className={p.fixed_version ? "fix mono" : "muted"}>
                        {p.vulnerable ? (p.fixed_version ?? t("deps.noFix")) : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {filtered.length > MAX_ROWS && <p className="muted">{t("deps.capped", { n: MAX_ROWS })}</p>}
        </>
      )}
    </div>
  );
}
