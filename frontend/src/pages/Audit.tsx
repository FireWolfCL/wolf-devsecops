// Export del audit trail (admin · feature Pro). El backend es la autoridad (402);
// aquí solo se muestra el botón o el aviso Pro según la edición.
import { useState } from "react";

import { downloadAudit } from "../api/client";
import { ProNotice } from "../components/Pro";
import { errText } from "../components/util";
import { useFeature } from "../hooks";
import { useT } from "../i18n";

export function Audit() {
  const t = useT();
  const isPro = useFeature("audit.export");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run(format: "json" | "csv") {
    setBusy(true);
    setError(null);
    try {
      await downloadAudit(format);
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="audit-admin">
      <h3 style={{ marginTop: 0 }}>{t("audit.title")}</h3>
      <p className="muted">{t("audit.desc")}</p>
      {!isPro ? (
        <ProNotice msg={t("audit.proFeature")} />
      ) : (
        <>
          <div className="form-actions">
            <button type="button" className="btn primary" disabled={busy} onClick={() => run("json")}>
              {t("audit.exportJson")}
            </button>
            <button type="button" className="btn" disabled={busy} onClick={() => run("csv")}>
              {t("audit.exportCsv")}
            </button>
          </div>
          {error && <div className="alert err" role="alert">{error}</div>}
        </>
      )}
    </div>
  );
}
