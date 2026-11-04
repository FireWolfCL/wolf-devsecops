import { useCallback, useEffect, useRef, useState, type CSSProperties } from "react";
import { useNavigate } from "react-router-dom";

import { fetchEvents, fetchUnreadCount, markEventsRead } from "../api/client";
import { useI18n } from "../i18n";
import type { EventItem } from "../api/types";

/**
 * Campana de notificaciones. Sondea los no-leídos, y al abrirla lista los cambios
 * (vuln nueva, crítico resuelto, mejora/empeora de postura) y los marca como leídos.
 * Bilingüe es/en (sigue el idioma del panel); arma el texto desde kind + deltas.
 */

type Lang = "es" | "en";
const COLOR: Record<string, string> = {
  good: "#34d399", warn: "#f5a524", danger: "#f87171", info: "#6f9fcf",
};

// Marcadores geométricos coherentes con el panel (sin emojis): el glifo es un
// trazo fino sobre un chip teñido con el color de severidad. Mismo vocabulario
// que .sev-pill / .band-pill (fondo rgba + borde del color).
const GLYPH_KINDS = ["resolved_critical", "resolved_high", "new_critical", "new_high", "improved", "worsened"];

function GlyphPath({ kind }: { kind: string }) {
  switch (kind) {
    case "resolved_critical":
    case "resolved_high":
      return <path d="M3.5 8.5 L6.5 11.5 L12.5 5" />; // check: resuelto
    case "new_critical":
    case "new_high":
      return <path d="M8 3.5 V12.5 M3.5 8 H12.5" />; // plus: nuevo hallazgo
    case "improved":
      return <path d="M8 12.5 V4 M4.5 7.5 L8 4 L11.5 7.5" />; // flecha arriba: mejora
    case "worsened":
      return <path d="M8 3.5 V12 M4.5 8.5 L8 12 L11.5 8.5" />; // flecha abajo: retroceso
    default:
      return <circle cx="8" cy="8" r="2.2" />;
  }
}

function KindMarker({ kind, color }: { kind: string; color: string }) {
  const isDot = !GLYPH_KINDS.includes(kind);
  return (
    <span aria-hidden="true" style={{ ...marker, color, background: `${color}1f`, borderColor: `${color}59` }}>
      <svg viewBox="0 0 16 16" width="15" height="15"
        fill={isDot ? color : "none"} stroke={isDot ? "none" : color}
        strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
        <GlyphPath kind={kind} />
      </svg>
    </span>
  );
}

function BellIcon() {
  return (
    <svg viewBox="0 0 24 24" width="19" height="19" aria-hidden="true"
      fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
      <path d="M6 9.5a6 6 0 0 1 12 0c0 3.6 1 5 1.8 5.9.4.4.1 1.1-.5 1.1H4.7c-.6 0-.9-.7-.5-1.1C5 14.5 6 13.1 6 9.5Z" />
      <path d="M9.8 19a2.3 2.3 0 0 0 4.4 0" />
    </svg>
  );
}

const BELL_CSS = `
.nb-bell { color: var(--muted); transition: color 160ms cubic-bezier(0.16,1,0.3,1); }
.nb-bell:hover { color: var(--text); }
.nb-bell.open { color: var(--accent); }
.nb-bell:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; border-radius: 8px; }
@media (prefers-reduced-motion: reduce) { .nb-bell { transition: none; } }
`;

function rel(iso: string, lang: Lang): string {
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  const m = Math.floor(s / 60), h = Math.floor(m / 60), d = Math.floor(h / 24);
  if (lang === "en") {
    if (d > 0) return `${d}d ago`; if (h > 0) return `${h}h ago`;
    if (m > 0) return `${m}m ago`; return "just now";
  }
  if (d > 0) return `hace ${d}d`; if (h > 0) return `hace ${h}h`;
  if (m > 0) return `hace ${m}min`; return "recién";
}

function text(ev: EventItem, lang: Lang): string {
  const c = Math.abs(ev.crit_delta), h = Math.abs(ev.high_delta);
  const es = lang === "es";
  switch (ev.kind) {
    case "new_critical": return es ? `${c} vulnerabilidad(es) crítica(s) nueva(s)` : `${c} new critical vulnerabilit${c === 1 ? "y" : "ies"}`;
    case "resolved_critical": return es ? `${c} crítico(s) resuelto(s)` : `${c} critical(s) resolved`;
    case "new_high": return es ? `${h} hallazgo(s) alto(s) nuevo(s)` : `${h} new high finding(s)`;
    case "resolved_high": return es ? `${h} alto(s) resuelto(s)` : `${h} high(s) resolved`;
    case "improved": return es ? `Mejoró: ${ev.band_from} → ${ev.band_to}` : `Improved: ${ev.band_from} → ${ev.band_to}`;
    case "worsened": return es ? `Empeoró: ${ev.band_from} → ${ev.band_to}` : `Worsened: ${ev.band_from} → ${ev.band_to}`;
    case "ci_failed": return es ? "CI en rojo" : "CI is failing";
    case "gate_error": return es ? "Quality gate en ERROR" : "Quality gate is ERROR";
    case "exception_expired": return es ? "Venció una aceptación de riesgo" : "A risk acceptance expired";
    case "exception_expire_failed": return es ? "Venció un riesgo y no se pudo revertir" : "A risk expired but revert failed";
    default: return ev.kind;
  }
}

