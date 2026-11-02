import { useEffect, useRef, useState, CSSProperties } from "react";

/**
 * Fire_Wolf developer easter egg — self-contained, zero dependencies beyond React.
 * Konami code (↑↑↓↓←→←→ B A) summons a fire wolf that howls. Inline styles, so it
 * drops into any React project regardless of CSS framework.
 *
 * Two visual/audio modes:
 *  - **Bundled (default):** an illustrated SVG fire wolf + a howl synthesized via
 *    Web Audio (no asset files needed).
 *  - **Custom assets:** pass `frames` (image URLs) to animate them like a GIF, and/or
 *    `howlSrc` (an audio file URL, e.g. an .mp3) to play a real howl. The kit ships a
 *    ready set under `assets/firewolf/` (Lobo-1..4.png + lobo.mp3) — copy them to your
 *    app's public dir and pass their URLs.
 *
 * Examples:
 *   <FireWolfEasterEgg developer="Fire_Wolf" />
 *   <FireWolfEasterEgg
 *      developer="Fire_Wolf.cl"
 *      frames={["/wolf/Lobo-1.png","/wolf/Lobo-2.png","/wolf/Lobo-3.png","/wolf/Lobo-4.png"]}
 *      howlSrc="/wolf/lobo.mp3" />
 */
const SEQUENCE = [
  "ArrowUp", "ArrowUp", "ArrowDown", "ArrowDown",
  "ArrowLeft", "ArrowRight", "ArrowLeft", "ArrowRight", "b", "a",
];

function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const t = target.tagName;
  return t === "INPUT" || t === "TEXTAREA" || t === "SELECT" || target.isContentEditable;
}

function playHowl() {
  try {
    const Ctx = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
    const ctx = new Ctx();
    const t = ctx.currentTime;
    const D = 2.0;
    const master = ctx.createGain();
    master.connect(ctx.destination);
    master.gain.setValueAtTime(0.0001, t);
    master.gain.exponentialRampToValueAtTime(0.32, t + 0.18);
    master.gain.setValueAtTime(0.32, t + D - 0.6);
    master.gain.exponentialRampToValueAtTime(0.0001, t + D);
    const lp = ctx.createBiquadFilter();
    lp.type = "lowpass"; lp.frequency.value = 1700; lp.connect(master);
    const glide = (o: OscillatorNode, lo: number, hi: number) => {
      o.frequency.setValueAtTime(lo, t);
      o.frequency.linearRampToValueAtTime(hi, t + 0.5);
      o.frequency.setValueAtTime(hi, t + D - 0.9);
      o.frequency.linearRampToValueAtTime(hi * 0.7, t + D - 0.1);
    };
    const osc = ctx.createOscillator(); osc.type = "sawtooth"; glide(osc, 300, 620); osc.connect(lp);
    const sub = ctx.createOscillator(); sub.type = "sine"; glide(sub, 150, 310);
    const subG = ctx.createGain(); subG.gain.value = 0.5; sub.connect(subG); subG.connect(lp);
    const lfo = ctx.createOscillator(); lfo.frequency.value = 5.5;
    const lfoG = ctx.createGain(); lfoG.gain.value = 14; lfo.connect(lfoG); lfoG.connect(osc.frequency);
    [osc, sub, lfo].forEach((n) => n.start(t));
    [osc, sub, lfo].forEach((n) => n.stop(t + D + 0.1));
    osc.onended = () => ctx.close();
  } catch { /* audio unavailable */ }
}

