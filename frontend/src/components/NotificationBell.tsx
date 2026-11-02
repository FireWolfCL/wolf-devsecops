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
const ICON: Record<string, string> = {
  improved: "▲", worsened: "▼", new_critical: "✚", resolved_critical: "✔",
  new_high: "✚", resolved_high: "✔",
};

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
    case "resolved_critical": return es ? `${c} crítico(s) resuelto(s) 🎉` : `${c} critical(s) resolved 🎉`;
    case "new_high": return es ? `${h} hallazgo(s) alto(s) nuevo(s)` : `${h} new high finding(s)`;
    case "resolved_high": return es ? `${h} alto(s) resuelto(s)` : `${h} high(s) resolved`;
    case "improved": return es ? `Mejoró: ${ev.band_from} → ${ev.band_to}` : `Improved: ${ev.band_from} → ${ev.band_to}`;
    case "worsened": return es ? `Empeoró: ${ev.band_from} → ${ev.band_to}` : `Worsened: ${ev.band_from} → ${ev.band_to}`;
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
      <button type="button" onClick={toggle} aria-label={lang === "es" ? "Notificaciones" : "Notifications"}
        aria-expanded={open} style={bellBtn} title={lang === "es" ? "Novedades" : "What's new"}>
        <span aria-hidden="true" style={{ fontSize: 17 }}>🔔</span>
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
                  <span style={{ ...dot2, color: col }}>{ICON[ev.kind] ?? "•"}</span>
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

const bellBtn: CSSProperties = { position: "relative", background: "transparent", border: "none", cursor: "pointer", padding: 4, lineHeight: 1 };
const badge: CSSProperties = { position: "absolute", top: -2, right: -2, minWidth: 16, height: 16, padding: "0 4px", borderRadius: 8, background: "#f87171", color: "#0b1016", fontSize: 10, fontWeight: 700, display: "grid", placeItems: "center" };
const panel: CSSProperties = { position: "absolute", right: 0, top: "calc(100% + 8px)", width: 320, maxWidth: "calc(100vw - 24px)", background: "var(--surface)", border: "1px solid var(--border)", borderRadius: 12, boxShadow: "0 12px 40px rgba(0,0,0,.5)", zIndex: 950, overflow: "hidden" };
const head: CSSProperties = { display: "flex", alignItems: "center", justifyContent: "space-between", padding: "10px 12px", borderBottom: "1px solid var(--border)" };
const link: CSSProperties = { background: "transparent", border: "none", color: "var(--accent)", cursor: "pointer", fontSize: 12 };
const empty: CSSProperties = { padding: "20px 14px", textAlign: "center", color: "var(--muted)", fontSize: 12.5, margin: 0 };
const row: CSSProperties = { display: "flex", gap: 10, width: "100%", textAlign: "left", background: "transparent", border: "none", borderBottom: "1px solid var(--border)", padding: "10px 12px", cursor: "pointer", alignItems: "flex-start" };
const dot2: CSSProperties = { fontSize: 13, fontWeight: 700, width: 16, textAlign: "center", flexShrink: 0 };
