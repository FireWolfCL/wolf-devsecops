import { useState, type ReactNode } from "react";

import { useT } from "../i18n";

export function errText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

/** href solo si es http(s); si no, null (nunca dejamos pasar un esquema raro). */
export function safeHref(url: string | null | undefined): string | null {
  return url && /^https?:\/\//i.test(url) ? url : null;
}

/** Enlace de salida a una herramienta. Abre en pestaña nueva, aislado del opener.
 * El destino se construye SIEMPRE en el backend desde config, nunca desde datos
 * de una fuente; aquí re-validamos el esquema por defensa en profundidad. */
export function ExtLink({
  href,
  children,
  className = "ext-link",
}: {
  href: string | null | undefined;
  children: ReactNode;
  className?: string;
}) {
  const safe = safeHref(href);
  if (!safe) return null;
  return (
    <a className={className} href={safe} target="_blank" rel="noopener noreferrer">
      {children} <span aria-hidden="true">↗</span>
    </a>
  );
}

/** Copia texto al portapapeles. El texto se pasa por prop; nunca se ejecuta. */
export function CopyButton({ text, label }: { text: string; label?: string }) {
  const t = useT();
  const [state, setState] = useState<"idle" | "ok" | "fail">("idle");
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setState("ok");
    } catch {
      setState("fail");
    }
    window.setTimeout(() => setState("idle"), 2000);
  };
  return (
    <button type="button" className="btn sm" onClick={copy}>
      {state === "ok" ? t("common.copied") : state === "fail" ? t("common.copyFailed") : (label ?? t("common.copy"))}
    </button>
  );
}