function FireWolf() {
  return (
    <svg viewBox="-30 -34 316 322" width="240" height="240" role="img" aria-label="Fire Wolf">
      <defs>
        <linearGradient id="fw-fire" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#fde047" /><stop offset="45%" stopColor="#f97316" /><stop offset="100%" stopColor="#b91c1c" />
        </linearGradient>
        <linearGradient id="fw-flame" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#fef08a" /><stop offset="40%" stopColor="#fb923c" /><stop offset="100%" stopColor="#dc2626" />
        </linearGradient>
        <radialGradient id="fw-eye" cx="50%" cy="50%" r="60%">
          <stop offset="0%" stopColor="#fffbeb" /><stop offset="60%" stopColor="#fde047" /><stop offset="100%" stopColor="#f59e0b" />
        </radialGradient>
        <filter id="fw-glow" x="-40%" y="-40%" width="180%" height="180%">
          <feGaussianBlur stdDeviation="6" result="b" /><feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge>
        </filter>
        <filter id="fw-flicker" x="-30%" y="-30%" width="160%" height="160%">
          <feTurbulence type="fractalNoise" baseFrequency="0.02 0.06" numOctaves="2" seed="7" result="n">
            <animate attributeName="baseFrequency" dur="5s" values="0.02 0.05;0.025 0.085;0.02 0.05" repeatCount="indefinite" />
          </feTurbulence>
          <feDisplacementMap in="SourceGraphic" in2="n" scale="11" xChannelSelector="R" yChannelSelector="G" />
        </filter>
      </defs>
      <g filter="url(#fw-flicker)" opacity="0.92">
        <g filter="url(#fw-glow)" fill="url(#fw-flame)">
          <path d="M118 70 C 92 24 110 -6 128 -30 C 146 -6 164 24 138 70 Z" />
          <path d="M96 66 C 60 30 60 -4 50 -26 C 86 4 110 30 124 64 Z" />
          <path d="M160 66 C 196 30 196 -4 206 -26 C 170 4 146 30 132 64 Z" />
          <path d="M48 150 C 2 140 -22 116 -34 92 C 8 108 30 118 58 132 Z" />
          <path d="M208 150 C 254 140 278 116 290 92 C 248 108 226 118 198 132 Z" />
          <path d="M128 246 C 92 206 94 156 128 36 C 162 156 164 206 128 246 Z" />
          <path d="M84 240 C 56 208 54 162 84 96 C 94 156 104 168 94 198 C 100 220 98 230 84 240 Z" />
          <path d="M172 240 C 200 208 202 162 172 96 C 162 156 152 168 162 198 C 156 220 158 230 172 240 Z" />
          <path d="M54 232 C 36 208 36 172 52 128 C 58 162 68 172 60 204 C 66 218 64 226 54 232 Z" />
          <path d="M202 232 C 220 208 220 172 204 128 C 198 162 188 172 196 204 C 190 218 192 226 202 232 Z" />
        </g>
      </g>
      <g filter="url(#fw-glow)">
        <path d="M60 72 L38 16 L96 56 L128 46 L160 56 L218 16 L196 72 L226 122 L176 152 L150 196 L128 214 L106 196 L80 152 L30 122 Z"
          fill="url(#fw-fire)" stroke="#7c2d12" strokeWidth="2.5" strokeLinejoin="round" />
        <g stroke="#7c2d12" strokeWidth="1.6" strokeOpacity="0.45" fill="none" strokeLinecap="round">
          <path d="M128 46 L128 150" /><path d="M96 56 L128 150 L160 56" /><path d="M30 122 L80 152 L106 196" />
          <path d="M226 122 L176 152 L150 196" /><path d="M80 152 L128 150 L176 152" />
        </g>
        <path d="M52 60 L46 30 L84 58 Z" fill="#7c2d12" fillOpacity="0.35" />
        <path d="M204 60 L210 30 L172 58 Z" fill="#7c2d12" fillOpacity="0.35" />
        <path d="M78 104 L104 98 L100 117 L80 114 Z" fill="url(#fw-eye)" />
        <path d="M178 104 L152 98 L156 117 L176 114 Z" fill="url(#fw-eye)" />
        <path d="M106 196 L128 214 L150 196 L128 176 Z" fill="#7c2d12" fillOpacity="0.25" />
        <path d="M118 184 L138 184 L128 198 Z" fill="#3f1208" />
      </g>
    </svg>
  );
}

