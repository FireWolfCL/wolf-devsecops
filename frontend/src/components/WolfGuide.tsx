import { useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { useProjects } from "../hooks";
import { useI18n } from "../i18n";
import type { ProjectListItem } from "../api/types";

/**
 * WolfGuide — mascota guía (lobo cibernético) de Wolf DevSecOps.
 *
 * Un compañero persistente que flota en la esquina, LEE el contexto real del panel
 * (ruta actual + proyectos + postura + hallazgos) y recomienda el siguiente paso con
 * un botón que te lleva ahí. Nunca falsea el estado operacional (regla wolf-genjutsu):
 * cada consejo se deriva de datos reales; si no hay datos, lo dice.
 *
 * - SVG a medida en la paleta cyber del panel (sin dependencias, hereda el tema oscuro).
 * - Bilingüe: sigue el selector de idioma del panel (useI18n().lang).
 * - Respetuoso: colapsado por defecto, un punto cuando hay algo accionable, se puede
 *   silenciar ("no me molestes" → localStorage wolf_guide_off), Esc cierra, y honra
 *   prefers-reduced-motion.
 *
 * El Fire_Wolf naranja + aullido queda para el easter egg (Konami). Este es su primo
 * sobrio que trabaja: el guía.
 */

type Lang = "es" | "en";
type Tone = "info" | "warn" | "good";
interface Tip {
  id: string;
  tone: Tone;
  msg: Record<Lang, string>;
  cta?: { label: Record<Lang, string>; to: string };
}

const STORE_OFF = "wolf_guide_off";
const STORE_SEEN = "wolf_guide_seen"; // ids de tips ya auto-mostrados esta sesión (sessionStorage)

const ACCENT: Record<Tone, string> = {
  info: "#34d399", // verde — var(--accent)
  warn: "#f5a524", // ámbar — var(--warn)
  good: "#6f9fcf", // azul calmo
};

// Ranking de bandas: peor primero (para elegir el proyecto que más necesita atención).
const BAND_RANK: Record<string, number> = {
  critico: 0,
  en_riesgo: 1,
  evidencia_insuficiente: 2,
  sin_datos: 3,
  aceptable: 4,
  solido: 5,
};

function crit(p: ProjectListItem): number {
  return (p.findings?.critical ?? 0) + (p.container_findings?.critical ?? 0);
}
function activeTotal(p: ProjectListItem): number {
  const f = p.findings, c = p.container_findings;
  const sum = (s: typeof f) => s ? s.critical + s.high + s.medium + s.low : 0;
  return sum(f) + sum(c);
}

function reduceMotion(): boolean {
  try { return window.matchMedia("(prefers-reduced-motion: reduce)").matches; } catch { return false; }
}

/** Aullido corto sintetizado (Web Audio) — solo tras gesto del usuario. */
function howl() {
  try {
    const Ctx = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
    const ctx = new Ctx();
    const t = ctx.currentTime, D = 1.1;
    const g = ctx.createGain();
    g.gain.setValueAtTime(0.0001, t);
    g.gain.exponentialRampToValueAtTime(0.18, t + 0.12);
    g.gain.exponentialRampToValueAtTime(0.12, t + 0.6);
    g.gain.exponentialRampToValueAtTime(0.0001, t + D);
    const o = ctx.createOscillator();
    o.type = "sawtooth";
    o.frequency.setValueAtTime(320, t);
    o.frequency.exponentialRampToValueAtTime(560, t + 0.25);
    o.frequency.exponentialRampToValueAtTime(430, t + D);
    const lp = ctx.createBiquadFilter();
    lp.type = "lowpass"; lp.frequency.value = 1400;
    o.connect(lp).connect(g).connect(ctx.destination);
    o.start(t); o.stop(t + D + 0.05);
    o.onended = () => ctx.close();
  } catch { /* sin audio, sin drama */ }
}

/** El motor: de la ruta + los proyectos a UN consejo accionable. */
function pickTip(pathname: string, projects: ProjectListItem[] | null, loading: boolean): Tip {
  // Dentro de un proyecto concreto
  const mProj = pathname.match(/^\/projects\/([^/]+)(\/([^/]+))?/);
  if (mProj) {
    const key = decodeURIComponent(mProj[1]);
    const sub = mProj[3];
    const p = projects?.find((x) => x.key === key);
    if (sub === "code-review") {
      return { id: "review", tone: "info",
        msg: { es: "Elige un hallazgo, pulsa «Proponer fix» y reviso el código con IA. Si te convence, «Abrir rama» crea el PR en borrador.",
               en: "Pick a finding, hit “Propose fix” and I’ll review the code with AI. If you like it, “Open branch” creates a draft PR." } };
    }
    if (sub === "deps") return { id: "deps", tone: "info",
      msg: { es: "Inventario de dependencias: las de severidad alta son las que conviene subir de versión primero.",
             en: "Dependency inventory: the high-severity ones are the first worth bumping." } };
    if (sub === "history") return { id: "hist", tone: "info",
      msg: { es: "La curva de postura cuenta la historia: cada escaneo deja un punto. Si baja, algo nuevo entró sin arreglarse.",
             en: "The posture curve tells the story: each scan drops a point. A dip means something new slipped in." } };
    if (p && crit(p) > 0) return { id: `crit-${key}`, tone: "warn",
      msg: { es: `Olfateé ${crit(p)} hallazgo(s) crítico(s) en ${p.name}. Puedo proponerte el fix.`,
             en: `I sniffed out ${crit(p)} critical finding(s) in ${p.name}. I can propose the fix.` },
      cta: { label: { es: "Revisar con IA", en: "Review with AI" }, to: `/projects/${encodeURIComponent(key)}/code-review` } };
    if (p && activeTotal(p) > 0) return { id: `find-${key}`, tone: "info",
      msg: { es: `${activeTotal(p)} hallazgo(s) activo(s) aquí. Mira el detalle y, si quieres, los paso por la revisión IA.`,
             en: `${activeTotal(p)} active finding(s) here. Check the detail and I can run them through AI review.` },
      cta: { label: { es: "Revisión IA", en: "AI review" }, to: `/projects/${encodeURIComponent(key)}/code-review` } };
    return { id: `clean-${key}`, tone: "good",
      msg: { es: `${p?.name ?? "Este proyecto"} se ve limpio por ahora. Buen rastro. 🐺`,
             en: `${p?.name ?? "This project"} looks clean for now. Good trail. 🐺` } };
  }

  if (pathname.startsWith("/tools")) return { id: "tools", tone: "info",
    msg: { es: "Verde = conectado. Rojo = revisa el token de esa herramienta en Integraciones.",
           en: "Green = connected. Red = check that tool’s token under Integrations." },
    cta: { label: { es: "Integraciones", en: "Integrations" }, to: "/admin/integraciones" } };

  if (pathname.startsWith("/admin/settings")) return { id: "onboard", tone: "info",
    msg: { es: "Para sumar un repo: «Nuevo proyecto» y declara sus fuentes (github, sonarqube, defectdojo, trivy). Yo me encargo del resto.",
           en: "To add a repo: “New project” and declare its sources (github, sonarqube, defectdojo, trivy). I’ll handle the rest." } };

  if (pathname.startsWith("/admin")) return { id: "admin", tone: "info",
    msg: { es: "Zona admin: usuarios, integraciones, IA del reviewer y actualizaciones. ¿Qué cazamos hoy?",
           en: "Admin zone: users, integrations, the AI reviewer and updates. What are we hunting today?" } };

  // Dashboard / raíz
  if (loading && !projects) return { id: "sniff", tone: "info",
    msg: { es: "Olfateando el estado de la flota…", en: "Sniffing the fleet’s status…" } };

  const list = projects ?? [];
  if (list.length === 0) return { id: "empty", tone: "warn",
    msg: { es: "Aún no hay proyectos conectados. Conecta tu primer repo y empiezo a cazar hallazgos.",
           en: "No projects connected yet. Add your first repo and I’ll start hunting findings." },
    cta: { label: { es: "Conectar repo", en: "Connect repo" }, to: "/admin/settings" } };

  const attention = list
    .filter((p) => (BAND_RANK[p.posture_band] ?? 9) <= 1 || crit(p) > 0)
    .sort((a, b) => (BAND_RANK[a.posture_band] ?? 9) - (BAND_RANK[b.posture_band] ?? 9) || crit(b) - crit(a));
  if (attention.length > 0) {
    const worst = attention[0];
    return { id: "attn", tone: "warn",
      msg: { es: `${attention.length} proyecto(s) necesitan atención. El más urgente: ${worst.name}.`,
             en: `${attention.length} project(s) need attention. Most urgent: ${worst.name}.` },
      cta: { label: { es: `Ir a ${worst.name}`, en: `Go to ${worst.name}` }, to: `/projects/${encodeURIComponent(worst.key)}` } };
  }
  return { id: "solid", tone: "good",
    msg: { es: "La flota se ve sólida. Ningún crítico a la vista. Aullido merecido. 🐺",
           en: "The fleet looks solid. No criticals in sight. A howl is in order. 🐺" } };
}

// ─── El SVG del lobo cibernético ─────────────────────────────────────────────
function CyberWolf({ color, awake, rm }: { color: string; awake: boolean; rm: boolean }) {
  return (
    <svg viewBox="0 0 64 64" width="42" height="42" aria-hidden="true"
      style={{ filter: `drop-shadow(0 0 6px ${color}aa)`, overflow: "visible" }}>
      <defs>
        <linearGradient id="wg-fur" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#1b2531" />
          <stop offset="1" stopColor="#0f151c" />
        </linearGradient>
      </defs>
      {/* cabeza angular low-poly */}
      <path d="M32 7 L14 16 L10 30 L18 40 L24 54 L32 47 L40 54 L46 40 L54 30 L50 16 Z"
        fill="url(#wg-fur)" stroke={color} strokeWidth="1.6" strokeLinejoin="round" />
      {/* orejas */}
      <path d="M14 16 L12 4 L24 12 Z" fill="url(#wg-fur)" stroke={color} strokeWidth="1.6" strokeLinejoin="round" />
      <path d="M50 16 L52 4 L40 12 Z" fill="url(#wg-fur)" stroke={color} strokeWidth="1.6" strokeLinejoin="round" />
      {/* hocico */}
      <path d="M24 40 L32 46 L40 40 L36 34 L28 34 Z" fill="#11171e" stroke={color} strokeWidth="1.2" strokeLinejoin="round" />
      <circle cx="32" cy="44" r="1.6" fill={color} />
      {/* ojos neón */}
      <g className={awake && !rm ? "wg-blink" : undefined}>
        <path d="M20 26 L28 24 L26 30 Z" fill={color} />
        <path d="M44 26 L36 24 L38 30 Z" fill={color} />
      </g>
      {/* líneas de circuito */}
      <path d="M32 7 L32 20 M18 40 L26 34 M46 40 L38 34" fill="none" stroke={color} strokeWidth="0.9" opacity="0.55" />
    </svg>
  );
}

export function WolfGuide() {
  const { lang: appLang } = useI18n();
  const lang: Lang = appLang === "en" ? "en" : "es";
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const { data: projects, loading } = useProjects();

  const [off, setOff] = useState<boolean>(() => {
    try { return localStorage.getItem(STORE_OFF) === "1"; } catch { return false; }
  });
  const [open, setOpen] = useState(false);
  const rm = reduceMotion();

  const tip = useMemo(() => pickTip(pathname, projects, loading), [pathname, projects, loading]);
  const color = ACCENT[tip.tone];
  const actionable = tip.tone !== "good" && tip.tone !== "info" || !!tip.cta;

  // Auto-asomar UNA vez por sesión cuando un consejo es accionable (warn o con CTA).
  const peekedRef = useRef(false);
  useEffect(() => {
    if (off || open || peekedRef.current) return;
    if (tip.tone !== "warn" && !tip.cta) return;
    let seen: string[] = [];
    try { seen = JSON.parse(sessionStorage.getItem(STORE_SEEN) || "[]"); } catch { /* */ }
    if (seen.includes(tip.id)) return;
    const timer = setTimeout(() => {
      setOpen(true);
      peekedRef.current = true;
      try { sessionStorage.setItem(STORE_SEEN, JSON.stringify([...seen, tip.id])); } catch { /* */ }
    }, 1400);
    return () => clearTimeout(timer);
  }, [tip.id, tip.tone, tip.cta, off, open]);

  // Esc cierra la burbuja.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  if (off) {
    return (
      <button type="button" onClick={() => { setOff(false); try { localStorage.removeItem(STORE_OFF); } catch { /* */ } }}
        title={lang === "es" ? "Reactivar al guía lobo" : "Re-enable the wolf guide"}
        style={{ ...dotBtn, opacity: 0.5 }}>🐺</button>
    );
  }

  const L = (m: Record<Lang, string>) => m[lang];

  return (
    <>
      <style>{KEYFRAMES}</style>
      <div style={wrap} data-wolf-guide>
        {open && (
          <div role="dialog" aria-label={lang === "es" ? "Guía lobo" : "Wolf guide"}
            style={{ ...bubble, borderColor: `${color}66`, animation: rm ? undefined : "wg-rise .22s ease-out" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
              <span style={{ ...badge, background: `${color}22`, color, border: `1px solid ${color}55` }}>
                {lang === "es" ? "Tu guía" : "Your guide"}
              </span>
              <strong style={{ fontSize: 13, color: "var(--text)" }}>Cibernético</strong>
              <button type="button" aria-label={lang === "es" ? "Cerrar" : "Close"} onClick={() => setOpen(false)}
                style={closeBtn}>✕</button>
            </div>
            <p style={{ margin: "0 0 10px", fontSize: 13.5, lineHeight: 1.5, color: "var(--text)" }}>{L(tip.msg)}</p>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              {tip.cta && (
                <button type="button"
                  onClick={() => { navigate(tip.cta!.to); setOpen(false); }}
                  style={{ ...cta, background: color, boxShadow: `0 2px 12px ${color}55` }}>
                  {L(tip.cta.label)} →
                </button>
              )}
              {tip.tone === "good" && (
                <button type="button" onClick={howl} style={ghostBtn} title={lang === "es" ? "Aullar" : "Howl"}>
                  {lang === "es" ? "Aullar 🌙" : "Howl 🌙"}
                </button>
              )}
              <button type="button" onClick={() => { setOff(true); setOpen(false); try { localStorage.setItem(STORE_OFF, "1"); } catch { /* */ } }}
                style={{ ...ghostBtn, marginLeft: "auto" }}>
                {lang === "es" ? "No me molestes" : "Don’t nudge me"}
              </button>
            </div>
          </div>
        )}
        <button type="button" onClick={() => setOpen((v) => !v)}
          aria-label={lang === "es" ? "Abrir guía lobo" : "Open wolf guide"}
          aria-expanded={open}
          style={{ ...avatar, borderColor: `${color}88`, boxShadow: `0 6px 20px rgba(0,0,0,.45), 0 0 0 1px ${color}33` }}
          className={!rm && actionable && !open ? "wg-pulse" : undefined}>
          <CyberWolf color={color} awake={open} rm={rm} />
          {actionable && !open && <span style={{ ...dot, background: color }} />}
        </button>
      </div>
    </>
  );
}

// ─── estilos ─────────────────────────────────────────────────────────────────
const wrap: CSSProperties = { position: "fixed", right: 20, bottom: 20, zIndex: 900, display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 10 };
const avatar: CSSProperties = { position: "relative", width: 60, height: 60, borderRadius: "50%", border: "1.5px solid", background: "radial-gradient(circle at 50% 35%, #1a222c, #0e1217)", display: "grid", placeItems: "center", cursor: "pointer", padding: 0 };
const dot: CSSProperties = { position: "absolute", top: 6, right: 6, width: 11, height: 11, borderRadius: "50%", border: "2px solid #0e1217" };
const bubble: CSSProperties = { width: 290, maxWidth: "calc(100vw - 40px)", background: "var(--surface, #1a222c)", border: "1px solid", borderRadius: 14, padding: 14, boxShadow: "0 12px 40px rgba(0,0,0,.5)" };
const badge: CSSProperties = { fontSize: 10.5, fontWeight: 700, letterSpacing: ".04em", textTransform: "uppercase", padding: "2px 7px", borderRadius: 6 };
const closeBtn: CSSProperties = { marginLeft: "auto", background: "transparent", border: "none", color: "var(--muted, #9fb0c0)", cursor: "pointer", fontSize: 14, lineHeight: 1, padding: 2 };
const cta: CSSProperties = { border: "none", color: "#0b1016", fontWeight: 700, fontSize: 12.5, padding: "7px 12px", borderRadius: 8, cursor: "pointer" };
const ghostBtn: CSSProperties = { background: "transparent", border: "1px solid var(--border, #2a3542)", color: "var(--muted, #9fb0c0)", fontSize: 12, padding: "6px 10px", borderRadius: 8, cursor: "pointer" };
const dotBtn: CSSProperties = { position: "fixed", right: 20, bottom: 20, zIndex: 900, width: 40, height: 40, borderRadius: "50%", border: "1px solid var(--border,#2a3542)", background: "var(--surface,#1a222c)", cursor: "pointer", fontSize: 18 };

const KEYFRAMES = `
@keyframes wg-rise { from { opacity:0; transform: translateY(8px) } to { opacity:1; transform:none } }
@keyframes wg-pulse-k { 0%,100% { box-shadow:0 6px 20px rgba(0,0,0,.45), 0 0 0 1px rgba(52,211,153,.2) } 50% { box-shadow:0 6px 24px rgba(0,0,0,.45), 0 0 0 7px rgba(52,211,153,.08) } }
.wg-pulse { animation: wg-pulse-k 2.4s ease-in-out infinite; }
@keyframes wg-blink-k { 0%,92%,100% { opacity:1 } 95% { opacity:.15 } }
.wg-blink { animation: wg-blink-k 4.5s ease-in-out infinite; transform-box: fill-box; }
@media (prefers-reduced-motion: reduce) { .wg-pulse, .wg-blink { animation: none !important; } }
`;