export function NotificationBell() {
  const { lang: appLang } = useI18n();
  const lang: Lang = appLang === "en" ? "en" : "es";
  const navigate = useNavigate();
  const [unread, setUnread] = useState(0);
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<EventItem[] | null>(null);
  const boxRef = useRef<HTMLDivElement>(null);

  const poll = useCallback(() => {
    fetchUnreadCount().then((r) => setUnread(r.count)).catch(() => { /* silencioso */ });
  }, []);

  useEffect(() => {
    poll();
    const id = setInterval(poll, 60000);
    return () => clearInterval(id);
  }, [poll]);

  // Cerrar al click afuera / Esc.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("mousedown", onDown); window.removeEventListener("keydown", onKey); };
  }, [open]);

  const toggle = () => {
    const next = !open;
    setOpen(next);
    if (next) {
      fetchEvents(50).then((r) => setItems(r.events)).catch(() => setItems([]));
      if (unread > 0) markEventsRead().then(() => setUnread(0)).catch(() => { /* */ });
    }
  };

  return (
    <div ref={boxRef} style={{ position: "relative" }}>
      <style>{BELL_CSS}</style>
      <button type="button" onClick={toggle} aria-label={lang === "es" ? "Notificaciones" : "Notifications"}
        aria-expanded={open} className={`nb-bell${open ? " open" : ""}`} style={bellBtn}
        title={lang === "es" ? "Novedades" : "What's new"}>
        <BellIcon />
        {unread > 0 && <span style={badge}>{unread > 99 ? "99+" : unread}</span>}
      </button>
      {open && (
        <div role="dialog" style={panel}>
          <div style={head}>
            <strong style={{ fontSize: 13 }}>{lang === "es" ? "Novedades" : "What's new"}</strong>
            <button type="button" onClick={() => { navigate("/activity"); setOpen(false); }} style={link}>
              {lang === "es" ? "Ver reporte →" : "View report →"}
            </button>
          </div>
          <div style={{ maxHeight: 360, overflowY: "auto" }}>
            {items === null && <p style={empty}>{lang === "es" ? "Cargando…" : "Loading…"}</p>}
            {items !== null && items.length === 0 && (
              <p style={empty}>{lang === "es" ? "Sin novedades. Todo tranquilo. 🐺" : "Nothing new. All quiet. 🐺"}</p>
            )}
            {items?.map((ev) => {
              const col = COLOR[ev.severity] ?? COLOR.info;
              return (
                <button key={ev.id} type="button"
                  onClick={() => { navigate(`/projects/${encodeURIComponent(ev.project_key)}`); setOpen(false); }}
                  style={row}>
                  <KindMarker kind={ev.kind} color={col} />
                  <span style={{ flex: 1, minWidth: 0 }}>
                    <span style={{ display: "block", fontSize: 12.5, color: "var(--text)" }}>{text(ev, lang)}</span>
                    <span style={{ fontSize: 11, color: "var(--muted)" }}>{ev.project_key} · {rel(ev.created_at, lang)}</span>
                  </span>
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

const bellBtn: CSSProperties = { position: "relative", background: "transparent", border: "none", cursor: "pointer", padding: 5, lineHeight: 0, borderRadius: 8, display: "grid", placeItems: "center" };
const badge: CSSProperties = { position: "absolute", top: 0, right: 0, minWidth: 15, height: 15, padding: "0 3px", borderRadius: 999, background: "var(--danger)", color: "#1a0d0d", fontSize: 9.5, fontWeight: 700, fontFamily: "var(--mono)", display: "grid", placeItems: "center", border: "2px solid var(--bg-alt)" };
const panel: CSSProperties = { position: "absolute", right: 0, top: "calc(100% + 8px)", width: 320, maxWidth: "calc(100vw - 24px)", background: "var(--surface)", border: "1px solid var(--border)", borderRadius: 12, boxShadow: "0 12px 40px rgba(0,0,0,.5)", zIndex: 950, overflow: "hidden" };
const head: CSSProperties = { display: "flex", alignItems: "center", justifyContent: "space-between", padding: "10px 12px", borderBottom: "1px solid var(--border)" };
const link: CSSProperties = { background: "transparent", border: "none", color: "var(--accent)", cursor: "pointer", fontSize: 12 };
const empty: CSSProperties = { padding: "20px 14px", textAlign: "center", color: "var(--muted)", fontSize: 12.5, margin: 0 };
const row: CSSProperties = { display: "flex", gap: 10, width: "100%", textAlign: "left", background: "transparent", border: "none", borderBottom: "1px solid var(--border)", padding: "10px 12px", cursor: "pointer", alignItems: "center" };
const marker: CSSProperties = { flexShrink: 0, width: 24, height: 24, borderRadius: 7, border: "1px solid", display: "grid", placeItems: "center" };