export function FireWolfEasterEgg({
  developer = "Fire_Wolf",
  subtitle = "developer",
  tagline = "hecho con 🔥",
  frames,
  howlSrc,
  frameMs = 140,
}: {
  developer?: string;
  subtitle?: string;
  tagline?: string;
  /** Image URLs animated like a GIF. If omitted, the built-in SVG wolf is shown. */
  frames?: string[];
  /** Audio file URL for the howl. If omitted, a synthesized howl is used. */
  howlSrc?: string;
  frameMs?: number;
}) {
  const [show, setShow] = useState(false);
  const [frame, setFrame] = useState(0);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  const howl = () => {
    if (howlSrc) {
      try {
        const a = new Audio(howlSrc);
        a.volume = 0.85;
        audioRef.current = a;
        a.play().catch(() => { /* autoplay blocked: stays silent */ });
      } catch { /* audio unavailable */ }
    } else {
      playHowl();
    }
  };

  useEffect(() => {
    let pos = 0;
    function onKey(e: KeyboardEvent) {
      if (isEditableTarget(e.target)) return;
      const key = e.key.length === 1 ? e.key.toLowerCase() : e.key;
      if (key === SEQUENCE[pos]) {
        pos += 1;
        if (pos === SEQUENCE.length) { pos = 0; howl(); setShow(true); }
      } else { pos = key === SEQUENCE[0] ? 1 : 0; }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [howlSrc]);

  // Animate frames + allow Esc to close while open.
  useEffect(() => {
    if (!show) return;
    let interval: number | undefined;
    if (frames && frames.length > 1) {
      interval = window.setInterval(() => setFrame((f) => (f + 1) % frames.length), frameMs);
    }
    function onKey(e: KeyboardEvent) { if (e.key === "Escape") setShow(false); }
    document.addEventListener("keydown", onKey);
    return () => {
      if (interval) window.clearInterval(interval);
      document.removeEventListener("keydown", onKey);
      if (audioRef.current) { audioRef.current.pause(); audioRef.current = null; }
      setFrame(0);
    };
  }, [show, frames, frameMs]);

  if (!show) return null;

  const overlay: CSSProperties = {
    position: "fixed", inset: 0, zIndex: 2147483000, display: "flex",
    alignItems: "center", justifyContent: "center", background: "rgba(0,0,0,0.85)",
  };
  const btn: CSSProperties = {
    borderRadius: 6, background: "#f97316", color: "#fff", border: "none",
    padding: "8px 16px", fontSize: 14, fontWeight: 600, cursor: "pointer",
  };

  return (
    <div style={overlay} onClick={() => setShow(false)}>
      <style>{"@keyframes fw-pulse{0%,100%{opacity:1}50%{opacity:.85}}"}</style>
      <div style={{ textAlign: "center", userSelect: "none" }} onClick={(e) => e.stopPropagation()}>
        <div style={{ animation: "fw-pulse 1.6s ease-in-out infinite", filter: "drop-shadow(0 0 25px rgba(249,115,22,.7))" }}>
          {frames && frames.length > 0 ? (
            <img
              src={frames[frame]}
              alt="Fire Wolf"
              draggable={false}
              style={{ width: "min(60vmin, 420px)", height: "min(60vmin, 420px)", objectFit: "contain" }}
            />
          ) : (
            <FireWolf />
          )}
        </div>
        <div style={{ marginTop: 12, fontSize: 30, fontWeight: 700, letterSpacing: 1, color: "#fb923c", textShadow: "0 0 18px rgba(249,115,22,.8)" }}>
          {developer}
        </div>
        <div style={{ marginTop: 4, fontSize: 13, textTransform: "uppercase", letterSpacing: "0.35em", color: "#cbd5e1" }}>{subtitle}</div>
        <div style={{ marginTop: 20, fontSize: 12, color: "#64748b" }}>{tagline}</div>
        <div style={{ marginTop: 24, display: "flex", gap: 12, justifyContent: "center", alignItems: "center" }}>
          <button style={btn} onClick={() => howl()}>🔊 ¡Aúúú! 🐺</button>
          <button style={{ background: "none", border: "none", color: "#94a3b8", fontSize: 14, cursor: "pointer" }} onClick={() => setShow(false)}>cerrar</button>
        </div>
      </div>
    </div>
  );
}
